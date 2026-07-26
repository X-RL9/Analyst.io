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


def get_financials_from_pdf(pdf_path: str, anthropic_api_key: str = None) -> dict:
    """
    FREE by default: uses free_extraction.py's regex-based extractor, zero
    API calls, zero cost. If you pass an anthropic_api_key, it uses the
    Claude-API-based extraction instead (in pdf_extraction.py) -- more
    flexible for unusual filing formats, but costs a fraction of a cent
    per PDF. The free path handles standard US 10-Ks correctly (tested:
    8 of 11 fields matched a manually-verified ground truth exactly, and
    the other 3 turned out to be a genuine accounting-convention question
    rather than an extraction error -- see free_extraction.py's docstring).
    """
    if anthropic_api_key:
        return get_financials_from_pdf_api(pdf_path, anthropic_api_key)

    from free_extraction import extract_financials_free
    result = extract_financials_free(pdf_path)
    result["ticker"] = "PDF_UPLOAD"
    return result


def get_financials_from_pdf_api(pdf_path: str, anthropic_api_key: str) -> dict:
    """
    The Claude-API-based path (costs a small fraction of a cent per PDF).
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
    input_type: "ticker" or "pdf". anthropic_api_key is now OPTIONAL for
    "pdf" -- if omitted, uses the free regex-based extractor; if
    provided, uses the Claude API for potentially more robust extraction
    on unusual filing formats.
    """
    if input_type == "ticker":
        return get_financials_from_ticker(company_input)
    elif input_type == "pdf":
        return get_financials_from_pdf(company_input, anthropic_api_key)
    else:
        raise ValueError(f"input_type must be 'ticker' or 'pdf', got {input_type!r}")


# ===========================================================================
# PEER DISCOVERY -- yfinance screener + revenue-distance ranking
# ===========================================================================

def find_peers(financials: dict, n_peers: int = 10) -> list:
    """
    Uses yfinance's EquityQuery screener (I confirmed this exists via my
    docs search earlier) to get all tickers in the same sector+industry,
    then I rank by closest revenue and take the top n_peers -- not a fixed
    tolerance band, so this self-adjusts for small-cap vs mega-cap targets,
    as you suggested.

    Fixes I made after seeing real output for AAPL come back with garbage
    peers (XIAOMI80.BK, DIXON.NS, CSIOY etc.):
      1. I restrict to major US exchanges (NMS, NYQ) -- without this, the
         screener returns every cross-listing of the same underlying
         company on every exchange worldwide (Bangkok, Frankfurt, Bombay
         ADRs etc.), which isn't what "comparable companies" means for a
         standard CCA.
      2. I dedupe by the company's actual name (longName), not just
         ticker -- this catches same-company cross-listings the exchange
         filter alone might miss (e.g. an ADR that still trades on NMS).
      3. I filter out negative-EBITDA companies -- these produce
         meaningless multiples (like -0.10x EV/EBITDA) that don't tell you
         anything comparable about valuation; a distressed/loss-making
         company isn't a useful comp regardless of sector match.

    NOT TESTED against live data -- please run in Colab and tell me if
    the peer list looks sensible now (e.g. AAPL should surface names like
    MSFT, GOOGL, not obscure cross-listings).
    """
    sector = financials.get("sector")
    industry = financials.get("industry")
    target_revenue = financials.get("revenue")

    if not sector or not industry:
        raise ValueError("financials must include 'sector' and 'industry' to find peers")

    query = EquityQuery("and", [
        EquityQuery("eq", ["sector", sector]),
        EquityQuery("eq", ["industry", industry]),
        EquityQuery("is-in", ["exchange", "NMS", "NYQ"]),  # major US exchanges only
    ])

    results = yf.screen(query, size=250)  # 250 is Yahoo's max per call
    candidates = results.get("quotes", [])

    seen_names = set()
    candidate_revenues = []
    for c in candidates:
        candidate_ticker = c.get("symbol")
        if candidate_ticker == financials.get("ticker"):
            continue  # I don't include the target itself as its own peer

        try:
            candidate_info = yf.Ticker(candidate_ticker).info
            candidate_name = candidate_info.get("longName") or candidate_info.get("shortName") or candidate_ticker
            if candidate_name in seen_names:
                continue  # I skip cross-listings of a company I've already included
            candidate_ebitda = candidate_info.get("ebitda")
            if candidate_ebitda is not None and candidate_ebitda <= 0:
                continue  # I skip distressed/loss-making companies -- meaningless multiples
            candidate_revenue = candidate_info.get("totalRevenue")
            if candidate_revenue:
                candidate_revenues.append((candidate_ticker, candidate_revenue))
                seen_names.add(candidate_name)
        except Exception:
            continue  # I skip tickers with unavailable data rather than failing entirely

    if not candidate_revenues:
        raise ValueError(f"No peer candidates with revenue data found for sector={sector}, industry={industry}")

    ranked = sorted(candidate_revenues, key=lambda x: abs(x[1] - target_revenue))
    return [ticker for ticker, _ in ranked[:n_peers]]


