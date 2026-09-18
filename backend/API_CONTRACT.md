# Контракт API — версия 0.1

**Дата:** 2026-09-18
**Статус:** черновик, может меняться до интеграции
**Кому:** фронтенд, API-слой, интеграционные тесты

Этот документ фиксирует форму запросов и ответов между backend'ом и
фронтендом. Любые изменения — через раздел «Changelog» в конце файла.

Frontend может строить mock-сервер по этому контракту **до того**, как
backend будет готов. Когда API-эндпоинты поднимутся, достаточно заменить
URL мока на реальный адрес — структура ответов уже совпадает.

---

## 1. Общие соглашения

| Параметр | Значение |
|---|---|
| Base URL | `http://localhost:8000` (переменная окружения `BACKEND_URL`) |
| Формат | JSON, UTF-8 |
| `Content-Type` | `application/json` для POST |
| Время (в расписании) | `"HH:MM"`, например `"14:35"` |
| Дата+время (в заявках) | ISO 8601 с таймзоной, например `"2026-08-17T14:00:00+03:00"` |
| Координаты | `[lat, lon]` — **широта первая**, например `[55.700846, 37.7822191]` |
| Расстояние | километры, `float` (например `2.4`) |
| Длительность | минуты, `int` |
| Ошибки | `{"detail": "..."}`, HTTP 400/404/500 (стандарт FastAPI) |

**Важно про координаты:** в этом API везде `[lat, lon]`. Это **не**
GeoJSON-порядок. Для `GET /map/geojson` — там будет наоборот, по
стандарту GeoJSON (`[lon, lat]`). Об этом сказано в разделе эндпоинта.

**Авторизации нет.** Все эндпоинты открыты. Состояние между запросами
на сервере **не хранится** — фронт сам держит текущий `Plan` у себя и
передаёт его туда, где он нужен (например, в `/replan`).

---

## 2. Справочники значений

Все enum-значения, которые встретятся в ответах.

### Навыки (`skill`)

| id | Отображаемое имя |
|---|---|
| `local` | Локальные работы |
| `connect` | Работы на подключение и дознаказы |
| `emergency` | Аварийные работы |

### Приоритеты (`priority`)

| Значение | Смысл |
|---|---|
| `Обычная` | обычная заявка |
| `Срочная` | срочная, обрабатывается в первую очередь при replan |

### Тип транспорта (`vehicle`)

| Значение | Описание |
|---|---|
| `Автомобиль` | 50 км/ч |
| `Пешеход` | 5 км/ч |
| `Велосипед` | 15 км/ч |
| `ОТ` | общественный транспорт, 40 км/ч |

### Статус заявки (`job.status`)

| Значение | Смысл |
|---|---|
| `planned` | в плане, ещё не начата |
| `assigned` | назначена инженеру |
| `unassigned` | не удалось назначить |
| `in_progress` | инженер выехал / на объекте (**заморожена**) |
| `done` | выполнена (**заморожена**) |
| `cancelled` | отменена |

### Статус инженера (`engineer.status`)

| Значение | Смысл |
|---|---|
| `Доступен` | работает |
| `Заболел` | недоступен |
| `На перерыве` | недоступен временно |

### Тип события (`event.type`)

| Значение | Payload |
|---|---|
| `urgent_job` | полный объект заявки (см. п.4.3) |
| `cancel_job` | `{"job_id": "..."}` |
| `engineer_unavailable` | `{"engineer_id": "..."}` |

### Решение диспетчера (`action` в `/resolve_review`)

| Значение | Смысл |
|---|---|
| `keep` | оставить как есть, убрать из needs_review |
| `reassign` | переназначить (полный пересчёт) |
| `cancel` | отменить заявку |

### Коды предупреждений (`warning.code`)

| Код | Когда возникает |
|---|---|
| `IN_PROGRESS_ENGINEER_UNAVAILABLE` | инженер с выполняемой заявкой стал недоступен |
| `CANCEL_IGNORED_FROZEN` | попытка отменить `in_progress` / `done` заявку |
| `UNASSIGNED_AFTER_REPLAN` | после replan заявка осталась неназначенной |
| `OPTIMIZER_TIMEOUT` | полный пересчёт не успел за отведённое время |

---

## 3. Схемы объектов

### 3.1. `Job` (заявка)

