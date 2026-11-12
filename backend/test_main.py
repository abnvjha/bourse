import threading
import time

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


class FakeResp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status
        self.ok = 200 <= status < 300

    def json(self):
        return self._p


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    main._cache.clear()
    main._inflight.clear()
    main._refreshing.clear()
    main._td_calls.clear()
    main._ip_hits.clear()
    main._td_blocked_until = 0.0
    monkeypatch.setattr(main, "TD_KEY", "test-key")
    monkeypatch.setattr(main, "TD_MAX_PER_MIN", 8)
    monkeypatch.setattr(main, "IP_LIMIT_PER_MIN", 10_000)
    yield


def td_quote_payload(price="190.5"):
    return {
        "name": "Apple Inc", "exchange": "NASDAQ", "currency": "USD", "close": price,
        "previous_close": "188.0", "change": "2.5", "percent_change": "1.33",
        "low": "187", "high": "191", "volume": "1000", "average_volume": "2000",
        "fifty_two_week": {"low": "120", "high": "200"},
    }


def yahoo_quote(sym="X"):
    return {
        "name": None, "exchange": "NSE", "currency": "INR", "price": 100.0,
        "previousClose": 99.0, "change": 1.0, "changePct": 1.01, "dayLow": 98.0,
        "dayHigh": 101.0, "fiftyTwoWeekLow": 80.0, "fiftyTwoWeekHigh": 120.0,
        "volume": 5.0, "avgVolume": 6.0, "marketCap": 1e9, "source": "yahoo",
    }


# ---------------------------------------------------------------- routing
def test_plain_us_ticker_uses_twelvedata_first(monkeypatch):
    calls = []
    monkeypatch.setattr(main.requests, "get", lambda url, **kw: calls.append(url) or FakeResp(td_quote_payload()))
    monkeypatch.setattr(main, "_yf_quote", lambda s: pytest.fail("yahoo must not be called"))
    r = client.get("/api/stock/aapl")
    assert r.status_code == 200
    d = r.json()
    assert d["source"] == "twelvedata" and d["symbol"] == "AAPL" and d["price"] == 190.5
    assert d["name"] == "Apple Inc" and d["fundamentals"]["Volume"] == 1000.0
    assert d["fundamentals"]["Prev Close"] == 188.0
    assert len(calls) == 1


def test_suffix_symbol_goes_to_yahoo_first(monkeypatch):
    monkeypatch.setattr(main.requests, "get", lambda *a, **k: pytest.fail("TD must not be called"))
    monkeypatch.setattr(main, "_yf_quote", lambda s: yahoo_quote(s))
    d = client.get("/api/stock/RELIANCE.NS").json()
    assert d["source"] == "yahoo" and d["currency"] == "INR" and d["name"] == "RELIANCE.NS"
    assert d["fundamentals"]["Market Cap"] == 1e9


def test_no_api_key_runs_on_yahoo_only(monkeypatch):
    monkeypatch.setattr(main, "TD_KEY", "")
    monkeypatch.setattr(main.requests, "get", lambda *a, **k: pytest.fail("no TD without key"))
    monkeypatch.setattr(main, "_yf_quote", lambda s: yahoo_quote(s))
    assert client.get("/api/stock/AAPL").json()["source"] == "yahoo"
    assert client.get("/api/health").json()["providers"] == ["yahoo"]


def test_td_rate_limit_in_body_falls_back_to_yahoo(monkeypatch):
    body = {"status": "error", "code": 429, "message": "run out of API credits"}
    monkeypatch.setattr(main.requests, "get", lambda *a, **k: FakeResp(body))
    monkeypatch.setattr(main, "_yf_quote", lambda s: yahoo_quote(s))
    d = client.get("/api/stock/AAPL").json()
    assert d["source"] == "yahoo"
    # cooldown engaged: TD is skipped without another HTTP call
    monkeypatch.setattr(main.requests, "get", lambda *a, **k: pytest.fail("cooldown ignored"))
    main._cache.clear()
    assert client.get("/api/stock/MSFT").json()["source"] == "yahoo"


