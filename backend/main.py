"""
Bourse — backend API (v2).

Data layer, in one paragraph
----------------------------
Two providers sit behind one API, and every endpoint walks a *provider chain*:

  * Twelve Data  — documented, stable. Primary for plain US tickers (AAPL, TSLA)
                   when TWELVE_DATA_API_KEY is set. Protected by a local credit
                   budget so we stop *before* the provider returns HTTP 429.
  * Yahoo (yfinance + curl_cffi browser impersonation) — free, covers every
                   exchange (RELIANCE.NS, VOD.L, 7203.T, ^GSPC), plus analyst
                   targets / company profile / news. Primary for non-US symbols
                   and automatic fallback for everything else.

If the first provider fails (rate limit, plan limit, outage, unknown symbol),
the next one is tried. With no API key at all the app still runs on Yahoo only.

Other behaviour
---------------
  * TTL cache with stale-while-revalidate and single-flight (N concurrent
    requests for the same key -> ONE upstream call). Failures are never cached.
  * /api/stock stays lean (quote only). Slow/optional data (fundamentals,
    analyst targets, About) lives in /api/profile so it can never delay or
    break the price.
  * Per-IP rate limit + symbol validation so strangers can't drain your quota.

Environment variables (all optional)
------------------------------------
  TWELVE_DATA_API_KEY   enable Twelve Data
  TD_MAX_PER_MIN        credits/min you may spend on Twelve Data (default 8 = free plan)
  ALLOWED_ORIGINS       extra CORS origins, comma separated (or "*")
  IP_LIMIT_PER_MIN      requests/min per client IP (default 240)
"""

import math
import os
import re
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timezone
from threading import Lock

import requests
import yfinance as yf
from curl_cffi import requests as cffi_requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

app = FastAPI(title="Bourse API")

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
TD_KEY = os.environ.get("TWELVE_DATA_API_KEY", "").strip()
TD_BASE = "https://api.twelvedata.com"
TD_MAX_PER_MIN = int(os.environ.get("TD_MAX_PER_MIN", "8"))
TD_STATS_COST = 50  # Twelve Data's /statistics is credit-heavy; paid plans only
IP_LIMIT_PER_MIN = int(os.environ.get("IP_LIMIT_PER_MIN", "240"))
EXTRA_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]

# UI range -> (Twelve Data interval, outputsize)
TD_RANGES = {
    "1D": ("5min", 100),
    "5D": ("30min", 100),
    "1M": ("1day", 22),
    "6M": ("1day", 130),
    "1Y": ("1day", 260),
    "5Y": ("1week", 260),
}
# UI range -> (yfinance period, interval)
YF_RANGES = {
    "1D": ("1d", "5m"),
    "5D": ("5d", "30m"),
    "1M": ("1mo", "1d"),
    "6M": ("6mo", "1d"),
    "1Y": ("1y", "1d"),
    "5Y": ("5y", "1wk"),
}

# Yahoo-style suffix -> Twelve Data exchange code (used only on the TD fallback
# path; Yahoo understands suffixes natively). Codes not verified against a
# live TD plan - treat the non-India ones as best effort.
SUFFIX_TO_EXCHANGE = {
    ".NS": "NSE",
    ".BO": "BSE",
    ".L": "LSE",
    ".T": "TSE",
    ".DE": "XETR",
    ".HK": "HKEX",
    ".TO": "TSX",
}
EXCHANGE_TO_SUFFIX = {v: k for k, v in SUFFIX_TO_EXCHANGE.items()}
US_EXCHANGES = {"NASDAQ", "NYSE", "NYSE ARCA", "NYSE MKT", "AMEX", "CBOE", "BATS"}

YF_EXCHANGES = {
    "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NYQ": "NYSE",
    "ASE": "AMEX", "PCX": "NYSE ARCA", "NSI": "NSE", "BSE": "BSE",
    "LSE": "LSE", "JPX": "TSE", "GER": "XETRA", "HKG": "HKEX", "TOR": "TSX",
}

