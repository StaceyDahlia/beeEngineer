(function () {
  "use strict";

  async function requestJson(url, options) {
    const response = await fetch(url, options);
    let body = null;
    try {
      body = await response.json();
    } catch (_) {
      body = null;
    }
    if (!response.ok) {
      const rawDetail = body && (body.error?.message || body.detail || body.warnings?.[0]);
      const detail = typeof rawDetail === "string" ? rawDetail : rawDetail?.message;
      const error = new Error(detail || "Сервис временно недоступен. Повторите попытку.");
      error.code = body?.error?.code || body?.detail?.code || null;
      error.status = response.status;
      throw error;
    }
    return body;
  }

  window.BeelineApi = {
    health: () => requestJson("/health"),
    plan: (planId) => requestJson(`/plans/${encodeURIComponent(planId)}`),
    scenarios: () => requestJson("/data/scenarios"),
    importScenario: (file) =>
      requestJson("/scenarios/import", {
        method: "POST",
        headers: {
          "Content-Type": file.type || (file.name.toLowerCase().endsWith(".json") ? "application/json" : "text/csv"),
          "X-Filename": file.name,
        },
        body: file,
      }),
    jobs: (scenario = "east") =>
      requestJson(`/data/jobs?scenario=${encodeURIComponent(scenario)}`),
    optimize: (scenario = "east", engine = "ortools_vrptw") =>
      requestJson("/optimize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario, engine }),
      }),
    createPlan: (scenario = "east", engine = "ortools_vrptw", solveTimeLimitMs = null) =>
      requestJson("/plans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scenario,
          engine,
          ...(solveTimeLimitMs ? { solve_time_limit_ms: solveTimeLimitMs } : {}),
        }),
      }),
    reoptimizePlan: (planId, baseVersion, engine = "ortools_vrptw", solveTimeLimitMs = null) =>
      requestJson(`/plans/${encodeURIComponent(planId)}/reoptimize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          base_version: baseVersion,
          engine,
          ...(solveTimeLimitMs ? { solve_time_limit_ms: solveTimeLimitMs } : {}),
        }),
      }),
    previewEvent: (planId, event) =>
      requestJson(`/plans/${encodeURIComponent(planId)}/events/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(event),
      }),
    applyEvent: (planId, previewId) =>
      requestJson(`/plans/${encodeURIComponent(planId)}/events/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preview_id: previewId }),
      }),
    history: (planId) =>
      requestJson(`/plans/${encodeURIComponent(planId)}/history`),
  };
})();