```json
{
  "id": "east:74198",
  "original_id": "74198",
  "input_order": 0,
  "type": "Подключение",
  "subtype": "Конвергенция абонента",
  "skill": "connect",
  "priority": "Обычная",
  "district": "Кузьминки",
  "address": "Город Москва, пр-кт.Волгоградский, д. 128 к 5",
  "location_id": "east:loc-001",
  "coords": [55.700846, 37.7822191],
  "window_start": "2026-08-17T20:00:00+03:00",
  "window_end": "2026-08-17T22:00:00+03:00",
  "duration_min": 70,
  "required_vehicle": null,
  "required_equipment": {"router": 1},
  "status": "assigned",
  "assigned_engineer_id": "east:eng-001",
  "arrive_planned": "20:15",
  "depart_planned": "21:25",
  "explanation": "Заявка #74198 назначена «Бригада Соколов» (east:eng-001).\n\nПричины:\n  • Навык «Работы на подключение и дознаказы» входит в компетенции бригады.\n  • Тип транспорта «ОТ» подходит (в заявке ограничений нет).\n  • Прибытие в 20:15 попадает в окно 20:00–22:00.\n  • Пробег до заявки: 2.4 км, время в пути: 12 мин.\n\nЗаявка встроена в существующий маршрут бригады."
}
```

**Обязательные поля:** `id`, `type`, `skill`, `priority`, `address`, `coords`,
`window_start`, `window_end`, `duration_min`, `status`.

**Nullable:** `original_id`, `subtype`, `district`, `location_id`,
`required_vehicle`, `assigned_engineer_id`, `arrive_planned`,
`depart_planned`, `explanation`.

### 3.2. `Engineer` (инженер)

```json
{
  "id": "east:eng-001",
  "name": "Бригада Соколов",
  "skills": ["local", "emergency"],
  "vehicle": "ОТ",
  "shift_start": "2026-08-17T10:00:00+03:00",
  "shift_end": "2026-08-17T19:00:00+03:00",
  "start_point": [55.702293, 37.77392],
  "start_address": "г. Москва, ул Юных Ленинцев, д 83с 4",
  "start_location_id": "east:office",
  "status": "Доступен",
  "inventory_start": {"router": 1, "tv_box": 1}
}
```

### 3.3. `RouteStop` (остановка маршрута)

```json
{
  "job_id": "east:74198",
  "arrival": "20:15",
  "departure": "21:25",
  "travel_min_from_prev": 14,
  "distance_km_from_prev": 3.2
}
```

### 3.4. `EngineerRoute` (маршрут инженера)

```json
{
  "engineer_id": "east:eng-001",
  "stops": [
    {
      "job_id": "east:74198",
      "arrival": "20:15",
      "departure": "21:25",
      "travel_min_from_prev": 14,
      "distance_km_from_prev": 3.2
    }
  ],
  "distance_km": 8.4,
  "jobs_count": 2
}
```

### 3.5. `PlanMetrics`

```json
{
  "engineers_used": 5,
  "total_distance_km": 42.7,
  "unassigned_count": 3,
  "per_engineer_distance_km": {
    "east:eng-001": 8.4,
    "east:eng-002": 12.1
  },
  "assigned_count": 63,
  "cancelled_count": 0,
  "total_jobs": 66
}
```

### 3.6. `PlanWarning`

```json
{
  "code": "IN_PROGRESS_ENGINEER_UNAVAILABLE",
  "job_id": "east:86160",
  "engineer_id": "east:eng-001",
  "message": "Заявка east:86160 выполняется инженером east:eng-001, который помечен недоступным."
}
```

### 3.7. `Plan` (главный объект ответа)

```json
{
  "jobs": [Job, Job, ...],
  "engineers": [Engineer, ...],
  "routes": [EngineerRoute, ...],
  "metrics": PlanMetrics,
  "baseline_metrics": PlanMetrics | null,
  "changed_job_ids": ["east:urgent-demo-001"] | null,
  "diff_summary": {
    "added": ["east:urgent-demo-001"],
    "removed": [],
    "moved": ["east:75001"],
    "newly_unassigned": [],
    "cancelled": []
  } | null,
  "needs_review": ["east:86160"] | null,
  "warnings": [PlanWarning, ...] | null
}
```

**Где что заполняется:**

| Поле | `/optimize` | `/replan` | `/resolve_review` |
|---|---|---|---|
| `baseline_metrics` | да | нет | нет |
| `changed_job_ids` | `null` | список | `null` |
| `diff_summary` | `null` | объект | `null` |
| `needs_review` | `null` | список или `null` | список или `null` |
| `warnings` | `null` | список или `null` | `null` |

---

## 4. Эндпоинты

### 4.1. `GET /health`

Проверка живости. Ответ 200.

```json
{"status": "ok"}
```

---

### 4.2. Данные сценария

Все четыре эндпоинта принимают query-параметр `scenario` (опциональный).
Если не указан — берётся активный сценарий, выбранный через
`scripts/select_scenario.py`.

#### `GET /data/jobs?scenario=east`

```json
{
  "scenario": "east",
  "count": 66,
  "jobs": [Job, Job, ...]
}
```

#### `GET /data/engineers?scenario=east`

