"""
data_layer.py
=============
Real implementations of get_financials, find_peers, and run_comps --
I've replaced every mock from pipeline_core.py with these.

A note on my testing status:
  I cannot execute yfinance calls from my sandbox (network restrictions
  on my end). I've written every function below to the best of my
  knowledge of the yfinance API (I confirmed the screener functionality
  via docs search earlier in this project), but you need to be the one
  to actually run these in Colab and confirm they work -- please tell me
  what breaks so I can fix it.

  The PDF path (get_financials_from_pdf) does combine code I've tested
  (pdf_extraction.py's statement-location logic, validated against your
  real Amazon 10-K) with the untested Claude API call (needs your key).
"""

import json
import pandas as pd
import yfinance as yf
from yfinance import EquityQuery

from pdf_extraction import extract_statement_text, build_extraction_prompt


# ===========================================================================
# GET FINANCIALS
# ===========================================================================

def _first_complete_column(df, required_rows: list):
    """
    I find the first column where ALL required rows have real (non-NaN)
    data. This fixes a bug I found: yfinance sometimes puts a "TTM"
    (trailing twelve months) column first in stock.financials/
    balance_sheet/cashflow, and TTM columns can have NaN for line items
    that aren't computed on a trailing basis (interest expense and total
    debt are common examples). My old code blindly grabbed columns[0]
    assuming it was a complete latest fiscal year -- if that column was
    actually an incomplete TTM column, NaN would silently flow through
    into WACC and poison the entire DCF with no error, no warning, just
    "$nanm" showing up in the app.
    """
    for col in df.columns:
        if all(row in df.index and pd.notna(df.loc[row, col]) for row in required_rows):
            return col
    return None


def get_financials_from_ticker(ticker: str) -> dict:
    """
    My real yfinance pull. I have NOT tested this in my sandbox (no
    network access to Yahoo Finance from here) -- please run this first
    in Colab and tell me what breaks, since yfinance's exact field names/
    behavior can shift between versions.
    """
    stock = yf.Ticker(ticker)
    info = stock.info

    income_stmt = stock.financials  # annual income statement
    balance_sheet = stock.balance_sheet
    cash_flow = stock.cashflow

    # THE FIX: I find a column where these three rows are all genuinely
    # present, instead of blindly trusting columns[0] is complete
    income_col = _first_complete_column(
        income_stmt, ["Total Revenue", "Operating Income", "Net Income"]
    )
    if income_col is None:
        raise ValueError(
            f"Could not find a complete fiscal year of income statement data for {ticker} "
            "-- yfinance may have changed its row names, or this ticker has incomplete data."
        )

    revenue = income_stmt.loc["Total Revenue", income_col]
    operating_income = income_stmt.loc["Operating Income", income_col]
    net_income = income_stmt.loc["Net Income", income_col]

    ebitda = info.get("ebitda")  # yfinance sometimes provides this directly
    if ebitda is None or pd.isna(ebitda):
        try:
            d_and_a = cash_flow.loc["Depreciation And Amortization", income_col]
            ebitda = operating_income + d_and_a if pd.notna(d_and_a) else None
        except KeyError:
            ebitda = None
    if ebitda is None or pd.isna(ebitda):
        raise ValueError(
            f"Could not determine EBITDA for {ticker} -- yfinance didn't provide "
            "it directly and I couldn't compute it from D&A either. This is a "
            "required field for the DCF, so I can't safely continue."
        )

    cash_flow_col = _first_complete_column(cash_flow, ["Operating Cash Flow", "Capital Expenditure"])
    if cash_flow_col is None:
        raise ValueError(f"Could not find complete cash flow statement data for {ticker}")

    cash_from_ops = cash_flow.loc["Operating Cash Flow", cash_flow_col]
    capex = abs(cash_flow.loc["Capital Expenditure", cash_flow_col])
    free_cash_flow = cash_from_ops - capex

    total_debt = None
    if "Total Debt" in balance_sheet.index:
        balance_col = _first_complete_column(balance_sheet, ["Total Debt"])
        if balance_col is not None:
            total_debt = balance_sheet.loc["Total Debt", balance_col]

    cash_and_equivalents = info.get("totalCash")
    shares_outstanding = info.get("sharesOutstanding")
    market_cap = info.get("marketCap")

    interest_expense = None
    try:
        raw_interest = income_stmt.loc["Interest Expense", income_col]
        interest_expense = abs(raw_interest) if pd.notna(raw_interest) else None
    except KeyError:
        pass

    tax_rate = info.get("effectiveTaxRate", 0.21)

    financials = {
        "ticker": ticker.upper(),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "revenue": revenue,
        "operating_income": operating_income,
        "net_income": net_income,
        "ebitda": ebitda,
        "cash_from_operations": cash_from_ops,
        "capex": capex,
        "free_cash_flow": free_cash_flow,
        "total_debt": total_debt,
        "cash_and_equivalents": cash_and_equivalents,
        "shares_outstanding": shares_outstanding,
        "market_cap": market_cap,
        "interest_expense": interest_expense,
        "tax_rate": tax_rate,
    }

    # I validate the fields the DCF/WACC absolutely cannot function without,
    # and raise a CLEAR error naming exactly what's missing -- rather than
    # letting None/NaN quietly flow through and surface as "$nanm" three
    # steps downstream with no indication of why
    required_for_dcf = ["ebitda", "free_cash_flow", "total_debt", "cash_and_equivalents", "market_cap"]
    missing = [f for f in required_for_dcf if financials[f] is None or (isinstance(financials[f], float) and pd.isna(financials[f]))]
    if missing:
        raise ValueError(
            f"yfinance data for {ticker} is missing required fields: {missing}. "
            f"Full data pulled: {financials}"
        )

    return financials


