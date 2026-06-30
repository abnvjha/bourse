import { useEffect, useRef } from "react";
import { createChart, CrosshairMode } from "lightweight-charts";

const PALETTE = {
  dark: {
    text: "#9b9b9b",
    grid: "#2b2b2b",
    up: "#06c167",
    down: "#e11900",
    volUp: "rgba(6,193,103,0.35)",
    volDown: "rgba(225,25,0,0.35)",
  },
  light: {
    text: "#6e6e6e",
    grid: "#e0e0e0",
    up: "#404040",
    down: "#000000",
    volUp: "rgba(64,64,64,0.25)",
    volDown: "rgba(0,0,0,0.25)",
  },
};

/**
 * TradingView-style candlestick chart with a volume histogram underneath.
 * Re-renders whenever new candle data arrives, or the theme changes
 * (the chart library needs literal colors, it can't read CSS variables).
 */
export default function PriceChart({ candles, theme = "dark" }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current) return;
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
    chartRef.current = chart;

    const candleSeries = chart.addCandlestickSeries({
      upColor: c.up,
      downColor: c.down,
      borderUpColor: c.up,
      borderDownColor: c.down,
      wickUpColor: c.up,
      wickDownColor: c.down,
    });

    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
    });
    // Pin the volume bars to the bottom 25% of the chart.
    chart.priceScale("vol").applyOptions({
      scaleMargins: { top: 0.78, bottom: 0 },
    });

    candleSeries.setData(candles);
    volumeSeries.setData(
      candles.map((cd) => ({
        time: cd.time,
        value: cd.volume,
        color: cd.close >= cd.open ? c.volUp : c.volDown,
      }))
    );
    chart.timeScale().fitContent();

    return () => chart.remove();
  }, [candles, theme]);

  return <div ref={containerRef} className="h-[420px] w-full" />;
}