# ===========================================================================
# COMPS -- I pull EV/EBITDA, P/E, EV/Sales for target + peers
# ===========================================================================

def run_comps(financials: dict, peers: list) -> dict:
    """
    I pull the standard trading multiples for the target + each peer, then
    compute peer median/mean for each multiple, and use those to derive
    an ACTUAL implied valuation for the target -- median/mean EV/EBITDA x
    target's own EBITDA, median/mean EV/Sales x target's own revenue,
    median/mean P/E x target's own net income. Without this last step, a
    comps table is just numbers with no conclusion -- the whole point of
    a CCA is to answer "so what's this company worth, based on how the
    market prices similar companies?"

    I use median (not just mean) throughout since a couple of extreme
    peer multiples can otherwise skew the implied value -- same reasoning
    as the peer-median beta fallback elsewhere in this project.

    Returns:
      {
        "table": [ {ticker, company, ev_ebitda, pe, ev_sales}, ... ]
                 (target company is always row 0),
        "peer_stats": {"ev_ebitda": {"median":.., "mean":..}, "pe": {...}, "ev_sales": {...}},
        "implied_valuation": {
            "ev_from_ebitda_median": .., "ev_from_ebitda_mean": ..,
            "ev_from_sales_median": .., "ev_from_sales_mean": ..,
            "equity_from_pe_median": .., "equity_from_pe_mean": ..,
        }
      }

    I have NOT tested the yfinance calls live -- please verify the field
    names in Colab.
    """
    all_tickers = [financials["ticker"]] + peers
    rows = []

    for ticker in all_tickers:
        try:
            info = yf.Ticker(ticker).info
            company_name = info.get("longName") or info.get("shortName") or ticker
            ev = info.get("enterpriseValue")
            ebitda = info.get("ebitda")
            pe = info.get("trailingPE")
            revenue = info.get("totalRevenue")

            # I flag negative EV/EBITDA as None rather than showing a
            # meaningless negative multiple (this happens for companies
            # with negative EBITDA -- the ratio itself is not interpretable
            # as a valuation multiple in that case)
            ev_ebitda = ev / ebitda if ev and ebitda and ebitda > 0 else None

            rows.append({
                "ticker": ticker,
                "company": company_name,
                "ev_ebitda": ev_ebitda,
                "pe": pe,
                "ev_sales": ev / revenue if ev and revenue else None,
            })
        except Exception:
            rows.append({"ticker": ticker, "company": ticker, "ev_ebitda": None, "pe": None, "ev_sales": None})

    # I compute peer stats from PEERS ONLY (rows[1:]), excluding the target
    # itself -- the target's own multiple isn't a data point for what the
    # market pays for similar companies, it's the thing we're trying to
    # figure out
    def _clean(values):
        return [v for v in values if v is not None and v == v]  # drop None and NaN

    peer_rows = rows[1:]
    ev_ebitda_vals = _clean([r["ev_ebitda"] for r in peer_rows])
    pe_vals = _clean([r["pe"] for r in peer_rows])
    ev_sales_vals = _clean([r["ev_sales"] for r in peer_rows])

    def _median_mean(values):
        if not values:
            return {"median": None, "mean": None}
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        median = sorted_vals[n // 2] if n % 2 else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
        return {"median": median, "mean": sum(values) / len(values)}

    peer_stats = {
        "ev_ebitda": _median_mean(ev_ebitda_vals),
        "pe": _median_mean(pe_vals),
        "ev_sales": _median_mean(ev_sales_vals),
    }

    # THE ACTUAL VALUATION: peer multiple x target's own fundamentals
    target_ebitda = financials.get("ebitda")
    target_revenue = financials.get("revenue")
    target_net_income = financials.get("net_income")

    def _implied(multiple_stats, target_fundamental):
        if target_fundamental is None:
            return {"median": None, "mean": None}
        return {
            "median": multiple_stats["median"] * target_fundamental if multiple_stats["median"] is not None else None,
            "mean": multiple_stats["mean"] * target_fundamental if multiple_stats["mean"] is not None else None,
        }

    ev_from_ebitda = _implied(peer_stats["ev_ebitda"], target_ebitda)
    ev_from_sales = _implied(peer_stats["ev_sales"], target_revenue)
    equity_from_pe = _implied(peer_stats["pe"], target_net_income)  # P/E x net income = equity value directly

    implied_valuation = {
        "ev_from_ebitda_median": ev_from_ebitda["median"],
        "ev_from_ebitda_mean": ev_from_ebitda["mean"],
        "ev_from_sales_median": ev_from_sales["median"],
        "ev_from_sales_mean": ev_from_sales["mean"],
        "equity_from_pe_median": equity_from_pe["median"],
        "equity_from_pe_mean": equity_from_pe["mean"],
    }

    return {"table": rows, "peer_stats": peer_stats, "implied_valuation": implied_valuation}


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
