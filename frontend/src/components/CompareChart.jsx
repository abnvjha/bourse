import { useEffect, useRef } from "react";
import { createChart, CrosshairMode, LineStyle } from "lightweight-charts";

const LINE_COLORS = ["#06c167", "#3b82f6", "#f59e0b", "#a855f7", "#ec4899"];

const PALETTE = {
  dark: { text: "#9b9b9b", grid: "#2b2b2b" },
  light: { text: "#6e6e6e", grid: "#e0e0e0" },
};

/**
 * Overlays multiple tickers on one chart by normalizing each to a
 * percentage change from the first candle in the visible range.
 * series: [{ symbol, candles }]
 */
export default function CompareChart({ series, theme = "dark" }) {
  const containerRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current || series.length === 0) return;
    const c = PALETTE[theme] || PALETTE.dark;

    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { color: "transparent" },
        textColor: c.text,
        fontFamily: "Uber Move, -apple-system, Helvetica Neue, Arial, sans-serif",
      },
      grid: {
        vertLines: { color: c.grid },
        horzLines: { color: c.grid },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: c.grid },
      timeScale: { borderColor: c.grid, timeVisible: true },
    });

    series.forEach(({ candles }, i) => {
      if (!candles || candles.length === 0) return;
      const base = candles[0].close;
      const line = chart.addLineSeries({
        color: LINE_COLORS[i % LINE_COLORS.length],
        lineWidth: 2,
        lineStyle: LineStyle.Solid,
        priceFormat: { type: "custom", formatter: (v) => `${v > 0 ? "+" : ""}${v.toFixed(2)}%` },
      });
      line.setData(
        candles.map((cd) => ({
          time: cd.time,
          value: ((cd.close - base) / base) * 100,
        }))
      );
    });

    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [series, theme]);

  return <div ref={containerRef} className="h-[420px] w-full" />;
}
