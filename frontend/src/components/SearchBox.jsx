import { useEffect, useRef, useState } from "react";
import { API_BASE } from "../api.js";

/**
 * Search box with a live autocomplete dropdown.
 *
 * As the user types a company name (or partial ticker) we query the backend's
 * /api/search endpoint — debounced so we don't fire a request on every keystroke
 * — and show matching stocks. Picking one (click, or arrow keys + Enter) calls
 * onSelect with the chosen symbol.
 *
 * When the box is focused with nothing typed yet, we show recent searches
 * instead (passed in via `recents`), with a Clear button.
 */
export default function SearchBox({ onSelect, recents = [], onClearRecents }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [loading, setLoading] = useState(false);
  const boxRef = useRef(null);

  // Debounced search: wait 250ms after the user stops typing.
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
        setResults(res.results || []);
        setActive(0);
        setOpen(true);
      } catch {
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, 250);
    return () => clearTimeout(timer);
  }, [query]);

  // Close the dropdown when clicking outside the box.
  useEffect(() => {
    const onClick = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const choose = (symbol) => {
    onSelect(symbol);
    setQuery("");
    setResults([]);
    setOpen(false);
  };

  const showingRecents = query.trim().length === 0 && results.length === 0;

  const onKeyDown = (e) => {
    if (showingRecents) {
      if (e.key === "Escape") setOpen(false);
      return;
    }
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
    <div ref={boxRef} className="relative w-72">
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={onKeyDown}
        onFocus={() => setOpen(true)}
        placeholder="Search company or ticker…"
        className="w-full rounded-lg border border-term-border bg-term-panel px-4 py-2 font-mono text-sm outline-none placeholder:text-term-muted focus:border-term-accent"
      />

      {loading && (
        <span className="absolute right-3 top-2.5 text-xs text-term-muted">…</span>
      )}

      {open && loading && query.trim().length > 0 && (
        <div className="absolute right-0 z-20 mt-2 w-80 rounded-xl border border-term-border bg-term-panel px-4 py-3 text-sm text-term-muted shadow-2xl shadow-black/50">
          Searching…
        </div>
      )}

      {open && !loading && !showingRecents && results.length > 0 && (
        <ul className="absolute right-0 z-20 mt-2 max-h-80 w-80 overflow-auto rounded-xl border border-term-border bg-term-panel py-1 shadow-2xl shadow-black/50">
          {results.map((r, i) => (
            <li key={r.symbol}>
              <button
                onMouseEnter={() => setActive(i)}
                onClick={() => choose(r.symbol)}
                className={`flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left transition ${
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

      {open && !loading && query.trim().length > 0 && results.length === 0 && (
        <div className="absolute right-0 z-20 mt-2 w-80 rounded-xl border border-term-border bg-term-panel px-4 py-3 text-sm text-term-muted shadow-2xl shadow-black/50">
          No matches for "{query.trim()}"
        </div>
      )}

      {open && showingRecents && (
        <div className="absolute right-0 z-20 mt-2 w-80 overflow-hidden rounded-xl border border-term-border bg-term-panel shadow-2xl shadow-black/50">
          <div className="flex items-center justify-between px-4 py-2.5">
            <span className="text-xs uppercase tracking-wider text-term-muted">Recent</span>
            {recents.length > 0 && (
              <button
                onClick={() => onClearRecents && onClearRecents()}
                className="text-xs text-term-muted hover:text-term-accent"
              >
                Clear
              </button>
            )}
          </div>
          {recents.length === 0 ? (
            <p className="px-4 pb-3 text-sm text-term-muted">No recent searches yet.</p>
          ) : (
            <ul className="max-h-72 overflow-auto pb-1">
              {recents.map((sym) => (
                <li key={sym}>
                  <button
                    onClick={() => choose(sym)}
                    className="flex w-full items-center justify-between px-4 py-2 text-left text-sm transition hover:bg-term-bg"
                  >
                    <span>{sym}</span>
                    <span className="text-term-muted">↗</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
