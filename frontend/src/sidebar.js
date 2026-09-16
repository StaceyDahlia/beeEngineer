// НАЗНАЧЕНИЕ: список заявок и инженеров, фильтры.
//
// ВХОД:  jobs, engineers, filter, selectedJobId.
// ВЫХОД: DOM-карточки, обработчики.
// СВЯЗИ: main.js, styles.css (.job-card, .filter-button).
//
// ФУНКЦИИ:
//   renderJobs(jobs, filter)   — карточки заявок
//   renderEngineers(engineers) — карточки инженеров + смена статуса
//   bindJobClick(cb)           — клик по заявке
//   bindFilterChange(cb)       — фильтр «Все / Срочные / Не назначены»
//
// ВАЖНО: смена статуса инженера («Заболел») → событие engineer_unavailable
//        → main.runReplan(event).
