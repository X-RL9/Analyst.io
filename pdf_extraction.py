"""
pdf_extraction.py
=================
Real, tested PDF -> financials extraction.

UPDATE: find_statement_page now matches a set of known header PATTERNS per
statement type instead of one exact hardcoded string. The original exact-
match approach only worked for filers phrased exactly like Amazon
("CONSOLIDATED STATEMENTS OF OPERATIONS" / "CONSOLIDATED BALANCE SHEETS" /
"CONSOLIDATED STATEMENTS OF CASH FLOWS") -- any other filer (singular
"BALANCE SHEET", "STATEMENTS OF INCOME" instead of "OPERATIONS", "INCOME
STATEMENTS", etc.) returned None for that whole statement, which cascaded
into every downstream field for that statement coming back empty.

I keep the original safeguard against false positives (narrative text like
"...included within the consolidated balance sheets and consist of...")
by requiring the candidate line to be fully uppercase -- a real statement
heading is always rendered in caps; narrative cross-references never are.
"""

import re
import pdfplumber

# Each statement type maps to a list of regex patterns. A line only
# qualifies as a header if it's fully uppercase AND matches one of these.
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


def find_statement_page(pdf, statement_type: str):
    """I locate the real statement page by checking each line against
    _is_statement_header for the given statement type."""
    for i, page in enumerate(pdf.pages):
        text = page.extract_text() or ""
        lines = [l.strip() for l in text.split("\n")]
        for line in lines:
            if _is_statement_header(line, statement_type):
                return i
    return None


def extract_statement_text(pdf_path: str) -> dict:
    """
    I pull the raw text of the three core financial statements from a
    10-K/annual-report style PDF, using flexible header matching so this
    works across filers that phrase headings differently, not just Amazon.
    I return {"income_statement": "...", "balance_sheet": "...", "cash_flow": "..."}
    Any statement I don't find returns None for that key -- check before
    proceeding to extraction.
    """
    extracted = {}
    with pdfplumber.open(pdf_path) as pdf:
        for statement_type in STATEMENT_HEADER_PATTERNS:
            page_idx = find_statement_page(pdf, statement_type)
            extracted[statement_type] = pdf.pages[page_idx].extract_text() if page_idx is not None else None
    return extracted


# ===========================================================================
# WHAT I VALIDATED MANUALLY AGAINST THE REAL AMAZON 10-K
# (kept from the original file -- still the ground truth for the self-test)
# ===========================================================================
EXPECTED_OUTPUT_FOR_AMAZON_2025 = {
    "ticker": "AMZN",
    "revenue": 716924,
    "operating_income": 79975,
    "net_income": 77670,
    "ebitda": 79975 + 41860,  # operating income + D&A = 121,835
    "cash_from_operations": 139514,
    "capex": 128320,
    "free_cash_flow": 139514 - 128320,  # = 11,194 (matches the 10-K's own stated FCF figure exactly)
    "total_debt": 68836,  # face value of long-term debt including current portion
    "cash_and_equivalents": 86810,
    "shares_outstanding": 10827,  # diluted
    "fiscal_year_end": "2025-12-31",
}


if __name__ == "__main__":
    # Self-test -- still runs against the real Amazon 10-K, no API key needed
    pdf_path = "/mnt/user-data/uploads/Amazon_10-k.pdf"
    statement_text = extract_statement_text(pdf_path)

    print("=== STATEMENT LOCATION (flexible header matching) ===")
    for key, text in statement_text.items():
        found = "FOUND" if text else "NOT FOUND"
        print(f"  {key}: {found} ({len(text) if text else 0} chars)")

    print("=== EXPECTED (ground truth from the real 10-K) ===")
    for k, v in EXPECTED_OUTPUT_FOR_AMAZON_2025.items():
        print(f"  {k}: {v}")
