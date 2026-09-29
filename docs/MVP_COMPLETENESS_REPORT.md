# Отчёт о полноте MVP

Дата проверки: 29 сентября 2026 года.

## Итог

Шесть обязательных сквозных сценариев реализованы без UI-заглушек. Во время итоговой проверки публичный OSRM был доступен по `http://router.project-osrm.org`: backend получил дорожную матрицу и дорожную геометрию, а UI отобразил статус `OSRM: дорожная матрица и геометрия`. Расчётная связь между остановками не выдаётся за дорогу: при недоступности OSRM она показывается отдельным пунктирным слоем с явной подписью и не возвращается как GeoJSON дорожного маршрута.

Итоговый автоматический прогон после сквозного исправления геометрии: **66 passed, 4 warnings in 14.74s**. Проверка синтаксиса изменённых JS-файлов завершилась успешно. DevTools-сценарий выбранной бригады завершился с `browserErrors: []`.

Повторная диагностика пользовательского запуска на порту 8000 обнаружила старое сохранённое состояние frontend. Контракт хранения повышен до schema version 7, ключ localStorage заменён на `beeline_route_mvp_api_v2`, а статические JS-файлы получили cache-busting `?v=8`. Режим маршрутизации по умолчанию теперь `osrm` и работает fail-closed: автоматическая подмена дорожного результата расчётной моделью без явной настройки запрещена.

## 1. Одна дорожная матрица в оптимизации и метриках

- Endpoint: `POST /optimize`, `POST /plans`.
- Модули: `backend/app/services/planner.py`, `backend/app/services/travel_matrix.py`, `backend/app/services/ortools_vrptw.py`, `backend/app/services/baseline_greedy.py`.
- Реализация: `planner.py` один раз создаёт `RoutingProvider` и передаёт один объект матрицы одновременно в `baseline_greedy` и оптимизированный планировщик. Итоговые расстояния и времена берутся из тех же дуг `RouteMetric`; `matrix_id` включён в метаданные результата и сегментов.
- Автотест: `tests/test_mvp_completeness.py::test_osrm_matrix_metrics_and_geometry_use_the_same_arcs`.
- Пример входа: две координаты Москвы `[55.75, 37.61]` и `[55.76, 37.64]`, профиль OSRM `driving`.
- Фактический результат финальной live-проверки адаптера: `provider=osrm`, `matrix_id=4e9bc65de0c2dba4`, `distance_m=3096`, `travel_min=6`; геометрия содержит 159 точек и тот же `matrix_id`. В полном восточном сценарии UI показал 66/66 назначенных заявок, 11 использованных бригад и 216,28 км; baseline — 40 заявок, 12 бригад и 279,38 км.
- Скриншот: [OSRM-план и метрики](screenshots/mvp-01-osrm-road-plan.png).
- Общий результат тестов: `66 passed, 4 warnings`.

## 2. Карта использует дорожную геометрию тех же сегментов

- Endpoint: `POST /optimize`, `POST /plans`, `POST /plans/{plan_id}/events/preview`.
- Модули: `backend/app/services/travel_matrix.py::attach_route_geometry`, `frontend/src/plan_adapter.js`, `frontend/index.html`.
- Реализация: backend запрашивает OSRM Route для ровно той же упорядоченной последовательности точек, которая была оценена таблицей, и возвращает `routes[].route_geometry` — GeoJSON `FeatureCollection`. Каждый `LineString` содержит `source=osrm`, `distance_m`, `travel_min`, `matrix_id` и более двух координат. Frontend читает только `route_geometry.features[*].geometry.coordinates`; старый клиентский повторный запрос OSRM/Valhalla удалён. Двухточечная линия с `source=osrm` отклоняется как нарушение контракта. При отсутствии геометрии UI показывает отдельный пунктирный слой `route-line--calculated` с подписью «Дорожная геометрия недоступна — расчётная связь».
- Автотесты: `tests/test_mvp_completeness.py::test_osrm_matrix_metrics_and_geometry_use_the_same_arcs`, `tests/test_route_geometry_contract.py::test_frontend_rejects_two_point_line_claiming_to_be_osrm`, `test_frontend_accepts_multi_point_backend_feature_collection`, `test_map_uses_backend_features_and_marks_calculation_fallback`; защитный тест `test_routing_provider_falls_back_without_fake_road_geometry` проверяет отсутствие фальшивого road-слоя при отказе OSRM.
- Пример входа: выбранный маршрут инженера в восточном сценарии.
- Фактический результат DevTools-прогона для `east:eng-001`: `route_geometry.features=4`; типы всех сегментов — `LineString`; числа координат — `95, 339, 19, 66`; у всех `source=osrm` и `matrix_id=49e4ad95d0c4b48f`. В DOM создано 4 слоя `.route-line--osrm`, 0 слоёв `.route-line--calculated`, и каждый SVG-path связан с feature, содержащим более двух координат.
- Проверка KPI: сумма сегментов выбранной бригады `3551 + 16288 + 350 + 1788 = 21977 м`, что равно `route.distance_m=21977`. Сумма `routes[].distance_m` равна `metrics.total_distance_m`; в браузерном прогоне — 216284 м.
- Диагностические команды: `.\.venv\Scripts\python.exe scripts\diagnose_route_geometry.py --base-url http://127.0.0.1:8000 --scenario east` и `node scripts/capture_osrm_route_proof.mjs`.
- Скриншот крупного масштаба: [линия OSRM повторяет улицы, повороты и проезды](screenshots/mvp-07-osrm-road-geometry-closeup.png).
- Общий результат тестов: `66 passed, 4 warnings`.

