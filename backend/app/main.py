# НАЗНАЧЕНИЕ: точка входа FastAPI. Собирает роутеры, CORS, обработчики ошибок.
#
# ВХОД:
#   HTTP-запросы от frontend/ (POST /api/v1/optimize, /replan, GET /jobs, /engineers).
#   Данные из data/processed/ через services.loader.
#
# ВЫХОД:
#   JSON-ответы: план, метрики, объяснения.
#   OpenAPI-схема на /docs.
#
# СВЯЗИ:
#   - app/api/routes_optimize.py
#   - app/api/routes_replan.py
#   - app/api/routes_data.py
#   - app/config.py (настройки)
#   - app/services/loader.py (при старте — прогрев данных)
#
# ЧТО ДЕЛАЕТ:
#   1. Создаёт FastAPI(title="Beeline Route Planner").
#   2. Подключает CORS (разрешить frontend на localhost:*).
#   3. Регистрирует роутеры с префиксом /api/v1.
#   4. На startup подгружает jobs.json и engineers.json.
#   5. /health — простой healthcheck.

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
# from .api import routes_optimize, routes_replan, routes_data

app = FastAPI(title="Beeline Route Planner", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # для хакатона; в проде — ограничить
    allow_methods=["*"],
    allow_headers=["*"],
)

# app.include_router(routes_data.router,      prefix="/api/v1", tags=["data"])
# app.include_router(routes_optimize.router,  prefix="/api/v1", tags=["optimize"])
# app.include_router(routes_replan.router,    prefix="/api/v1", tags=["replan"])

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
