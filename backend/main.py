"""
Stock Terminal — backend API.

A small FastAPI service that wraps Yahoo Finance (via yfinance) and exposes
clean JSON endpoints the React frontend consumes: company snapshot,
fundamentals, OHLC candles for the chart, and recent news.

Run with:  uvicorn main:app --reload
"""

from functools import lru_cache

import yfinance as yf
from curl_cffi import requests as cffi_requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Bourse API")

# Allow the Vite dev server (and a deployed frontend) to call us.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Yahoo Finance fingerprints and blocks plain Python `requests` traffic.
# curl_cffi impersonates a real Chrome TLS/HTTP signature, which yfinance
# (and our own /api/search call below) both lean on to stay reliable.
_yf_session = cffi_requests.Session(impersonate="chrome")

# Map a UI range button to a yfinance (period, interval) pair.
RANGES = {
    "1D": ("1d", "5m"),
    "5D": ("5d", "30m"),
    "1M": ("1mo", "1d"),
    "6M": ("6mo", "1d"),
    "1Y": ("1y", "1d"),
    "5Y": ("5y", "1wk"),
}


def _ticker(symbol: str) -> yf.Ticker:
    return yf.Ticker(symbol.upper().strip(), session=_yf_session)


@lru_cache(maxsize=256)
def _info(symbol: str) -> dict:
    """Cache .info per symbol — it's the slowest call and changes slowly."""
    info = _ticker(symbol).info
    if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
        raise HTTPException(404, f"No data found for '{symbol}'. Check the ticker.")
    return info


@app.get("/api/search")
def search(q: str):
    """Autocomplete: turn a company name (or partial ticker) into matching
    symbols, so users don't have to memorize codes like '7203.T'."""
    q = q.strip()
    if len(q) < 1:
        return {"results": []}
    try:
        resp = _yf_session.get(
            "https://query2.finance.yahoo.com/v1/finance/search",
            params={"q": q, "quotesCount": 8, "newsCount": 0},
            timeout=8,
        )
        resp.raise_for_status()
        quotes = resp.json().get("quotes", [])
    except Exception:
        return {"results": []}  # search is best-effort; never break the UI

    results = []
    for item in quotes:
        symbol = item.get("symbol")
        # Only suggest things we can actually chart (stocks, ETFs, indices).
        if not symbol or item.get("quoteType") not in {"EQUITY", "ETF", "INDEX"}:
            continue
        results.append({
            "symbol": symbol,
            "name": item.get("shortname") or item.get("longname") or symbol,
            "exchange": item.get("exchDisp", ""),
            "type": item.get("quoteType", ""),
        })
    return {"results": results}


@app.get("/api/stock/{symbol}")
def stock(symbol: str):
    """Company snapshot + fundamentals for the header and stats panels."""
    info = _info(symbol)
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    prev = info.get("previousClose") or info.get("regularMarketPreviousClose")
    change = (price - prev) if (price and prev) else None
    pct = (change / prev * 100) if (change is not None and prev) else None

    return {
        "symbol": symbol.upper(),
        "name": info.get("shortName") or info.get("longName"),
        "exchange": info.get("fullExchangeName") or info.get("exchange"),
        "currency": info.get("currency", "USD"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "website": info.get("website"),
        "summary": info.get("longBusinessSummary"),
        "logo": info.get("logo_url"),
        "price": price,
        "previousClose": prev,
        "change": change,
        "changePct": pct,
        "dayLow": info.get("dayLow"),
        "dayHigh": info.get("dayHigh"),
        "fiftyTwoWeekLow": info.get("fiftyTwoWeekLow"),
        "fiftyTwoWeekHigh": info.get("fiftyTwoWeekHigh"),
        "fundamentals": {
            "Market Cap": info.get("marketCap"),
            "P/E (TTM)": info.get("trailingPE"),
            "Forward P/E": info.get("forwardPE"),
            "EPS (TTM)": info.get("trailingEps"),
            "Dividend Yield": info.get("dividendYield"),
            "Beta": info.get("beta"),
            "Profit Margin": info.get("profitMargins"),
            "ROE": info.get("returnOnEquity"),
            "Revenue": info.get("totalRevenue"),
            "Volume": info.get("volume") or info.get("regularMarketVolume"),
            "Avg Volume": info.get("averageVolume"),
            "Target Mean": info.get("targetMeanPrice"),
        },
        "analyst": {
            "recommendation": info.get("recommendationKey"),
            "targetLow": info.get("targetLowPrice"),
            "targetMean": info.get("targetMeanPrice"),
            "targetHigh": info.get("targetHighPrice"),
            "numAnalysts": info.get("numberOfAnalystOpinions"),
        },
    }


@app.get("/api/history/{symbol}")
def history(symbol: str, range: str = "1M"):
    """OHLC candles for the price chart."""
    period, interval = RANGES.get(range.upper(), RANGES["1M"])
    df = _ticker(symbol).history(period=period, interval=interval)
    if df.empty:
        raise HTTPException(404, f"No price history for '{symbol}'.")

    candles = [
        {
            "time": int(ts.timestamp()),
            "open": round(row.Open, 4),
            "high": round(row.High, 4),
            "low": round(row.Low, 4),
            "close": round(row.Close, 4),
            "volume": int(row.Volume),
        }
        for ts, row in df.iterrows()
    ]
    return {"symbol": symbol.upper(), "range": range.upper(), "candles": candles}


@app.get("/api/news/{symbol}")
def news(symbol: str):
    """Recent headlines for the news panel."""
    items = []
    for n in (_ticker(symbol).news or [])[:8]:
        content = n.get("content", n)  # yfinance shapes vary by version
        items.append({
            "title": content.get("title"),
            "publisher": (content.get("provider") or {}).get("displayName")
                         or content.get("publisher"),
            "link": (content.get("canonicalUrl") or {}).get("url")
                    or content.get("link"),
        })
    return {"symbol": symbol.upper(), "news": [i for i in items if i["title"]]}


@app.get("/api/health")
def health():
    return {"status": "ok"}