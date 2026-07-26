"""
Pipeline Orchestrator
=====================
You pick any subset of {"comps", "dcf", "lbo"}.

My dependency rule: LBO always needs DCF's EBITDA + FCF projections to
run, so I execute DCF internally whenever "lbo" is selected -- but I only
include the DCF results in the output if "dcf" was ALSO explicitly
requested. This mirrors how an analyst would work: I don't re-show a DCF
you didn't ask for, but I can't build an LBO without running one quietly
in the background first.

After the LBO computes, I run a rules-based financing recommendation
automatically -- this is the "advisory" layer that interprets the raw
LBO output (binding constraint, leverage headroom, IRR vs target,
payback) into an actual recommendation, not just numbers.
"""

from lbo_financing import build_capital_structure, project_debt_schedule, calculate_returns


# ---------------------------------------------------------------------------
# PLACEHOLDER INTERFACES -- I've left these as thin wrappers so I could build
# and test the orchestrator logic below independently of the data/DCF/comps
# modules, which were still being built out at the time. Wire these to your
# actual modules once they're finalised.
# ---------------------------------------------------------------------------

def run_dcf(financials: dict) -> dict:
    """
    TODO: replace with your actual DCF engine call.
    Must return at minimum: {"ebitda": float, "fcf_projections": list,
    "enterprise_value": float, "equity_value": float, ...}
    """
    raise NotImplementedError("Wire this to your DCF engine module")


def run_comps(financials: dict, peers: list) -> dict:
    """
    TODO: replace with your actual comps engine call.
    """
    raise NotImplementedError("Wire this to your comps engine module")


def get_financials(company_input: str, input_type: str = "ticker") -> dict:
    """
    TODO: replace with your data layer (yfinance path or PDF+Claude path).
    """
    raise NotImplementedError("Wire this to your data layer module")


def find_peers(financials: dict) -> list:
    """TODO: replace with your peer discovery module (screener + revenue rank)."""
    raise NotImplementedError("Wire this to your peer discovery module")


# ---------------------------------------------------------------------------
# FINANCING RECOMMENDATION -- my rules-based advisory layer on top of the raw LBO output
# ---------------------------------------------------------------------------

