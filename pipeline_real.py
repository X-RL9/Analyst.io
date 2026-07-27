"""
pipeline_real.py
================
My final orchestrator -- same mode-selection logic as pipeline_core.py,
but calling REAL functions instead of mocks.
"""

from data_layer import get_financials, find_peers, run_comps
from wacc_beta import calculate_beta, get_risk_free_rate, calculate_market_risk_premium, \
    estimate_beta_and_premium_from_peers, estimate_capital_weights_from_peers, calculate_wacc
from dcf_engine import run_dcf_real
from lbo_financing import build_capital_structure, project_debt_schedule, calculate_returns
from pipeline_orchestrator import recommend_financing


def run_full_dcf(financials: dict) -> dict:
    """
    I wire together: risk-free rate -> market risk premium -> beta (with
    my peer-median fallback if unlisted) -> WACC -> DCF.

    Note: the peer-median fallback below is exactly why a PDF upload
    needs sector/industry attached (via manual_sector/manual_industry in
    get_financials) even when the user only wants DCF -- calculate_beta()
    always fails for ticker="PDF_UPLOAD", so this fallback always fires
    for PDF input, and find_peers() always needs sector/industry.

    Same reasoning extends one step further: a PDF also has no market_cap
    (no live share price), so I reuse the SAME peer list to estimate a
    peer-median capital structure (debt/(debt+equity)) via
    estimate_capital_weights_from_peers(), and pass that into calculate_wacc
    as a fallback for the equity/debt weighting.
    """
    risk_free_rate = get_risk_free_rate()
    market_risk_premium = calculate_market_risk_premium(risk_free_rate)
    peer_capital_weights = None

    ticker = financials.get("ticker")
    try:
        beta = calculate_beta(ticker)
    except Exception:
        peers = find_peers(financials, n_peers=10)
        fallback = estimate_beta_and_premium_from_peers(peers, risk_free_rate)
        beta = fallback["beta"]
        market_risk_premium = fallback["market_risk_premium"]
        if financials.get("market_cap") is None:
            peer_capital_weights = estimate_capital_weights_from_peers(peers)

    wacc_result = calculate_wacc(financials["ebitda"], financials, beta,
                                  risk_free_rate, market_risk_premium,
                                  peer_capital_weights)

    dcf_result = run_dcf_real(financials, wacc_result)
    dcf_result["beta"] = beta
    dcf_result["risk_free_rate"] = risk_free_rate
    dcf_result["market_risk_premium"] = market_risk_premium
    return dcf_result


def run_pipeline(company_input: str, modes: set, input_type: str = "ticker",
                  target_irr: float = 0.20, anthropic_api_key: str = None,
                  n_peers: int = 10, manual_sector: str = None,
                  manual_industry: str = None) -> dict:
    """
    Same mode-selection contract as before: modes = any non-empty subset
    of {"comps", "dcf", "lbo"}.

    manual_sector / manual_industry: only relevant when input_type="pdf".
    Pass these from the Streamlit dropdown (see industry_options.py) --
    they flow into get_financials(), which attaches them to the
    financials dict exactly as if they'd come from yfinance, so
    find_peers() works for both the DCF beta-fallback and the comps mode.
    """
    valid_modes = {"comps", "dcf", "lbo"}
    if not modes or not modes.issubset(valid_modes):
        raise ValueError(f"modes must be a non-empty subset of {valid_modes}")

    results = {"company_input": company_input, "modes_requested": modes}
    financials, dcf_result = None, None

    if "dcf" in modes or "lbo" in modes:
        financials = get_financials(company_input, input_type, anthropic_api_key,
                                     manual_sector, manual_industry)
        dcf_result = run_full_dcf(financials)
        if "dcf" in modes:
            results["dcf"] = dcf_result

    if "comps" in modes:
        if financials is None:
            financials = get_financials(company_input, input_type, anthropic_api_key,
                                         manual_sector, manual_industry)
        peers = find_peers(financials, n_peers=n_peers)
        results["comps"] = run_comps(financials, peers)

    if "lbo" in modes:
        if not dcf_result.get("feasible", True):
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
