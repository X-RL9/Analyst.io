"""
industry_options.py
====================
A curated dropdown of (sector, industry) pairs, using yfinance's ACTUAL
category strings -- not made-up labels. This exists because a PDF upload
has no sector/industry field anywhere in the financial statements
themselves (that's a yfinance/ticker-lookup-only field), so when the user
uploads a PDF I need them to tell me what kind of company it is, then I
use their choice exactly as if it had come from yfinance.

I kept this to ~15 common verticals rather than trying to cover every
GICS sub-industry -- enough to demo comps against a variety of PDF 10-Ks
without the dropdown becoming unwieldy. Add more pairs here if a PDF you
upload doesn't fit an existing option; the (sector, industry) values just
need to match what yf.screen()'s EquityQuery expects.
"""

PDF_INDUSTRY_OPTIONS = {
    "Software (Infrastructure)": ("Technology", "Software - Infrastructure"),
    "Software (Application)": ("Technology", "Software - Application"),
    "Consumer Electronics": ("Technology", "Consumer Electronics"),
    "Semiconductors": ("Technology", "Semiconductors"),
    "Aerospace & Defense": ("Industrials", "Aerospace & Defense"),
    "Banks (Diversified)": ("Financial Services", "Banks - Diversified"),
    "Insurance (Diversified)": ("Financial Services", "Insurance - Diversified"),
    "Oil & Gas E&P": ("Energy", "Oil & Gas E&P"),
    "Biotechnology": ("Healthcare", "Biotechnology"),
    "Drug Manufacturers (General)": ("Healthcare", "Drug Manufacturers - General"),
    "Auto Manufacturers": ("Consumer Cyclical", "Auto Manufacturers"),
    "Apparel Retail": ("Consumer Cyclical", "Apparel Retail"),
    "Telecom Services": ("Communication Services", "Telecom Services"),
    "Utilities (Regulated Electric)": ("Utilities", "Utilities - Regulated Electric"),
    "Packaged Foods": ("Consumer Defensive", "Packaged Foods"),
}
