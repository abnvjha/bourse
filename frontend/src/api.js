// In local dev, Vite's proxy (see vite.config.js) forwards "/api" to the
// FastAPI backend on :8001, so a relative path works fine.
//
// Once deployed, the frontend (Vercel) and backend (Render) live on two
// different domains, so we need the real backend URL. Set VITE_API_URL in
// Vercel's project settings (Settings → Environment Variables) to your
// Render backend URL, e.g. https://bourse-api.onrender.com — no trailing
// slash, no "/api" suffix.
export const API_BASE = import.meta.env.VITE_API_URL || "";
