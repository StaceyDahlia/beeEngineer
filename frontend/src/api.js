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
      throw new Error(detail || "Сервис временно недоступен. Повторите попытку.");
    }
    return body;
  }

  window.BeelineApi = {
    health: () => requestJson("/health"),
    scenarios: () => requestJson("/data/scenarios"),
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
