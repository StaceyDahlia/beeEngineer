(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.BeelineDispatcherUx = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  const CLOSED_STATUSES = new Set(["completed", "done", "cancelled", "closed"]);

  function isOpenUnassigned(job) {
    return String(job?.status || "").toLowerCase() === "unassigned"
      && !CLOSED_STATUSES.has(String(job?.status || "").toLowerCase());
  }

  function attentionReason(job) {
    const status = String(job?.status || "").toLowerCase();
    if (CLOSED_STATUSES.has(status)) return "";
    if (job?.priority === "urgent" || job?.priorityClass === "emergency") return "Срочная заявка";
    if (status === "unassigned") return job?.unassignedReason?.message || "Заявка не назначена";
    if (job?.riskLate) return "Есть риск опоздания";
    const code = String(job?.unassignedReason?.code || "");
    if (code === "NO_SKILL") return "Нет бригады с нужным навыком";
    if (code === "NO_VEHICLE") return "Нет бригады с нужным транспортом";
    if (code === "NO_EQUIPMENT") return "Нет бригады с нужным оборудованием";
    return "";
  }

  function visibleInMapMode(job, mode, selectedEngineerId) {
    if (mode === "unassigned") return isOpenUnassigned(job);
    if (mode === "problems") return Boolean(attentionReason(job));
    if (mode === "selected_route") {
      return selectedEngineerId !== "all" && String(job?.engineerId || "") === String(selectedEngineerId);
    }
    return true;
  }

  function engineerWorkStatus(engineer, jobs) {
    const raw = String(engineer?.status || "").toLowerCase();
    if (raw.includes("недоступ") || ["unavailable", "off_shift", "absent"].includes(raw)) return "Недоступна";
    const assigned = (jobs || []).filter(job => String(job?.engineerId || "") === String(engineer?.id || ""));
    if (assigned.some(job => String(job.status).toLowerCase() === "in_progress")) return "Выполняет работу (модель)";
    if (assigned.some(job => String(job.status).toLowerCase() === "en_route")) return "В пути (модель)";
    return "Доступна";
  }

  function humanDelta(before, after, kind) {
    const delta = Number(after || 0) - Number(before || 0);
    const abs = Math.abs(delta);
    if (kind === "distance") {
      if (!delta) return "Пробег не изменился";
      return `Пробег ${delta < 0 ? "меньше" : "больше"} на ${abs.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} км`;
    }
    const noun = kind === "unassigned" ? "Неназначенных" : kind === "crews" ? "Бригад" : "Назначенных";
    if (!delta) return `${noun} столько же`;
    return `${noun} стало на ${Math.round(abs)} ${delta < 0 ? "меньше" : "больше"}`;
  }

  function versionTitle(record, currentVersion) {
    if (record?.draft) return "Черновик перепланирования";
    if (Number(record?.version) === 1) return "Исходный план";
    if (Number(record?.version) === Number(currentVersion)) return "Применённый план";
    return "Версия плана";
  }

  function russianPlural(value, forms) {
    const number = Math.abs(Number(value) || 0) % 100;
    const last = number % 10;
    if (number > 10 && number < 20) return forms[2];
    if (last === 1) return forms[0];
    if (last >= 2 && last <= 4) return forms[1];
    return forms[2];
  }

  function datasetSummary(meta, jobs, engineers) {
    const source = meta?.name || "Встроенный набор";
    const region = meta?.regionLabel || meta?.scenario || "Регион не указан";
    const loaded = meta?.loadedAt ? new Date(meta.loadedAt).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }) : "—";
    const jobCount = jobs?.length || 0;
    const engineerCount = engineers?.length || 0;
    return `${source} · ${region} · ${jobCount} ${russianPlural(jobCount, ["заявка", "заявки", "заявок"])} · ${engineerCount} ${russianPlural(engineerCount, ["бригада", "бригады", "бригад"])} · загружено ${loaded}`;
  }

  function buildRouteSheets(plan) {
    return (plan?.engineers || []).filter(row => row.items?.length).map(row => ({
      engineerId: String(row.engineer.id),
      engineerName: row.engineer.name,
      vehicle: row.engineer.vehicle,
      stops: row.items.map(item => ({
        order: Number(item.order), address: item.address, arrival: item.arrivalMin,
        duration: Number(item.duration || 0), equipment: item.requiredEquipment || [], status: item.status,
      })),
    }));
  }

  function replaceDataset(state, prepared, metadata) {
    state.jobs = JSON.parse(JSON.stringify(prepared.jobs || []));
    if (prepared.engineers?.length) state.engineers = JSON.parse(JSON.stringify(prepared.engineers));
    state.datasetMeta = { ...metadata };
    state.planHistory = [];
    state.lastPlan = null;
    state.baseline = null;
    state.planId = null;
    state.parentPlanId = null;
    state.version = 0;
    state.selectedJobId = null;
    state.selectedEngineerId = "all";
    state.filter = "all";
    state.mapLayerMode = "all";
    return state;
  }

  return { CLOSED_STATUSES, isOpenUnassigned, attentionReason, visibleInMapMode, engineerWorkStatus, humanDelta, versionTitle, datasetSummary, buildRouteSheets, replaceDataset };
});
