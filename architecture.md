# Архитектура

Проект — локальный монолит: FastAPI обслуживает API и статический UI. `loader.py` и `scenario_import.py` готовят данные;
`travel_matrix.py` создаёт OSRM-матрицу и геометрию; `baseline_greedy.py` и `ortools_vrptw.py` строят планы; `replanner.py` обрабатывает события;
`plan_store.py` хранит версии в памяти.

Основные endpoint’ы:

- `POST /scenarios/import` — импорт и валидация CSV/JSON;
- `POST /plans` — создание v1;
- `GET /plans/{plan_id}` и `/history` — план и история;
- `POST /plans/{plan_id}/events/preview` и `/events/apply` — безопасное перепланирование;
- `POST /plans/{plan_id}/reoptimize` — повторная оптимизация текущего набора заявок;
- `GET /health` — health check.

Подробнее: [`docs/PROJECT_BRIEF.md`](docs/PROJECT_BRIEF.md), [`docs/ALGORITHM_SPEC.md`](docs/ALGORITHM_SPEC.md) и
[`docs/MVP_COMPLETENESS_REPORT.md`](docs/MVP_COMPLETENESS_REPORT.md).