```json
{
  "scenario": "east",
  "count": 12,
  "engineers": [Engineer, ...]
}
```

#### `GET /data/events?scenario=east`

```json
{
  "scenario": "east",
  "count": 3,
  "events": [
    {"type": "urgent_job", "time": "2026-08-17T14:30:00+03:00", "payload": { ... }},
    {"type": "cancel_job", "time": "2026-08-17T15:00:00+03:00", "payload": {"job_id": "east:86160"}},
    {"type": "engineer_unavailable", "time": "2026-08-17T15:10:00+03:00", "payload": {"engineer_id": "east:eng-001"}}
  ]
}
```

#### `GET /data/locations?scenario=east`

```json
{
  "scenario": "east",
  "count": 66,
  "locations": [
    {
      "id": "east:loc-001",
      "address": "Город Москва, пр-кт.Волгоградский, д. 128 к 5",
      "coords": [55.700846, 37.7822191],
      "geocode_status": "matched_building"
    },
    ...
  ]
}
```

---

### 4.3. `POST /optimize`

Строит план с нуля (базовый вариант + optimizer + сравнение с baseline).

**Запрос:**

```json
{
  "scenario": "east",
  "engine": "greedy"
}
```

| Поле | Тип | Обяз. | По умолчанию | Значения |
|---|---|---|---|---|
| `scenario` | string | нет | активный | `east` / `southeast` / `southcenter` |
| `engine` | string | нет | `greedy` | `greedy` / `ortools` |

**Ответ:** `Plan` с заполненным `baseline_metrics`.

```json
{
  "jobs": [...],
  "engineers": [...],
  "routes": [...],
  "metrics": {
    "engineers_used": 5,
    "total_distance_km": 42.7,
    "unassigned_count": 3,
    "per_engineer_distance_km": {...},
    "assigned_count": 63,
    "cancelled_count": 0,
    "total_jobs": 66
  },
  "baseline_metrics": {
    "engineers_used": 7,
    "total_distance_km": 61.2,
    "unassigned_count": 3,
    "per_engineer_distance_km": {...},
    "assigned_count": 63,
    "cancelled_count": 0,
    "total_jobs": 66
  },
  "changed_job_ids": null,
  "diff_summary": null,
  "needs_review": null,
  "warnings": null
}
```

---

### 4.4. `POST /replan`

Применяет **одно событие** к переданному плану.

**Запрос (пример с urgent_job):**

```json
{
  "scenario": "east",
  "plan": { ...Plan из предыдущего /optimize... },
  "event": {
    "type": "urgent_job",
    "time": "2026-08-17T14:30:00+03:00",
    "payload": {
      "id": "east:urgent-demo-001",
      "type": "Подключение",
      "subtype": "Конвергенция абонента",
      "skill": "connect",
      "priority": "Срочная",
      "district": "Кузьминки",
      "address": "Город Москва, пр-кт.Волгоградский, д. 128 к 5",
      "location_id": "east:loc-001",
      "coords": [55.700846, 37.7822191],
      "window_start": "2026-08-17T20:00:00+03:00",
      "window_end": "2026-08-17T22:00:00+03:00",
      "duration_min": 70,
      "input_order": 999
    }
  }
}
```

**Запрос (cancel_job):**

```json
{
  "plan": { ... },
  "event": {
    "type": "cancel_job",
    "time": "2026-08-17T15:00:00+03:00",
    "payload": {"job_id": "east:86160"}
  }
}
```

**Запрос (engineer_unavailable):**

```json
{
  "plan": { ... },
  "event": {
    "type": "engineer_unavailable",
    "time": "2026-08-17T15:10:00+03:00",
    "payload": {"engineer_id": "east:eng-001"}
  }
}
```

**Ответ:** `Plan` с заполненными `changed_job_ids`, `diff_summary`,
`needs_review`, `warnings`.

```json
{
  "...": "...",
  "changed_job_ids": ["east:urgent-demo-001", "east:75001"],
  "diff_summary": {
    "added": ["east:urgent-demo-001"],
    "removed": [],
    "moved": ["east:75001"],
    "newly_unassigned": [],
    "cancelled": []
  },
  "needs_review": [],
  "warnings": []
}
```

**Пример с `engineer_unavailable`, когда у инженера есть `in_progress`-заявка:**

```json
{
  "...": "...",
  "changed_job_ids": [],
  "diff_summary": {
    "added": [], "removed": [], "moved": [],
    "newly_unassigned": [], "cancelled": []
  },
  "needs_review": ["east:86160"],
  "warnings": [
    {
      "code": "IN_PROGRESS_ENGINEER_UNAVAILABLE",
      "job_id": "east:86160",
      "engineer_id": "east:eng-001",
      "message": "Заявка east:86160 выполняется инженером east:eng-001, который помечен недоступным."
    }
  ]
}
```

