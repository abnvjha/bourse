"""
Bourse — backend API.

FastAPI service wrapping Yahoo Finance (yfinance) with curl_cffi
browser-impersonation to avoid bot-blocking. Exposes clean JSON
endpoints for the React frontend.
"""

from functools import lru_cache

import yfinance as yf
from curl_cffi import requests as cffi_requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Bourse API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

RANGES = {
    "1D": ("1d", "5m"),
    "5D": ("5d", "30m"),
    "1M": ("1mo", "1d"),
    "6M": ("6mo", "1d"),
    "1Y": ("1y", "1d"),
    "5Y": ("5y", "1wk"),
}

# Browser-impersonating session — avoids Yahoo's bot detection.
_session = cffi_requests.Session(impersonate="chrome")

# Yahoo's blocking isn't perfectly consistent across browser signatures,
# so for the heavier .info call we retry with a couple of different
# impersonation profiles before giving up.
_INFO_PROFILES = ["chrome", "chrome124", "safari"]


@lru_cache(maxsize=256)
def _info_with_fallback(symbol: str) -> dict:
    for profile in _INFO_PROFILES:
        try:
            sess = cffi_requests.Session(impersonate=profile)
            t = yf.Ticker(symbol.upper().strip(), session=sess)
            info = t.info
            if info and (info.get("longBusinessSummary") or info.get("trailingPE") or info.get("sector")):
                return info
        except Exception:
            continue
    return {}


def _ticker(symbol: str) -> yf.Ticker:
    return yf.Ticker(symbol.upper().strip(), session=_session)


@app.get("/api/search")
def search(q: str):
    q = q.strip()
    if len(q) < 1:
        return {"results": []}
    try:
        resp = _session.get(
            "https://query2.finance.yahoo.com/v1/finance/search",
            params={"q": q, "quotesCount": 8, "newsCount": 0},
            timeout=8,
        )
        resp.raise_for_status()
        quotes = resp.json().get("quotes", [])
    except Exception:
        return {"results": []}

    results = []
    for item in quotes:
        symbol = item.get("symbol")
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
    t = _ticker(symbol)

    # fast_info hits a lighter Yahoo endpoint — much less likely to be blocked.
    try:
        fi = t.fast_info
        price = fi.last_price
        prev = fi.previous_close
        currency = fi.currency
        market_cap = getattr(fi, "market_cap", None)
    except Exception as e:
        raise HTTPException(404, f"Could not fetch data for '{symbol}': {e}")

    if not price:
        raise HTTPException(404, f"No data found for '{symbol}'. Check the ticker.")

    change = (price - prev) if (price and prev) else None
    pct = (change / prev * 100) if (change is not None and prev) else None

    # .info has richer fundamentals but hits a heavier, more-blocked endpoint.
    # Try a few browser signatures before giving up gracefully.
    info = _info_with_fallback(symbol)

    return {
        "symbol": symbol.upper(),
        "name": info.get("shortName") or info.get("longName") or symbol.upper(),
        "exchange": info.get("fullExchangeName") or info.get("exchange") or getattr(fi, "exchange", ""),
        "currency": currency or info.get("currency", "USD"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "website": info.get("website"),
        "summary": info.get("longBusinessSummary"),
        "price": price,
        "previousClose": prev,
        "change": change,
        "changePct": pct,
        "dayLow": getattr(fi, "day_low", None),
        "dayHigh": getattr(fi, "day_high", None),
        "fiftyTwoWeekLow": getattr(fi, "year_low", None),
        "fiftyTwoWeekHigh": getattr(fi, "year_high", None),
        "fundamentals": {
            "Market Cap": market_cap or info.get("marketCap"),
            "P/E (TTM)": info.get("trailingPE"),
            "Forward P/E": info.get("forwardPE"),
            "EPS (TTM)": info.get("trailingEps"),
            "Dividend Yield": info.get("dividendYield"),
            "Beta": info.get("beta"),
            "Profit Margin": info.get("profitMargins"),
            "ROE": info.get("returnOnEquity"),
            "Revenue": info.get("totalRevenue"),
            "Volume": getattr(fi, "three_month_average_volume", None) or info.get("volume"),
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
    items = []
    try:
        for n in (_ticker(symbol).news or [])[:8]:
            content = n.get("content", n)
            items.append({
                "title": content.get("title"),
                "publisher": (content.get("provider") or {}).get("displayName")
                             or content.get("publisher"),
                "link": (content.get("canonicalUrl") or {}).get("url")
                        or content.get("link"),
            })
    except Exception:
        return {"symbol": symbol.upper(), "news": []}
    return {"symbol": symbol.upper(), "news": [i for i in items if i["title"]]}


@app.get("/api/health")
def health():
    return {"status": "ok"}
