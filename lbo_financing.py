"""
LBO Financing / Advisory Module
================================
Sits alongside the DCF engine. Takes projected FCF + EBITDA from the DCF
module and answers the leveraged-finance questions:

  FORWARD PASS (default — how advisers actually size a deal):
    cash flow capacity -> debt ceiling -> capital structure -> debt paydown
    schedule -> equity IRR (output)

  REVERSE PASS (optional — how sponsors decide what to bid in an auction):
    fixed leverage + target IRR -> solve for max entry price (purchase price)

Design note: coverage ratio (EBITDA / interest) and leverage multiple
(Debt / EBITDA) are BOTH constraints. Real lenders bind on whichever is
tighter, so debt capacity = min(leverage-implied debt, coverage-implied debt).
"""

import numpy_financial as npf
from scipy.optimize import brentq


# ---------------------------------------------------------------------------
# 1. DEBT CAPACITY — how much can this company actually borrow?
# ---------------------------------------------------------------------------

def compute_debt_capacity(
    ebitda: float,
    interest_rate: float,
    max_leverage_multiple: float = 5.0,
    min_coverage_ratio: float = 2.5,
) -> dict:
    """
    Debt capacity is bound by TWO constraints — take whichever is tighter:
      1. Leverage constraint:   Debt <= max_leverage_multiple * EBITDA
      2. Coverage constraint:   EBITDA / (Debt * interest_rate) >= min_coverage_ratio
                                => Debt <= EBITDA / (min_coverage_ratio * interest_rate)
    """
    leverage_implied_debt = max_leverage_multiple * ebitda
    coverage_implied_debt = ebitda / (min_coverage_ratio * interest_rate)

    max_debt = min(leverage_implied_debt, coverage_implied_debt)
    binding_constraint = (
        "leverage" if leverage_implied_debt < coverage_implied_debt else "coverage"
    )

    return {
        "max_debt": max_debt,
        "leverage_implied_debt": leverage_implied_debt,
        "coverage_implied_debt": coverage_implied_debt,
        "binding_constraint": binding_constraint,
    }


# ---------------------------------------------------------------------------
# 2. CAPITAL STRUCTURE — split purchase price into debt tranches + equity
# ---------------------------------------------------------------------------

def build_capital_structure(
    purchase_price: float,
    ebitda: float,
    senior_rate: float = 0.07,
    sub_rate: float = 0.11,
    max_leverage_multiple: float = 5.0,
    min_coverage_ratio: float = 2.5,
    senior_pct_of_debt: float = 0.70,
) -> dict:
    """
    Sizes total debt against capacity, splits it into a senior tranche
    (cheaper, priority repayment) and a subordinated tranche (more
    expensive, more flexible), and fills the remainder with equity.

    Blended interest rate is used for the coverage-ratio check since both
    tranches draw on the same EBITDA for interest cover.
    """
    blended_rate = senior_pct_of_debt * senior_rate + (1 - senior_pct_of_debt) * sub_rate

    capacity = compute_debt_capacity(
        ebitda, blended_rate, max_leverage_multiple, min_coverage_ratio
    )
    max_debt = capacity["max_debt"]

    # Debt actually taken is capped by capacity, but never more than the
    # purchase price itself (no negative equity check)
    total_debt = min(max_debt, purchase_price)
    equity_check = purchase_price - total_debt

    senior_debt = total_debt * senior_pct_of_debt
    sub_debt = total_debt * (1 - senior_pct_of_debt)

    return {
        "purchase_price": purchase_price,
        "total_debt": total_debt,
        "equity_check": equity_check,
        "senior_debt": senior_debt,
        "sub_debt": sub_debt,
        "senior_rate": senior_rate,
        "sub_rate": sub_rate,
        "leverage_multiple_used": total_debt / ebitda if ebitda else None,
        "debt_capacity_detail": capacity,
    }


# ---------------------------------------------------------------------------
# 3. DEBT PAYDOWN SCHEDULE — waterfall: senior gets paid down first
# ---------------------------------------------------------------------------