## 3. Импорт проходит полный путь до результата

- Endpoint: `POST /scenarios/import` → `POST /plans` → отображение ответа в UI.
- Модули: `backend/app/services/scenario_import.py`, `backend/app/services/loader.py`, `frontend/src/api.js`, `frontend/index.html`.
- Реализация: файл загружается как CSV или JSON, валидируется, превращается в изолированный динамический сценарий, регистрируется в памяти процесса, оптимизируется обычным endpoint и заменяет текущий набор данных в интерфейсе.
- Автотест: `tests/test_mvp_completeness.py::test_import_creates_isolated_scenario_then_optimizes_it`.
- Пример входа: [`data/examples/mvp_import.json`](../data/examples/mvp_import.json) — 2 заявки и 1 бригада одного региона.
- Фактический результат: создан отдельный сценарий `import-*`; `IMP-1` назначена, `IMP-2` не назначена с кодом `NO_EQUIPMENT`; UI показал 1/2 назначенных, 1 использованную бригаду и 1,28 км.
- Скриншот: [результат импортированного сценария](screenshots/mvp-03-imported-plan.png).
- Общий результат тестов: `66 passed, 4 warnings`.

## 4. Обычная заявка вставляется в свободный интервал или объясняется

- Endpoint: `POST /plans/{plan_id}/events/preview`, затем `POST /plans/{plan_id}/events/apply`; тип события `new_normal_job`.
- Модуль: `backend/app/services/replanner.py::_insert_normal_job`.
- Реализация: перебираются допустимые позиции внутри оставшейся части маршрута. Проверяются навыки, транспорт, оборудование, смена, окно и дорожное время. Относительный порядок и время начала уже запланированных работ не изменяются. Если позиции нет, заявка остаётся неназначенной с машинным кодом и человеческим объяснением.
- Автотесты: `test_normal_job_is_inserted_without_reordering_existing_route` и `test_normal_job_reports_equipment_block_and_keeps_route_unchanged` в `tests/test_mvp_completeness.py`.
- Пример входа: новая заявка `NEW-NORMAL-1`, окно `12:00–14:00`, длительность 20 минут, навык `connect`, транспорт `Автомобиль`, оборудование `router: 1`.
- Фактический результат: preview добавил ровно одно назначение, сохранил прежний порядок маршрута; назначенные заявки изменились с 1 до 2, расстояние — с 1,28 до 5,38 км. Контрпример с `fiber_splicer: 1` возвращает `NO_EQUIPMENT` и не меняет маршрут.
- Скриншот: [предпросмотр вставки обычной заявки](screenshots/mvp-05-normal-job-preview.png).
- Общий результат тестов: `66 passed, 4 warnings`.

## 5. До/после события сравнивается на одном временном горизонте

- Endpoint: `POST /plans/{plan_id}/events/preview`, `POST /plans/{plan_id}/events/apply`.
- Модули: `backend/app/services/replanner.py::_event_scope_metrics`, `frontend/index.html`.
- Реализация: backend формирует `comparison.event_scope` с одинаковыми `horizon_start` и `horizon_end` для before/after. Уже завершённая часть смены исключается из обеих сторон; текущая стадия фиксируется одинаково. UI выводит только эти backend-метрики и явно подписывает их как изменения в оставшейся части смены.
- Автотест: `tests/test_mvp_completeness.py::test_event_comparison_has_one_temporal_horizon`.
- Пример входа: применение `NEW-NORMAL-1` в 11:00 к плану на дату сценария.
- Фактический результат: `same_horizon=true`, обе стороны имеют один интервал 11:00–18:00; в сравнении 1 → 2 назначенных и 1,28 → 5,38 км.
- Скриншот: [применённое изменение на одинаковом горизонте](screenshots/mvp-06-same-horizon-comparison.png).
- Общий результат тестов: `66 passed, 4 warnings`.

## 6. Навыки, транспорт и оборудование — жёсткие ограничения

- Endpoint: `POST /optimize`, `POST /plans`, endpoints событий плана.
- Модули: `backend/app/services/ortools_vrptw.py`, `backend/app/services/baseline_greedy.py`, `backend/app/services/replanner.py`, `backend/app/services/validation.py`.
- Реализация: несовпадение любого из трёх признаков исключает дугу назначения и отражается отдельным кодом `NO_SKILL`, `NO_VEHICLE` или `NO_EQUIPMENT`. Валидатор дополнительно отклоняет план, если ограничение нарушено.
- Автотесты: `test_normal_job_reports_equipment_block_and_keeps_route_unchanged`, `test_import_creates_isolated_scenario_then_optimizes_it`, а также constraint-тесты существующего набора.
- Пример входа: `IMP-2` требует навык `local` и `fiber_splicer: 1`; единственная бригада имеет навык, но не имеет `fiber_splicer`.
- Фактический результат: заявка не назначена, `unassigned_reason.code=NO_EQUIPMENT`, причина отображена диспетчеру. Аналогично обрабатываются несовместимые навыки и транспорт.
- Скриншот: [неназначенная заявка и причина ограничения](screenshots/mvp-04-hard-constraints.png).
- Общий результат тестов: `66 passed, 4 warnings`.

