"""
app.py -- my Streamlit interface for the DCF / Comps / LBO pipeline.

MOCK MODE: the real backend modules (get_financials, run_dcf, run_comps,
find_peers) are still placeholders (see pipeline_orchestrator.py). I've
built this app to run on mocked data so you can test the full UI/flow
now. Flip MOCK_MODE to False once those modules are wired in -- the
pipeline call itself (run_pipeline(...)) doesn't change, only the data
underneath it.

Run locally with: streamlit run app.py
"""

import streamlit as st
import pandas as pd

from lbo_financing import build_capital_structure, project_debt_schedule, calculate_returns
from pipeline_orchestrator import recommend_financing

MOCK_MODE = True  # flip to False once get_financials/run_dcf/run_comps/find_peers are real


# ---------------------------------------------------------------------------
# MY MOCK DATA -- delete this block once the real backend modules are wired in
# ---------------------------------------------------------------------------

def mock_get_financials(ticker: str) -> dict:
    return {"ticker": ticker.upper(), "sector": "Technology", "revenue": 500.0}


def mock_run_dcf(financials: dict) -> dict:
    ebitda = 100.0
    return {
        "ebitda": ebitda,
        "fcf_projections": [60, 65, 70, 75, 80],
        "enterprise_value": 650.0,
        "equity_value": 600.0,
        "exit_ebitda": 130.0,
        "entry_multiple": 6.5,
        "wacc": 0.09,
        "terminal_growth": 0.025,
    }


def mock_find_peers(financials: dict) -> list:
    return ["PEER1", "PEER2", "PEER3", "PEER4", "PEER5"]


def mock_run_comps(financials: dict, peers: list) -> pd.DataFrame:
    data = {
        "Ticker": [financials["ticker"]] + peers,
        "EV/EBITDA": [11.2, 10.5, 12.1, 9.8, 11.9, 10.7],
        "P/E": [22.4, 20.1, 24.3, 18.9, 23.5, 21.0],
        "EV/Sales": [3.1, 2.8, 3.4, 2.6, 3.2, 2.9],
    }
    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# PAGE CONFIG
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Valuation Pipeline", layout="wide")
st.title("DCF / Comps / LBO Valuation Pipeline")

if MOCK_MODE:
    st.warning(
        "Running in MOCK MODE -- backend modules (get_financials, run_dcf, "
        "run_comps, find_peers) aren't wired in yet. Numbers below are placeholders "
        "to test the interface, not real company data.",
        icon="⚠️",
    )

# ---------------------------------------------------------------------------
# SIDEBAR -- INPUT
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Input")

    input_type = st.radio("Input type", ["Ticker", "PDF Upload"], horizontal=True)

    if input_type == "Ticker":
        company_input = st.text_input("Ticker symbol", value="AAPL")
        uploaded_pdf = None
    else:
        company_input = None
        uploaded_pdf = st.file_uploader("Upload financial statement (PDF)", type=["pdf"])

    st.divider()
    st.header("Analysis modes")
    run_comps_mode = st.checkbox("Comparable Company Analysis", value=True)
    run_dcf_mode = st.checkbox("DCF Valuation", value=True)
    run_lbo_mode = st.checkbox("LBO / Financing", value=False)

    if run_lbo_mode:
        target_irr = st.slider("Target IRR (%)", 10, 35, 20) / 100
    else:
        target_irr = 0.20

    st.divider()
    run_button = st.button("Run Analysis", type="primary", use_container_width=True)


# ---------------------------------------------------------------------------
# MAIN -- ORCHESTRATION + DISPLAY
# ---------------------------------------------------------------------------

