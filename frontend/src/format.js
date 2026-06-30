// Small formatting helpers shared across the UI.

export function money(value, currency = "USD") {
  if (value == null) return "—";
  const symbol = { USD: "$", EUR: "€", GBP: "£", INR: "₹", JPY: "¥" }[currency] || "";
  return symbol + Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// 4138015391744 -> "$4.14T"
export function compact(value, currency = "") {
  if (value == null) return "—";
  const symbol = { USD: "$", EUR: "€", GBP: "£", INR: "₹", JPY: "¥" }[currency] || "";
  const abs = Math.abs(value);
  const units = [
    [1e12, "T"],
    [1e9, "B"],
    [1e6, "M"],
    [1e3, "K"],
  ];
  for (const [threshold, suffix] of units) {
    if (abs >= threshold) return symbol + (value / threshold).toFixed(2) + suffix;
  }
  return symbol + Number(value).toLocaleString();
}

export function percent(value) {
  if (value == null) return "—";
  return (value > 0 ? "+" : "") + value.toFixed(2) + "%";
}

// Some yfinance fields are already fractions (0.23 = 23%), format those.
export function ratioPercent(value) {
  if (value == null) return "—";
  return (value * 100).toFixed(2) + "%";
}

export function plain(value) {
  if (value == null) return "—";
  return Number(value).toFixed(2);
}
