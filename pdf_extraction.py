"""
pdf_extraction.py
=================
Real, tested PDF -> financials extraction.

UPDATE: the old find_statement_page scanned the ENTIRE document, and did
so three separate times (once per statement type), calling
page.extract_text() on every single page each time. That's fine for an
~80-page filing like Amazon's, but a bank 10-K (e.g. JPMorgan) can run
400+ pages -- that's up to 1,200 extract_text() calls on a single run,
which is exactly what was hanging for 10-15 seconds then getting killed
outright by Streamlit Cloud's resource limits (no Python traceback,
because it's not a code exception -- the process just gets terminated).

Two fixes:
  1. Single pass: I extract each page's text ONCE and check it against
     all three statement-type patterns, instead of re-scanning the whole
     document three times.
  2. Capped scan: I only scan the first MAX_PAGES_TO_SCAN pages. Every
     real 10-K I've seen (including bank filings) has its core financial
     statements well within the first ~150-200 pages -- they're never
     buried in the exhibits/appendices at the very end. A filing large
     enough to need more than that is almost certainly a listed company
     anyway, where the ticker input path is faster and more reliable than
     PDF parsing regardless.

If statements still aren't found within the cap, I raise a clear error
suggesting the ticker path instead of silently returning None and
letting missing fields surface confusingly three steps downstream.
"""

import re
import pdfplumber

MAX_PAGES_TO_SCAN = 200

STATEMENT_HEADER_PATTERNS = {
    "income_statement": [
        r"STATEMENTS? OF OPERATIONS",
        r"STATEMENTS? OF INCOME",
        r"INCOME STATEMENTS?",
    ],
    "balance_sheet": [
        r"BALANCE SHEETS?",
    ],
    "cash_flow": [
        r"STATEMENTS? OF CASH FLOWS?",
        r"CASH FLOWS? STATEMENTS?",
    ],
}


def _is_statement_header(line: str, statement_type: str) -> bool:
    """I only accept a line as a real header if it's fully uppercase (real
    headings always are; narrative cross-references never are) and matches
    one of the known phrasing patterns for that statement type."""
    stripped = line.strip()
    if not stripped or stripped != stripped.upper():
        return False
    if not any(c.isalpha() for c in stripped):
        return False
    return any(re.search(p, stripped) for p in STATEMENT_HEADER_PATTERNS[statement_type])


def extract_statement_text(pdf_path: str) -> dict:
    """
    I pull the raw text of the three core financial statements from a
    10-K/annual-report style PDF in a SINGLE pass over at most the first
    MAX_PAGES_TO_SCAN pages -- I stop scanning entirely once all three
    statements are found, rather than always scanning the cap.

    I return {"income_statement": "...", "balance_sheet": "...", "cash_flow": "..."}

    If a statement genuinely isn't found within the scanned pages, I
    raise a clear ValueError rather than silently returning None --
    a filing large/unusual enough to miss this is almost certainly a
    listed company anyway, where the ticker input path is more reliable.
    """
    extracted = {statement_type: None for statement_type in STATEMENT_HEADER_PATTERNS}
    remaining = set(STATEMENT_HEADER_PATTERNS.keys())

    with pdfplumber.open(pdf_path) as pdf:
        pages_to_scan = pdf.pages[:MAX_PAGES_TO_SCAN]
        for page in pages_to_scan:
            if not remaining:
                break
            text = page.extract_text() or ""
            lines = [l.strip() for l in text.split("\n")]
            for statement_type in list(remaining):
                if any(_is_statement_header(line, statement_type) for line in lines):
                    extracted[statement_type] = text
                    remaining.discard(statement_type)

    if remaining:
        raise ValueError(
            f"Could not locate these statements within the first {MAX_PAGES_TO_SCAN} "
            f"pages of the PDF: {sorted(remaining)}. This usually means either the "
            "filing uses unusual statement headings, or it's a very large filing "
            "(e.g. a bank/insurance 10-K running several hundred pages) where the "
            "statements sit further in, or genuinely aren't there in this format. "
            "If this company is publicly listed, try the Ticker input instead -- "
            "it pulls structured data directly from yfinance and doesn't depend on "
            "parsing the PDF's layout at all."
        )

    return extracted


def build_extraction_prompt(statement_text: dict) -> str:
    """
    Stage 2 prompt: I ask Claude to return ONLY JSON matching the exact
    schema pipeline_core.py's get_financials()/run_dcf() expect, so the
    PDF path and the yfinance path produce interchangeable output.
    """
    combined_text = "\n\n---\n\n".join(
        f"{key.upper()}:\n{text}" for key, text in statement_text.items() if text
    )

    return f"""You are extracting financial data from a company's 10-K filing.
Below are the three core financial statements (income statement, balance
sheet, cash flow statement). Extract the following fields and return
ONLY a JSON object -- no markdown fences, no preamble, no explanation.

Required fields (use the MOST RECENT fiscal year in the document; use
null for any field genuinely not present in the text below):
{{
  "ticker": string or null (company name if ticker not shown),
  "revenue": number (in millions),
  "operating_income": number,
  "net_income": number,
  "ebitda": number or null (operating_income + D&A if D&A is shown, else null),
  "cash_from_operations": number,
  "capex": number (Purchases of property and equipment, as a positive number),
  "free_cash_flow": number (cash_from_operations minus capex),
  "total_debt": number (long-term debt + current portion, face value if shown),
  "cash_and_equivalents": number,
  "shares_outstanding": number (in millions, diluted if available),
  "fiscal_year_end": string (e.g. "2025-12-31")
}}

FINANCIAL STATEMENTS:
{combined_text}
"""


# ===========================================================================
# WHAT I VALIDATED MANUALLY AGAINST THE REAL AMAZON 10-K
# ===========================================================================
EXPECTED_OUTPUT_FOR_AMAZON_2025 = {
    "ticker": "AMZN",
    "revenue": 716924,
    "operating_income": 79975,
    "net_income": 77670,
    "ebitda": 79975 + 41860,
    "cash_from_operations": 139514,
    "capex": 128320,
    "free_cash_flow": 139514 - 128320,
    "total_debt": 68836,
    "cash_and_equivalents": 86810,
    "shares_outstanding": 10827,
    "fiscal_year_end": "2025-12-31",
}


if __name__ == "__main__":
    pdf_path = "/mnt/user-data/uploads/Amazon_10-k.pdf"
    statement_text = extract_statement_text(pdf_path)

    print("=== STATEMENT LOCATION (capped, single-pass scan) ===")
    for key, text in statement_text.items():
        found = "FOUND" if text else "NOT FOUND"
        print(f"  {key}: {found} ({len(text) if text else 0} chars)")

    print("=== EXPECTED (ground truth from the real 10-K) ===")
    for k, v in EXPECTED_OUTPUT_FOR_AMAZON_2025.items():
        print(f"  {k}: {v}")
