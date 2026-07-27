"""
industry_options.py
====================
A curated dropdown of (sector, industry) pairs for PDF-uploaded companies.

UPDATE: the previous version guessed at plausible-looking industry
strings (e.g. "Software - Application" with a hyphen) rather than using
yfinance's ACTUAL valid values -- which caused
"ValueError: Invalid EQ value" the moment someone picked most options.

These are now copied exactly from yfinance's own documented valid_values
table (https://ranaroussi.github.io/yfinance/reference/api/yfinance.EquityQuery.html),
including the EM DASH ("—", not a hyphen "-") that yfinance uses in every
compound industry name like "Software—Application" or
"Banks—Diversified". Get that character wrong and the query fails with
the exact same error again.
"""

PDF_INDUSTRY_OPTIONS = {
    "Internet Retail (e.g. Amazon)": ("Consumer Cyclical", "Internet Retail"),
    "Software (Infrastructure)": ("Technology", "Software—Infrastructure"),
    "Software (Application)": ("Technology", "Software—Application"),
    "Consumer Electronics": ("Technology", "Consumer Electronics"),
    "Semiconductors": ("Technology", "Semiconductors"),
    "Aerospace & Defense": ("Industrials", "Aerospace & Defense"),
    "Banks (Diversified)": ("Financial Services", "Banks—Diversified"),
    "Insurance (Diversified)": ("Financial Services", "Insurance—Diversified"),
    "Oil & Gas E&P": ("Energy", "Oil & Gas E&P"),
    "Biotechnology": ("Healthcare", "Biotechnology"),
    "Drug Manufacturers (General)": ("Healthcare", "Drug Manufacturers—General"),
    "Auto Manufacturers": ("Consumer Cyclical", "Auto Manufacturers"),
    "Apparel Retail": ("Consumer Cyclical", "Apparel Retail"),
    "Telecom Services": ("Communication Services", "Telecom Services"),
    "Internet Content & Information": ("Communication Services", "Internet Content & Information"),
    "Utilities (Regulated Electric)": ("Utilities", "Utilities—Regulated Electric"),
    "Packaged Foods": ("Consumer Defensive", "Packaged Foods"),
}