FUNDAMENTAL_KEYS = [
    "Market Cap", "P/E (TTM)", "Forward P/E", "EPS (TTM)", "Dividend Yield", "Beta",
    "Profit Margin", "ROE", "Revenue", "Volume", "Avg Volume", "Prev Close",
]

SYMBOL_RE = re.compile(r"^[A-Z0-9.\-^=]{1,15}$")
PLAIN_US_RE = re.compile(r"^[A-Z]{1,5}(-[A-Z])?$")


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------
class ProviderError(Exception):
    """A single provider failed. The chain decides what the client sees."""


class NotFound(ProviderError):
    pass


class RateLimited(ProviderError):
    pass


class Upstream(ProviderError):
    pass


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _num(v):
    """Safe float: handles Twelve Data's string numbers, None, NaN, inf."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(x) or math.isinf(x)) else x


def _clean_symbol(raw: str) -> str:
    sym = (raw or "").upper().strip()
    if not SYMBOL_RE.match(sym):
        raise HTTPException(400, "That doesn't look like a valid ticker symbol.")
    return sym


def _is_plain_us(sym: str) -> bool:
    return bool(PLAIN_US_RE.match(sym))


def _looks_missing(exc: Exception) -> bool:
    return bool(re.search(r"(not found|no data|delisted|no price|404)", str(exc), re.I))


def _run_chain(providers, what: str):
    """Try each (name, fn) in order; first success wins."""
    errs = []
    for name, fn in providers:
        try:
            return fn()
        except ProviderError as exc:
            errs.append((name, exc))
        except Exception as exc:  # yfinance and friends raise all sorts of things
            kind = NotFound if _looks_missing(exc) else Upstream
            errs.append((name, kind(f"{type(exc).__name__}: {exc}")))
    _raise_chain(errs, what)


def _raise_chain(errs, what: str):
    if any(isinstance(e, NotFound) for _, e in errs):
        raise HTTPException(404, f"No {what} found. Check the ticker symbol.")
    if any(isinstance(e, RateLimited) for _, e in errs):
        raise HTTPException(
            429,
            "Market data is rate-limited right now. Try again in a minute.",
            headers={"Retry-After": "30"},
        )
    names = ", ".join(n for n, _ in errs) or "none configured"
    raise HTTPException(502, f"Market data providers are unavailable right now ({names}).")


# --------------------------------------------------------------------------
# Cache: TTL + stale-while-revalidate + single-flight + bounded size
# --------------------------------------------------------------------------
MAX_CACHE_ENTRIES = 1500
_cache: dict[str, tuple[float, object]] = {}
_cache_lock = Lock()
_inflight: dict[str, Future] = {}
_refreshing: set[str] = set()
_bg_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="bourse-bg")


def _store(key: str, value):
    """Caller must hold _cache_lock."""
    _cache[key] = (time.monotonic(), value)
    if len(_cache) > MAX_CACHE_ENTRIES:
        oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[: MAX_CACHE_ENTRIES // 10]
        for k, _ in oldest:
            _cache.pop(k, None)


def _as_stale(value):
    return {**value, "stale": True} if isinstance(value, dict) else value


def _refresh(key: str, fetch):
    try:
        value = fetch()
        with _cache_lock:
            _store(key, value)
    except Exception:
        pass  # keep serving the last good value until it ages out
    finally:
        with _cache_lock:
            _refreshing.discard(key)


def cached(key: str, ttl: int, fetch, stale_ttl: int = 900):
    """
    Fresh (< ttl)            -> cached value.
    Stale (< ttl+stale_ttl)  -> cached value flagged "stale", refreshed in background.
    Miss                     -> exactly one caller runs fetch(); concurrent callers
                                for the same key wait for that result.
    Exceptions are never cached.
    """
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
                    _bg_pool.submit(_refresh, key, fetch)
                return _as_stale(hit[1])
        fut = _inflight.get(key)
        leader = fut is None
        if leader:
            fut = Future()
            _inflight[key] = fut

    if not leader:
        try:
            return fut.result(timeout=45)
        except FutureTimeout:
            raise HTTPException(504, "Market data request timed out.")

    try:
        value = fetch()
    except BaseException as exc:
        with _cache_lock:
            _inflight.pop(key, None)
        fut.set_exception(exc)
        raise
    with _cache_lock:
        _store(key, value)
        _inflight.pop(key, None)
    fut.set_result(value)
    return value


# --------------------------------------------------------------------------
# Twelve Data
# --------------------------------------------------------------------------
_td_calls: deque = deque()  # (timestamp, credits)
_td_lock = Lock()
_td_blocked_until = 0.0


def td_enabled() -> bool:
    return bool(TD_KEY)


def _td_budget_left() -> int:
    now = time.monotonic()
    with _td_lock:
        while _td_calls and now - _td_calls[0][0] > 60:
            _td_calls.popleft()
        return max(0, TD_MAX_PER_MIN - sum(c for _, c in _td_calls))


def _td_reserve(cost: int = 1):
    """Spend credits locally *before* calling, so we back off before a 429."""
    now = time.monotonic()
    with _td_lock:
        if now < _td_blocked_until:
            raise RateLimited("Twelve Data cooling down")
        while _td_calls and now - _td_calls[0][0] > 60:
            _td_calls.popleft()
        if sum(c for _, c in _td_calls) + cost > TD_MAX_PER_MIN:
            raise RateLimited("Twelve Data credit budget used up for this minute")
        _td_calls.append((now, cost))


def _td_cooldown(seconds: float = 30.0):
    global _td_blocked_until
    with _td_lock:
        _td_blocked_until = time.monotonic() + seconds


def _td_get(path: str, params: dict, cost: int = 1):
    if not TD_KEY:
        raise Upstream("Twelve Data is not configured")
    _td_reserve(cost)
    try:
        resp = requests.get(
            f"{TD_BASE}/{path}", params={**params, "apikey": TD_KEY}, timeout=(3, 7)
        )
    except requests.RequestException as exc:
        raise Upstream("Twelve Data unreachable") from exc
    if resp.status_code == 429:
        _td_cooldown()
        raise RateLimited("Twelve Data HTTP 429")
    if not resp.ok:
        raise Upstream(f"Twelve Data HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise Upstream("Twelve Data returned invalid JSON") from exc
    if isinstance(data, dict) and data.get("status") == "error":
        code, msg = data.get("code"), data.get("message", "")
        if code == 429:  # TD reports rate limits as HTTP 200 + code 429 in the body
            _td_cooldown()
            raise RateLimited(msg)
        if code in (400, 404):
            raise NotFound(msg)
        raise Upstream(f"Twelve Data {code}: {msg}")
    return data


def _td_symbol(sym: str) -> dict:
    for suffix, exchange in SUFFIX_TO_EXCHANGE.items():
        if sym.endswith(suffix) and len(sym) > len(suffix):
            return {"symbol": sym[: -len(suffix)], "exchange": exchange}
    return {"symbol": sym.replace("-", ".")}  # BRK-B -> BRK.B


def _td_epoch(s: str) -> int:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            # Exchange wall-clock labelled as UTC: the chart renders UTC, so the
            # axis shows 09:30-16:00 for US stocks instead of shifting by server tz.
            return int(datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    raise Upstream(f"Unparseable timestamp from Twelve Data: {s!r}")


def _td_quote(sym: str) -> dict:
    q = _td_get("quote", _td_symbol(sym))
    price = _num(q.get("close"))
    if price is None:
        raise NotFound(f"Twelve Data has no quote for {sym}")
    prev = _num(q.get("previous_close"))
    change, pct = _num(q.get("change")), _num(q.get("percent_change"))
    if change is None and prev is not None:
        change = price - prev
    if pct is None and change is not None and prev:
        pct = change / prev * 100
    wk = q.get("fifty_two_week") or {}
    return {
        "name": q.get("name"),
        "exchange": q.get("exchange", ""),
        "currency": q.get("currency") or "USD",
        "price": price,
        "previousClose": prev,
        "change": change,
        "changePct": pct,
        "dayLow": _num(q.get("low")),
        "dayHigh": _num(q.get("high")),
        "fiftyTwoWeekLow": _num(wk.get("low")),
        "fiftyTwoWeekHigh": _num(wk.get("high")),
        "volume": _num(q.get("volume")),
        "avgVolume": _num(q.get("average_volume")),
        "marketCap": None,
        "source": "twelvedata",
    }


def _td_history(sym: str, rng: str) -> list:
    interval, size = TD_RANGES[rng]
    data = _td_get("time_series", {**_td_symbol(sym), "interval": interval, "outputsize": size})
    candles = []
    for v in data.get("values") or []:
        o, h, l, c = (_num(v.get(k)) for k in ("open", "high", "low", "close"))
        if None in (o, h, l, c):
            continue
        candles.append({
            "time": _td_epoch(v["datetime"]),
            "open": round(o, 4), "high": round(h, 4), "low": round(l, 4), "close": round(c, 4),
            "volume": int(_num(v.get("volume")) or 0),
        })
    if not candles:
        raise NotFound(f"Twelve Data has no history for {sym}")
    return candles


def _td_search(q: str) -> list:
    data = _td_get("symbol_search", {"symbol": q, "outputsize": 8})
    out = []
    for item in (data.get("data") or [])[:8]:
        if item.get("instrument_type") not in {"Common Stock", "ETF", "Index", None}:
            continue
        sym, exch = item.get("symbol"), item.get("exchange", "")
        if not sym:
            continue
        if exch in EXCHANGE_TO_SUFFIX:
            sym += EXCHANGE_TO_SUFFIX[exch]
        elif exch not in US_EXCHANGES:
            continue  # unknown foreign listing: skip rather than open the wrong instrument
        out.append({
            "symbol": sym,
            "name": item.get("instrument_name") or sym,
            "exchange": exch,
            "type": item.get("instrument_type") or "",
        })
    return out


def _td_profile(sym: str) -> dict:
    if TD_MAX_PER_MIN < TD_STATS_COST:
        raise Upstream("Twelve Data statistics needs a paid plan")
    s = _td_get("statistics", _td_symbol(sym), cost=TD_STATS_COST).get("statistics", {}) or {}
    val = s.get("valuations_metrics") or {}
    fin = s.get("financials") or {}
    inc = fin.get("income_statement") or {}
    st = s.get("stock_statistics") or {}
    return {
        "name": None, "sector": None, "industry": None, "website": None, "summary": None,
        "fundamentals": {
            "Market Cap": _num(val.get("market_capitalization")),
            "P/E (TTM)": _num(val.get("trailing_pe")),
            "Forward P/E": _num(val.get("forward_pe")),
            "EPS (TTM)": _num(inc.get("diluted_eps_ttm")),
            "Dividend Yield": _num(st.get("trailing_annual_dividend_yield")),
            "Beta": _num(st.get("beta")),
            "Profit Margin": _num(fin.get("profit_margin")),
            "ROE": _num(fin.get("return_on_equity_ttm")),
            "Revenue": _num(inc.get("revenue_ttm")),
        },
        "analyst": {},
    }


# --------------------------------------------------------------------------
# Yahoo (yfinance through a browser-impersonating session)
# --------------------------------------------------------------------------
_yf_session = cffi_requests.Session(impersonate="chrome", timeout=8)
_YF_SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
_YF_TYPES = {"EQUITY": "Common Stock", "ETF": "ETF", "INDEX": "Index"}


def _ticker(symbol: str) -> yf.Ticker:
    return yf.Ticker(symbol, session=_yf_session)


def _fi(fast_info, key: str):
    try:
        return fast_info[key]
    except Exception:
        return None


def _yf_symbol_exists(sym: str):
    """True/False if Yahoo's search endpoint can confirm the symbol; None if we couldn't ask."""
    try:
        resp = _yf_session.get(
            _YF_SEARCH_URL,
            params={"q": sym, "quotesCount": 5, "newsCount": 0, "listsCount": 0},
            timeout=5,
        )
        if resp.status_code != 200:
            return None
        return any((i.get("symbol") or "").upper() == sym for i in resp.json().get("quotes", []))
    except Exception:
        return None


def _yf_empty(sym: str, what: str) -> ProviderError:
    """
    Yahoo returns *nothing* both for typos and when it is blocking us, so an empty
    answer alone must not be reported as 'bad ticker'. Only call it NotFound when
    the search endpoint confirms the symbol doesn't exist.
    """
    if _yf_symbol_exists(sym) is False:
        return NotFound(f"Yahoo has no {what} for {sym}")
    return Upstream(f"Yahoo returned no {what} for {sym} (unavailable or blocked)")


def _yf_quote(sym: str) -> dict:
    fi = _ticker(sym).fast_info
    price = _num(_fi(fi, "last_price"))
    if price is None:
        raise _yf_empty(sym, "quote")
    prev = _num(_fi(fi, "previous_close"))
    change = price - prev if prev else None
    pct = change / prev * 100 if (change is not None and prev) else None
    exch = _fi(fi, "exchange")
    return {
        "name": None,  # fast_info has no company name; /api/profile supplies it
        "exchange": YF_EXCHANGES.get(exch, exch or ""),
        "currency": _fi(fi, "currency") or "USD",
        "price": price,
        "previousClose": prev,
        "change": change,
        "changePct": pct,
        "dayLow": _num(_fi(fi, "day_low")),
        "dayHigh": _num(_fi(fi, "day_high")),
        "fiftyTwoWeekLow": _num(_fi(fi, "year_low")),
        "fiftyTwoWeekHigh": _num(_fi(fi, "year_high")),
        "volume": _num(_fi(fi, "last_volume")),
        "avgVolume": _num(_fi(fi, "three_month_average_volume")),
        "marketCap": _num(_fi(fi, "market_cap")),
        "source": "yahoo",
    }


def _wall_clock_epoch(ts) -> int:
    try:
        ts = ts.tz_localize(None)  # drop tz, keep exchange wall-clock time
    except (TypeError, AttributeError):
        pass
    return int(ts.to_pydatetime().replace(tzinfo=timezone.utc).timestamp())


def _yf_history(sym: str, rng: str) -> list:
    period, interval = YF_RANGES[rng]
    df = _ticker(sym).history(period=period, interval=interval, auto_adjust=False)
    if df is None or df.empty:
        raise _yf_empty(sym, "history")
    candles = []
    for ts, row in df.iterrows():
        o, h, l, c = (_num(row.get(k)) for k in ("Open", "High", "Low", "Close"))
        if None in (o, h, l, c):
            continue
        candles.append({
            "time": _wall_clock_epoch(ts),
            "open": round(o, 4), "high": round(h, 4), "low": round(l, 4), "close": round(c, 4),
            "volume": int(_num(row.get("Volume")) or 0),
        })
    if not candles:
        raise Upstream(f"Yahoo returned no usable candles for {sym}")
    return candles


def _yf_search(q: str) -> list:
    resp = _yf_session.get(
        _YF_SEARCH_URL,
        params={"q": q, "quotesCount": 8, "newsCount": 0, "listsCount": 0},
        timeout=5,
    )
    if resp.status_code == 429:
        raise RateLimited("Yahoo search HTTP 429")
    if resp.status_code != 200:
        raise Upstream(f"Yahoo search HTTP {resp.status_code}")
    out = []
    for item in resp.json().get("quotes", []):
        label = _YF_TYPES.get(item.get("quoteType"))
        sym = item.get("symbol")
        if not label or not sym:
            continue
        out.append({
            "symbol": sym.upper(),
            "name": item.get("longname") or item.get("shortname") or sym,
            "exchange": item.get("exchDisp") or item.get("exchange") or "",
            "type": label,
        })
    return out[:8]


def _yf_profile(sym: str) -> dict:
    info = _ticker(sym).get_info() or {}
    name = info.get("longName") or info.get("shortName")
    if not name and info.get("marketCap") is None:
        raise Upstream(f"Yahoo returned no profile for {sym}")
    div = _num(info.get("trailingAnnualDividendYield"))
    if div is None:  # newer yfinance reports dividendYield in percent
        d = _num(info.get("dividendYield"))
        div = d / 100 if d is not None else None
    return {
        "name": name,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "website": info.get("website"),
        "summary": info.get("longBusinessSummary"),
        "fundamentals": {
            "Market Cap": _num(info.get("marketCap")),
            "P/E (TTM)": _num(info.get("trailingPE")),
            "Forward P/E": _num(info.get("forwardPE")),
            "EPS (TTM)": _num(info.get("trailingEps")),
            "Dividend Yield": div,
            "Beta": _num(info.get("beta")),
            "Profit Margin": _num(info.get("profitMargins")),
            "ROE": _num(info.get("returnOnEquity")),
            "Revenue": _num(info.get("totalRevenue")),
        },
        "analyst": {
            "recommendation": info.get("recommendationKey"),
            "targetLow": _num(info.get("targetLowPrice")),
            "targetMean": _num(info.get("targetMeanPrice")),
            "targetHigh": _num(info.get("targetHighPrice")),
            "numAnalysts": info.get("numberOfAnalystOpinions"),
        },
    }


# --------------------------------------------------------------------------
# Provider ordering
# --------------------------------------------------------------------------
def _ordered(sym: str, yahoo, td):
    """Plain US ticker + key -> Twelve Data first. Anything else -> Yahoo first."""
    chain = []
    if td_enabled() and _is_plain_us(sym):
        chain = [("twelvedata", td), ("yahoo", yahoo)]
    else:
        chain = [("yahoo", yahoo)]
        if td_enabled() and not sym.startswith("^") and "=" not in sym:
            chain.append(("twelvedata", td))
    return chain


def _finish_candles(candles: list, rng: str) -> list:
    """Sorted, de-duplicated (lightweight-charts rejects dupes); 1D = latest session."""
    by_time = {c["time"]: c for c in candles}
    out = [by_time[t] for t in sorted(by_time)]
    if rng == "1D" and out:
        last_day = out[-1]["time"] // 86400
        latest = [c for c in out if c["time"] // 86400 == last_day]
        if len(latest) >= 10:  # just after the open, keep the prior session for context
            out = latest
    return out


# --------------------------------------------------------------------------
# Middleware (rate limit registered BEFORE CORS so CORS wraps it and 429s
# still carry CORS headers)
# --------------------------------------------------------------------------
_ip_hits: dict[str, deque] = {}
_ip_lock = Lock()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _ip_allowed(ip: str) -> bool:
    now = time.monotonic()
    with _ip_lock:
        dq = _ip_hits.setdefault(ip, deque())
        while dq and now - dq[0] > 60:
            dq.popleft()
        if len(dq) >= IP_LIMIT_PER_MIN:
            return False
        dq.append(now)
        if len(_ip_hits) > 2000:
            for k in [k for k, v in _ip_hits.items() if not v or now - v[-1] > 60]:
                _ip_hits.pop(k, None)
    return True


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path != "/api/health" and request.method != "OPTIONS":
        if not _ip_allowed(_client_ip(request)):
            return JSONResponse(
                {"detail": "Too many requests. Slow down for a moment."},
                status_code=429,
                headers={"Retry-After": "30"},
            )
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=EXTRA_ORIGINS,
    allow_origin_regex=r"^(https://([a-z0-9-]+\.)*vercel\.app|http://(localhost|127\.0\.0\.1)(:\d+)?)$",
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
@app.get("/api/search")
def search(q: str):
    q = q.strip()[:40]
    if not q:
        return {"results": []}

    def fetch():
        providers = [("yahoo", lambda: _yf_search(q))]
        if td_enabled():
            providers.append(("twelvedata", lambda: _td_search(q)))
        return {"results": _run_chain(providers, "matches")}

    try:
        return cached(f"search:{q.lower()}", 120, fetch, stale_ttl=3600)
    except HTTPException:
        # Autocomplete should degrade to "nothing" rather than error. Not cached.
        return {"results": [], "unavailable": True}


@app.get("/api/stock/{symbol}")
def stock(symbol: str):
    sym = _clean_symbol(symbol)

    def fetch():
        q = _run_chain(
            _ordered(sym, lambda: _yf_quote(sym), lambda: _td_quote(sym)),
            f"quote for '{sym}'",
        )
        funds = {k: None for k in FUNDAMENTAL_KEYS}
        funds["Market Cap"] = q.pop("marketCap", None)
        funds["Volume"] = q.pop("volume", None)
        funds["Avg Volume"] = q.pop("avgVolume", None)
        funds["Prev Close"] = q.get("previousClose")
        return {
            "symbol": sym,
            "sector": None, "industry": None, "website": None, "summary": None,
            **q,
            "name": q.get("name") or sym,
            "fundamentals": funds,
            "analyst": {
                "recommendation": None, "targetLow": None, "targetMean": None,
                "targetHigh": None, "numAnalysts": None,
            },
            "fetchedAt": int(time.time()),
        }

    return cached(f"stock:{sym}", 60, fetch, stale_ttl=600)


@app.get("/api/profile/{symbol}")
def profile(symbol: str):
    """Slow/optional data: company profile, fundamentals, analyst targets."""
    sym = _clean_symbol(symbol)

    def fetch():
        providers = [("yahoo", lambda: _yf_profile(sym))]
        if td_enabled():
            providers.append(("twelvedata", lambda: _td_profile(sym)))
        p = _run_chain(providers, f"profile for '{sym}'")
        return {
            "symbol": sym,
            "available": True,
            "name": p.get("name"),
            "sector": p.get("sector"),
            "industry": p.get("industry"),
            "website": p.get("website"),
            "summary": p.get("summary"),
            "fundamentals": {k: v for k, v in (p.get("fundamentals") or {}).items() if v is not None},
            "analyst": p.get("analyst") or {},
        }

    try:
        return cached(f"profile:{sym}", 900, fetch, stale_ttl=86400)
    except HTTPException:
        return {"symbol": sym, "available": False, "fundamentals": {}, "analyst": {}}


@app.get("/api/history/{symbol}")
def history(symbol: str, range: str = "1M"):
    sym = _clean_symbol(symbol)
    rng = range.upper() if range.upper() in TD_RANGES else "1M"

    def fetch():
        candles = _run_chain(
            _ordered(sym, lambda: _yf_history(sym, rng), lambda: _td_history(sym, rng)),
            f"price history for '{sym}'",
        )
        return {"symbol": sym, "range": rng, "candles": _finish_candles(candles, rng)}

    ttl = 120 if rng in ("1D", "5D") else 300
    return cached(f"history:{sym}:{rng}", ttl, fetch, stale_ttl=3600)


@app.get("/api/news/{symbol}")
def news(symbol: str):
    sym = _clean_symbol(symbol)

    def fetch():
        items = []
        for n in (_ticker(sym).news or [])[:8]:
            content = n.get("content", n)
            items.append({
                "title": content.get("title"),
                "publisher": (content.get("provider") or {}).get("displayName")
                             or content.get("publisher"),
                "link": (content.get("canonicalUrl") or {}).get("url") or content.get("link"),
            })
        return {"symbol": sym, "news": [i for i in items if i["title"]]}

    try:
        return cached(f"news:{sym}", 600, fetch, stale_ttl=3600)
    except Exception:
        return {"symbol": sym, "news": []}  # news is a nice-to-have; never cached on failure


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "twelveDataConfigured": td_enabled(),
        "twelveDataCreditsLeft": _td_budget_left() if td_enabled() else None,
        "providers": (["twelvedata"] if td_enabled() else []) + ["yahoo"],
    }
