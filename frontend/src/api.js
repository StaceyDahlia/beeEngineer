// НАЗНАЧЕНИЕ: тонкая обёртка над fetch к backend.
//
// ВХОД:  BASE_URL из window.FRONTEND_API_BASE или http://localhost:8000
// ВЫХОД: Promise<JSON>
// СВЯЗИ: main.js, backend/app/api/routes_*.py
//
// ЭНДПОИНТЫ:
//   getJobs()               GET  /api/v1/jobs
//   getEngineers()          GET  /api/v1/engineers
//   optimize(payload)       POST /api/v1/optimize
//   replan(plan, event)     POST /api/v1/replan
//
// FALLBACK:
//   если backend недоступен — читать public/demo-data.json
//   (чтобы демо не падало на защите).

const BASE = window.FRONTEND_API_BASE || "http://localhost:8000";

export async function getJobs() {
  const r = await fetch(`${BASE}/api/v1/jobs`);
  if (!r.ok) throw new Error("jobs unavailable");
  return r.json();
}

export async function optimize(payload) {
  const r = await fetch(`${BASE}/api/v1/optimize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error("optimize failed");
  return r.json();
}

export async function replan(plan, event) {
  const r = await fetch(`${BASE}/api/v1/replan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plan, event }),
  });
  if (!r.ok) throw new Error("replan failed");
  return r.json();
}
