"""
analyst.io_streamlit.py -- my Streamlit interface for the DCF / Comps / LBO pipeline.

I've wired this to the REAL pipeline (pipeline_real.py) now, not mock data.

ON THE API KEY: I removed the user-facing API key field entirely. The
app's OWNER (you) sets ANTHROPIC_API_KEY once via Streamlit Cloud's
dashboard -> your app -> Settings -> Secrets, as:

    ANTHROPIC_API_KEY = "sk-ant-your-key-here"

This is stored server-side by Streamlit Cloud, is NEVER committed to your
GitHub repo, and is NEVER shown to or entered by anyone using the app --
it's read silently via st.secrets. This means anyone can upload a PDF and
use the app without ever seeing or needing their own key. Note this does
mean YOUR account is billed for every PDF a user processes -- each call
is a small fraction of a cent, but worth knowing if this app gets shared
widely.

My testing status, to be precise: I've validated the LBO math, DCF math,
WACC math, and PDF statement-location logic myself, with synthetic/real
data -- including at realistic billion-dollar scale after your Apple test
run. I still have NOT been able to test the actual yfinance screener/
comps calls live myself.

Run with: streamlit run analyst.io_streamlit.py
Needs these files in the same folder: lbo_financing.py,
pipeline_orchestrator.py, data_layer.py, wacc_beta.py, dcf_engine.py,
pdf_extraction.py, pipeline_real.py, requirements.txt
"""

import streamlit as st
import pandas as pd
import tempfile

from pipeline_real import run_pipeline


def format_currency(value: float) -> str:
    """
    I auto-scale to billions or millions correctly. Bug I found: yfinance
    returns raw dollar figures (e.g. Apple's revenue comes back as
    ~391,000,000,000, not 391,000), but my old display code assumed
    everything was already "in millions" -- leftover from testing with
    synthetic numbers like ebitda=100 meaning "$100m". That mislabeled a
    genuine $736.8B enterprise value as "$736,822,936,618.2m", which reads
    like $736 trillion. This scales properly regardless of company size.
    """
    abs_value = abs(value)
    if abs_value >= 1e9:
        return f"${value/1e9:,.1f}B"
    elif abs_value >= 1e6:
        return f"${value/1e6:,.1f}M"
    else:
        return f"${value:,.0f}"


st.set_page_config(page_title="Analyst.io", layout="wide")
st.title("Analyst.io")