def test_local_budget_stops_before_provider_429(monkeypatch):
    monkeypatch.setattr(main, "TD_MAX_PER_MIN", 2)
    n = {"c": 0}

    def fake_get(*a, **k):
        n["c"] += 1
        return FakeResp(td_quote_payload())

    monkeypatch.setattr(main.requests, "get", fake_get)
    monkeypatch.setattr(main, "_yf_quote", lambda s: yahoo_quote(s))
    sources = [client.get(f"/api/stock/{s}").json()["source"] for s in ("AAA", "BBB", "CCC")]
    assert sources == ["twelvedata", "twelvedata", "yahoo"] and n["c"] == 2


# ---------------------------------------------------------------- errors
def test_error_mapping(monkeypatch):
    nf = {"status": "error", "code": 400, "message": "symbol not found"}
    monkeypatch.setattr(main.requests, "get", lambda *a, **k: FakeResp(nf))
    monkeypatch.setattr(main, "_yf_quote", lambda s: (_ for _ in ()).throw(main.NotFound("x")))
    assert client.get("/api/stock/ZZZZ").status_code == 404

    main._cache.clear(); main._td_blocked_until = 0.0
    monkeypatch.setattr(main.requests, "get", lambda *a, **k: FakeResp({"status": "error", "code": 429, "message": "m"}))
    monkeypatch.setattr(main, "_yf_quote", lambda s: (_ for _ in ()).throw(main.RateLimited("y")))
    r = client.get("/api/stock/ZZZZ")
    assert r.status_code == 429 and r.headers["retry-after"] == "30"

    main._cache.clear(); main._td_blocked_until = 0.0
    monkeypatch.setattr(main.requests, "get", lambda *a, **k: FakeResp({}, status=500))
    monkeypatch.setattr(main, "_yf_quote", lambda s: (_ for _ in ()).throw(RuntimeError("boom")))
    assert client.get("/api/stock/ZZZZ").status_code == 502


def test_yahoo_exception_that_says_missing_is_404(monkeypatch):
    monkeypatch.setattr(main, "TD_KEY", "")
    monkeypatch.setattr(main, "_yf_quote", lambda s: (_ for _ in ()).throw(RuntimeError("No data found, symbol may be delisted")))
    assert client.get("/api/stock/NOPE").status_code == 404


def test_invalid_symbol_rejected_without_upstream_calls(monkeypatch):
    monkeypatch.setattr(main.requests, "get", lambda *a, **k: pytest.fail("should not call upstream"))
    for bad in ("AAPL;DROP", "A" * 40, "A B", "%3Cscript%3E"):
        assert client.get(f"/api/stock/{bad}").status_code == 400


# ---------------------------------------------------------------- cache
def test_failures_are_not_cached_search(monkeypatch):
    monkeypatch.setattr(main, "TD_KEY", "")
    state = {"fail": True, "calls": 0}

    def ys(q):
        state["calls"] += 1
        if state["fail"]:
            raise main.Upstream("down")
        return [{"symbol": "AAPL", "name": "Apple", "exchange": "NASDAQ", "type": "Common Stock"}]

    monkeypatch.setattr(main, "_yf_search", ys)
    r1 = client.get("/api/search?q=apple").json()
    assert r1["results"] == [] and r1["unavailable"] is True
    state["fail"] = False
    r2 = client.get("/api/search?q=apple").json()  # immediately after: must retry, not serve cached failure
    assert r2["results"][0]["symbol"] == "AAPL" and state["calls"] == 2


def test_news_failure_not_cached(monkeypatch):
    class T:
        calls = 0

        @property
        def news(self):
            T.calls += 1
            if T.calls == 1:
                raise RuntimeError("blocked")
            return [{"content": {"title": "Hello", "provider": {"displayName": "Wire"},
                                 "canonicalUrl": {"url": "https://x"}}}]

    monkeypatch.setattr(main, "_ticker", lambda s: T())
    assert client.get("/api/news/AAPL").json()["news"] == []
    got = client.get("/api/news/AAPL").json()["news"]
    assert got == [{"title": "Hello", "publisher": "Wire", "link": "https://x"}]