---

### 4.5. `POST /resolve_review`

Диспетчер принимает решение по заявке из `needs_review`.

**Запрос:**

```json
{
  "scenario": "east",
  "plan": { ...Plan из предыдущего /replan... },
  "job_id": "east:86160",
  "action": "reassign"
}
```

| Поле | Тип | Обяз. | Значения |
|---|---|---|---|
| `job_id` | string | да | id заявки из `needs_review` |
| `action` | string | да | `keep` / `reassign` / `cancel` |

**Ответ:** `Plan` с обновлённым `needs_review` (заявка из него убрана).

---

### 4.6. `GET /map/geojson?scenario=east`

Отдаёт точки и маршруты для карты в формате GeoJSON FeatureCollection.
Готов к отрисовке Leaflet'ом без преобразований.

**Важно:** здесь координаты в порядке GeoJSON — `[lon, lat]`, **не так
как в остальном API**.

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": {"type": "Point", "coordinates": [37.77392, 55.702293]},
      "properties": {
        "kind": "engineer_start",
        "engineer_id": "east:eng-001",
        "name": "Бригада Соколов"
      }
    },
    {
      "type": "Feature",
      "geometry": {"type": "Point", "coordinates": [37.7822191, 55.700846]},
      "properties": {
        "kind": "job",
        "job_id": "east:74198",
        "status": "assigned",
        "assigned_engineer_id": "east:eng-001",
        "priority": "Обычная",
        "window_start": "20:00",
        "window_end": "22:00"
      }
    },
    {
      "type": "Feature",
      "geometry": {
        "type": "LineString",
        "coordinates": [
          [37.77392, 55.702293],
          [37.7822191, 55.700846],
          [37.7786035, 55.6981178]
        ]
      },
      "properties": {
        "kind": "route",
        "engineer_id": "east:eng-001",
        "distance_km": 8.4,
        "jobs_count": 2
      }
    }
  ]
}
```

**Типы features по `properties.kind`:**

| kind | Описание |
|---|---|
| `engineer_start` | стартовая точка инженера |
| `job` | точка заявки |
| `route` | линия маршрута инженера (LineString) |

**Фронт может фильтровать:**
- `kind === "job"` + `status === "unassigned"` — показать неназначенные.
- `kind === "route"` + `engineer_id` — покрасить маршрут под цвет инженера.

---

## 5. Обработка ошибок

| HTTP | Когда | Тело |
|---|---|---|
| 400 | невалидное тело запроса, неизвестный `action`, неизвестный `event.type` | `{"detail": "..."}` |
| 404 | заявка / инженер / сценарий не найден | `{"detail": "..."}` |
| 500 | внутренняя ошибка (баг) | `{"detail": "..."}` |

**Пример:**

```json
{"detail": "Заявка east:unknown не найдена в плане"}
```

---

## 6. Полный сценарий демонстрации (для фронта)

Это последовательность вызовов, которую должен отыграть UI на защите:

1. `GET /health` → проверка, что backend жив.
2. `GET /data/jobs?scenario=east` → таблица заявок.
3. `POST /optimize {"scenario": "east"}` → карта + расписание + метрики на плашках.
4. Пользователь кликает на заявку → показать `job.explanation`.
5. `POST /replan` с `event.type=urgent_job` → подсветить `changed_job_ids`.
6. `POST /replan` с `event.type=cancel_job` → подсветить удалённые.
7. `POST /replan` с `event.type=engineer_unavailable` → плашка `needs_review`, диспетчер жмёт действие.
8. `POST /resolve_review {"action": "reassign"}` → план обновился.
9. `GET /map/geojson?scenario=east` → перерисовать карту.

**Плитки метрик на UI берутся из `plan.metrics`:**
- «Занято инженеров: `engineers_used`».
- «Суммарный пробег: `total_distance_km` км».
- «Не назначено: `unassigned_count`».
- «Сравнение с базовым: `baseline_metrics.engineers_used` / `total_distance_km`».

---

## 7. Changelog

| Дата | Версия | Что изменилось |
|---|---|---|
| 2026-09-18 | 0.1 | Первая версия контракта |

---

## 8. Открытые вопросы (TBD)

- **Пагинация для `/data/jobs`** — пока нет, 66 заявок влезают целиком.
  Если появится сценарий на 500+ заявок, добавим `limit` / `offset`.
- **Кэширование на фронте** — не описано, но `Plan` неизменяем, можно
  кэшировать по `scenario`.
- **WebSocket** — не используется. Все обновления через явные POST.
- **Авторизация** — нет.
- **Сохранение состояния на сервере** — нет. Фронт держит `Plan` сам.
- **Формат координат** — в основном API `[lat, lon]`, в `/map/geojson` —
  `[lon, lat]` (по стандарту GeoJSON).
