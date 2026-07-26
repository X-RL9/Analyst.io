"""
pipeline_real.py
================
My final orchestrator -- same mode-selection logic as pipeline_core.py,
but calling REAL functions instead of mocks. This is what you'll actually
run in Colab.

My testing status summary (read this before running):
  REAL + TESTED (by me):
    - lbo_financing.py logic (debt capacity, capital structure, paydown
      schedule, IRR, reverse-solve) -- I fully tested this with synthetic
      numbers
    - pdf_extraction.py statement-location -- I tested this against your
      real Amazon 10-K, and it correctly handles the "header text also
      appears in cross-references" edge case I found
    - wacc_beta.py regression/WACC math -- I tested this with synthetic
      data; it correctly recovers a known beta, and correctly uses median
      not mean
    - dcf_engine.py DCF math -- I tested this with synthetic numbers;
      terminal value share sits in the expected 60-80% range

  REAL BUT UNTESTED (needs you to run in Colab):
    - data_layer.py's actual yfinance calls (get_financials_from_ticker,
      find_peers, run_comps) -- I wrote these to the best of my knowledge
      of the yfinance API but Yahoo Finance isn't reachable from my
      sandbox, so I cannot confirm these work until you run them
    - get_financials_from_pdf's actual Claude API call -- needs your key

Run this, see what breaks, and tell me the exact error -- that's the
fastest way for me to get the untested parts working.
"""

from data_layer import get_financials, find_peers, run_comps
from wacc_beta import calculate_beta, get_risk_free_rate, calculate_market_risk_premium, \
    estimate_beta_and_premium_from_peers, calculate_wacc
from dcf_engine import run_dcf_real
from lbo_financing import build_capital_structure, project_debt_schedule, calculate_returns
from pipeline_orchestrator import recommend_financing  # my rules-based advisory, already tested


def run_full_dcf(financials: dict) -> dict:
    """
    I wire together: risk-free rate -> market risk premium -> beta (with
    my peer-median fallback if unlisted) -> WACC -> DCF. This is the real
    version of run_dcf() that pipeline_core.py mocked out.
    """
    risk_free_rate = get_risk_free_rate()
    market_risk_premium = calculate_market_risk_premium(risk_free_rate)

    ticker = financials.get("ticker")
    try:
        beta = calculate_beta(ticker)
    except Exception:
        # My fallback for unlisted firms / firms with unreliable stock
        # data, as you specified: I use peer median beta and return
        peers = find_peers(financials, n_peers=10)
        fallback = estimate_beta_and_premium_from_peers(peers, risk_free_rate)
        beta = fallback["beta"]
        market_risk_premium = fallback["market_risk_premium"]

    wacc_result = calculate_wacc(financials["ebitda"], financials, beta,
                                  risk_free_rate, market_risk_premium)

    dcf_result = run_dcf_real(financials, wacc_result)
    dcf_result["beta"] = beta
    dcf_result["risk_free_rate"] = risk_free_rate
    dcf_result["market_risk_premium"] = market_risk_premium
    return dcf_result


def run_pipeline(company_input: str, modes: set, input_type: str = "ticker",
                  target_irr: float = 0.20, anthropic_api_key: str = None,
                  n_peers: int = 10) -> dict:
    """
    I use the same mode-selection contract as pipeline_core.py's version:
    modes = any non-empty subset of {"comps", "dcf", "lbo"}.
    I run DCF internally whenever "lbo" is requested, but only include it
    in the output if "dcf" was ALSO explicitly requested.
    """
    valid_modes = {"comps", "dcf", "lbo"}
    if not modes or not modes.issubset(valid_modes):
        raise ValueError(f"modes must be a non-empty subset of {valid_modes}")

    results = {"company_input": company_input, "modes_requested": modes}
    financials, dcf_result = None, None

    if "dcf" in modes or "lbo" in modes:
        financials = get_financials(company_input, input_type, anthropic_api_key)
        dcf_result = run_full_dcf(financials)
        if "dcf" in modes:
            results["dcf"] = dcf_result

    if "comps" in modes:
        if financials is None:
            financials = get_financials(company_input, input_type, anthropic_api_key)
        peers = find_peers(financials, n_peers=n_peers)
        results["comps"] = run_comps(financials, peers)

    if "lbo" in modes:
        if not dcf_result.get("feasible", True):
            # LBO needs enterprise_value/exit_ebitda from the DCF -- if the
            # DCF itself was infeasible (e.g. WACC <= terminal growth),
            # there's nothing valid to build an LBO on top of. I surface
            # that clearly rather than crashing on a None enterprise_value.
            results["lbo"] = {"feasible": False}
            results["financing_recommendation"] = {
                "recommendation": "NOT_FEASIBLE",
                "notes": [
                    "LBO can't be built because the underlying DCF was infeasible: "
                    + dcf_result.get("reason", "enterprise value could not be computed.")
                ],
            }
        else:
            ebitda = dcf_result["ebitda"]
            fcf_projections = dcf_result["fcf_projections"]
            exit_ebitda = dcf_result["exit_ebitda"]
            exit_multiple = dcf_result["entry_multiple"]
            purchase_price = dcf_result["enterprise_value"]

            structure = build_capital_structure(purchase_price, ebitda)
            schedule = project_debt_schedule(structure["senior_debt"], structure["sub_debt"],
                                              structure["senior_rate"], structure["sub_rate"], fcf_projections)
            returns = calculate_returns(structure["equity_check"], schedule, exit_ebitda, exit_multiple)

            results["lbo"] = {"capital_structure": structure, "debt_schedule": schedule, "returns": returns}
            results["financing_recommendation"] = recommend_financing(results["lbo"], target_irr)

    return results


if __name__ == "__main__":
    print("Run this in Colab with a real ticker, e.g.:")
    print()
    print("  from pipeline_real import run_pipeline")
    print("  result = run_pipeline('AAPL', modes={'dcf', 'comps', 'lbo'})")
    print("  print(result)")
    print()
    print("For PDF input:")
    print("  result = run_pipeline(")
    print("      '/path/to/10k.pdf', modes={'dcf'}, input_type='pdf',")
    print("      anthropic_api_key='your-NEW-key-here'")
    print("  )")
    print()
    print("Please tell me the exact error message if/when something")
    print("breaks -- my best guesses for likely candidates: yfinance")
    print("field names in data_layer.py, or the screener query syntax")
    print("in find_peers().")