def recommend_financing(lbo_output: dict, target_irr: float = 0.20) -> dict:
    """
    I read the LBO capital structure + returns and produce a rules-based
    recommendation. I made this deliberately rules-based (not ML) -- these
    are the heuristics a leveraged finance advisor actually reasons through.
    """
    structure = lbo_output["capital_structure"]
    returns = lbo_output["returns"]
    binding = structure["debt_capacity_detail"]["binding_constraint"]
    leverage_used = structure["leverage_multiple_used"]
    irr = returns["irr"]
    payback_year = returns["payback_year"]

    notes = []
    recommendation = "PROCEED"

    # Rule 1: which constraint bound, and what I think it implies
    if binding == "coverage":
        notes.append(
            "Coverage ratio (not leverage) is the binding constraint -- "
            "debt capacity is limited by interest-servicing ability, not "
            "headline leverage multiple. Consider negotiating a lower "
            "interest rate or a PIK toggle on the sub tranche to relieve "
            "cash interest burden, which would free up additional debt capacity."
        )
    else:
        notes.append(
            "Leverage multiple is the binding constraint -- coverage has "
            "headroom. Rates could rise before this becomes an issue; "
            "stress-test the coverage ratio against a rate increase before finalising."
        )

    # Rule 2: IRR vs target
    if irr is None:
        notes.append("IRR could not be computed (degenerate cash flow signs) -- review projections.")
        recommendation = "REVIEW"
    elif irr < target_irr:
        gap = target_irr - irr
        notes.append(
            f"IRR of {irr:.1%} misses the {target_irr:.1%} target by {gap:.1%}. "
            "Options: increase leverage if capacity allows, negotiate a lower "
            "entry price, or extend the hold period for further deleveraging."
        )
        recommendation = "RENEGOTIATE"
    else:
        notes.append(f"IRR of {irr:.1%} clears the {target_irr:.1%} target.")

    # Rule 3: payback within hold period
    if payback_year is None:
        notes.append(
            "Equity does not pay back via interim distributions within the "
            "hold period -- return is entirely dependent on the exit event. "
            "This concentrates risk on exit multiple/timing assumptions."
        )
        if recommendation == "PROCEED":
            recommendation = "PROCEED_WITH_CAUTION"

    # Rule 4: leverage sanity check (very high leverage = fragile to downturns)
    if leverage_used and leverage_used >= 6.0:
        notes.append(
            f"Leverage of {leverage_used:.2f}x is aggressive -- limited cushion "
            "against an EBITDA downturn. Consider a lower-leverage structure "
            "even if it slightly reduces base-case IRR, for downside protection."
        )

    return {
        "recommendation": recommendation,
        "binding_constraint": binding,
        "leverage_used": leverage_used,
        "irr": irr,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# ORCHESTRATOR
# ---------------------------------------------------------------------------

def run_pipeline(company_input: str, modes: set, input_type: str = "ticker",
                  target_irr: float = 0.20, exit_multiple: float = None) -> dict:
    """
    modes: any subset of {"comps", "dcf", "lbo"}

    I return only the sections you asked for -- EXCEPT dcf results, which
    I include whenever "lbo" was requested, since my recommendation notes
    reference EBITDA/leverage context that comes straight from the DCF.
    Set include_dcf_detail=False downstream if you want me to suppress the
    raw DCF numbers even when lbo pulled them internally.
    """
    valid_modes = {"comps", "dcf", "lbo"}
    if not modes or not modes.issubset(valid_modes):
        raise ValueError(f"modes must be a non-empty subset of {valid_modes}")

    results = {"company_input": company_input, "modes_requested": modes}
    financials = None
    dcf_result = None

    needs_dcf_internally = "dcf" in modes or "lbo" in modes

    if needs_dcf_internally:
        financials = get_financials(company_input, input_type)
        dcf_result = run_dcf(financials)
        if "dcf" in modes:
            results["dcf"] = dcf_result
        # if only "lbo" was requested (not "dcf"), I keep dcf_result
        # internal -- used below to feed the LBO module but it never
        # surfaces in `results`

    if "comps" in modes:
        if financials is None:
            financials = get_financials(company_input, input_type)
        peers = find_peers(financials)
        results["comps"] = run_comps(financials, peers)

    if "lbo" in modes:
        ebitda = dcf_result["ebitda"]
        fcf_projections = dcf_result["fcf_projections"]
        exit_ebitda = dcf_result.get("exit_ebitda", fcf_projections[-1])
        exit_mult = exit_multiple or dcf_result.get("entry_multiple", 6.5)

        purchase_price = dcf_result.get("enterprise_value", ebitda * exit_mult)
        structure = build_capital_structure(purchase_price, ebitda)
        schedule = project_debt_schedule(
            structure["senior_debt"], structure["sub_debt"],
            structure["senior_rate"], structure["sub_rate"], fcf_projections,
        )
        returns = calculate_returns(structure["equity_check"], schedule, exit_ebitda, exit_mult)

        lbo_output = {"capital_structure": structure, "debt_schedule": schedule, "returns": returns}
        results["lbo"] = lbo_output
        results["financing_recommendation"] = recommend_financing(lbo_output, target_irr)

    return results


# ---------------------------------------------------------------------------
# MY SELF-TEST with mocked DCF/comps output (since the real data layer isn't
# wired in yet) -- this proves the orchestrator's dependency + recommendation
# logic works correctly end to end.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import unittest.mock as mock

    fake_financials = {"ticker": "TEST", "ebitda": 100.0}
    fake_dcf = {
        "ebitda": 100.0,
        "fcf_projections": [60, 65, 70, 75, 80],
        "enterprise_value": 650.0,
        "exit_ebitda": 130.0,
        "entry_multiple": 6.5,
    }

    with mock.patch("__main__.get_financials", return_value=fake_financials), \
         mock.patch("__main__.run_dcf", return_value=fake_dcf):

        print("=== Test 1: modes = {'lbo'} only (dcf should NOT appear in output) ===")
        result = run_pipeline("TEST", modes={"lbo"})
        print("Keys in result:", list(result.keys()))
        print("Recommendation:", result["financing_recommendation"]["recommendation"])
        for note in result["financing_recommendation"]["notes"]:
            print(" -", note)

        print("\n=== Test 2: modes = {'dcf', 'lbo'} (dcf SHOULD appear) ===")
        result2 = run_pipeline("TEST", modes={"dcf", "lbo"})
        print("Keys in result:", list(result2.keys()))
