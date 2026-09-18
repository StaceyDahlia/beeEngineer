# НАЗНАЧЕНИЕ: расчёт обязательных метрик плана.
#
# ВХОД:
#   Plan (маршруты, назначения, статусы заявок).
#
# ВЫХОД:
#   PlanMetrics:
#     engineers_used          — сколько инженеров задействовано (>=1 stop)
#     total_distance_km       — суммарный пробег
#     per_engineer_distance_km — пробег по каждому инженеру
#     unassigned_count        — сколько заявок не назначено
#     + дополнительные поля для UI (assigned, cancelled, total_jobs)
#
# СВЯЗИ:
#   - вызывается из optimizer.py, baseline.py, replanner.py
#   - используется в api/routes_optimize.py
#   - frontend показывает на верхних плашках
#
# ПРАВИЛА:
#   - metrics НЕ вызывает distance.travel. Километры уже посчитаны
#     optimizer'ом и лежат в route.distance_km.
#   - engineers_used считается по непустым маршрутам (route.stops),
#     а не по полю job.assigned_engineer_id.
#   - unassigned_count считается по job.status == "unassigned",
#     а не по assigned_engineer_id is None (иначе cancelled/done
#     попадут в эту метрику).
#
# ВАЛИДАЦИЯ (dev-режим, settings.debug_validate_metrics=True):
#   - сумма stop.distance_km_from_prev должна равняться route.distance_km
#   - каждая заявка со status="assigned" должна иметь stop в каком-то маршруте
#   - каждый stop должен ссылаться на существующую заявку

from __future__ import annotations

from typing import Dict, Iterable, List

from ..config import settings
from ..schemas.plan import Plan, PlanMetrics


# ---------------------------------------------------------------------------
# Публичный интерфейс
# ---------------------------------------------------------------------------

def compute(plan: Plan) -> PlanMetrics:
    """
    Считает обязательные метрики по плану.
    Чистая функция: не меняет plan, не ходит в сеть, не читает файлы.
    """
    per_eng_full: Dict[str, float] = {}
    engineers_used = 0

    for route in plan.routes:
        per_eng_full[route.engineer_id] = float(route.distance_km)
        if route.stops:
            engineers_used += 1

    total_full = sum(per_eng_full.values())

    assigned_count = sum(1 for j in plan.jobs if j.status == "assigned")
    unassigned_count = sum(1 for j in plan.jobs if j.status == "unassigned")
    cancelled_count = sum(1 for j in plan.jobs if j.status == "cancelled")

    metrics = PlanMetrics(
        engineers_used=engineers_used,
        total_distance_km=round(total_full, 3),
        unassigned_count=unassigned_count,
        per_engineer_distance_km={
            eid: round(v, 3) for eid, v in per_eng_full.items()
        },
    )

    # Дополнительные поля — если PlanMetrics их поддерживает.
    # (см. правку schemas/plan.py ниже)
    if hasattr(metrics, "assigned_count"):
        metrics.assigned_count = assigned_count
        metrics.cancelled_count = cancelled_count
        metrics.total_jobs = len(plan.jobs)

    if getattr(settings, "debug_validate_metrics", False):
        _validate(plan)

    return metrics


# ---------------------------------------------------------------------------
# Валидация (только в dev-режиме)
# ---------------------------------------------------------------------------

def _validate(plan: Plan) -> None:
    """
    Проверяет внутреннюю согласованность плана.
    Падает с AssertionError при расхождении — это баг optimizer, не metrics.
    """
    jobs_by_id = {j.id: j for j in plan.jobs}
    stopped_ids = set()

    for route in plan.routes:
        # 1. Сумма километров по стопам = route.distance_km
        stop_sum = round(
            sum(stop.distance_km_from_prev for stop in route.stops), 3
        )
        route_km = round(route.distance_km, 3)
        assert abs(stop_sum - route_km) < 1e-6, (
            f"Route {route.engineer_id}: distance_km={route_km} "
            f"≠ sum(stops)={stop_sum}"
        )

        # 2. Каждый stop ссылается на существующую заявку
        for stop in route.stops:
            assert stop.job_id in jobs_by_id, (
                f"Route {route.engineer_id}: stop ссылается на "
                f"несуществующую заявку {stop.job_id}"
            )
            stopped_ids.add(stop.job_id)

            # 3. Заявка в стопе должна быть assigned этому инженеру
            job = jobs_by_id[stop.job_id]
            assert job.assigned_engineer_id == route.engineer_id, (
                f"Заявка {job.id}: assigned_engineer_id="
                f"{job.assigned_engineer_id}, но stop в маршруте "
                f"{route.engineer_id}"
            )

    # 4. Каждая assigned-заявка имеет stop в каком-то маршруте
    for job in plan.jobs:
        if job.status == "assigned":
            assert job.id in stopped_ids, (
                f"Заявка {job.id} помечена assigned, но stop отсутствует "
                f"в маршрутах"
            )

    # 5. Каждая unassigned-заявка не имеет assigned_engineer_id
    for job in plan.jobs:
        if job.status == "unassigned":
            assert job.assigned_engineer_id is None, (
                f"Заявка {job.id} помечена unassigned, "
                f"но assigned_engineer_id={job.assigned_engineer_id}"
            )
