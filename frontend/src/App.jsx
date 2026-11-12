import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import PriceChart from "./components/PriceChart.jsx";
import CompareChart from "./components/CompareChart.jsx";
import SearchBox from "./components/SearchBox.jsx";
import CompareTickerInput from "./components/CompareTickerInput.jsx";
import Landing from "./components/Landing.jsx";
import { API_BASE } from "./api.js";
import { money, compact, percent, ratioPercent, plain } from "./format.js";

const RANGES = ["1D", "5D", "1M", "6M", "1Y", "5Y"];
const COMPARE_COLORS = ["#06c167", "#3b82f6", "#f59e0b", "#a855f7", "#ec4899"];
const MAX_COMPARE = 4; // base symbol + 3 others
const MAX_RECENTS = 8;

// Render's free tier can take ~30-50s to wake from sleep; don't give up before it does.
const REQUEST_TIMEOUT_MS = 60000;
const SLOW_HINT_MS = 4000;
const WATCHLIST_REFRESH_MS = 90000;

// Which fundamentals are ratios (need *100), big numbers, or plain money values.
const RATIO_KEYS = new Set(["Dividend Yield", "Profit Margin", "ROE"]);
const BIG_KEYS = new Set(["Market Cap", "Revenue", "Volume", "Avg Volume"]);
const CURRENCY_BIG_KEYS = new Set(["Market Cap", "Revenue"]);
const MONEY_KEYS = new Set(["Prev Close"]);

const enc = encodeURIComponent;

function readLocal(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function writeLocal(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage blocked (private mode, quota) - the app still works, it just won't persist */
  }
}

/** GET JSON from the backend with a generous timeout and caller-controlled cancellation. */
async function getJson(path, signal) {
  const ctrl = new AbortController();
  const onAbort = () => ctrl.abort();
  signal?.addEventListener("abort", onAbort);
  const timer = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(`${API_BASE}${path}`, { signal: ctrl.signal });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
    return data;
  } catch (e) {
    if (e?.name === "AbortError" && !signal?.aborted) {
      throw new Error("The data service took too long to respond. Try again.");
    }
    throw e;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
  }
}

const safeMoney = (v, cur) => (v == null ? "—" : money(v, cur));
const safePercent = (v) => (v == null ? "—" : percent(v));

function formatStat(label, value, currency) {
  if (value == null) return "—";
  if (RATIO_KEYS.has(label)) return ratioPercent(value);
  if (BIG_KEYS.has(label)) return compact(value, CURRENCY_BIG_KEYS.has(label) ? currency : "");
  if (MONEY_KEYS.has(label)) return money(value, currency);
  return plain(value);
}