def get_financials_from_pdf(pdf_path: str, anthropic_api_key: str) -> dict:
    """
    My PDF path: pdfplumber (I've TESTED this, working -- see
    pdf_extraction.py) + Claude API JSON extraction (needs your key,
    I haven't been able to test this part myself).
    """
    import anthropic

    statement_text = extract_statement_text(pdf_path)
    if not all(statement_text.values()):
        missing = [k for k, v in statement_text.items() if not v]
        raise ValueError(f"Could not locate these statements in the PDF: {missing}")

    prompt = build_extraction_prompt(statement_text)

    client = anthropic.Anthropic(api_key=anthropic_api_key)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )

    result_text = response.content[0].text.strip()
    # I strip markdown fences if Claude includes them despite my instructions not to
    if result_text.startswith("```"):
        result_text = result_text.split("\n", 1)[1].rsplit("```", 1)[0]

    return json.loads(result_text)


def get_financials(company_input: str, input_type: str = "ticker",
                    anthropic_api_key: str = None) -> dict:
    """
    My unified entry point, matching pipeline_core.py's expected signature.
    input_type: "ticker" or "pdf". I require anthropic_api_key for "pdf".
    """
    if input_type == "ticker":
        return get_financials_from_ticker(company_input)
    elif input_type == "pdf":
        if not anthropic_api_key:
            raise ValueError("anthropic_api_key required for PDF input")
        return get_financials_from_pdf(company_input, anthropic_api_key)
    else:
        raise ValueError(f"input_type must be 'ticker' or 'pdf', got {input_type!r}")


# ===========================================================================
# PEER DISCOVERY -- yfinance screener + revenue-distance ranking
# ===========================================================================

def find_peers(financials: dict, n_peers: int = 10) -> list:
    """
    I use yfinance's EquityQuery screener (I confirmed this exists via my
    docs search earlier) to get all tickers in the same sector+industry,
    then I rank by closest revenue and take the top n_peers -- not a fixed
    tolerance band, so this self-adjusts for small-cap vs mega-cap targets,
    as you suggested.

    I have NOT tested this -- yfinance screener behavior (exact field
    names for industry/sector filtering) needs verification in Colab; the
    GitHub issues I found earlier showed some version-dependent quirks
    combining sector+industry filters.
    """
    sector = financials.get("sector")
    industry = financials.get("industry")
    target_revenue = financials.get("revenue")

    if not sector or not industry:
        raise ValueError("financials must include 'sector' and 'industry' to find peers")

    query = EquityQuery("and", [
        EquityQuery("eq", ["sector", sector]),
        EquityQuery("eq", ["industry", industry]),
    ])

    results = yf.screen(query, size=250)  # 250 is Yahoo's max per call
    candidates = results.get("quotes", [])

    candidate_revenues = []
    for c in candidates:
        candidate_ticker = c.get("symbol")
        if candidate_ticker == financials.get("ticker"):
            continue  # I don't include the target itself as its own peer
        try:
            candidate_info = yf.Ticker(candidate_ticker).info
            candidate_revenue = candidate_info.get("totalRevenue")
            if candidate_revenue:
                candidate_revenues.append((candidate_ticker, candidate_revenue))
        except Exception:
            continue  # I skip tickers with unavailable data rather than failing entirely

    if not candidate_revenues:
        raise ValueError(f"No peer candidates with revenue data found for sector={sector}, industry={industry}")

    ranked = sorted(candidate_revenues, key=lambda x: abs(x[1] - target_revenue))
    return [ticker for ticker, _ in ranked[:n_peers]]


# ===========================================================================
# COMPS -- I pull EV/EBITDA, P/E, EV/Sales for target + peers
# ===========================================================================

def run_comps(financials: dict, peers: list) -> list:
    """
    I pull the standard trading multiples for the target + each peer.
    I have NOT tested this -- please verify the field names in Colab.
    """
    all_tickers = [financials["ticker"]] + peers
    rows = []

    for ticker in all_tickers:
        try:
            info = yf.Ticker(ticker).info
            ev = info.get("enterpriseValue")
            ebitda = info.get("ebitda")
            pe = info.get("trailingPE")
            revenue = info.get("totalRevenue")

            rows.append({
                "ticker": ticker,
                "ev_ebitda": ev / ebitda if ev and ebitda else None,
                "pe": pe,
                "ev_sales": ev / revenue if ev and revenue else None,
            })
        except Exception:
            rows.append({"ticker": ticker, "ev_ebitda": None, "pe": None, "ev_sales": None})

    return rows


if __name__ == "__main__":
    print("This module needs live yfinance access to test -- please run in")
    print("Colab with a real ticker, e.g.:")
    print()
    print("  from data_layer import get_financials, find_peers, run_comps")
    print("  financials = get_financials('AAPL', input_type='ticker')")
    print("  print(financials)")
    print()
    print("Then tell me what actually comes back (or what errors you hit)")
    print("so I can fix field names / add fallbacks where yfinance's real")
    print("behavior differs from what I expected.")
