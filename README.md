# Beeline Business — планировщик маршрутов

Веб-прототип для диспетчера выездных бригад: распределение заявок, маршруты, карта,
объяснения назначений, сравнение baseline с OR-Tools и перепланирование после события.

## Документация решения

- [Архитектура](architecture.md) — схема, компоненты, потоки и API;
- [Алгоритм](algorithm.md) — оптимизация, ограничения и метрики;
- [Данные](data.md) — сценарии, поля, единицы и формат импорта;
- [Ограничения](limitations.md) — границы MVP и направления развития.

## Возможности

- `baseline_greedy` и оптимизация `ortools_vrptw`;
- жёсткие ограничения навыков, транспорта, оборудования, смен и временных окон;
- одна OSRM-матрица для оптимизации, KPI и GeoJSON-геометрии;
- импорт CSV/JSON как изолированного региона;
- обычная заявка вставляется в свободный интервал или получает объяснённую причину отказа;
- срочная заявка перестраивает остаток дня, не прерывая текущую работу;
- preview/apply, история версий и повторная оптимизация текущих заявок;
- причины неназначения и backend-объяснения для UI.

## Требования

- Python 3.11 или 3.12;
- интернет-доступ к `router.project-osrm.org`, OpenStreetMap tiles и CDN Leaflet;
- свободный порт `8000`.

Node.js для работы приложения не нужен; он используется только browser-screenshot scripts.

## Запуск на Windows PowerShell

Выполните из корня клонированного репозитория:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:ROUTING_PROVIDER="osrm"
$env:OSRM_BASE_URL="http://router.project-osrm.org"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Если `py` недоступен, используйте `python -m venv .venv`.

## Запуск на macOS / Linux

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
export ROUTING_PROVIDER=osrm
export OSRM_BASE_URL=http://router.project-osrm.org
./.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Откройте <http://127.0.0.1:8000/>. Swagger UI: <http://127.0.0.1:8000/docs>.

После обновления кода остановите сервер через `Ctrl+C`, запустите его заново и обновите страницу через `Ctrl+F5`.

## Проверка запуска

Windows:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

macOS/Linux:

```bash
curl http://127.0.0.1:8000/health
```

Ожидаемый ответ: `{"status":"ok","service":"beeline-integration"}`.

## Тесты

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

На macOS/Linux замените путь к Python на `./.venv/bin/python`. Тесты используют детерминированный offline provider.
Живой OSRM-контракт проверяется на запущенном приложении:

```powershell
.\.venv\Scripts\python.exe scripts\diagnose_route_geometry.py --base-url http://127.0.0.1:8000 --scenario east
```

## Демо-сценарий

1. Выберите регион или импортируйте `data/examples/mvp_import.json`.
2. Запустите «Базовый план», затем «Построить план» и сравните KPI.
3. Выберите заявку на карте и проверьте объяснение.
4. Добавьте обычную заявку и примените preview.
5. Добавьте срочную заявку: после apply линии на карте должны идти по дорогам.
6. Повторно нажмите «Построить план»: добавленные заявки сохраняются.

## Routing provider и ограничения MVP

По умолчанию включён fail-closed `osrm`: недоступная дорожная маршрутизация не подменяется прямыми линиями.
Для offline-диагностики можно явно задать `ROUTING_PROVIDER=static`; UI покажет пунктирные расчётные связи.

- PlanStore и импорты хранятся в памяти backend и очищаются при перезапуске;
- UI распознаёт устаревший `plan_id` и пересчитывает встроенный сценарий;
- публичный OSRM не имеет SLA и данных о пробках;
- карта, тайлы и геокодирование зависят от внешних сервисов;
- для общественного транспорта в MVP используется OSRM `driving`;
- статусы движения расчётные, телематики нет.

Отчёт и доказательства: [`docs/MVP_COMPLETENESS_REPORT.md`](docs/MVP_COMPLETENESS_REPORT.md).

## Структура

```text
backend/   FastAPI, планировщики, routing provider, replanning
frontend/  HTML/CSS/JS, Leaflet-карта, UI диспетчера
data/      встроенные и импортируемые сценарии
docs/      правила, спецификации, отчёт, скриншоты
scripts/   диагностика и demo/browser scripts
tests/     backend, API и routing-contract tests
```
