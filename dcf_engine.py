"""
dcf_engine.py
=============
Real DCF calculation. I project FCF forward, apply a Gordon growth
terminal value, and discount everything back at WACC to get enterprise
value, then equity value.

Note: I deliberately kept the growth-rate projection simple here (flat
historical growth rate applied forward) as a reasonable default -- you
may want to replace this with analyst estimates or a more detailed
build-up (revenue growth x margin assumptions) later. I've flagged where
to plug that in.
"""


def project_fcf(last_fcf: float, growth_rate: float, years: int = 5) -> list:
    """
    My simple flat-growth FCF projection. TODO if you want more
    sophistication: replace with a declining growth rate (fade to terminal
    growth), or a revenue-growth x margin build-up instead of a single FCF
    growth number.
    """
    return [last_fcf * (1 + growth_rate) ** t for t in range(1, years + 1)]


def terminal_value(final_year_fcf: float, wacc: float, terminal_growth: float) -> float:
    """I use Gordon growth terminal value: TV = FCF_(n+1) / (WACC - g)"""
    if wacc <= terminal_growth:
        raise ValueError(
            f"WACC ({wacc:.2%}) must exceed terminal growth ({terminal_growth:.2%}) "
            "or the terminal value formula produces a negative/infinite result."
        )
    next_year_fcf = final_year_fcf * (1 + terminal_growth)
    return next_year_fcf / (wacc - terminal_growth)


def discount_cash_flows(cash_flows: list, wacc: float) -> float:
    """I calculate the present value of a stream of cash flows at rate wacc."""
    return sum(cf / (1 + wacc) ** (t + 1) for t, cf in enumerate(cash_flows))


def run_dcf_real(financials: dict, wacc_result: dict, growth_rate: float = 0.05,
                  terminal_growth: float = 0.025, projection_years: int = 5) -> dict:
    """
    My full DCF: I project FCF, discount the explicit period, add the
    discounted terminal value, get enterprise value, then subtract net
    debt for equity value.

    I require financials to contain: free_cash_flow, total_debt,
    cash_and_equivalents, shares_outstanding, ebitda.
    wacc_result comes straight from wacc_beta.calculate_wacc().
    """
    wacc = wacc_result["wacc"]
    last_fcf = financials["free_cash_flow"]

    fcf_projections = project_fcf(last_fcf, growth_rate, projection_years)
    pv_explicit = discount_cash_flows(fcf_projections, wacc)

    tv = terminal_value(fcf_projections[-1], wacc, terminal_growth)
    pv_terminal = tv / (1 + wacc) ** projection_years

    enterprise_value = pv_explicit + pv_terminal

    net_debt = financials["total_debt"] - financials["cash_and_equivalents"]
    equity_value = enterprise_value - net_debt

    exit_ebitda = financials["ebitda"] * (1 + growth_rate) ** projection_years

    return {
        "ebitda": financials["ebitda"],
        "fcf_projections": fcf_projections,
        "pv_explicit_period": pv_explicit,
        "pv_terminal_value": pv_terminal,
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
        "exit_ebitda": exit_ebitda,
        "entry_multiple": enterprise_value / financials["ebitda"] if financials["ebitda"] else None,
        "wacc": wacc,
        "terminal_growth": terminal_growth,
        "growth_rate_used": growth_rate,
    }


if __name__ == "__main__":
    # My self-test, using the SAME numbers I used throughout our earlier
    # testing (ebitda=100, fcf pattern ~60-80) so results are comparable to
    # what you've already seen in lbo_financing.py / pipeline_core.py
    financials = {
        "free_cash_flow": 60.0, "total_debt": 200.0, "cash_and_equivalents": 50.0,
        "shares_outstanding": 100.0, "ebitda": 100.0,
    }
    wacc_result = {"wacc": 0.09}

    result = run_dcf_real(financials, wacc_result, growth_rate=0.05, terminal_growth=0.025)

    print("=== My DCF self-test ===")
    print(f"FCF projections: {[round(f, 1) for f in result['fcf_projections']]}")
    print(f"PV of explicit period: {result['pv_explicit_period']:.1f}")
    print(f"PV of terminal value: {result['pv_terminal_value']:.1f}")
    print(f"Enterprise Value: {result['enterprise_value']:.1f}")
    print(f"Equity Value: {result['equity_value']:.1f}")
    print(f"Implied entry multiple: {result['entry_multiple']:.2f}x EBITDA")

    # I sanity-check that terminal value dominates for a 5-year DCF (this is
    # normal and expected -- I'd flag it to you if it looked off)
    tv_share = result["pv_terminal_value"] / result["enterprise_value"]
    print(f"\nTerminal value as % of total EV: {tv_share:.1%}")
    print("(Typically 60-80% for a 5yr DCF -- if this seems too dominant,")
    print(" I'd call it a known DCF characteristic, not a bug, but it's")
    print(" worth sanity-checking your growth/WACC assumptions if it's")
    print(" much higher than that.)")