## Аудит алгоритмических правил

| Правило | Статус | Подтверждение |
|---|---|---|
| `completed` и `cancelled` не перепланируются | Реализовано | `tests/test_replan.py::test_completed_job_is_not_replanned`, `test_cancelled_job_is_removed_from_new_route` |
| Текущая работа не прерывается | Реализовано | `test_emergency_does_not_interrupt_in_progress_and_gets_event_window` |
| Приоритет авария → подключение → ремонт/локальная → дозаказ | Реализовано | `tests/test_priority_regions.py` |
| Обычная заявка сначала пробует локальную вставку | Реализовано | `test_normal_job_is_inserted_without_reordering_existing_route` |
| Навык, транспорт, оборудование, окно и смена — hard constraints | Реализовано | constraint-тесты и `test_mvp_completeness.py` |
| Один импортированный файл — один изолированный регион | Реализовано | `test_import_creates_isolated_scenario_then_optimizes_it` и `test_scenarios_are_allowlisted_and_never_mix_regional_ids` |
| Повторные переходы между явно заданными зонами нежелательны, но допустимы | Реализовано как soft penalty | `test_zone_penalty_is_soft_and_discourages_repeated_crossings` |
| Статусы движения не выдаются за телеметрию | Реализовано | UI помечает `В пути` и `Выполняет работу` как модельные статусы |

## Принятые допущения

- **ASSUMPTION:** наличие оборудования — атрибут бригады; складской остаток и расход между заявками не моделируются, потому что источник не задаёт складской процесс.
- **ASSUMPTION:** публичный OSRM предоставляет профиль `driving`; тот же дорожный граф применяется ко всем допустимым типам транспорта, чтобы расчёт и карта оставались согласованными. Тип транспорта при этом остаётся жёстким ограничением назначения. Прототип не заявляет поддержку расписаний общественного транспорта.
- **ASSUMPTION:** штраф 20 км за переход между разными непустыми `zone` влияет только на целевую функцию. Он не добавляется в физические метрики и не запрещает пересечение границы.

## Ограничения прототипа и путь в production

- Импортированные сценарии и версии планов хранятся в памяти процесса и исчезают после перезапуска. Для production нужны БД и объектное хранилище исходных файлов.
- Публичный OSRM не имеет SLA; адаптер ограничивает один запрос 100 точками. Для production нужен собственный кластер маршрутизации или коммерческий провайдер с SLA, лимитами и профилями транспорта.
- Карта загружает Leaflet и тайлы OpenStreetMap из сети. Для production следует зафиксировать и раздавать библиотеку локально, а также выбрать tile-провайдера с подходящими лимитами и SLA.
- При `ROUTING_PROVIDER=auto` недоступность OSRM включает явно подписанную расчётную модель без дорожной геометрии. Такой режим пригоден для локальной диагностики, но не считается подтверждением полностью готового дорожного MVP.
- Статусы выполнения сейчас вычисляются из плана. Для фактических статусов нужны интеграции с мобильным приложением инженера/HelpDesk и журнал событий.

## Команды итоговой проверки

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check frontend/src/api.js
node --check frontend/src/plan_adapter.js
node --check frontend/src/dispatcher_ux.js
node --check scripts/capture_mvp_completeness_screenshots.mjs
node --check scripts/capture_osrm_route_proof.mjs
node scripts/capture_mvp_completeness_screenshots.mjs
node scripts/capture_osrm_route_proof.mjs
```

Фактический результат:

```text
66 passed, 4 warnings in 14.70s
browserErrors: []
```

## Регрессия добавления заявок и повторной оптимизации

- Endpoint/модули: `POST /plans/{plan_id}/events/preview`, `POST /plans/{plan_id}/events/apply`, `POST /plans/{plan_id}/reoptimize`, `backend/app/services/replanner.py`, `backend/app/services/plan_store.py`.
- После срочного события все дуги итогового маршрута повторно рассчитываются одним routing provider; KPI и GeoJSON имеют один `matrix_id`.
- Кнопка «Построить план» при наличии текущей версии вызывает `reoptimize`, а не повторно загружает исходный CSV. Применённые обычные и срочные заявки сохраняются в новой версии.
- Автотесты: `test_applied_normal_job_is_visible_and_survives_reoptimization`, `test_applied_emergency_job_survives_reoptimization`.
- Живая OSRM-диагностика 2026-09-29: `provider=osrm`, `geometry_complete=true`, 40 сегментов, минимум 28 точек в LineString, единый `matrix_id=cbec254c161a0241`; срочная заявка присутствует в версии 3 после повторного построения.
