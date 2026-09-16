// НАЗНАЧЕНИЕ: работа с Leaflet.
//
// ВХОД:  jobs (с coords), routes (EngineerRoute), selectedJobId.
// ВЫХОД: маркеры, полилинии, popup.
// СВЯЗИ: main.js, Leaflet CDN.
//
// ФУНКЦИИ:
//   initMap(elId)              — создать карту, тайлы OSM
//   renderJobs(jobs)           — маркеры по статусу (assigned/express/unassigned)
//   renderRoutes(routes, engineers) — полилинии по цвету инженера
//   highlightRoute(engineerId) — приглушить остальные
//   flyToJob(job)              — центрировать на заявке
//
// ВАЖНО: coords приходят из backend (уже геокодированные).
//        Если coords == null — рисовать маркер в центроиде района.
