"""
wacc_beta.py
============
Real WACC and beta calculation.

UPDATE: calculate_wacc now accepts an optional peer_capital_weights
fallback for when financials["market_cap"] is None -- which is always
true for a PDF upload, since a PDF has no live share price to compute
market value of equity from. Standard practice for valuing a private/
unlisted company: use the peer group's median capital structure
(debt / (debt+equity)) instead, and relever off that -- same principle
as the existing peer-median beta fallback, just applied to weights
instead of beta.
"""

import numpy as np
import pandas as pd
import statsmodels.api as sm
import yfinance as yf


def _get_close_series(price_data: pd.DataFrame, ticker: str) -> pd.Series:
    close = price_data["Close"]
    if isinstance(close, pd.DataFrame):
        if ticker in close.columns:
            close = close[ticker]
        else:
            close = close.iloc[:, 0]
    return close


def calculate_beta(ticker: str, market_index: str = "^GSPC", period: str = "5y") -> float:
    stock_data = yf.download(ticker, period=period, interval="1wk", progress=False, auto_adjust=True)
    market_data = yf.download(market_index, period=period, interval="1wk", progress=False, auto_adjust=True)

    if stock_data.empty or market_data.empty:
        raise ValueError(f"yfinance returned no price data for {ticker} or {market_index}")

    stock_close = _get_close_series(stock_data, ticker)
    market_close = _get_close_series(market_data, market_index)

    stock_returns = stock_close.pct_change()
    market_returns = market_close.pct_change()

    aligned = pd.concat([stock_returns, market_returns], axis=1, join="inner")
    aligned.columns = ["stock", "market"]
    aligned = aligned.replace([np.inf, -np.inf], np.nan).dropna()

    if len(aligned) < 10:
        raise ValueError(
            f"Only {len(aligned)} clean overlapping data points found for "
            f"{ticker} vs {market_index} -- not enough to regress beta "
            "reliably. Check the ticker is correct and has enough trading history."
        )

    X = sm.add_constant(aligned["market"])
    model = sm.OLS(aligned["stock"], X).fit()
    beta = model.params["market"]
    return float(beta)


def get_risk_free_rate() -> float:
    tnx = yf.Ticker("^TNX")
    latest = tnx.history(period="5d")["Close"].iloc[-1]
    return latest / 100


def calculate_market_risk_premium(risk_free_rate: float, period: str = "1y") -> float:
    market_data = yf.download("^GSPC", period=period, progress=False, auto_adjust=True)
    if market_data.empty:
        raise ValueError("yfinance returned no price data for ^GSPC")
    close = _get_close_series(market_data, "^GSPC")
    close = close.dropna()
    if close.empty:
        raise ValueError("^GSPC price data was all NaN after cleaning -- check yfinance connectivity")
    start_price = close.iloc[0]
    end_price = close.iloc[-1]
    market_return = (end_price / start_price) - 1
    return float(market_return - risk_free_rate)


def estimate_beta_and_premium_from_peers(peer_tickers: list, risk_free_rate: float) -> dict:
    betas, returns = [], []
    for peer in peer_tickers:
        try:
            betas.append(calculate_beta(peer))
            hist = yf.download(peer, period="1y", progress=False, auto_adjust=True)
            close = _get_close_series(hist, peer).dropna()
            if close.empty:
                continue
            returns.append((close.iloc[-1] / close.iloc[0]) - 1)
        except Exception:
            continue

    if not betas:
        raise ValueError("Could not calculate beta/premium from any peer -- check peer_tickers list")

    median_beta = float(np.median(betas))
    median_return = float(np.median(returns))
    return {"beta": median_beta, "market_risk_premium": median_return - risk_free_rate}


def estimate_capital_weights_from_peers(peer_tickers: list) -> dict:
    """
    Fallback for unlisted/PDF-uploaded companies: there's no observable
    market value of equity, so I use the peer group's MEDIAN capital
    structure (debt / (debt+equity)) instead -- standard practice for
    valuing a private company by relevering off public comps. Median
    (not mean) for the same reason as the beta fallback: one distressed,
    heavily-levered peer shouldn't skew the whole group's weighting.
    """
    debt_weights = []
    for peer in peer_tickers:
        try:
            info = yf.Ticker(peer).info
            mcap = info.get("marketCap")
            debt = info.get("totalDebt")
            if mcap and debt is not None and (mcap + debt) > 0:
                debt_weights.append(debt / (mcap + debt))
        except Exception:
            continue

    if not debt_weights:
        raise ValueError(
            "Could not estimate peer capital structure weights -- no peer "
            "had both marketCap and totalDebt available."
        )

    debt_weight = float(np.median(debt_weights))
    return {"equity_weight": 1 - debt_weight, "debt_weight": debt_weight}


def calculate_wacc(ebitda: float, financials: dict, beta: float, risk_free_rate: float,
                    market_risk_premium: float, peer_capital_weights: dict = None) -> dict:
    """
    WACC = (E/(E+D)) * cost_of_equity + (D/(E+D)) * cost_of_debt * (1 - tax_rate)
    where cost_of_equity (CAPM) = risk_free_rate + beta * market_risk_premium

    peer_capital_weights: optional fallback used ONLY when
    financials["market_cap"] is missing (always true for a PDF upload,
    since there's no live share price). Pass the output of
    estimate_capital_weights_from_peers() here in that case.
    """
    cost_of_equity = risk_free_rate + beta * market_risk_premium

    market_cap = financials.get("market_cap")
    total_debt = financials.get("total_debt")
    interest_expense = financials.get("interest_expense")
    tax_rate = financials.get("tax_rate", 0.21)

    if interest_expense and total_debt:
        cost_of_debt = interest_expense / total_debt
    else:
        cost_of_debt = risk_free_rate + 0.015

    if market_cap is not None and total_debt is not None:
        total_capital = market_cap + total_debt
        equity_weight = market_cap / total_capital if total_capital else 1.0
        debt_weight = total_debt / total_capital if total_capital else 0.0
    elif peer_capital_weights is not None:
        equity_weight = peer_capital_weights["equity_weight"]
        debt_weight = peer_capital_weights["debt_weight"]
    else:
        raise ValueError(
            "Cannot determine capital structure weights: financials has no "
            "market_cap (expected for a PDF upload), and no "
            "peer_capital_weights fallback was provided. Pass the output of "
            "estimate_capital_weights_from_peers() as peer_capital_weights "
            "in this case."
        )

    wacc = equity_weight * cost_of_equity + debt_weight * cost_of_debt * (1 - tax_rate)

    if wacc != wacc or wacc in (float("inf"), float("-inf")):
        raise ValueError(
            f"WACC calculation produced an invalid result ({wacc}). Inputs were: "
            f"cost_of_equity={cost_of_equity}, cost_of_debt={cost_of_debt}, "
            f"equity_weight={equity_weight}, debt_weight={debt_weight}, tax_rate={tax_rate}. "
            "One of these is likely None/NaN -- check the financials dict."
        )

    return {
        "wacc": wacc, "cost_of_equity": cost_of_equity, "cost_of_debt": cost_of_debt,
        "equity_weight": equity_weight, "debt_weight": debt_weight, "beta_used": beta,
    }