def project_debt_schedule(
    senior_debt: float,
    sub_debt: float,
    senior_rate: float,
    sub_rate: float,
    fcf_projections: list,  # annual free cash flow available for debt service
    mandatory_amort_pct: float = 0.05,  # % of ORIGINAL senior principal repaid/year
    cash_sweep_pct: float = 0.75,  # % of remaining FCF (after mandatory amort) swept to debt
) -> list:
    """
    Builds a year-by-year schedule. Cash sweep waterfall: mandatory
    senior amortisation first, then excess FCF sweeps senior debt down
    before touching sub debt (standard seniority in real deals).

    Returns a list of dicts, one per year.
    """
    schedule = []
    senior_balance = senior_debt
    sub_balance = sub_debt
    mandatory_amort_amount = senior_debt * mandatory_amort_pct

    for year, fcf in enumerate(fcf_projections, start=1):
        interest = senior_balance * senior_rate + sub_balance * sub_rate
        cash_after_interest = fcf - interest

        # Mandatory senior amortisation (can't exceed remaining balance)
        mand_amort = min(mandatory_amort_amount, senior_balance)
        senior_balance -= mand_amort
        cash_after_mand = cash_after_interest - mand_amort

        # Cash sweep: excess cash pays down senior first, then sub
        sweep_available = max(cash_after_mand, 0) * cash_sweep_pct
        senior_sweep = min(sweep_available, senior_balance)
        senior_balance -= senior_sweep
        remaining_sweep = sweep_available - senior_sweep
        sub_sweep = min(remaining_sweep, sub_balance)
        sub_balance -= sub_sweep

        total_debt_paydown = mand_amort + senior_sweep + sub_sweep
        cash_to_equity = cash_after_mand - senior_sweep - sub_sweep  # residual FCF/dividend

        schedule.append({
            "year": year,
            "fcf": fcf,
            "interest": interest,
            "mandatory_amort": mand_amort,
            "senior_sweep": senior_sweep,
            "sub_sweep": sub_sweep,
            "total_debt_paydown": total_debt_paydown,
            "senior_balance": senior_balance,
            "sub_balance": sub_balance,
            "total_debt_balance": senior_balance + sub_balance,
            "cash_to_equity": cash_to_equity,
        })

    return schedule


# ---------------------------------------------------------------------------
# 4. RETURNS — payback period + equity IRR (the OUTPUT of the forward pass)
# ---------------------------------------------------------------------------

def calculate_returns(
    equity_check: float,
    debt_schedule: list,
    exit_ebitda: float,
    exit_multiple: float,
) -> dict:
    """
    equity IRR cash flow stream:
      t=0: -equity_check (invested)
      t=1..n-1: interim cash_to_equity distributions (often 0 in real deals,
                but included here since your DCF may project dividends)
      t=n: final cash_to_equity + exit equity value (enterprise value at exit
           minus remaining debt)
    """
    remaining_debt_at_exit = debt_schedule[-1]["total_debt_balance"]
    exit_enterprise_value = exit_ebitda * exit_multiple
    exit_equity_value = exit_enterprise_value - remaining_debt_at_exit

    cash_flows = [-equity_check]
    for i, year in enumerate(debt_schedule):
        distribution = year["cash_to_equity"]
        if i == len(debt_schedule) - 1:
            distribution += exit_equity_value
        cash_flows.append(distribution)

    irr = npf.irr(cash_flows)

    # Payback period: first year cumulative distributions >= equity_check
    cumulative = 0.0
    payback_year = None
    for i, year in enumerate(debt_schedule, start=1):
        cumulative += year["cash_to_equity"]
        if cumulative >= equity_check and payback_year is None:
            payback_year = i

    return {
        "cash_flows": cash_flows,
        "irr": irr,
        "exit_equity_value": exit_equity_value,
        "remaining_debt_at_exit": remaining_debt_at_exit,
        "payback_year": payback_year,  # None = doesn't pay back within hold period
    }


# ---------------------------------------------------------------------------
# 5. REVERSE PASS — fix leverage + target IRR, solve for max entry price
# ---------------------------------------------------------------------------