def run_pipeline_ui(company_input: str, modes: set, target_irr: float) -> dict:
    """
    My thin wrapper mirroring pipeline_orchestrator.run_pipeline's
    dependency logic (dcf runs internally whenever lbo is selected, but I
    only show it if explicitly requested) -- using mock functions for now.
    """
    results = {}
    financials = None
    dcf_result = None

    needs_dcf_internally = "dcf" in modes or "lbo" in modes

    if needs_dcf_internally:
        financials = mock_get_financials(company_input)
        dcf_result = mock_run_dcf(financials)
        if "dcf" in modes:
            results["dcf"] = dcf_result

    if "comps" in modes:
        if financials is None:
            financials = mock_get_financials(company_input)
        peers = mock_find_peers(financials)
        results["comps"] = mock_run_comps(financials, peers)

    if "lbo" in modes:
        ebitda = dcf_result["ebitda"]
        fcf_projections = dcf_result["fcf_projections"]
        exit_ebitda = dcf_result["exit_ebitda"]
        exit_multiple = dcf_result["entry_multiple"]
        purchase_price = dcf_result["enterprise_value"]

        structure = build_capital_structure(purchase_price, ebitda)
        schedule = project_debt_schedule(
            structure["senior_debt"], structure["sub_debt"],
            structure["senior_rate"], structure["sub_rate"], fcf_projections,
        )
        returns = calculate_returns(structure["equity_check"], schedule, exit_ebitda, exit_multiple)

        lbo_output = {"capital_structure": structure, "debt_schedule": schedule, "returns": returns}
        results["lbo"] = lbo_output
        results["financing_recommendation"] = recommend_financing(lbo_output, target_irr)

    return results


if run_button:
    modes = set()
    if run_comps_mode:
        modes.add("comps")
    if run_dcf_mode:
        modes.add("dcf")
    if run_lbo_mode:
        modes.add("lbo")

    if not modes:
        st.error("Select at least one analysis mode in the sidebar.")
    elif input_type == "Ticker" and not company_input:
        st.error("Enter a ticker symbol.")
    elif input_type == "PDF Upload" and uploaded_pdf is None:
        st.error("Upload a PDF.")
    else:
        target = company_input if input_type == "Ticker" else uploaded_pdf.name
        results = run_pipeline_ui(target or "MOCK", modes, target_irr)

        tabs_needed = [m for m in ["dcf", "comps", "lbo"] if m in results]
        if tabs_needed:
            tabs = st.tabs([t.upper() for t in tabs_needed])

            for tab, mode in zip(tabs, tabs_needed):
                with tab:
                    if mode == "dcf":
                        dcf = results["dcf"]
                        col1, col2, col3 = st.columns(3)
                        col1.metric("Enterprise Value", f"£{dcf['enterprise_value']:.1f}m")
                        col2.metric("Equity Value", f"£{dcf['equity_value']:.1f}m")
                        col3.metric("WACC", f"{dcf['wacc']:.1%}")
                        st.subheader("FCF Projections")
                        fcf_df = pd.DataFrame({
                            "Year": range(1, len(dcf["fcf_projections"]) + 1),
                            "FCF (£m)": dcf["fcf_projections"],
                        })
                        st.bar_chart(fcf_df.set_index("Year"))

                    elif mode == "comps":
                        st.subheader("Comparable Company Analysis")
                        st.dataframe(results["comps"], use_container_width=True)
                        st.caption("First row is the target company; remaining rows are identified peers.")

                    elif mode == "lbo":
                        lbo = results["lbo"]
                        structure = lbo["capital_structure"]
                        returns = lbo["returns"]
                        rec = results["financing_recommendation"]

                        col1, col2, col3, col4 = st.columns(4)
                        col1.metric("Total Debt", f"£{structure['total_debt']:.1f}m")
                        col2.metric("Equity Check", f"£{structure['equity_check']:.1f}m")
                        col3.metric("Leverage", f"{structure['leverage_multiple_used']:.2f}x")
                        col4.metric("IRR", f"{returns['irr']:.1%}" if returns["irr"] else "N/A")

                        st.subheader("Debt Paydown Schedule")
                        schedule_df = pd.DataFrame(lbo["debt_schedule"])
                        st.dataframe(schedule_df, use_container_width=True)

                        st.subheader("Financing Recommendation")
                        badge_color = {
                            "PROCEED": "green", "PROCEED_WITH_CAUTION": "orange",
                            "RENEGOTIATE": "red", "REVIEW": "red",
                        }.get(rec["recommendation"], "gray")
                        st.markdown(f":{badge_color}[**{rec['recommendation']}**]")
                        for note in rec["notes"]:
                            st.write(f"- {note}")
else:
    st.info("Configure your input and analysis modes in the sidebar, then click Run Analysis.")
