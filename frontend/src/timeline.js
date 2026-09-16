// НАЗНАЧЕНИЕ: горизонтальный таймлайн по инженерам.
//
// ВХОД:  routes (stops с arrival/departure), engineers.
// ВЫХОД: DOM-блоки с заявками и переездами.
// СВЯЗИ: main.js, styles.css (.timeline-grid, .timeline-block).
//
// ЛОГИКА:
//   1. Ось X: 08:00–22:00, шаг 2 часа.
//   2. Для каждого инженера — строка.
//   3. Блоки: заявки + «В пути» между ними.
//   4. Hover → highlightRoute на карте.
//   5. Click → selectJob.
