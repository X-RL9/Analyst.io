# analyst.io
 
A DCF / Comparable Company Analysis / LBO valuation tool. Give it a stock ticker or a company's financial statements as a PDF, pick which analyses you want, and it builds a full valuation model — including an implied valuation from trading multiples and a leveraged-finance recommendation, not just raw numbers.
 
Built as a Streamlit app: [analyst.io_streamlit.py](analyst.io_streamlit.py)
 
## What it does
 
- **DCF Valuation** — projects free cash flow forward, calculates WACC from a real CAPM beta regression (with a peer-median fallback for unlisted companies), applies a Gordon growth terminal value, and discounts back to enterprise and equity value.
- **Comparable Company Analysis** — finds peer companies (same sector/industry, ranked by closest revenue, not a fixed tolerance band), pulls their trading multiples (EV/EBITDA, P/E, EV/Sales), computes peer median/mean, and uses those to derive an **actual implied valuation** for the target company — not just a table of numbers with no conclusion.
- **LBO / Financing** — sizes debt capacity against leverage and coverage constraints (whichever binds tighter), builds a senior/subordinated capital structure, projects a full debt paydown schedule with a cash sweep waterfall, calculates equity IRR and payback period, and produces a rules-based financing recommendation (proceed / renegotiate / review), including a reverse-solve mode that finds the maximum entry price for a target IRR.
Any combination of the three can be run together. If you select LBO alone, DCF still runs internally to supply the cash flow projections it needs — it just won't show up in your results unless you also ticked DCF.
 
## Input methods
 
**Ticker** — pulls live data via `yfinance`: financials, beta (regressed against the S&P 500), risk-free rate (`^TNX`), peer screening, and trading multiples.
 
**PDF Upload** — reads a company's financial statements (10-K, annual report) directly. Two extraction modes:
- **Free (default)** — a regex-based extractor that locates the three core statements and pulls out known line items. No API calls, no cost, ever. Tested against a real Amazon 10-K: 8 of 11 fields matched exactly; the other 3 came down to a genuine accounting-convention question (gross vs. net capex), not an extraction error.
- **Claude API (optional)** — if `ANTHROPIC_API_KEY` is set in your Streamlit Secrets, PDF extraction uses the Claude API instead, which is more flexible for unusual filing formats or non-standard terminology. Costs a small fraction of a cent per PDF. Not required — the free path is the default and handles standard US 10-Ks correctly.
**Note:** Comparable Company Analysis only works with the Ticker input, since peer discovery needs sector/industry classification, which only comes from a yfinance lookup — PDF financial statements don't contain that field anywhere.
 
## Files
 
| File | What it does |
|---|---|
| `analyst.io_streamlit.py` | The Streamlit interface — input, mode selection, results display |
| `pipeline_real.py` | Orchestrator — wires the data layer, WACC/beta, DCF, and LBO modules together, with the mode-selection dependency logic |
| `data_layer.py` | `get_financials`, `find_peers`, `run_comps` — the yfinance/PDF data layer |
| `free_extraction.py` | Free, zero-cost regex-based PDF financial extraction |
| `pdf_extraction.py` | pdfplumber statement-location logic + the Claude API prompt (used only if a key is configured) |
| `wacc_beta.py` | Beta regression (OLS vs. S&P 500), risk-free rate, market risk premium, WACC calculation |
| `dcf_engine.py` | FCF projection, terminal value, discounting to enterprise/equity value |
| `lbo_financing.py` | Debt capacity, capital structure, paydown schedule, IRR, reverse-solve for entry price |
| `pipeline_orchestrator.py` | The rules-based financing recommendation engine |
| `requirements.txt` | Python dependencies |
 
## Setup
 
1. Clone this repo and push it to your own GitHub, or deploy directly via [share.streamlit.io](https://share.streamlit.io)
2. Point Streamlit Cloud at `analyst.io_streamlit.py` as the main file
3. That's it — no API key needed for the default (free) experience
**Optional — enabling Claude API extraction:**
If you want the option of higher-accuracy PDF extraction for unusual filings, add your key under your app's **Settings → Secrets** in the Streamlit Cloud dashboard:
```toml
ANTHROPIC_API_KEY = "sk-ant-your-key-here"
```
This is stored server-side by Streamlit, never committed to the repo, and never shown to anyone using the app. Note: if a key is configured, it becomes the default extraction path for every PDF upload (not just a fallback), so every user's upload will use your account's credits.
 
### Running locally
```bash
pip install -r requirements.txt
streamlit run analyst.io_streamlit.py
```
 
## Known limitations
 
- **yfinance data reliability** — field names and behavior can shift between yfinance versions; some functions include defensive handling for known quirks (e.g. a leading "TTM" column with incomplete data, NaN price rows), but this hasn't been exhaustively tested against every ticker or edge case.
- **Peer discovery** — restricted to major US exchanges and deduped by company name to avoid counting cross-listings of the same company as separate peers; very niche industries may still return a small peer set (this reflects a genuinely small pool of comparable companies, not a bug).
- **Free PDF extraction** — works off known line-item labels (e.g. "Total net sales", "Net income"). A filing with unusual terminology or a non-standard layout could cause it to miss fields that the Claude API path would catch.
- **DCF/LBO infeasibility** — when the underlying math breaks down (e.g. WACC below terminal growth, or no real IRR solves a deal's cash flows), the app shows this clearly along with whatever was computed up to that point, rather than crashing or showing silently wrong numbers.
- **Total debt (PDF path)** — only captures what's on the face of the balance sheet; some companies disclose the current portion of long-term debt only in a footnote, which isn't part of the three core statements this tool reads.
## Roadmap ideas
 
- Broaden peer matching to sector-level as a fallback when industry-only matching returns very few candidates
- Gamma/vega hedging extensions for the underlying derivatives work this project grew out of
- FX options support
- A toggle to let the app owner test Claude API extraction on a specific PDF without making it the default for all users
