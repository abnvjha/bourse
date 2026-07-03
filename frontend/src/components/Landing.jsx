import { useEffect, useState } from "react";
import SearchBox from "./SearchBox.jsx";

const QUICK_PICKS = ["AAPL", "TSLA", "NVDA", "MSFT", "RELIANCE.NS", "INFY.NS"];

/**
 * Home screen shown before any stock is selected. The hero fades out
 * and drifts up slightly as the user scrolls, then gives way to a
 * row of quick-pick tickers to jump into the dashboard.
 */
export default function Landing({ onSelect, recents, onClearRecents }) {
  const [scrollY, setScrollY] = useState(0);

  useEffect(() => {
    const onScroll = () => setScrollY(window.scrollY);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const fade = Math.max(0, 1 - scrollY / 280);
  const shift = Math.min(40, scrollY * 0.15);

  return (
    <div>
      <section
        className="flex min-h-[60vh] flex-col items-center justify-center px-4 text-center transition-opacity sm:px-6"
        style={{ opacity: fade, transform: `translateY(-${shift}px)` }}
      >
        <h1 className="font-display text-4xl font-extrabold tracking-tight sm:text-6xl">
          Bourse<span className="text-term-accent">.</span>
        </h1>
        <p className="font-display mt-4 max-w-md text-base font-semibold text-term-muted sm:text-lg">
          Live prices, candlestick charts, analyst targets, and news — for any stock, anywhere in the
          world.
        </p>
        <p className="font-display mt-3 max-w-lg text-sm font-medium leading-relaxed text-term-muted">
          A personal, Bloomberg-style research terminal: type a company name and get a full dashboard —
          price action, key fundamentals, what Wall Street analysts expect, and the latest headlines —
          covering markets from the US and India to London, Tokyo, and beyond.
        </p>

        <div className="mt-8 flex w-full justify-center px-2 sm:w-auto sm:px-0">
          <SearchBox onSelect={onSelect} recents={recents} onClearRecents={onClearRecents} />
        </div>
      </section>

      <section className="mx-auto max-w-3xl px-4 pb-16 sm:px-6 sm:pb-24">
        <h2 className="mb-4 text-center font-mono text-xs uppercase tracking-wider text-term-muted">
          Popular right now
        </h2>
        <div className="flex flex-wrap justify-center gap-2 sm:gap-3">
          {QUICK_PICKS.map((s) => (
            <button
              key={s}
              onClick={() => onSelect(s)}
              className="rounded-xl border border-term-border bg-term-panel px-4 py-2.5 font-mono text-sm font-semibold transition hover:border-term-accent hover:text-term-accent sm:px-5 sm:py-3"
            >
              {s}
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
