"""
analyst.io_streamlit.py -- my Streamlit interface for the DCF / Comps / LBO pipeline.

I've wired this to the REAL pipeline (pipeline_real.py) now, not mock data.

My testing status, to be precise: I've validated the LBO math, DCF math,
WACC math, and PDF statement-location logic myself, with synthetic/real
data. I have NOT been able to test the actual yfinance calls (find_peers,
run_comps, get_financials for tickers) because Yahoo Finance isn't
reachable from my sandbox -- this will be the first time they run
against live data. If something breaks, please send me the exact error
message (screenshot is fine) so I can fix it.

Run with: streamlit run analyst.io_streamlit.py
Needs these files in the same folder: lbo_financing.py,
pipeline_orchestrator.py, data_layer.py, wacc_beta.py, dcf_engine.py,
pdf_extraction.py, pipeline_real.py, requirements.txt
"""

import streamlit as st
import pandas as pd
import tempfile

from pipeline_real import run_pipeline

st.set_page_config(page_title="Valuation Pipeline", layout="wide")
st.title("DCF / Comps / LBO Valuation Pipeline")

st.info(
    "I've wired this to real data now (yfinance for tickers, the Claude API "
    "for PDFs) instead of placeholders. If something errors out, please "
    "screenshot it and send it back -- I haven't been able to test the "
    "live data calls myself."
)

# ---------------------------------------------------------------------------
# SIDEBAR -- INPUT
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Input")

    input_type_choice = st.radio("Input type", ["Ticker", "PDF Upload"], horizontal=True)

    if input_type_choice == "Ticker":
        company_input = st.text_input("Ticker symbol", value="AAPL")
        uploaded_pdf = None
        api_key = None
    else:
        company_input = None
        uploaded_pdf = st.file_uploader("Upload financial statement (PDF)", type=["pdf"])
        api_key = st.text_input(
            "Your Anthropic API key (needed for PDF extraction)",
            type="password",
            help="From console.anthropic.com -- API Keys. Never shared or logged by this app.",
        )

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
# MAIN -- REAL PIPELINE CALL + DISPLAY
# ---------------------------------------------------------------------------

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
    elif input_type_choice == "Ticker" and not company_input:
        st.error("Enter a ticker symbol.")
    elif input_type_choice == "PDF Upload" and uploaded_pdf is None:
        st.error("Upload a PDF.")
    elif input_type_choice == "PDF Upload" and not api_key:
        st.error("Enter your Anthropic API key to process a PDF.")
    else:
        results = None
        with st.spinner("Running analysis -- this calls real yfinance/Claude API data, so it may take a few seconds..."):
            try:
                if input_type_choice == "Ticker":
                    results = run_pipeline(
                        company_input, modes, input_type="ticker", target_irr=target_irr
                    )
                else:
                    # I save the uploaded file to a temp path since my
                    # pipeline expects a file path, not an in-memory object
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                        tmp.write(uploaded_pdf.read())
                        tmp_path = tmp.name
                    results = run_pipeline(
                        tmp_path, modes, input_type="pdf",
                        target_irr=target_irr, anthropic_api_key=api_key,
                    )
            except Exception as e:
                st.error(
                    f"Something broke -- please screenshot this and send it "
                    f"back to me so I can fix it:\n\n{type(e).__name__}: {e}"
                )

        if results:
            tabs_needed = [m for m in ["dcf", "comps", "lbo"] if m in results]
            if tabs_needed:
                tabs = st.tabs([t.upper() for t in tabs_needed])

                for tab, mode in zip(tabs, tabs_needed):
                    with tab:
                        if mode == "dcf":
                            dcf = results["dcf"]
                            col1, col2, col3 = st.columns(3)
                            col1.metric("Enterprise Value", f"${dcf['enterprise_value']:,.1f}m")
                            col2.metric("Equity Value", f"${dcf['equity_value']:,.1f}m")
                            col3.metric("WACC", f"{dcf['wacc']:.1%}")
                            st.subheader("FCF Projections")
                            fcf_df = pd.DataFrame({
                                "Year": range(1, len(dcf["fcf_projections"]) + 1),
                                "FCF ($m)": dcf["fcf_projections"],
                            })
                            st.bar_chart(fcf_df.set_index("Year"))
                            with st.expander("WACC / beta detail"):
                                st.write(f"Beta used: {dcf.get('beta', 'N/A')}")
                                st.write(f"Risk-free rate: {dcf.get('risk_free_rate', 'N/A')}")
                                st.write(f"Market risk premium: {dcf.get('market_risk_premium', 'N/A')}")

                        elif mode == "comps":
                            st.subheader("Comparable Company Analysis")
                            comps_df = pd.DataFrame(results["comps"])
                            rename_map = {
                                "ticker": "Ticker", "ev_ebitda": "EV/EBITDA",
                                "pe": "P/E", "ev_sales": "EV/Sales",
                            }
                            comps_df = comps_df.rename(columns=rename_map)
                            st.dataframe(comps_df, use_container_width=True)
                            st.caption("First row is the target company; remaining rows are identified peers.")

                        elif mode == "lbo":
                            lbo = results["lbo"]
                            structure = lbo["capital_structure"]
                            returns = lbo["returns"]
                            rec = results["financing_recommendation"]

                            col1, col2, col3, col4 = st.columns(4)
                            col1.metric("Total Debt", f"${structure['total_debt']:,.1f}m")
                            col2.metric("Equity Check", f"${structure['equity_check']:,.1f}m")
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
