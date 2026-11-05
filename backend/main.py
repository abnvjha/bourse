"""
Bourse — backend API.

Core market data (quotes, charts, fundamentals, search) comes from
Twelve Data — a documented, stable, official API — instead of scraping
Yahoo Finance. News still comes from yfinance/Yahoo, since that part
was reliable and Twelve Data's free tier doesn't include news.

Requires a free Twelve Data API key: https://twelvedata.com
Set it as the TWELVE_DATA_API_KEY environment variable.
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Lock

import requests
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

TD_KEY = os.environ.get("TWELVE_DATA_API_KEY", "")
TD_BASE = "https://api.twelvedata.com"

RANGES = {
    # UI range -> (Twelve Data interval, outputsize)
    "1D": ("5min", 100),
    "5D": ("30min", 100),
    "1M": ("1day", 22),
    "6M": ("1day", 130),
    "1Y": ("1day", 260),
    "5Y": ("1week", 260),
}

# Keep the exchange context when converting Twelve Data search results into
# user-facing ticker symbols. Twelve Data expects the bare symbol plus exchange.
SUFFIX_TO_EXCHANGE = {
    ".NS": "NSE",
    ".BO": "BSE",
    ".L": "LSE",
    ".T": "TSE",
    ".DE": "XETR",
    ".HK": "HKEX",
    ".TO": "TSX",
}


def _provider_symbol(symbol: str) -> dict:
    sym = symbol.upper().strip()
    for suffix, exchange in SUFFIX_TO_EXCHANGE.items():
        if sym.endswith(suffix):
            return {"symbol": sym[:-len(suffix)], "exchange": exchange}
    return {"symbol": sym}

# yfinance session for news only — still needs browser impersonation.
_yf_session = cffi_requests.Session(impersonate="chrome", timeout=8)


def _ticker(symbol: str) -> yf.Ticker:
    return yf.Ticker(symbol.upper().strip(), session=_yf_session)


# --- Simple in-memory TTL cache -------------------------------------------
_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = Lock()
_refreshing: set[str] = set()
_upstream_pool = ThreadPoolExecutor(max_workers=12, thread_name_prefix="bourse-upstream")


def _refresh_cache(key: str, fetch):
    try:
        value = fetch()
        with _cache_lock:
            _cache[key] = (time.monotonic(), value)
    except Exception:
        # Keep the last good response when an upstream provider is flaky.
        pass
    finally:
        with _cache_lock:
            _refreshing.discard(key)


def cached(key: str, ttl: int, fetch, stale_ttl: int = 3600):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit:
            age = now - hit[0]
            if age < ttl:
                return hit[1]
            if age < ttl + stale_ttl:
                if key not in _refreshing:
                    _refreshing.add(key)
                    _upstream_pool.submit(_refresh_cache, key, fetch)
                return hit[1]

    value = fetch()
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
    return value


def _td_get(path: str, params: dict):
    if not TD_KEY:
        raise HTTPException(500, "Server is missing TWELVE_DATA_API_KEY.")
    params = {**params, "apikey": TD_KEY}
    try:
        resp = requests.get(f"{TD_BASE}/{path}", params=params, timeout=(3, 7))
    except requests.RequestException as exc:
        raise HTTPException(502, "Market data provider is temporarily unavailable.") from exc
    if not resp.ok:
        raise HTTPException(502, f"Market data provider returned HTTP {resp.status_code}.")
    data = resp.json()
    if isinstance(data, dict) and data.get("status") == "error":
        raise HTTPException(404, data.get("message", f"Twelve Data error for {path}"))
    return data


def _num(v):
    """Twelve Data returns most numbers as strings — convert safely."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------


@app.get("/api/search")
def search(q: str):
    q = q.strip()
    if len(q) < 1:
        return {"results": []}

    def fetch():
        try:
            data = _td_get("symbol_search", {"symbol": q, "outputsize": 8})
        except Exception:
            return {"results": []}

        results = []
        for item in (data.get("data") or [])[:8]:
            if item.get("instrument_type") not in {"Common Stock", "ETF", "Index", None}:
                continue
            symbol = item.get("symbol")
            exchange = item.get("exchange", "")
            if symbol and not any(symbol.upper().endswith(s) for s in SUFFIX_TO_EXCHANGE):
                if exchange == "NSE":
                    symbol += ".NS"
                elif exchange == "BSE":
                    symbol += ".BO"
            results.append({
                "symbol": symbol,
                "name": item.get("instrument_name") or item.get("symbol"),
                "exchange": exchange,
                "type": item.get("instrument_type", ""),
            })
        return {"results": results}

    return cached(f"search:{q.lower()}", 60, fetch)


