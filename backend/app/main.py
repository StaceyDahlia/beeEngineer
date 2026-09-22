# НАЗНАЧЕНИЕ: точка входа FastAPI. Собирает роутеры, CORS, healthcheck.
#
# ВХОД:  HTTP-запросы от frontend/.
# ВЫХОД: JSON-ответы + OpenAPI на /docs.
# СВЯЗИ: app/api/routes_*.py.

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import (
    routes_data,
    routes_optimize,
    routes_replan,
    routes_review,
    routes_map,
)


app = FastAPI(title="Beeline Route Planner", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],     # для хакатона; в проде — ограничить
    allow_methods=["*"],
    allow_headers=["*"],
)

# Префикса /api/v1 нет — эндпоинты как в API_CONTRACT.md.
app.include_router(routes_data.router,     tags=["data"])
app.include_router(routes_optimize.router, tags=["optimize"])
app.include_router(routes_replan.router,   tags=["replan"])
app.include_router(routes_review.router,   tags=["replan"])
app.include_router(routes_map.router,      tags=["map"])


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
