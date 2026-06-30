# 📊 Bourse

A personal **Bloomberg-style stock research terminal**. Search any ticker and get a
live dashboard: real-time price, TradingView-grade candlestick charts, key fundamentals,
analyst price targets, and the latest news — all in a clean dark UI.

Built as a **full-stack** app: a Python **FastAPI** backend wrapping Yahoo Finance, and a
**React + Tailwind** frontend with [`lightweight-charts`](https://github.com/tradingview/lightweight-charts).

> **Live demo:** _add your deployed URL here_

![screenshot](screenshot.png)

## Features

- 🔎 **Search any ticker** — AAPL, TSLA, NVDA, and thousands more
- 📈 **Candlestick charts** with volume and a 1D / 5D / 1M / 6M / 1Y / 5Y range selector
- 📊 **Key statistics** — market cap, P/E, EPS, margins, ROE, beta, volume, and more
- 🎯 **Analyst targets** — low / mean / high visualized against the current price
- 📰 **Latest news** headlines per company
- 🌑 Polished dark "terminal" UI

## Architecture

```
React + Tailwind (Vite)  ──/api──▶  FastAPI  ──▶  Yahoo Finance (yfinance)
      frontend/                      backend/
```

The frontend never talks to Yahoo directly — it calls our own typed JSON API, which keeps
the React code clean and means we could swap the data source without touching the UI.

## Tech stack

| Layer | Tools |
|-------|-------|
| Frontend | React, Tailwind CSS v4, Vite, lightweight-charts |
| Backend | Python, FastAPI, Uvicorn, yfinance |
| Data | Yahoo Finance (no API key required) |

## Run it locally

**Requirements:** Python 3.11+ and Node 18+.

```bash
# Backend
cd backend
python3 -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install fastapi "uvicorn[standard]" yfinance

# Frontend (in a second terminal)
cd frontend
npm install
```

Then from the project root, launch both at once:

```bash
./start.sh
```

Open **http://localhost:5173**.

## API endpoints

| Endpoint | Returns |
|----------|---------|
| `GET /api/stock/{symbol}` | Snapshot + fundamentals + analyst targets |
| `GET /api/history/{symbol}?range=1M` | OHLC candles for the chart |
| `GET /api/news/{symbol}` | Recent headlines |

## Notes

Data comes from Yahoo Finance via `yfinance`, which is free and unofficial — it can rate-limit
or change shape occasionally. For production you'd swap in a paid data provider behind the
same API. This project is for learning/demo purposes, not investment advice.