def test_single_flight_collapses_concurrent_requests():
    n = {"c": 0}

    def slow():
        n["c"] += 1
        time.sleep(0.3)
        return {"v": 1}

    out = []
    ts = [threading.Thread(target=lambda: out.append(main.cached("k", 60, slow))) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert n["c"] == 1 and len(out) == 8 and all(o == {"v": 1} for o in out)


def test_single_flight_propagates_error_then_recovers():
    n = {"c": 0}

    def flaky():
        n["c"] += 1
        time.sleep(0.2)
        raise main.HTTPException(502, "down")

    errs = []

    def run():
        try:
            main.cached("e", 60, flaky)
        except main.HTTPException as e:
            errs.append(e.status_code)

    ts = [threading.Thread(target=run) for _ in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert errs == [502] * 4 and n["c"] == 1
    assert main.cached("e", 60, lambda: {"ok": 1}) == {"ok": 1}  # nothing poisoned


def test_stale_while_revalidate():
    state = {"v": 1}
    assert main.cached("s", 0.05, lambda: {"v": state["v"]}) == {"v": 1}
    time.sleep(0.08)
    state["v"] = 2
    stale = main.cached("s", 0.05, lambda: {"v": state["v"]})
    assert stale == {"v": 1, "stale": True}
    time.sleep(0.2)  # background refresh lands
    assert main.cached("s", 5, lambda: pytest.fail("fresh now")) == {"v": 2}


def test_stale_value_expires_instead_of_lying_forever():
    main.cached("x", 0.01, lambda: {"v": 1}, stale_ttl=0.02)
    time.sleep(0.05)
    assert main.cached("x", 0.01, lambda: {"v": 9}, stale_ttl=0.02) == {"v": 9}


def test_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(main, "MAX_CACHE_ENTRIES", 50)
    for i in range(200):
        main.cached(f"k{i}", 60, lambda i=i: {"i": i})
    assert len(main._cache) <= 50


# ---------------------------------------------------------------- history
def test_history_sorted_deduped_and_1d_trimmed(monkeypatch):
    monkeypatch.setattr(main, "TD_KEY", "")
    base = 1_700_000_000 - (1_700_000_000 % 86400)
    candles = [{"time": base - 86400 + i * 300, "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 1} for i in range(5)]
    candles += [{"time": base + i * 300, "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 1} for i in range(20)]
    candles += [candles[-1]]  # duplicate timestamp
    candles.reverse()  # unsorted (newest first)
    monkeypatch.setattr(main, "_yf_history", lambda s, r: candles)
    d = client.get("/api/history/AAPL?range=1d").json()
    times = [c["time"] for c in d["candles"]]
    assert d["range"] == "1D" and times == sorted(set(times)) and len(times) == 20

    main._cache.clear()
    d5 = client.get("/api/history/AAPL?range=5D").json()
    assert len(d5["candles"]) == 25  # no trimming outside 1D


def test_history_unknown_range_defaults_to_1m(monkeypatch):
    monkeypatch.setattr(main, "TD_KEY", "")
    monkeypatch.setattr(main, "_yf_history", lambda s, r: [{"time": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0}])
    assert client.get("/api/history/AAPL?range=bogus").json()["range"] == "1M"


def test_td_history_parsing_and_utc_epoch(monkeypatch):
    vals = [
        {"datetime": "2026-01-03", "open": "3", "high": "4", "low": "2", "close": "3.5", "volume": "10"},
        {"datetime": "2026-01-02", "open": "1", "high": "2", "low": "0.5", "close": "1.5", "volume": "5"},
        {"datetime": "2026-01-01", "open": "bad", "high": "2", "low": "1", "close": "1", "volume": "5"},
    ]
    monkeypatch.setattr(main.requests, "get", lambda *a, **k: FakeResp({"values": vals}))
    c = client.get("/api/history/AAPL?range=1M").json()["candles"]
    assert len(c) == 2 and c[0]["time"] < c[1]["time"]
    assert c[0]["time"] == 1767312000  # 2026-01-02T00:00:00Z regardless of server tz


def test_yahoo_history_conversion(monkeypatch):
    idx = pd.DatetimeIndex(["2026-01-02 09:30", "2026-01-02 09:35"], tz="America/New_York")
    df = pd.DataFrame(
        {"Open": [1.0, 2.0], "High": [2.0, 3.0], "Low": [0.5, 1.0], "Close": [1.5, float("nan")], "Volume": [10, 20]},
        index=idx,
    )

    class T:
        def history(self, **kw):
            return df

    monkeypatch.setattr(main, "_ticker", lambda s: T())
    out = main._yf_history("AAPL", "1D")
    assert len(out) == 1  # NaN row dropped
    assert out[0]["time"] == int(pd.Timestamp("2026-01-02 09:30", tz="UTC").timestamp())  # wall-clock as UTC


def test_yahoo_quote_and_profile_parsing(monkeypatch):
    class T:
        fast_info = {"last_price": 50.0, "previous_close": 40.0, "currency": "INR", "exchange": "NSI",
                     "day_low": 1, "day_high": 2, "year_low": 3, "year_high": 4,
                     "last_volume": 5, "three_month_average_volume": 6, "market_cap": 7}

        def get_info(self):
            return {"longName": "Acme Ltd", "sector": "Tech", "dividendYield": 0.5, "marketCap": 10,
                    "targetMeanPrice": 60, "targetLowPrice": 40, "targetHighPrice": 80,
                    "recommendationKey": "strong_buy", "numberOfAnalystOpinions": 12}

    monkeypatch.setattr(main, "_ticker", lambda s: T())
    q = main._yf_quote("ACME.NS")
    assert q["exchange"] == "NSE" and q["change"] == 10.0 and q["changePct"] == 25.0
    p = main._yf_profile("ACME.NS")
    assert p["name"] == "Acme Ltd" and p["fundamentals"]["Dividend Yield"] == 0.005
    assert p["analyst"]["targetMean"] == 60


# ---------------------------------------------------------------- profile / search
def test_profile_unavailable_is_200_and_not_cached(monkeypatch):
    monkeypatch.setattr(main, "TD_KEY", "")
    state = {"fail": True}

    def yp(s):
        if state["fail"]:
            raise main.Upstream("blocked")
        return {"name": "Acme", "sector": "Tech", "industry": None, "website": None, "summary": "S",
                "fundamentals": {"P/E (TTM)": 12.0, "Beta": None}, "analyst": {"targetMean": 5}}

    monkeypatch.setattr(main, "_yf_profile", yp)
    r = client.get("/api/profile/AAPL")
    assert r.status_code == 200 and r.json()["available"] is False
    state["fail"] = False
    d = client.get("/api/profile/AAPL").json()
    assert d["available"] and d["fundamentals"] == {"P/E (TTM)": 12.0} and d["name"] == "Acme"


def test_td_profile_needs_paid_budget(monkeypatch):
    with pytest.raises(main.Upstream):
        main._td_profile("AAPL")  # TD_MAX_PER_MIN = 8 < 50


def test_search_yahoo_first_and_td_fallback_adds_suffixes(monkeypatch):
    monkeypatch.setattr(main, "_yf_search", lambda q: [{"symbol": "RELIANCE.NS", "name": "Reliance", "exchange": "NSE", "type": "Common Stock"}])
    monkeypatch.setattr(main.requests, "get", lambda *a, **k: pytest.fail("TD not needed"))
    assert client.get("/api/search?q=reliance").json()["results"][0]["symbol"] == "RELIANCE.NS"

    main._cache.clear()
    monkeypatch.setattr(main, "_yf_search", lambda q: (_ for _ in ()).throw(main.Upstream("down")))
    td = {"data": [
        {"symbol": "VOD", "instrument_name": "Vodafone", "exchange": "LSE", "instrument_type": "Common Stock"},
        {"symbol": "VOD", "instrument_name": "Vodafone ADR", "exchange": "NASDAQ", "instrument_type": "Common Stock"},
        {"symbol": "VOD", "instrument_name": "Mystery", "exchange": "MEXI", "instrument_type": "Common Stock"},
        {"symbol": "VODW", "instrument_name": "Warrant", "exchange": "NASDAQ", "instrument_type": "Warrant"},
    ]}
    monkeypatch.setattr(main.requests, "get", lambda *a, **k: FakeResp(td))
    got = [r["symbol"] for r in client.get("/api/search?q=vod").json()["results"]]
    assert got == ["VOD.L", "VOD"]


def test_search_blank_and_long_queries(monkeypatch):
    assert client.get("/api/search?q=%20").json() == {"results": []}
    seen = []
    monkeypatch.setattr(main, "TD_KEY", "")
    monkeypatch.setattr(main, "_yf_search", lambda q: seen.append(q) or [])
    client.get("/api/search?q=" + "a" * 500)
    assert len(seen[0]) == 40


# ---------------------------------------------------------------- middleware
def test_cors_allows_vercel_and_localhost_only(monkeypatch):
    def h(origin):
        return client.get("/api/health", headers={"Origin": origin}).headers.get("access-control-allow-origin")

    assert h("https://bourse-gules.vercel.app") == "https://bourse-gules.vercel.app"
    assert h("https://bourse-abnv.vercel.app") == "https://bourse-abnv.vercel.app"
    assert h("http://localhost:5173") == "http://localhost:5173"
    assert h("https://evil.example.com") is None
    assert h("https://vercel.app.evil.com") is None


def test_ip_rate_limit_returns_429_with_cors_headers(monkeypatch):
    monkeypatch.setattr(main, "IP_LIMIT_PER_MIN", 3)
    monkeypatch.setattr(main, "TD_KEY", "")
    monkeypatch.setattr(main, "_yf_search", lambda q: [])
    hdr = {"Origin": "http://localhost:5173"}
    codes = [client.get(f"/api/search?q=a{i}", headers=hdr).status_code for i in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    r = client.get("/api/search?q=zzz", headers=hdr)
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert client.get("/api/health").status_code == 200  # health exempt


def test_health_reports_budget(monkeypatch):
    d = client.get("/api/health").json()
    assert d["twelveDataConfigured"] is True and d["twelveDataCreditsLeft"] == 8
    assert d["providers"] == ["twelvedata", "yahoo"]


# ---------------------------------------------------------------- yahoo "empty" disambiguation
def _empty_ticker(monkeypatch):
    class T:
        fast_info = {}

        def history(self, **kw):
            return pd.DataFrame()

    monkeypatch.setattr(main, "_ticker", lambda s: T())


@pytest.mark.parametrize("exists,expected", [(False, main.NotFound), (None, main.Upstream), (True, main.Upstream)])
def test_yahoo_empty_quote_only_not_found_when_confirmed_missing(monkeypatch, exists, expected):
    _empty_ticker(monkeypatch)
    monkeypatch.setattr(main, "_yf_symbol_exists", lambda s: exists)
    with pytest.raises(main.ProviderError) as e:
        main._yf_quote("AAPL")
    assert type(e.value) is expected
    with pytest.raises(main.ProviderError) as e2:
        main._yf_history("AAPL", "1M")
    assert type(e2.value) is expected


def test_outage_reports_502_not_bad_ticker(monkeypatch):
    monkeypatch.setattr(main, "TD_KEY", "")
    _empty_ticker(monkeypatch)
    monkeypatch.setattr(main, "_yf_symbol_exists", lambda s: None)  # can't reach Yahoo at all
    assert client.get("/api/stock/AAPL").status_code == 502
    monkeypatch.setattr(main, "_yf_symbol_exists", lambda s: False)  # Yahoo reachable, symbol unknown
    main._cache.clear()
    assert client.get("/api/stock/ASDFGH").status_code == 404


def test_yf_symbol_exists_parsing(monkeypatch):
    class R:
        status_code = 200

        def json(self):
            return {"quotes": [{"symbol": "aapl"}, {"symbol": "AAPLX"}]}

    monkeypatch.setattr(main._yf_session, "get", lambda *a, **k: R())
    assert main._yf_symbol_exists("AAPL") is True
    assert main._yf_symbol_exists("AAP") is False

    def boom(*a, **k):
        raise RuntimeError("dns")

    monkeypatch.setattr(main._yf_session, "get", boom)
    assert main._yf_symbol_exists("AAPL") is None


def test_yfinance_fast_info_keys_still_exist():
    """_fi() swallows errors, so a renamed key would silently turn every price into None."""
    import yfinance as yf

    valid = set(yf.Ticker("AAPL").fast_info._keys)  # exactly what FastInfo.__getitem__ validates against
    for k in ("last_price", "previous_close", "currency", "exchange", "day_low", "day_high",
              "year_low", "year_high", "last_volume", "three_month_average_volume", "market_cap"):
        assert k in valid, f"yfinance renamed fast_info key {k!r}"

