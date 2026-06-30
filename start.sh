#!/usr/bin/env bash
# Launches the backend (FastAPI) and frontend (Vite) together.
# Usage:  ./start.sh   then open http://localhost:5173
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "▶ Starting backend on :8001 ..."
( cd "$ROOT/backend" && ./venv/bin/uvicorn main:app --port 8001 ) &
BACKEND=$!

echo "▶ Starting frontend on :5173 ..."
( cd "$ROOT/frontend" && npm run dev ) &
FRONTEND=$!

# Stop both servers when you press Ctrl+C.
trap "echo; echo 'Stopping...'; kill $BACKEND $FRONTEND 2>/dev/null" INT TERM
echo "✓ Open http://localhost:5173"
wait
