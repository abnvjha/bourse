import { useEffect, useRef, useState } from "react";
import { API_BASE } from "../api.js";

/**
 * Compact autocomplete input used to add tickers in Compare mode.
 * Mirrors SearchBox's debounced /api/search lookup, but smaller and
 * scoped to "add this symbol to the comparison" instead of navigating.
 */
export default function CompareTickerInput({ onAdd, exclude = [] }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [loading, setLoading] = useState(false);
  const boxRef = useRef(null);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 1) {
      setResults([]);
      return;
    }
    setLoading(true);
    const timer = setTimeout(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/search?q=${encodeURIComponent(q)}`).then((r) => r.json());
        const filtered = (res.results || []).filter((r) => !exclude.includes(r.symbol));
        setResults(filtered);
        setActive(0);
        setOpen(true);
      } catch {
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, 250);
    return () => clearTimeout(timer);
  }, [query, exclude]);

  useEffect(() => {
    const onClick = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const choose = (symbol) => {
    onAdd(symbol);
    setQuery("");
    setResults([]);
    setOpen(false);
  };

  const onKeyDown = (e) => {
    if (!open || results.length === 0) {
      if (e.key === "Enter" && query.trim()) choose(query.trim().toUpperCase());
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => (a + 1) % results.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => (a - 1 + results.length) % results.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(results[active].symbol);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div ref={boxRef} className="relative">
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={onKeyDown}
        onFocus={() => results.length && setOpen(true)}
        placeholder="Add ticker…"
        className="w-28 rounded-md border border-term-border bg-term-bg px-2 py-1 font-mono text-xs outline-none focus:border-term-accent"
      />
      {open && loading && (
        <div className="absolute left-0 z-20 mt-2 w-64 rounded-xl border border-term-border bg-term-panel px-3 py-2 text-xs text-term-muted shadow-2xl shadow-black/50">
          Searching…
        </div>
      )}
      {open && !loading && results.length > 0 && (
        <ul className="absolute left-0 z-20 mt-2 max-h-64 w-64 overflow-auto rounded-xl border border-term-border bg-term-panel py-1 shadow-2xl shadow-black/50">
          {results.map((r, i) => (
            <li key={r.symbol}>
              <button
                onMouseEnter={() => setActive(i)}
                onClick={() => choose(r.symbol)}
                className={`flex w-full items-center justify-between gap-3 px-3 py-2 text-left transition ${
                  i === active ? "bg-term-accent/15" : "hover:bg-term-bg"
                }`}
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm">{r.name}</span>
                  <span className="text-xs text-term-muted">{r.exchange}</span>
                </span>
                <span className="shrink-0 rounded-md bg-term-bg px-2 py-1 font-mono text-xs text-term-accent">
                  {r.symbol}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
