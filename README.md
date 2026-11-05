# 📊 Bourse

A personal **Bloomberg-style stock research terminal**. Search a ticker and get a
market dashboard with a price snapshot, candlestick chart, available fundamentals,
and recent company news in a clean dark UI.

Built as a **full-stack** app: a Python **FastAPI** backend using Twelve Data for market data and Yahoo Finance for news, and a
**React + Tailwind** frontend with [`lightweight-charts`](https://github.com/tradingview/lightweight-charts).

> **Live demo:** https://bourse-gules.vercel.app/

![screenshot](screenshot.png)

## Features

- 🔎 **Search any ticker** — AAPL, TSLA, NVDA, and thousands more
- 📈 **Candlestick charts** with volume and a 1D / 5D / 1M / 6M / 1Y / 5Y range selector
- 📊 **Key statistics** — market cap, P/E, EPS, margins, ROE, beta, volume, and more
- 📰 **Latest news** headlines per company
- 🌑 Polished dark "terminal" UI

## Architecture

```
React + Tailwind (Vite)  ──/api──▶  FastAPI  ──▶  Twelve Data
      frontend/                      backend/       └── Yahoo Finance (news)
```

The frontend never talks to Yahoo directly — it calls our own typed JSON API, which keeps
the React code clean and means we could swap the data source without touching the UI.

## Tech stack

| Layer | Tools |
|-------|-------|
| Frontend | React, Tailwind CSS v4, Vite, lightweight-charts |
| Backend | Python, FastAPI, Uvicorn, requests, yfinance |
| Data | Twelve Data for market data; Yahoo Finance for news |

## Run it locally

**Requirements:** Python 3.11+, Node 18+, and a Twelve Data API key.

```bash
# Backend
cd backend
python3 -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
export TWELVE_DATA_API_KEY="your-key"

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
| `GET /api/stock/{symbol}` | Snapshot + available fundamentals |
| `GET /api/history/{symbol}?range=1M` | OHLC candles for the chart |
| `GET /api/news/{symbol}` | Recent headlines |

## Notes

Market data comes from Twelve Data and requires `TWELVE_DATA_API_KEY`; configure that
variable in the Render service. The news endpoint still uses Yahoo Finance through
`yfinance`, so news can be less reliable. The frontend uses the deployed Render URL as a
production fallback if `VITE_API_URL` is not set. This project is for learning/demo
purposes, not investment advice.
