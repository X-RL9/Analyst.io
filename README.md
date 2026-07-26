# analyst.io

A DCF / Comparable Company Analysis / LBO valuation tool. Give it a stock ticker or a company's financial statements as a PDF, pick which analyses you want, and it builds a full valuation model — including an implied valuation from trading multiples and a leveraged-finance recommendation, not just raw numbers.

Built as a Streamlit app: [analyst.io_streamlit.py](analyst.io_streamlit.py)

## What it does

- **DCF Valuation** — projects free cash flow forward, calculates WACC from a real CAPM beta regression (with a peer-median fallback for unlisted companies), applies a Gordon growth terminal value, and discounts back to enterprise and equity value.
- **Comparable Company Analysis** — finds peer companies (same sector/industry, ranked by closest revenue), pulls their trading multiples (EV/EBITDA, P/E, EV/Sales), computes peer median/mean, and uses those to derive an actual implied valuation for the target company.
- **LBO / Financing** — sizes debt capacity against leverage and coverage constraints, builds a senior/subordinated capital structure, projects a full debt paydown schedule, calculates equity IRR and payback period, and produces a rules-based financing recommendation — including a reverse-solve mode that finds the maximum entry price for a target IRR.

Any combination of the three can be run together.

## Input methods

**Ticker** — pulls live data via `yfinance`: financials, beta, risk-free rate, peer screening, and trading multiples.

**PDF Upload** — reads a company's financial statements (10-K, annual report) directly, using a free regex-based extractor by default — no API cost.

**Note:** Comparable Company Analysis doesn't work with PDF upload yet — this is coming soon.

## Files

| File | What it does |
|---|---|
| `analyst.io_streamlit.py` | The Streamlit interface — input, mode selection, results display |
| `pipeline_real.py` | Orchestrator — wires the data layer, WACC/beta, DCF, and LBO modules together |
| `data_layer.py` | `get_financials`, `find_peers`, `run_comps` — the yfinance/PDF data layer |
| `free_extraction.py` | Free, zero-cost regex-based PDF financial extraction |
| `pdf_extraction.py` | pdfplumber statement-location logic + optional Claude API prompt |
| `wacc_beta.py` | Beta regression, risk-free rate, market risk premium, WACC calculation |
| `dcf_engine.py` | FCF projection, terminal value, discounting to enterprise/equity value |
| `lbo_financing.py` | Debt capacity, capital structure, paydown schedule, IRR, reverse-solve |
| `pipeline_orchestrator.py` | The rules-based financing recommendation engine |
| `requirements.txt` | Python dependencies |

## Roadmap

- Comparable Company Analysis support for PDF uploads
- Broaden peer matching for niche industries with few candidates
- Gamma/vega hedging extensions
- FX options support
