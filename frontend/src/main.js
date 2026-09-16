// НАЗНАЧЕНИЕ: state и оркестрация UI.
//
// ВХОД:
//   - ответы api.js (jobs, engineers, plan)
//   - действия пользователя (клик по заявке, кнопки)
//
// ВЫХОД:
//   - обновлённый DOM (через sidebar.js, timeline.js, map.js)
//   - вызовы api.js
//
// СВЯЗИ:
//   api.js, map.js, sidebar.js, timeline.js, styles.css
//
// СТРУКТУРА state:
//   {
//     jobs: [],
//     engineers: [],
//     plan: null,
//     baseline: null,
//     selectedJobId: null,
//     filter: "all",
//     changedJobIds: []
//   }
//
// КЛЮЧЕВЫЕ ФУНКЦИИ:
//   init()               — загрузка данных и первичный рендер
//   runOptimize()        — POST /optimize, обновление метрик и карты
//   runReplan(event)     — POST /replan, подсветка changedJobIds
//   selectJob(id)        — показать объяснение
//   applyFilter(filter)  — фильтр заявок
