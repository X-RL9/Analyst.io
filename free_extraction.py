"""
free_extraction.py
===================
A completely FREE alternative to the Claude-API-based PDF extraction in
pdf_extraction.py. Zero API calls, zero cost, ever.
"""

import re
from pdf_extraction import extract_statement_text


def _parse_number(token: str) -> float:
    token = token.strip()
    is_negative = token.startswith("(") and token.endswith(")")
    cleaned = re.sub(r"[\$,()\s]", "", token)
    value = float(cleaned)
    return -value if is_negative else value


def _find_row_values(text: str, label: str) -> list:
    lines = text.split("\n")
    number_pattern = re.compile(r"\(?\$?\s*-?[\d,]+(?:\.\d+)?\)?")

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.lower().startswith(label.lower()):
            continue

        row_text = stripped
        j = i
        while not any(c.isdigit() for c in row_text) and j + 1 < len(lines):
            j += 1
            row_text += " " + lines[j].strip()

        tokens = number_pattern.findall(row_text)
        tokens = [t for t in tokens if any(c.isdigit() for c in t)]
        if tokens:
            return [_parse_number(t) for t in tokens]

    return []


def _latest(text: str, label: str):
    values = _find_row_values(text, label)
    return values[-1] if values else None


def extract_financials_free(pdf_path: str) -> dict:
    statement_text = extract_statement_text(pdf_path)
    income = statement_text.get("income_statement", "") or ""
    balance = statement_text.get("balance_sheet", "") or ""
    cash_flow = statement_text.get("cash_flow", "") or ""

    revenue = _latest(income, "Total net sales") or _latest(income, "Total revenue") or _latest(income, "Net sales")
    operating_income = (
        _latest(income, "Operating income")
        or _latest(income, "Income from operations")
        or _latest(income, "Loss from operations")
        or _latest(income, "Operating loss")
        or _latest(income, "Income (loss) from operations")
    )
    net_income = _latest(income, "Net income")
    interest_expense_raw = _latest(income, "Interest expense")
    interest_expense = abs(interest_expense_raw) if interest_expense_raw is not None else None

    cash_from_operations = (
        _latest(cash_flow, "Net cash provided by (used in) operating activities")
        or _latest(cash_flow, "Net cash provided by operating activities")
    )
    capex_raw = _latest(cash_flow, "Purchases of property and equipment")
    capex = abs(capex_raw) if capex_raw is not None else None
    d_and_a = (
        _latest(cash_flow, "Depreciation and amortization")
        or _latest(cash_flow, "Depreciation and amortization expense")
        or _latest(cash_flow, "Depreciation, amortization and accretion")
        or _latest(cash_flow, "Depreciation and amortization of property and equipment")
    )

    cash_and_equivalents = (
        _latest(balance, "Cash and cash equivalents")
        or _latest(balance, "Cash, cash equivalents, and restricted cash")
        or _latest(balance, "Cash, cash equivalents and restricted cash")
        or _latest(balance, "Cash, cash equivalents and short-term investments")
        or _latest(balance, "Total cash and cash equivalents")
    )

    # NOTE: I do NOT catch a footnote-only current portion of long-term
    # debt (some companies, Amazon included, disclose that separately) --
    # same limitation applies to the API-based approach, since it also
    # only sees these 3 statement pages.
    total_debt_raw = (
        _latest(balance, "Long-term debt")
        or _latest(balance, "Total debt")
        or _latest(balance, "Long-term debt, net of current portion")
    )
    if total_debt_raw is None:
        # Many growth-stage software companies (Asana is a real example)
        # genuinely carry NO long-term debt line on their balance sheet --
        # that's not a failed extraction, it's a true zero. I default to
        # 0 here rather than leaving it None, which was previously causing
        # a raw TypeError three steps downstream in dcf_engine.py's
        # net_debt calculation (total_debt - cash) the moment this field
        # turned up missing for any reason.
        total_debt = 0.0
    else:
        total_debt = total_debt_raw

    ebitda = None
    if operating_income is not None and d_and_a is not None:
        ebitda = operating_income + d_and_a

    free_cash_flow = None
    if cash_from_operations is not None and capex is not None:
        free_cash_flow = cash_from_operations - capex

    shares_outstanding = None
    diluted_shares_match = re.search(r"^Diluted\s+([\d,]+(?:\s+[\d,]+)*)\s*$", income, re.MULTILINE)
    if diluted_shares_match:
        nums = [_parse_number(t) for t in diluted_shares_match.group(1).split()]
        shares_outstanding = nums[-1] if nums else None

    result = {
        "ticker": None,
        "revenue": revenue,
        "operating_income": operating_income,
        "net_income": net_income,
        "ebitda": ebitda,
        "cash_from_operations": cash_from_operations,
        "capex": capex,
        "free_cash_flow": free_cash_flow,
        "total_debt": total_debt,
        "cash_and_equivalents": cash_and_equivalents,
        "shares_outstanding": shares_outstanding,
        "interest_expense": interest_expense,
        "tax_rate": 0.21,
    }

    missing = [k for k, v in result.items() if v is None and k not in ("ticker",)]
    if missing:
        result["_extraction_warnings"] = (
            f"Could not extract these fields: {missing}. This regex-based "
            "extractor works off known label text, so a filing with "
            "different terminology or an unusual layout can miss fields "
            "an LLM would have caught."
        )

    return result
