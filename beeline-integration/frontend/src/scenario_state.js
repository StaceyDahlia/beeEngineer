(function (root) {
  "use strict";

  const ALLOWED_SCENARIOS = ["east", "southeast", "southcenter"];

  function reset(state, scenario) {
    if (!ALLOWED_SCENARIOS.includes(scenario)) {
      throw new Error(`Неизвестный сценарий: ${scenario}`);
    }
    Object.assign(state, {
      scenario,
      version: 0,
      planId: null,
      parentPlanId: null,
      lastPlan: null,
      baseline: null,
      planHistory: [],
      changes: [],
      lastDiff: null,
      selectedJobId: null,
      selectedEngineerId: "all",
      mapLayerMode: "all",
      scenarioBefore: null,
      scenarioAfter: null,
      scenarioEvent: null,
      scenarioSnapshots: null,
      scenarioPreview: null,
      scenarioBeforeVersion: null,
      scenarioAfterVersion: null,
      historyViewVersion: null,
      routingContext: null,
      datasetMeta: { name: `Встроенный сценарий ${scenario}`, scenario, loadedAt: new Date().toISOString() },
      jobs: [],
      engineers: [],
      dataRevision: Number(state.dataRevision || 0) + 1,
    });
    return state;
  }

  const api = { ALLOWED_SCENARIOS, reset };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.BeelineScenarioState = api;
})(typeof window !== "undefined" ? window : globalThis);