export default function App() {
  const [symbol, setSymbol] = useState(null);
  const [range, setRange] = useState("1M");
  const [stock, setStock] = useState(null);
  const [profile, setProfile] = useState(null);
  const [candles, setCandles] = useState([]);
  const [news, setNews] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [chartLoading, setChartLoading] = useState(false);
  const [chartError, setChartError] = useState(null);
  const [newsLoading, setNewsLoading] = useState(false);
  const [profileLoading, setProfileLoading] = useState(false);
  const [slow, setSlow] = useState(false);
  const [reloadTick, setReloadTick] = useState(0); // bump to retry the current symbol

  // ---- Wake the backend as soon as the page opens (Render free tier sleeps) ----
  useEffect(() => {
    fetch(`${API_BASE}/api/health`).catch(() => {});
  }, []);

  // ---- Theme ----
  const [theme, setTheme] = useState(() => readLocal("st_theme", "dark"));
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    writeLocal("st_theme", theme);
  }, [theme]);

  useEffect(() => {
    document.title = symbol ? `${symbol} · Bourse` : "Bourse.";
  }, [symbol]);

  // ---- Watchlist ----
  const [watchlist, setWatchlist] = useState(() => readLocal("st_watchlist", []));
  const [watchlistData, setWatchlistData] = useState({}); // sym -> quote | null (failed)
  const watchlistRef = useRef(watchlist);
  const requestedRef = useRef(new Set());

  const refreshWatchlist = useCallback(async (symbols) => {
    await Promise.all(
      symbols.map(async (s) => {
        try {
          const q = await getJson(`/api/stock/${enc(s)}`);
          setWatchlistData((prev) => ({ ...prev, [s]: q }));
        } catch {
          // keep the last good quote if we have one; otherwise mark as unavailable
          setWatchlistData((prev) => (prev[s] ? prev : { ...prev, [s]: null }));
        }
      })
    );
  }, []);

  useEffect(() => {
    watchlistRef.current = watchlist;
    writeLocal("st_watchlist", watchlist);
    // forget anything that was un-starred so re-adding it fetches again
    for (const s of [...requestedRef.current]) {
      if (!watchlist.includes(s)) requestedRef.current.delete(s);
    }
    setWatchlistData((prev) => {
      const kept = Object.entries(prev).filter(([k]) => watchlist.includes(k));
      return kept.length === Object.keys(prev).length ? prev : Object.fromEntries(kept);
    });
    // only fetch tickers we have not requested yet - starring one stock must not refetch them all
    const fresh = watchlist.filter((s) => !requestedRef.current.has(s));
    fresh.forEach((s) => requestedRef.current.add(s));
    if (fresh.length) refreshWatchlist(fresh);
  }, [watchlist, refreshWatchlist]);

  // keep chips live while the tab is visible (backend cache keeps this cheap)
  useEffect(() => {
    if (watchlist.length === 0) return undefined;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") refreshWatchlist(watchlist);
    }, WATCHLIST_REFRESH_MS);
    return () => clearInterval(id);
  }, [watchlist, refreshWatchlist]);

  const toggleWatch = (sym) => {
    setWatchlist((prev) => (prev.includes(sym) ? prev.filter((s) => s !== sym) : [...prev, sym]));
  };

  // ---- Recent searches ----
  const [recents, setRecents] = useState(() => readLocal("st_recent", []));
  useEffect(() => {
    writeLocal("st_recent", recents);
  }, [recents]);
  const addRecent = useCallback((sym) => {
    setRecents((prev) => [sym, ...prev.filter((s) => s !== sym)].slice(0, MAX_RECENTS));
  }, []);

  // ---- Compare mode ----
  const [compareMode, setCompareMode] = useState(false);
  const [compareSymbols, setCompareSymbols] = useState([]);
  const [compareData, setCompareData] = useState({}); // sym -> { candles, error? }
  useEffect(() => {
    if (!compareMode || compareSymbols.length === 0) {
      setCompareData({});
      return undefined;
    }
    const controller = new AbortController();
    (async () => {
      const entries = await Promise.all(
        compareSymbols.map(async (s) => {
          try {
            const r = await getJson(`/api/history/${enc(s)}?range=${enc(range)}`, controller.signal);
            return [s, { candles: r.candles || [] }];
          } catch (e) {
            return [s, { candles: [], error: e.message || "No data" }];
          }
        })
      );
      if (!controller.signal.aborted) setCompareData(Object.fromEntries(entries));
    })();
    return () => controller.abort();
  }, [compareMode, compareSymbols, range]);

  const addCompare = (sym) => {
    sym = sym.trim().toUpperCase();
    if (!sym || sym === symbol || compareSymbols.includes(sym)) return;
    if (compareSymbols.length >= MAX_COMPARE - 1) return;
    setCompareSymbols((prev) => [...prev, sym]);
  };
  const removeCompare = (sym) => setCompareSymbols((prev) => prev.filter((s) => s !== sym));

  // ---- Load quote + news + profile (once per symbol, NOT per chart range) ----
  useEffect(() => {
    if (!symbol) return undefined;
    const controller = new AbortController();
    const { signal } = controller;
    const sym = symbol;

    setLoading(true);
    setError(null);
    setNewsLoading(true);
    setProfileLoading(true);

    getJson(`/api/stock/${enc(sym)}`, signal)
      .then((data) => {
        if (signal.aborted) return;
        setStock(data);
        setError(null);
        setLoading(false);
        addRecent(sym);
        if (watchlistRef.current.includes(data.symbol)) {
          setWatchlistData((prev) => ({ ...prev, [data.symbol]: data }));
        }
      })
      .catch((e) => {
        if (signal.aborted) return;
        setError(e.message || `Couldn't load "${sym}". Check the ticker symbol.`);
        setStock(null);
        setLoading(false);
      });

    getJson(`/api/news/${enc(sym)}`, signal)
      .then((data) => {
        if (!signal.aborted) setNews(Array.isArray(data.news) ? data.news : []);
      })
      .catch(() => {
        if (!signal.aborted) setNews([]);
      })
      .finally(() => {
        if (!signal.aborted) setNewsLoading(false);
      });

    getJson(`/api/profile/${enc(sym)}`, signal)
      .then((data) => {
        if (!signal.aborted) setProfile(data && data.available ? data : null);
      })
      .catch(() => {
        if (!signal.aborted) setProfile(null);
      })
      .finally(() => {
        if (!signal.aborted) setProfileLoading(false);
      });

    return () => controller.abort();
  }, [symbol, reloadTick, addRecent]);

  // ---- Load chart history (re-runs on range change only) ----
  useEffect(() => {
    if (!symbol) return undefined;
    const controller = new AbortController();
    const { signal } = controller;

    setChartLoading(true);
    setChartError(null);

    getJson(`/api/history/${enc(symbol)}?range=${enc(range)}`, signal)
      .then((data) => {
        if (signal.aborted) return;
        setCandles(Array.isArray(data.candles) ? data.candles : []);
      })
      .catch((e) => {
        if (signal.aborted) return;
        setCandles([]);
        setChartError(e.message || "Chart data is temporarily unavailable.");
      })
      .finally(() => {
        if (!signal.aborted) setChartLoading(false);
      });

    return () => controller.abort();
  }, [symbol, range, reloadTick]);

  // ---- "Waking up the server" hint if the first load drags ----
  useEffect(() => {
    if (!(loading && !stock)) {
      setSlow(false);
      return undefined;
    }
    const id = setTimeout(() => setSlow(true), SLOW_HINT_MS);
    return () => clearTimeout(id);
  }, [loading, stock]);

  const selectSymbol = (sym) => {
    setCompareMode(false);
    setCompareSymbols([]);
    if (sym === symbol) {
      // Same ticker again: nothing to reset. If the last attempt failed, treat it as a retry.
      if (error || !stock) setReloadTick((t) => t + 1);
      return;
    }
    setStock(null);
    setProfile(null);
    setCandles([]);
    setNews([]);
    setError(null);
    setChartError(null);
    setSymbol(sym);
  };

  const up = !!stock && (stock.change ?? 0) >= 0;
  const accent = up ? "text-term-up" : "text-term-down";
  const isWatched = !!stock && watchlist.includes(stock.symbol);

  const displayName =
    (stock && stock.name && stock.name !== stock.symbol ? stock.name : null) ||
    profile?.name ||
    stock?.name;
  const sector = profile?.sector || stock?.sector;
  const industry = profile?.industry || stock?.industry;
  const summary = profile?.summary || stock?.summary;
  const website = profile?.website || stock?.website;

  // quote-derived stats first, enriched by profile data when it arrives
  const fundamentals = useMemo(() => {
    const base = stock?.fundamentals || {};
    const extra = profile?.fundamentals || {};
    const out = {};
    for (const k of Object.keys(base)) out[k] = extra[k] ?? base[k];
    return out;
  }, [stock, profile]);

  const analyst = useMemo(
    () => ({ ...(stock?.analyst || {}), ...(profile?.analyst || {}) }),
    [stock, profile]
  );

  const compareSeries = [
    { symbol, candles },
    ...compareSymbols.map((s) => ({ symbol: s, candles: compareData[s]?.candles || [] })),
  ];
  const compareFailures = compareSymbols.filter((s) => compareData[s]?.error);

  return (
    <div className="min-h-screen bg-term-bg text-current">
      {/* ---- Top bar ---- */}
      <header className="sticky top-0 z-10 border-b border-term-border bg-term-bg/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-3 sm:gap-4 sm:px-6">
          <button
            onClick={() => setSymbol(null)}
            className="flex items-center gap-2 text-base font-black uppercase tracking-tight transition hover:opacity-80 sm:text-lg"
          >
            Bourse<span className="text-term-accent">.</span>
          </button>
          <button
            onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
            className="rounded-lg border border-term-border px-2.5 py-1.5 text-xs text-term-muted transition hover:text-current sm:ml-2 sm:px-3"
            title="Toggle theme"
          >
            {theme === "dark" ? "☀ Light" : "☾ Dark"}
          </button>
          {symbol && (
            <div className="order-last w-full sm:order-none sm:ml-auto sm:w-auto">
              <SearchBox onSelect={selectSymbol} recents={recents} onClearRecents={() => setRecents([])} />
            </div>
          )}
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-5 sm:px-6 sm:py-6">
        {/* ---- Watchlist strip (shown on both the homepage and the dashboard) ---- */}
        {watchlist.length > 0 && (
          <div className="mb-6 flex gap-3 overflow-x-auto pb-1">
            {watchlist.map((s) => {
              const w = watchlistData[s];
              const wUp = !!w && (w.change ?? 0) >= 0;
              return (
                <button
                  key={s}
                  onClick={() => selectSymbol(s)}
                  className={`flex shrink-0 flex-col items-start rounded-xl border-2 px-3 py-2 text-left transition sm:px-4 ${
                    s === symbol
                      ? "border-term-accent bg-term-accent/10"
                      : "border-term-border bg-term-panel hover:border-term-accent/50"
                  }`}
                >
                  <span className="font-mono text-sm font-bold">{s}</span>
                  {w ? (
                    <span className={`font-mono text-xs ${wUp ? "text-term-up" : "text-term-down"}`}>
                      {safeMoney(w.price, w.currency)} ({safePercent(w.changePct)})
                    </span>
                  ) : w === null ? (
                    <span className="text-xs text-term-muted" title="Quote unavailable right now">
                      unavailable
                    </span>
                  ) : (
                    <span className="text-xs text-term-muted">…</span>
                  )}
                </button>
              );
            })}
          </div>
        )}

        {!symbol && (
          <Landing onSelect={selectSymbol} recents={recents} onClearRecents={() => setRecents([])} />
        )}

        {symbol && (
          <>
            {error && (
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-term-down/40 bg-term-down/10 px-5 py-4 text-term-down">
                <span>{error}</span>
                <button
                  onClick={() => setReloadTick((t) => t + 1)}
                  className="rounded-lg border border-term-down/40 px-3 py-1 font-mono text-xs transition hover:bg-term-down/10"
                >
                  Retry
                </button>
              </div>
            )}

            {loading && !stock && (
              <div className="py-32 text-center font-mono text-term-muted">
                <div>Loading {symbol}…</div>
                {slow && (
                  <div className="mt-3 text-xs">
                    Waking up the data server. The first request after a quiet spell can take up to a minute.
                  </div>
                )}
              </div>
            )}

            {stock && (
              <div className="space-y-6">
                {/* ---- Header: name, price, change ---- */}
                <section className="flex flex-col gap-4 rounded-xl border border-term-border bg-term-panel p-4 sm:flex-row sm:flex-wrap sm:items-end sm:justify-between sm:p-6">
                  <div>
                    <div className="flex items-center gap-3">
                      <button
                        onClick={() => toggleWatch(stock.symbol)}
                        className={`text-xl leading-none transition ${
                          isWatched ? "text-term-accent" : "text-term-muted hover:text-current"
                        }`}
                        title={isWatched ? "Remove from watchlist" : "Add to watchlist"}
                      >
                        {isWatched ? "★" : "☆"}
                      </button>
                      <h1 className="font-mono text-xl font-bold sm:text-2xl">{stock.symbol}</h1>
                      {stock.exchange && (
                        <span className="rounded-md bg-term-bg px-2 py-0.5 text-xs text-term-muted">
                          {stock.exchange}
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-term-muted">{displayName}</p>
                    {sector && (
                      <p className="mt-1 text-xs text-term-muted">
                        {sector}
                        {industry ? ` · ${industry}` : ""}
                      </p>
                    )}
                  </div>
                  <div className="text-left sm:text-right">
                    <div className="font-mono text-3xl font-black tabular-nums sm:text-4xl">
                      {safeMoney(stock.price, stock.currency)}
                    </div>
                    <div className={`mt-1 font-mono text-base font-bold tabular-nums sm:text-lg ${accent}`}>
                      {up ? "▲" : "▼"} {safeMoney(Math.abs(stock.change ?? 0), stock.currency)} (
                      {safePercent(stock.changePct)})
                    </div>
                    {stock.stale && (
                      <div
                        className="mt-1 text-xs text-term-muted"
                        title="The data provider is slow or unavailable, showing the last known quote"
                      >
                        delayed quote
                      </div>
                    )}
                  </div>
                </section>

                {/* ---- Chart with range selector + compare mode ---- */}
                <section className="rounded-xl border border-term-border bg-term-panel p-4 sm:p-6">
                  <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                    <h2 className="font-mono text-sm uppercase tracking-wider text-term-muted">
                      {compareMode ? "Compare" : "Price"}
                    </h2>
                    <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:gap-3">
                      <button
                        onClick={() => setCompareMode((v) => !v)}
                        className={`rounded-lg border px-3 py-1.5 font-mono text-xs transition ${
                          compareMode
                            ? "border-term-accent bg-term-accent text-white"
                            : "border-term-border text-term-muted hover:text-current"
                        }`}
                      >
                        Compare
                      </button>
                      <div className="flex flex-1 gap-1 overflow-x-auto rounded-lg bg-term-bg p-1 sm:flex-none">
                        {RANGES.map((r) => (
                          <button
                            key={r}
                            onClick={() => setRange(r)}
                            className={`shrink-0 rounded-md px-3 py-1 font-mono text-xs transition ${
                              range === r
                                ? "bg-term-accent text-white"
                                : "text-term-muted hover:text-current"
                            }`}
                          >
                            {r}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>

                  {compareMode && (
                    <div className="mb-4 flex flex-wrap items-center gap-2">
                      <span
                        className="rounded-md px-2 py-1 font-mono text-xs font-semibold text-white"
                        style={{ backgroundColor: COMPARE_COLORS[0] }}
                      >
                        {symbol}
                      </span>
                      {compareSymbols.map((s, i) => (
                        <span
                          key={s}
                          className={`flex items-center gap-1.5 rounded-md px-2 py-1 font-mono text-xs font-semibold text-white ${
                            compareData[s]?.error ? "opacity-50" : ""
                          }`}
                          style={{ backgroundColor: COMPARE_COLORS[(i + 1) % COMPARE_COLORS.length] }}
                          title={compareData[s]?.error || undefined}
                        >
                          {s}
                          <button onClick={() => removeCompare(s)} className="hover:opacity-70">
                            ✕
                          </button>
                        </span>
                      ))}
                      {compareSymbols.length < MAX_COMPARE - 1 && (
                        <div className="w-full sm:w-auto">
                          <CompareTickerInput onAdd={addCompare} exclude={[symbol, ...compareSymbols]} />
                        </div>
                      )}
                    </div>
                  )}

                  {compareMode && compareFailures.length > 0 && (
                    <p className="mb-3 text-xs text-term-down">
                      No chart data for {compareFailures.join(", ")}. Remove it or try another ticker.
                    </p>
                  )}

                  {compareMode ? (
                    compareSeries.some((s) => s.candles.length > 0) ? (
                      <CompareChart series={compareSeries} theme={theme} />
                    ) : (
                      <div className="flex h-[280px] items-center justify-center text-term-muted sm:h-[420px]">
                        Add a ticker above to compare
                      </div>
                    )
                  ) : chartLoading ? (
                    <div className="flex h-[280px] items-center justify-center text-term-muted sm:h-[420px]">
                      Loading chart…
                    </div>
                  ) : chartError ? (
                    <div className="flex h-[280px] items-center justify-center px-6 text-center text-term-muted sm:h-[420px]">
                      Chart unavailable: {chartError}
                    </div>
                  ) : candles.length > 0 ? (
                    <PriceChart candles={candles} theme={theme} />
                  ) : (
                    <div className="flex h-[280px] items-center justify-center text-term-muted sm:h-[420px]">
                      No chart data
                    </div>
                  )}
                </section>

                {/* ---- Fundamentals + Analyst ---- */}
                <div className="grid gap-4 sm:gap-6 lg:grid-cols-3">
                  <section className="lg:col-span-2 rounded-xl border border-term-border bg-term-panel p-4 sm:p-6">
                    <h2 className="mb-4 font-mono text-sm uppercase tracking-wider text-term-muted">
                      Key Statistics
                    </h2>
                    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl bg-term-border sm:grid-cols-3">
                      {Object.entries(fundamentals).map(([label, value]) => (
                        <div key={label} className="bg-term-panel p-3 sm:p-4">
                          <div className="text-xs text-term-muted">{label}</div>
                          <div className="mt-1 font-mono text-base font-semibold sm:text-lg">
                            {value == null && profileLoading ? "…" : formatStat(label, value, stock.currency)}
                          </div>
                        </div>
                      ))}
                    </div>
                  </section>

                  <AnalystPanel
                    analyst={analyst}
                    price={stock.price}
                    currency={stock.currency}
                    loading={profileLoading}
                  />
                </div>

                {/* ---- News + Summary ---- */}
                <div className="grid gap-4 sm:gap-6 lg:grid-cols-3">
                  <section className="lg:col-span-2 rounded-xl border border-term-border bg-term-panel p-4 sm:p-6">
                    <h2 className="mb-4 font-mono text-sm uppercase tracking-wider text-term-muted">
                      Latest News
                    </h2>
                    {newsLoading && news.length === 0 && <p className="text-term-muted">Loading news…</p>}
                    {!newsLoading && news.length === 0 && <p className="text-term-muted">No recent news.</p>}
                    <ul className="space-y-3">
                      {news.map((n, i) => (
                        <li key={n.link || i}>
                          <a
                            href={n.link}
                            target="_blank"
                            rel="noreferrer"
                            className="group flex items-start gap-3 rounded-lg p-3 transition hover:bg-term-bg"
                          >
                            <span className="mt-1 text-term-accent">›</span>
                            <span>
                              <span className="block leading-snug group-hover:text-term-accent">
                                {n.title}
                              </span>
                              {n.publisher && <span className="text-xs text-term-muted">{n.publisher}</span>}
                            </span>
                          </a>
                        </li>
                      ))}
                    </ul>
                  </section>

                  <section className="rounded-xl border border-term-border bg-term-panel p-4 sm:p-6">
                    <h2 className="mb-4 font-mono text-sm uppercase tracking-wider text-term-muted">
                      About
                    </h2>
                    <p className="text-sm leading-relaxed text-term-muted line-clamp-[12]">
                      {summary ||
                        (profileLoading ? "Loading company profile…" : "No description available.")}
                    </p>
                    {website && (
                      <a
                        href={website}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-3 inline-block text-sm text-term-accent hover:underline"
                      >
                        Visit website →
                      </a>
                    )}
                  </section>
                </div>
              </div>
            )}
          </>
        )}

        <footer className="mt-10 pb-6 text-center text-xs text-term-muted">
          Made by{" "}
          <a
            href="https://www.linkedin.com/in/abnvjha/"
            target="_blank"
            rel="noreferrer"
            className="font-semibold text-term-accent hover:underline"
          >
            ABNV
          </a>
        </footer>
      </main>
    </div>
  );
}

/** Visual analyst price-target panel: low — current — mean — high on a bar. */
function AnalystPanel({ analyst = {}, price, currency, loading }) {
  const { targetLow, targetMean, targetHigh, recommendation, numAnalysts } = analyst;
  const hasTargets = targetLow != null && targetHigh != null && targetHigh > targetLow;
  const hasRecommendation = !!recommendation && recommendation !== "none";
  const clamp = (v) => Math.max(0, Math.min(100, v));
  const pos = (v) => clamp(((v - targetLow) / (targetHigh - targetLow)) * 100);

  return (
    <section className="rounded-xl border border-term-border bg-term-panel p-4 sm:p-6">
      <h2 className="mb-4 font-mono text-sm uppercase tracking-wider text-term-muted">
        Analyst Targets
      </h2>
      {hasRecommendation && (
        <div className="mb-4 inline-block rounded-lg bg-term-accent/15 px-3 py-1 font-mono text-sm font-semibold uppercase text-term-accent">
          {String(recommendation).replaceAll("_", " ")}
          {numAnalysts ? ` · ${numAnalysts} analysts` : ""}
        </div>
      )}
      {hasTargets ? (
        <div className="mt-8">
          <div className="relative h-1.5 rounded-full bg-term-border">
            {targetMean != null && (
              <div
                className="absolute -top-1 h-3.5 w-3.5 -translate-x-1/2 rounded-full border-2 border-term-panel bg-term-accent"
                style={{ left: `${pos(targetMean)}%` }}
                title="Mean target"
              />
            )}
            {price != null && (
              <div
                className="absolute -top-0.5 h-2.5 w-2.5 -translate-x-1/2 rounded-full bg-current"
                style={{ left: `${pos(price)}%` }}
                title="Current price"
              />
            )}
          </div>
          <div className="mt-3 flex justify-between font-mono text-xs">
            <div>
              <div className="text-term-muted">Low</div>
              <div className="text-term-down">{safeMoney(targetLow, currency)}</div>
            </div>
            <div className="text-center">
              <div className="text-term-muted">Mean</div>
              <div className="text-term-accent">{safeMoney(targetMean, currency)}</div>
            </div>
            <div className="text-right">
              <div className="text-term-muted">High</div>
              <div className="text-term-up">{safeMoney(targetHigh, currency)}</div>
            </div>
          </div>
          <p className="mt-4 text-xs text-term-muted">
            ● Marker = current price ({safeMoney(price, currency)})
          </p>
        </div>
      ) : loading && !hasRecommendation ? (
        <p className="text-term-muted">Loading analyst data…</p>
      ) : (
        !hasRecommendation && <p className="text-term-muted">No analyst coverage available.</p>
      )}
    </section>
  );
}