@app.get("/api/stock/{symbol}")
def stock(symbol: str):
    sym = symbol.upper().strip()

    def fetch():
        # Fundamentals are optional and can be much slower than the live quote.
        # Fetch them concurrently, then cap how long they can delay the snapshot.
        stats_future = _upstream_pool.submit(
            _td_get, "statistics", _provider_symbol(sym)
        )
        quote = _td_get("quote", _provider_symbol(sym))

        price = _num(quote.get("close"))
        prev = _num(quote.get("previous_close"))
        if price is None:
            raise HTTPException(404, f"No data found for '{sym}'. Check the ticker.")

        change = _num(quote.get("change"))
        pct = _num(quote.get("percent_change"))
        if change is None and price is not None and prev is not None:
            change = price - prev
        if pct is None and change is not None and prev:
            pct = (change / prev) * 100

        fifty_two = quote.get("fifty_two_week") or {}

        # Fundamentals are best-effort — wrapped so a failure here never
        # breaks the price/chart the user actually came for.
        val, fin, stats = {}, {}, {}
        try:
            s = stats_future.result(timeout=1.25).get("statistics", {})
            val = s.get("valuations_metrics", {}) or {}
            fin = s.get("financials", {}) or {}
            stats = s.get("stock_statistics", {}) or {}
        except Exception:
            pass

        return {
            "symbol": sym,
            "name": quote.get("name") or sym,
            "exchange": quote.get("exchange", ""),
            "currency": quote.get("currency", "USD"),
            "sector": None,
            "industry": None,
            "website": None,
            "summary": None,
            "price": price,
            "previousClose": prev,
            "change": change,
            "changePct": pct,
            "dayLow": _num(quote.get("low")),
            "dayHigh": _num(quote.get("high")),
            "fiftyTwoWeekLow": _num(fifty_two.get("low")),
            "fiftyTwoWeekHigh": _num(fifty_two.get("high")),
            "fundamentals": {
                "Market Cap": _num(val.get("market_capitalization")),
                "P/E (TTM)": _num(val.get("trailing_pe")),
                "Forward P/E": _num(val.get("forward_pe")),
                "EPS (TTM)": _num((fin.get("income_statement") or {}).get("diluted_eps_ttm")),
                "Dividend Yield": _num(stats.get("trailing_annual_dividend_yield")),
                "Beta": _num(stats.get("beta")),
                "Profit Margin": _num(fin.get("profit_margin")),
                "ROE": _num(fin.get("return_on_equity_ttm")),
                "Revenue": _num((fin.get("income_statement") or {}).get("revenue_ttm")),
                "Volume": _num(quote.get("volume")),
                "Avg Volume": _num(quote.get("average_volume")),
                "Target Mean": None,
            },
            "analyst": {
                "recommendation": None,
                "targetLow": None,
                "targetMean": None,
                "targetHigh": None,
                "numAnalysts": None,
            },
        }

    return cached(f"stock:{sym}", 60, fetch)


@app.get("/api/history/{symbol}")
def history(symbol: str, range: str = "1M"):
    sym = symbol.upper().strip()
    interval, outputsize = RANGES.get(range.upper(), RANGES["1M"])

    def fetch():
        data = _td_get("time_series", {
            **_provider_symbol(sym), "interval": interval, "outputsize": outputsize,
        })
        values = data.get("values") or []
        if not values:
            raise HTTPException(404, f"No price history for '{sym}'.")

        candles = []
        for v in reversed(values):  # Twelve Data returns newest-first
            try:
                dt = datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                dt = datetime.strptime(v["datetime"], "%Y-%m-%d")
            candles.append({
                "time": int(dt.timestamp()),
                "open": round(_num(v["open"]), 4),
                "high": round(_num(v["high"]), 4),
                "low": round(_num(v["low"]), 4),
                "close": round(_num(v["close"]), 4),
                "volume": int(_num(v.get("volume")) or 0),
            })
        return {"symbol": sym, "range": range.upper(), "candles": candles}

    return cached(f"history:{sym}:{range.upper()}", 300, fetch)


@app.get("/api/news/{symbol}")
def news(symbol: str):
    def fetch():
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

    return cached(f"news:{symbol.upper()}", 600, fetch)


@app.get("/api/health")
def health():
    return {"status": "ok", "twelveDataConfigured": bool(TD_KEY)}
