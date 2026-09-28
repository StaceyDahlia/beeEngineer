(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.BeelineUx = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  const numberFormats = new Map();

  function number(value, maximumFractionDigits = 2) {
    const numeric = Number(value);
    const safe = Number.isFinite(numeric) ? numeric : 0;
    const normalized = Math.abs(safe) < 1e-9 ? 0 : safe;
    const digits = Math.max(0, Math.min(2, Number(maximumFractionDigits) || 0));
    if (!numberFormats.has(digits)) {
      numberFormats.set(
        digits,
        new Intl.NumberFormat("ru-RU", {
          minimumFractionDigits: 0,
          maximumFractionDigits: digits,
        })
      );
    }
    return numberFormats.get(digits).format(normalized);
  }

  function integer(value) {
    const numeric = Number(value);
    return Math.round(Number.isFinite(numeric) ? numeric : 0);
  }

  function plural(value, forms) {
    const n = Math.abs(integer(value));
    const mod10 = n % 10;
    const mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return forms[0];
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return forms[1];
    return forms[2];
  }

  function formatKm(value) {
    return `${number(value, 2)} км`;
  }

  function formatMinutes(value) {
    return `${number(integer(value), 0)} мин`;
  }

  function delta(value, kind) {
    const numeric = Number(value);
    const safe = Number.isFinite(numeric) ? numeric : 0;
    const rounded = kind === "km" ? Math.round(safe * 100) / 100 : integer(safe);
    const sign = rounded > 0 ? "+" : rounded < 0 ? "−" : "";
    const absolute = Math.abs(rounded);
    if (kind === "km") return `${sign}${number(absolute, 2)} км`;
    if (kind === "minutes") return `${sign}${number(absolute, 0)} мин`;
    if (kind === "crews") return `${sign}${number(absolute, 0)} ${plural(absolute, ["бригада", "бригады", "бригад"])}`;
    return `${sign}${number(absolute, 0)} ${plural(absolute, ["заявка", "заявки", "заявок"])}`;
  }

  function planStatus(plan) {
    if (!plan) return "План ещё не построен";
    return `План построен: ${number(plan.assigned, 0)} ${plural(plan.assigned, ["заявка", "заявки", "заявок"])}, ${number(plan.usedEngineers, 0)} ${plural(plan.usedEngineers, ["бригада", "бригады", "бригад"])}, ${formatKm(plan.totalDistance)}.`;
  }

  function indexById(list) {
    return new Map((list || []).map((item) => [String(item.id), item]));
  }

  function diffCategoryLabel(category) {
    return ({
      assigned: "Назначена",
      unassigned: "Снята с назначения",
      reassigned: "Переназначена",
      reordered: "Изменён порядок",
      delayed: "Задержана",
      cancelled: "Отменена",
    })[category] || "Изменена";
  }

  function humanDiff(item, jobs, engineers, options = {}) {
    const jobMap = jobs instanceof Map ? jobs : indexById(jobs);
    const engineerMap = engineers instanceof Map ? engineers : indexById(engineers);
    const job = jobMap.get(String(item?.job_id)) || {};
    const address = job.address || job.title || "Заявка";
    const teamName = (value) => String(value || "").replace(/^бригада\s+/i, "");
    const from = teamName(engineerMap.get(String(item?.from_engineer_id || ""))?.name || item?.from_engineer_name || "прежняя");
    const to = teamName(engineerMap.get(String(item?.to_engineer_id || ""))?.name || item?.to_engineer_name || "новая");
    let action;
    switch (item?.category) {
      case "assigned":
        action = `назначена бригаде «${to}»`;
        break;
      case "unassigned":
        action = `снята с маршрута бригады «${from}»`;
        break;
      case "reassigned":
        action = `переназначена с бригады «${from}» на бригаду «${to}»`;
        break;
      case "reordered": {
        const before = item.from_order ?? item.from_sequence ?? item.old_sequence ?? item.before_order;
        const after = item.to_order ?? item.to_sequence ?? item.new_sequence ?? item.after_order;
        action = before != null && after != null ? `переставлена в маршруте: ${before} → ${after}` : "переставлена в маршруте";
        break;
      }
      case "delayed": {
        const minutes = item.delay_min ?? item.delta_minutes;
        action = minutes != null ? `задержана на ${formatMinutes(minutes)}` : "перенесена на более позднее время";
        break;
      }
      case "cancelled":
        action = "отменена и исключена из маршрута";
        break;
      default:
        action = String(item?.message || "изменена").replace(/^#?[^:]+:\s*/, "");
    }
    const reason = options.emergency && String(item?.job_id) !== String(options.emergencyId)
      ? " Ради аварийной заявки."
      : "";
    return `${address} — ${action}.${reason}`.replace("..", ".");
  }

  return {
    number,
    integer,
    plural,
    formatKm,
    formatMinutes,
    delta,
    planStatus,
    diffCategoryLabel,
    humanDiff,
  };
});