st.info(
    "Input a ticker or upload a company's 10-k, then select which models you would like to review"
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
    else:
        company_input = None
        uploaded_pdf = st.file_uploader("Upload financial statement (PDF)", type=["pdf"])

    st.divider()
    st.header("Analysis modes")
    if input_type_choice == "PDF Upload":
        st.caption(
            "Comparable Company Analysis isn't available for PDF uploads -- "
            "it needs sector/industry classification, which only comes from "
            "a ticker lookup (yfinance), not from the financial statements "
            "themselves."
        )
        run_comps_mode = False
    else:
        run_comps_mode = st.checkbox("Comparable Company Analysis", value=True)
    run_dcf_mode = st.checkbox("DCF Valuation", value=True)
    run_lbo_mode = st.checkbox("LBO / Financing", value=False)

    if run_comps_mode:
        n_peers = st.slider("Number of peer companies", 3, 20, 10)
    else:
        n_peers = 10

    if run_lbo_mode:
        target_irr = st.slider("Target IRR (%)", 10, 100, 20) / 100
        if target_irr >= 0.60:
            st.caption("⚠️ Above ~60% is unconventional for a target IRR -- sanity-check your assumptions.")
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
    else:
        # PDF extraction is FREE by default (regex-based, no API calls).
        # If you've optionally configured ANTHROPIC_API_KEY in Streamlit
        # Secrets, that gets used instead for potentially more robust
        # extraction on unusual filing formats -- but it's no longer
        # required for the PDF path to work at all.
        api_key = st.secrets.get("ANTHROPIC_API_KEY") if input_type_choice == "PDF Upload" else None
        if input_type_choice == "PDF Upload":
            if api_key:
                st.caption("Using Claude API extraction (key found in Secrets).")
            else:
                st.caption("Using free regex-based extraction (no API key configured -- this costs nothing).")

        results = None
        with st.spinner("Running analysis -- this calls real yfinance/Claude API data, so it may take a few seconds..."):
            try:
                if input_type_choice == "Ticker":
                    results = run_pipeline(
                        company_input, modes, input_type="ticker", target_irr=target_irr, n_peers=n_peers
                    )
                else:
                    # I save the uploaded file to a temp path since my
                    # pipeline expects a file path, not an in-memory object
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                        tmp.write(uploaded_pdf.read())
                        tmp_path = tmp.name
                    results = run_pipeline(
                        tmp_path, modes, input_type="pdf",
                        target_irr=target_irr, anthropic_api_key=api_key, n_peers=n_peers,
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
                            if not dcf.get("feasible", True):
                                st.warning(f"**Not feasible with these assumptions:** {dcf['reason']}")
                                st.subheader("Workings computed before hitting the issue")
                                st.write(f"WACC: {dcf['wacc']:.2%}  |  Terminal growth: {dcf['terminal_growth']:.2%}")
                                st.write(f"PV of explicit period (still valid): {format_currency(dcf['pv_explicit_period'])}")
                                fcf_df = pd.DataFrame({
                                    "Year": range(1, len(dcf["fcf_projections"]) + 1),
                                    "FCF": dcf["fcf_projections"],
                                })
                                st.subheader("FCF Projections (still valid)")
                                st.bar_chart(fcf_df.set_index("Year"))
                            else:
                                col1, col2, col3 = st.columns(3)
                                col1.metric("Enterprise Value", format_currency(dcf['enterprise_value']))
                                col2.metric("Equity Value", format_currency(dcf['equity_value']))
                                col3.metric("WACC", f"{dcf['wacc']:.1%}")
                                st.subheader("FCF Projections")
                                fcf_df = pd.DataFrame({
                                    "Year": range(1, len(dcf["fcf_projections"]) + 1),
                                    "FCF": dcf["fcf_projections"],
                                })
                                st.bar_chart(fcf_df.set_index("Year"))
                                with st.expander("WACC / beta detail"):
                                    st.write(f"Beta used: {dcf.get('beta', 'N/A')}")
                                    st.write(f"Risk-free rate: {dcf.get('risk_free_rate', 'N/A')}")
                                    st.write(f"Market risk premium: {dcf.get('market_risk_premium', 'N/A')}")

                        elif mode == "comps":
                            st.subheader("Comparable Company Analysis")
                            comps = results["comps"]
                            comps_df = pd.DataFrame(comps["table"])
                            rename_map = {
                                "company": "Company", "ticker": "Ticker",
                                "ev_ebitda": "EV/EBITDA", "pe": "P/E", "ev_sales": "EV/Sales",
                            }
                            comps_df = comps_df.rename(columns=rename_map)
                            column_order = [c for c in ["Company", "Ticker", "EV/EBITDA", "P/E", "EV/Sales"] if c in comps_df.columns]
                            comps_df = comps_df[column_order]
                            st.dataframe(comps_df, use_container_width=True)
                            st.caption(f"Row 1 is the target company; the other {len(comps['table'])-1} rows are identified peers.")

                            st.subheader("Peer median / mean")
                            stats = comps["peer_stats"]
                            stat_cols = st.columns(3)
                            labels = {"ev_ebitda": "EV/EBITDA", "pe": "P/E", "ev_sales": "EV/Sales"}
                            for col, key in zip(stat_cols, ["ev_ebitda", "pe", "ev_sales"]):
                                median = stats[key]["median"]
                                mean = stats[key]["mean"]
                                col.metric(
                                    f"{labels[key]} (median)",
                                    f"{median:.2f}x" if median is not None else "N/A",
                                )
                                col.caption(f"Mean: {mean:.2f}x" if mean is not None else "Mean: N/A")

                            st.subheader("Implied Valuation")
                            st.caption("Peer multiple x this company's own EBITDA/revenue/net income")
                            iv = comps["implied_valuation"]
                            val_col1, val_col2, val_col3 = st.columns(3)
                            val_col1.metric(
                                "EV from EV/EBITDA (median)",
                                format_currency(iv["ev_from_ebitda_median"]) if iv["ev_from_ebitda_median"] else "N/A",
                            )
                            val_col1.caption(
                                f"Mean: {format_currency(iv['ev_from_ebitda_mean'])}" if iv["ev_from_ebitda_mean"] else "Mean: N/A"
                            )
                            val_col2.metric(
                                "EV from EV/Sales (median)",
                                format_currency(iv["ev_from_sales_median"]) if iv["ev_from_sales_median"] else "N/A",
                            )
                            val_col2.caption(
                                f"Mean: {format_currency(iv['ev_from_sales_mean'])}" if iv["ev_from_sales_mean"] else "Mean: N/A"
                            )
                            val_col3.metric(
                                "Equity from P/E (median)",
                                format_currency(iv["equity_from_pe_median"]) if iv["equity_from_pe_median"] else "N/A",
                            )
                            val_col3.caption(
                                f"Mean: {format_currency(iv['equity_from_pe_mean'])}" if iv["equity_from_pe_mean"] else "Mean: N/A"
                            )

                        elif mode == "lbo":
                            lbo = results["lbo"]
                            if not lbo.get("returns", {}).get("feasible", lbo.get("feasible", True)):
                                rec = results["financing_recommendation"]
                                st.warning("**Not feasible:** " + rec["notes"][0])
                                if "returns" in lbo:
                                    st.subheader("Workings: the cash flow stream that failed")
                                    cash_flows = lbo["returns"]["cash_flows"]
                                    cf_df = pd.DataFrame({
                                        "Period": ["Initial equity"] + [f"Year {i}" for i in range(1, len(cash_flows))],
                                        "Cash flow": cash_flows,
                                    })
                                    st.dataframe(cf_df, use_container_width=True)
                                    structure = lbo["capital_structure"]
                                    col1, col2, col3 = st.columns(3)
                                    col1.metric("Total Debt", format_currency(structure['total_debt']))
                                    col2.metric("Equity Check", format_currency(structure['equity_check']))
                                    col3.metric("Leverage", f"{structure['leverage_multiple_used']:.2f}x")
                            else:
                                structure = lbo["capital_structure"]
                                returns = lbo["returns"]
                                rec = results["financing_recommendation"]

                                col1, col2, col3, col4 = st.columns(4)
                                col1.metric("Total Debt", format_currency(structure['total_debt']))
                                col2.metric("Equity Check", format_currency(structure['equity_check']))
                                col3.metric("Leverage", f"{structure['leverage_multiple_used']:.2f}x")
                                irr_val = returns.get("irr")
                                irr_display = f"{irr_val:.1%}" if irr_val is not None and irr_val == irr_val else "N/A"
                                col4.metric("IRR", irr_display)

                                st.subheader("Debt Paydown Schedule")
                                schedule_df = pd.DataFrame(lbo["debt_schedule"])
                                st.dataframe(schedule_df, use_container_width=True)

                                st.subheader("Financing Recommendation")
                                badge_color = {
                                    "PROCEED": "green", "PROCEED_WITH_CAUTION": "orange",
                                    "RENEGOTIATE": "red", "REVIEW": "red", "NOT_FEASIBLE": "red",
                                }.get(rec["recommendation"], "gray")
                                st.markdown(f":{badge_color}[**{rec['recommendation']}**]")
                                for note in rec["notes"]:
                                    st.write(f"- {note}")
else:
    st.info("Configure your input and analysis modes in the sidebar, then click Run Analysis.")
