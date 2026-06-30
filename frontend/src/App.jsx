import { useEffect, useState, useCallback } from "react";
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

// Which fundamentals are ratios (need *100) vs big numbers vs plain values.
const RATIO_KEYS = new Set(["Dividend Yield", "Profit Margin", "ROE"]);
const BIG_KEYS = new Set(["Market Cap", "Revenue", "Volume", "Avg Volume"]);

function readLocal(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

export default function App() {
  const [symbol, setSymbol] = useState(null);
  const [range, setRange] = useState("1M");
  const [stock, setStock] = useState(null);
  const [candles, setCandles] = useState([]);
  const [news, setNews] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // ---- Theme ----
  const [theme, setTheme] = useState(() => readLocal("st_theme", "dark"));
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("st_theme", JSON.stringify(theme));
  }, [theme]);

  // ---- Watchlist ----
  const [watchlist, setWatchlist] = useState(() => readLocal("st_watchlist", []));
  const [watchlistData, setWatchlistData] = useState({});
  useEffect(() => {
    localStorage.setItem("st_watchlist", JSON.stringify(watchlist));
    if (watchlist.length === 0) {
      setWatchlistData({});
      return;
    }
    let cancelled = false;
    (async () => {
      const entries = await Promise.all(
        watchlist.map(async (s) => {
          try {
            const r = await fetch(`${API_BASE}/api/stock/${s}`).then((res) => (res.ok ? res.json() : null));
            return [s, r];
          } catch {
            return [s, null];
          }
        })
      );
      if (!cancelled) setWatchlistData(Object.fromEntries(entries));
    })();
    return () => {
      cancelled = true;
    };
  }, [watchlist]);
  const toggleWatch = (sym) => {
    setWatchlist((prev) => (prev.includes(sym) ? prev.filter((s) => s !== sym) : [...prev, sym]));
  };

  // ---- Recent searches ----
  const [recents, setRecents] = useState(() => readLocal("st_recent", []));
  useEffect(() => {
    localStorage.setItem("st_recent", JSON.stringify(recents));
  }, [recents]);
  const addRecent = (sym) => {
    setRecents((prev) => [sym, ...prev.filter((s) => s !== sym)].slice(0, MAX_RECENTS));
  };

  // ---- Compare mode ----
  const [compareMode, setCompareMode] = useState(false);
  const [compareSymbols, setCompareSymbols] = useState([]);
  const [compareData, setCompareData] = useState({});
  useEffect(() => {
    if (!compareMode || compareSymbols.length === 0) {
      setCompareData({});
      return;
    }
    let cancelled = false;
    (async () => {
      const entries = await Promise.all(
        compareSymbols.map(async (s) => {
          try {
            const r = await fetch(`${API_BASE}/api/history/${s}?range=${range}`).then((res) => res.json());
            return [s, r.candles || []];
          } catch {
            return [s, []];
          }
        })
      );
      if (!cancelled) setCompareData(Object.fromEntries(entries));
    })();
    return () => {
      cancelled = true;
    };
  }, [compareMode, compareSymbols, range]);

  const addCompare = (sym) => {
    sym = sym.trim().toUpperCase();
    if (!sym || sym === symbol || compareSymbols.includes(sym)) return;
    if (compareSymbols.length >= MAX_COMPARE - 1) return;
    setCompareSymbols((prev) => [...prev, sym]);
  };
  const removeCompare = (sym) => setCompareSymbols((prev) => prev.filter((s) => s !== sym));

  // ---- Load main stock data ----
  const load = useCallback(async (sym, rng) => {
    setLoading(true);
    setError(null);
    try {
      const [s, h, n] = await Promise.all([
        fetch(`${API_BASE}/api/stock/${sym}`).then((r) => (r.ok ? r.json() : Promise.reject(r))),
        fetch(`${API_BASE}/api/history/${sym}?range=${rng}`).then((r) => (r.ok ? r.json() : Promise.reject(r))),
        fetch(`${API_BASE}/api/news/${sym}`).then((r) => (r.ok ? r.json() : { news: [] })),
      ]);
      setStock(s);
      setCandles(h.candles);
      setNews(n.news);
      addRecent(sym);
    } catch (e) {
      const detail = e.json ? (await e.json().catch(() => null))?.detail : null;
      setError(detail || `Couldn't load "${sym}". Check the ticker symbol.`);
      setStock(null);
      setCandles([]);
      setNews([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!symbol) return;
    load(symbol, range);
  }, [symbol, range, load]);

  const selectSymbol = (sym) => {
    setCompareMode(false);
    setCompareSymbols([]);
    setSymbol(sym);
  };

  const up = stock && stock.change >= 0;
  const accent = up ? "text-term-up" : "text-term-down";
  const isWatched = stock && watchlist.includes(stock.symbol);

  const compareSeries = [
    { symbol, candles },
    ...compareSymbols.map((s) => ({ symbol: s, candles: compareData[s] || [] })),
  ];

  return (
    <div className="min-h-screen bg-term-bg text-current">
      {/* ---- Top bar ---- */}
      <header className="sticky top-0 z-10 border-b border-term-border bg-term-bg/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-4 px-6 py-3">
          <button
            onClick={() => setSymbol(null)}
            className="flex items-center gap-2 text-lg font-black uppercase tracking-tight transition hover:opacity-80"
          >
            Bourse<span className="text-term-accent">.</span>
          </button>
          <button
            onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
            className="ml-2 rounded-lg border border-term-border px-3 py-1.5 text-xs text-term-muted transition hover:text-current"
            title="Toggle theme"
          >
            {theme === "dark" ? "☀ Light" : "☾ Dark"}
          </button>
          {symbol && (
            <div className="ml-auto">
              <SearchBox onSelect={selectSymbol} recents={recents} onClearRecents={() => setRecents([])} />
            </div>
          )}
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-6">
        {/* ---- Watchlist strip (shown on both the homepage and the dashboard) ---- */}
        {watchlist.length > 0 && (
          <div className="mb-6 flex gap-3 overflow-x-auto pb-1">
            {watchlist.map((s) => {
              const w = watchlistData[s];
              const wUp = w && w.change >= 0;
              return (
                <button
                  key={s}
                  onClick={() => selectSymbol(s)}
                  className={`flex shrink-0 flex-col items-start rounded-xl border-2 px-4 py-2 text-left transition ${
                    s === symbol
                      ? "border-term-accent bg-term-accent/10"
                      : "border-term-border bg-term-panel hover:border-term-accent/50"
                  }`}
                >
                  <span className="font-mono text-sm font-bold">{s}</span>
                  {w ? (
                    <span className={`font-mono text-xs ${wUp ? "text-term-up" : "text-term-down"}`}>
                      {money(w.price, w.currency)} ({percent(w.changePct)})
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
              <div className="rounded-xl border border-term-down/40 bg-term-down/10 px-5 py-4 text-term-down">
                {error}
              </div>
            )}

            {loading && !stock && (
              <div className="py-32 text-center font-mono text-term-muted">Loading {symbol}…</div>
            )}

            {stock && (
              <div className="space-y-6">
                {/* ---- Header: name, price, change ---- */}
                <section className="flex flex-wrap items-end justify-between gap-4 rounded-xl border border-term-border bg-term-panel p-6">
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
                  <h1 className="font-mono text-2xl font-bold">{stock.symbol}</h1>
                  <span className="rounded-md bg-term-bg px-2 py-0.5 text-xs text-term-muted">
                    {stock.exchange}
                  </span>
                </div>
                <p className="mt-1 text-term-muted">{stock.name}</p>
                {stock.sector && (
                  <p className="mt-1 text-xs text-term-muted">
                    {stock.sector} · {stock.industry}
                  </p>
                )}
              </div>
              <div className="text-right">
                <div className="font-mono text-4xl font-black tabular-nums">
                  {money(stock.price, stock.currency)}
                </div>
                <div className={`mt-1 font-mono text-lg font-bold tabular-nums ${accent}`}>
                  {up ? "▲" : "▼"} {money(Math.abs(stock.change), stock.currency)} ({percent(stock.changePct)})
                </div>
              </div>
            </section>

            {/* ---- Chart with range selector + compare mode ---- */}
            <section className="rounded-xl border border-term-border bg-term-panel p-6">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <h2 className="font-mono text-sm uppercase tracking-wider text-term-muted">
                  {compareMode ? "Compare" : "Price"}
                </h2>
                <div className="flex flex-wrap items-center gap-3">
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
                  <div className="flex gap-1 rounded-lg bg-term-bg p-1">
                    {RANGES.map((r) => (
                      <button
                        key={r}
                        onClick={() => setRange(r)}
                        className={`rounded-md px-3 py-1 font-mono text-xs transition ${
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
                      className="flex items-center gap-1.5 rounded-md px-2 py-1 font-mono text-xs font-semibold text-white"
                      style={{ backgroundColor: COMPARE_COLORS[(i + 1) % COMPARE_COLORS.length] }}
                    >
                      {s}
                      <button onClick={() => removeCompare(s)} className="hover:opacity-70">
                        ✕
                      </button>
                    </span>
                  ))}
                  {compareSymbols.length < MAX_COMPARE - 1 && (
                    <CompareTickerInput onAdd={addCompare} exclude={[symbol, ...compareSymbols]} />
                  )}
                </div>
              )}

              {compareMode ? (
                compareSeries.some((s) => s.candles.length > 0) ? (
                  <CompareChart series={compareSeries} theme={theme} />
                ) : (
                  <div className="flex h-[420px] items-center justify-center text-term-muted">
                    Add a ticker above to compare
                  </div>
                )
              ) : candles.length > 0 ? (
                <PriceChart candles={candles} theme={theme} />
              ) : (
                <div className="flex h-[420px] items-center justify-center text-term-muted">
                  No chart data
                </div>
              )}
            </section>

            {/* ---- Fundamentals + Analyst ---- */}
            <div className="grid gap-6 lg:grid-cols-3">
              <section className="lg:col-span-2 rounded-xl border border-term-border bg-term-panel p-6">
                <h2 className="mb-4 font-mono text-sm uppercase tracking-wider text-term-muted">
                  Key Statistics
                </h2>
                <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl bg-term-border sm:grid-cols-3">
                  {Object.entries(stock.fundamentals).map(([label, value]) => (
                    <div key={label} className="bg-term-panel p-4">
                      <div className="text-xs text-term-muted">{label}</div>
                      <div className="mt-1 font-mono text-lg font-semibold">
                        {RATIO_KEYS.has(label)
                          ? ratioPercent(value)
                          : BIG_KEYS.has(label)
                          ? compact(value, BIG_KEYS.has(label) && label !== "Volume" && label !== "Avg Volume" ? stock.currency : "")
                          : plain(value)}
                      </div>
                    </div>
                  ))}
                </div>
              </section>

              <AnalystPanel analyst={stock.analyst} price={stock.price} currency={stock.currency} />
            </div>

            {/* ---- News + Summary ---- */}
            <div className="grid gap-6 lg:grid-cols-3">
              <section className="lg:col-span-2 rounded-xl border border-term-border bg-term-panel p-6">
                <h2 className="mb-4 font-mono text-sm uppercase tracking-wider text-term-muted">
                  Latest News
                </h2>
                {news.length === 0 && <p className="text-term-muted">No recent news.</p>}
                <ul className="space-y-3">
                  {news.map((n, i) => (
                    <li key={i}>
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
                          {n.publisher && (
                            <span className="text-xs text-term-muted">{n.publisher}</span>
                          )}
                        </span>
                      </a>
                    </li>
                  ))}
                </ul>
              </section>

              <section className="rounded-xl border border-term-border bg-term-panel p-6">
                <h2 className="mb-4 font-mono text-sm uppercase tracking-wider text-term-muted">
                  About
                </h2>
                <p className="text-sm leading-relaxed text-term-muted line-clamp-[12]">
                  {stock.summary || "No description available."}
                </p>
                {stock.website && (
                  <a
                    href={stock.website}
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
function AnalystPanel({ analyst, price, currency }) {
  const { targetLow, targetMean, targetHigh, recommendation, numAnalysts } = analyst;
  const hasTargets = targetLow && targetHigh && targetHigh > targetLow;
  const pos = (v) => ((v - targetLow) / (targetHigh - targetLow)) * 100;

  return (
    <section className="rounded-xl border border-term-border bg-term-panel p-6">
      <h2 className="mb-4 font-mono text-sm uppercase tracking-wider text-term-muted">
        Analyst Targets
      </h2>
      {recommendation && (
        <div className="mb-4 inline-block rounded-lg bg-term-accent/15 px-3 py-1 font-mono text-sm font-semibold uppercase text-term-accent">
          {recommendation.replace("_", " ")}
          {numAnalysts ? ` · ${numAnalysts} analysts` : ""}
        </div>
      )}
      {hasTargets ? (
        <div className="mt-8">
          <div className="relative h-1.5 rounded-full bg-term-border">
            <div
              className="absolute -top-1 h-3.5 w-3.5 -translate-x-1/2 rounded-full border-2 border-term-panel bg-term-accent"
              style={{ left: `${pos(targetMean)}%` }}
              title="Mean target"
            />
            <div
              className="absolute -top-0.5 h-2.5 w-2.5 -translate-x-1/2 rounded-full bg-current"
              style={{ left: `${Math.max(0, Math.min(100, pos(price)))}%` }}
              title="Current price"
            />
          </div>
          <div className="mt-3 flex justify-between font-mono text-xs">
            <div>
              <div className="text-term-muted">Low</div>
              <div className="text-term-down">{money(targetLow, currency)}</div>
            </div>
            <div className="text-center">
              <div className="text-term-muted">Mean</div>
              <div className="text-term-accent">{money(targetMean, currency)}</div>
            </div>
            <div className="text-right">
              <div className="text-term-muted">High</div>
              <div className="text-term-up">{money(targetHigh, currency)}</div>
            </div>
          </div>
          <p className="mt-4 text-xs text-term-muted">
            ● Marker = current price ({money(price, currency)})
          </p>
        </div>
      ) : (
        <p className="text-term-muted">No analyst coverage available.</p>
      )}
    </section>
  );
}
