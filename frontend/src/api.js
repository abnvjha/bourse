// In local dev, Vite's proxy forwards relative /api requests to FastAPI.
// Production can override the backend URL at build time. Keep the deployed
// Render URL as a fallback so a missing Vercel environment variable does not
// silently turn every data request into a Vercel 404.
const configuredBase = import.meta.env.VITE_API_URL?.trim().replace(/\/+$/, "");
const productionFallback = "https://bourse-api-4bv6.onrender.com";

export const API_BASE = configuredBase || (import.meta.env.PROD ? productionFallback : "");