def solve_max_entry_price(
    target_irr: float,
    ebitda: float,
    fcf_projections: list,
    exit_ebitda: float,
    exit_multiple: float,
    senior_rate: float = 0.07,
    sub_rate: float = 0.11,
    max_leverage_multiple: float = 5.0,
    min_coverage_ratio: float = 2.5,
    senior_pct_of_debt: float = 0.70,
    mandatory_amort_pct: float = 0.05,
    cash_sweep_pct: float = 0.75,
    price_search_multiples: tuple = (0.5, 15.0),  # search bounds as multiples of EBITDA
    # NB: keep the upper bound realistic. Debt capacity is capped by the
    # coverage/leverage constraints and does NOT grow with price, so at
    # very high multiples the equity check balloons while distributions
    # stay flat -- this makes the IRR root numerically unstable well
    # outside normal deal territory (which is why 50x blew up above).
) -> dict:
    """
    Root-finds the purchase price such that resulting equity IRR == target_irr.
    Leverage assumptions stay FIXED (as a sponsor would fix them going into
    an auction) — only price moves.
    """

    def irr_gap(purchase_price):
        cap_structure = build_capital_structure(
            purchase_price, ebitda, senior_rate, sub_rate,
            max_leverage_multiple, min_coverage_ratio, senior_pct_of_debt,
        )
        schedule = project_debt_schedule(
            cap_structure["senior_debt"], cap_structure["sub_debt"],
            senior_rate, sub_rate, fcf_projections,
            mandatory_amort_pct, cash_sweep_pct,
        )
        returns = calculate_returns(
            cap_structure["equity_check"], schedule, exit_ebitda, exit_multiple
        )
        computed_irr = returns["irr"]
        if computed_irr is None or computed_irr != computed_irr:  # None or NaN
            return 1e6  # force brentq away from degenerate region
        return computed_irr - target_irr

    lo = price_search_multiples[0] * ebitda
    hi = price_search_multiples[1] * ebitda
    max_price = brentq(irr_gap, lo, hi, xtol=1e-2)

    # Rebuild full detail at the solved price for output
    final_structure = build_capital_structure(
        max_price, ebitda, senior_rate, sub_rate,
        max_leverage_multiple, min_coverage_ratio, senior_pct_of_debt,
    )
    final_schedule = project_debt_schedule(
        final_structure["senior_debt"], final_structure["sub_debt"],
        senior_rate, sub_rate, fcf_projections,
        mandatory_amort_pct, cash_sweep_pct,
    )
    final_returns = calculate_returns(
        final_structure["equity_check"], final_schedule, exit_ebitda, exit_multiple
    )

    return {
        "max_entry_price": max_price,
        "implied_entry_multiple": max_price / ebitda if ebitda else None,
        "capital_structure": final_structure,
        "debt_schedule": final_schedule,
        "returns": final_returns,
    }


# ---------------------------------------------------------------------------
# QUICK SELF-TEST — run this file directly to sanity-check the whole flow
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ebitda = 100.0  # £m
    fcf_projections = [60, 65, 70, 75, 80]  # 5-year hold, £m/year

    print("=== FORWARD PASS: purchase price = 6.5x EBITDA ===")
    purchase_price = 6.5 * ebitda
    cap = build_capital_structure(purchase_price, ebitda)
    print(f"Total debt: {cap['total_debt']:.1f}  Equity check: {cap['equity_check']:.1f}  "
          f"Leverage: {cap['leverage_multiple_used']:.2f}x  "
          f"Binding: {cap['debt_capacity_detail']['binding_constraint']}")

    schedule = project_debt_schedule(
        cap["senior_debt"], cap["sub_debt"], cap["senior_rate"], cap["sub_rate"],
        fcf_projections,
    )
    for row in schedule:
        print(f"  Year {row['year']}: interest={row['interest']:.1f} "
              f"paydown={row['total_debt_paydown']:.1f} "
              f"remaining debt={row['total_debt_balance']:.1f}")

    returns = calculate_returns(cap["equity_check"], schedule, exit_ebitda=130, exit_multiple=6.5)
    print(f"IRR: {returns['irr']:.1%}  Exit equity value: {returns['exit_equity_value']:.1f}  "
          f"Payback year: {returns['payback_year']}")

    print("\n=== REVERSE PASS: solve max entry price for 25% target IRR ===")
    solved = solve_max_entry_price(
        target_irr=0.25, ebitda=ebitda, fcf_projections=fcf_projections,
        exit_ebitda=130, exit_multiple=6.5,
    )
    print(f"Max entry price: {solved['max_entry_price']:.1f}  "
          f"Implied entry multiple: {solved['implied_entry_multiple']:.2f}x  "
          f"IRR achieved: {solved['returns']['irr']:.1%}")
