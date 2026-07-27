"""
data_layer.py
=============
Real implementations of get_financials, find_peers, and run_comps --
I've replaced every mock from pipeline_core.py with these.
"""

import json
import pandas as pd
import yfinance as yf
from yfinance import EquityQuery

from pdf_extraction import extract_statement_text, build_extraction_prompt


def _first_complete_column(df, required_rows: list):
    for col in df.columns:
        if all(row in df.index and pd.notna(df.loc[row, col]) for row in required_rows):
            return col
    return None


def get_financials_from_ticker(ticker: str) -> dict:
    stock = yf.Ticker(ticker)
    info = stock.info

    income_stmt = stock.financials
    balance_sheet = stock.balance_sheet
    cash_flow = stock.cashflow

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

    ebitda = info.get("ebitda")
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

    required_for_dcf = ["ebitda", "free_cash_flow", "total_debt", "cash_and_equivalents", "market_cap"]
    missing = [f for f in required_for_dcf if financials[f] is None or (isinstance(financials[f], float) and pd.isna(financials[f]))]
    if missing:
        raise ValueError(
            f"yfinance data for {ticker} is missing required fields: {missing}. "
            f"Full data pulled: {financials}"
        )

    return financials


def get_financials_from_pdf(pdf_path: str, anthropic_api_key: str = None,
                             manual_sector: str = None, manual_industry: str = None) -> dict:
    """
    FREE by default: uses free_extraction.py's regex-based extractor.

    manual_sector / manual_industry: since a PDF's financial statements
    never contain sector/industry classification (that's a yfinance/
    ticker-lookup-only field), the user picks it from a dropdown
    (industry_options.PDF_INDUSTRY_OPTIONS) and I attach it here exactly
    as if it had come from yfinance -- this is what lets find_peers() and
    Comparable Company Analysis work for PDF uploads.

    UNIT FIX: a 10-K reports every dollar figure "in millions" -- so
    free_extraction.py (correctly) parses revenue as e.g. 716924, meaning
    $716,924 million. But get_financials_from_ticker() returns RAW DOLLAR
    figures from yfinance (e.g. Apple's revenue comes back as
    391000000000, not 391000). Those two units never matched, which
    silently understated every PDF-derived DCF/comps figure by exactly
    1,000,000x -- Amazon's real ~$70,577M enterprise value was displaying
    as "$70,577" (seventy-thousand dollars) since format_currency's B/M
    scaling assumed raw dollars. I scale every dollar- and share-
    denominated field here so the PDF path matches the ticker path's
    units from this point on, regardless of which extractor produced it.
    """
    if anthropic_api_key:
        result = get_financials_from_pdf_api(pdf_path, anthropic_api_key)
    else:
        from free_extraction import extract_financials_free
        result = extract_financials_free(pdf_path)
        result["ticker"] = "PDF_UPLOAD"

    dollar_and_share_fields = [
        "revenue", "operating_income", "net_income", "ebitda",
        "cash_from_operations", "capex", "free_cash_flow", "total_debt",
        "cash_and_equivalents", "interest_expense", "shares_outstanding",
    ]
    for field in dollar_and_share_fields:
        if result.get(field) is not None:
            result[field] = result[field] * 1_000_000

    result["sector"] = manual_sector
    result["industry"] = manual_industry

    # Validate the fields the DCF absolutely cannot function without --
    # same pattern as get_financials_from_ticker's validation. Without
    # this, a genuinely missing field (an unusual filer's label wording
    # slipping past every fallback in free_extraction.py) surfaces as a
    # raw TypeError three steps downstream in dcf_engine.py instead of a
    # clear, actionable message. total_debt is deliberately excluded --
    # free_extraction.py already treats a missing debt line as a
    # legitimate zero, not a failure.
    required_for_dcf = ["free_cash_flow", "cash_and_equivalents", "ebitda"]
    missing = [f for f in required_for_dcf if result.get(f) is None]
    if missing:
        raise ValueError(
            f"Could not extract these required field(s) from the PDF: {missing}. "
            "This regex-based extractor works off known label text, so a filing "
            "with different terminology or an unusual layout can miss fields. "
            "If this company is publicly listed, try the Ticker input instead."
        )

    return result


def get_financials_from_pdf_api(pdf_path: str, anthropic_api_key: str) -> dict:
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
    if result_text.startswith("```"):
        result_text = result_text.split("\n", 1)[1].rsplit("```", 1)[0]

    return json.loads(result_text)


def get_financials(company_input: str, input_type: str = "ticker",
                    anthropic_api_key: str = None,
                    manual_sector: str = None, manual_industry: str = None) -> dict:
    """
    Unified entry point. manual_sector/manual_industry are only used
    when input_type == "pdf" -- ignored otherwise, since ticker lookups
    already get real sector/industry from yfinance.
    """
    if input_type == "ticker":
        return get_financials_from_ticker(company_input)
    elif input_type == "pdf":
        return get_financials_from_pdf(company_input, anthropic_api_key,
                                        manual_sector, manual_industry)
    else:
        raise ValueError(f"input_type must be 'ticker' or 'pdf', got {input_type!r}")


def find_peers(financials: dict, n_peers: int = 10) -> list:
    sector = financials.get("sector")
    industry = financials.get("industry")
    target_revenue = financials.get("revenue")

    if not sector or not industry:
        raise ValueError(
            "Comparable Company Analysis needs sector/industry classification "
            "to find peers. For a PDF upload, pick a sector from the dropdown "
            "before running comps."
        )

    query = EquityQuery("and", [
        EquityQuery("eq", ["sector", sector]),
        EquityQuery("eq", ["industry", industry]),
        EquityQuery("is-in", ["exchange", "NMS", "NYQ"]),
    ])

    results = yf.screen(query, size=250)
    candidates = results.get("quotes", [])

    seen_names = set()
    candidate_revenues = []
    for c in candidates:
        candidate_ticker = c.get("symbol")
        if candidate_ticker == financials.get("ticker"):
            continue

        try:
            candidate_info = yf.Ticker(candidate_ticker).info
            candidate_name = candidate_info.get("longName") or candidate_info.get("shortName") or candidate_ticker
            if candidate_name in seen_names:
                continue
            candidate_ebitda = candidate_info.get("ebitda")
            if candidate_ebitda is not None and candidate_ebitda <= 0:
                continue
            candidate_revenue = candidate_info.get("totalRevenue")
            if candidate_revenue:
                candidate_revenues.append((candidate_ticker, candidate_revenue))
                seen_names.add(candidate_name)
        except Exception:
            continue

    if not candidate_revenues:
        raise ValueError(f"No peer candidates with revenue data found for sector={sector}, industry={industry}")

    ranked = sorted(candidate_revenues, key=lambda x: abs(x[1] - target_revenue))
    return [ticker for ticker, _ in ranked[:n_peers]]


def run_comps(financials: dict, peers: list) -> dict:
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

    def _clean(values):
        return [v for v in values if v is not None and v == v]

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
    equity_from_pe = _implied(peer_stats["pe"], target_net_income)

    implied_valuation = {
        "ev_from_ebitda_median": ev_from_ebitda["median"],
        "ev_from_ebitda_mean": ev_from_ebitda["mean"],
        "ev_from_sales_median": ev_from_sales["median"],
        "ev_from_sales_mean": ev_from_sales["mean"],
        "equity_from_pe_median": equity_from_pe["median"],
        "equity_from_pe_mean": equity_from_pe["mean"],
    }

    return {"table": rows, "peer_stats": peer_stats, "implied_valuation": implied_valuation}
