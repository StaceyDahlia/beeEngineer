# НАЗНАЧЕНИЕ: базовый жадный алгоритм из ТЗ п.2.3 — точка сравнения для optimizer.
#
# ВХОД:
#   List[Job]      — в порядке поступления (input_order).
#   List[Engineer] — в порядке из данных.
#
# ВЫХОД:
#   Plan с заполненными routes, metrics и explanations по неназначенным.
#
# СВЯЗИ:
#   - использует constraints.can_assign
#   - использует metrics.compute
#   - использует explainer.explain_unassigned (для причин отказа)
#   - вызывается из optimizer.py и scripts/compare_baseline.py
#
# ЛОГИКА (строго по ТЗ п.2.3):
#   1. Заявки обрабатываются по порядку input_order.
#   2. Каждая назначается ПЕРВОМУ по порядку инженеру,
#      который удовлетворяет всем обязательным ограничениям.
#   3. Порядок посещения = порядок назначения.
#   4. Глобальная оптимизация НЕ выполняется.
#
# ОГРАНИЧЕНИЯ MVP:
#   - Оборудование не учитывается (check_equipment=False).
#     Причина: суммарного инвентаря в inventory.json не хватит на все
#     заявки; учёт оборудования резко увеличит долю NO_EQUIPMENT.
#   - Возврат в стартовую точку после последней заявки не требуется.
#
# ДОПУЩЕНИЯ:
#   - duration_min — время на объекте БЕЗ дороги; дорога считается отдельно.
#   - Заявки со status "cancelled" или "done" пропускаются.

from __future__ import annotations

from typing import Dict, List

from ..schemas.job import Job
from ..schemas.engineer import Engineer
from ..schemas.plan import (
    Plan, PlanMetrics, EngineerRoute, RouteStop,
)
from . import constraints as cons
from . import distance as dist_mod
from . import metrics as metrics_mod
from . import explainer as expl
from .optimizer import RouteState, min_of_day, fmt_hhmm, hhmm_to_min



# ---------------------------------------------------------------------------
# Внутреннее состояние маршрута
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def _is_assignable(job: Job) -> bool:
    """Участвует ли заявка в назначении (не отменена, не выполнена)."""
    return job.status in ("planned", "assigned")


# ---------------------------------------------------------------------------
# Публичный интерфейс
# ---------------------------------------------------------------------------

def build_baseline_plan(
    jobs: List[Job],
    engineers: List[Engineer],
    *,
    check_equipment: bool = False,
) -> Plan:
    """
    Строит базовый план строго по ТЗ п.2.3.
    Не оптимизирует ничего — просто первое подходящее назначение.
    """
    if not engineers:
        # Никого нет — все assignable-заявки становятся unassigned.
        return _finalize_empty(jobs, engineers)

    # 1. Сортируем заявки по input_order (в порядке поступления).
    jobs_sorted = sorted(jobs, key=lambda j: j.input_order)

    # 2. Инициализируем состояние по каждому инженеру в порядке данных.
    states: List[_RouteState] = [RouteState(e) for e in engineers]

    # 3. Основной цикл.
    result_jobs: List[Job] = []

    for job in jobs_sorted:
        # Пропускаем отменённые/выполненные — они не участвуют.
        if not _is_assignable(job):
            result_jobs.append(job)
            continue

        placed = False
        last_reason = cons.NO_ENGINEER   # дефолт: не нашли никого
        last_arrive_min = None
        last_depart_min = None
        last_travel_min = 0
        last_km = 0.0
        last_engineer_id = None

        for st in states:
            result = cons.can_assign(
                job=job,
                engineer=st.engineer,
                prev_point=st.point,
                ready_min=st.ready_min,
                window_start_min=min_of_day(job.window_start),
                window_end_min=min_of_day(job.window_end),
                shift_start_min=min_of_day(st.engineer.shift_start),
                shift_end_min=min_of_day(st.engineer.shift_end),
                inventory_remaining=st.inventory,
                check_equipment=check_equipment,
            )

            if result.ok:
                # --- назначаем ---
                stop = RouteStop(
                    job_id=job.id,
                    arrival=fmt_hhmm(result.arrive_min),
                    departure=fmt_hhmm(result.depart_min),
                    travel_min_from_prev=result.travel_min,
                    distance_km_from_prev=round(result.distance_km, 3),
                )
                st.stops.append(stop)
                st.distance_km += result.distance_km
                st.ready_min = result.depart_min
                st.point = list(job.coords)
                st.location_id = job.location_id or f"{job.id}:loc"

                # Списываем оборудование, если учитываем.
                if check_equipment:
                    cons.apply_inventory_consumption(job, st.inventory)

                # Обновляем заявку.
                updated = job.model_copy(update={
                    "assigned_engineer_id": st.engineer.id,
                    "status": "assigned",
                    "arrive_planned": fmt_hhmm(result.arrive_min),
                    "depart_planned": fmt_hhmm(result.depart_min),
                })
                # Объяснение назначения — с контекстом «первый подходящий».
                updated = updated.model_copy(update={
                    "explanation": expl.explain_assignment(
                        job=updated,
                        engineer=st.engineer,
                        arrive_min=result.arrive_min,
                        depart_min=result.depart_min,
                        distance_km=result.distance_km,
                        travel_min=result.travel_min,
                        new_route=(len(st.stops) == 1),
                        baseline=True,
                    ),
                })
                result_jobs.append(updated)
                placed = True
                break

            # Запоминаем причину последнего отказа — если никого не найдём,
            # объясним самую информативную (обычно это NO_SKILL или
            # OUT_OF_WINDOW). Логика: NO_SKILL важнее NO_ENGINEER,
            # OUT_OF_WINDOW важнее OUT_OF_SHIFT для диспетчера.
            last_reason, last_arrive_min, last_depart_min, last_travel_min, last_km, last_engineer_id = \
                _pick_better_reason(
                    current=(last_reason, last_arrive_min, last_depart_min,
                             last_travel_min, last_km, last_engineer_id),
                    new=(result.reason, result.arrive_min, result.depart_min,
                         result.travel_min, result.distance_km, st.engineer.id),
                )

        if not placed:
            updated = job.model_copy(update={
                "status": "unassigned",
                "assigned_engineer_id": None,
                "explanation": expl.explain_unassigned(
                    job=job,
                    reason_code=last_reason,
                    engineers=engineers,
                    arrive_min=last_arrive_min,
                    window_end_min=min_of_day(job.window_end),
                    shift_end_min=(min_of_day(engineers[0].shift_end)
                                   if engineers else None),
                ),
            })
            result_jobs.append(updated)

    # 4. Собираем маршруты.
    routes = [st.to_route() for st in states]

    # 5. Формируем Plan с временными пустыми метриками.
    plan = Plan(
        jobs=result_jobs,
        engineers=engineers,
        routes=routes,
        metrics=PlanMetrics(
            engineers_used=0,
            total_distance_km=0.0,
            unassigned_count=0,
            per_engineer_distance_km={},
        ),
    )

    # 6. Считаем метрики и подменяем.
    plan = plan.model_copy(update={"metrics": metrics_mod.compute(plan)})
    return plan


# ---------------------------------------------------------------------------
# Служебное
# ---------------------------------------------------------------------------

def _finalize_empty(jobs: List[Job], engineers: List[Engineer]) -> Plan:
    """Случай: инженеров нет вообще. Все assignable-заявки unassigned."""
    result_jobs: List[Job] = []
    for job in sorted(jobs, key=lambda j: j.input_order):
        if not _is_assignable(job):
            result_jobs.append(job)
            continue
        result_jobs.append(job.model_copy(update={
            "status": "unassigned",
            "assigned_engineer_id": None,
            "explanation": expl.explain_unassigned(
                job=job, reason_code=cons.NO_ENGINEER,
                engineers=[], arrive_min=None,
                window_end_min=min_of_day(job.window_end),
                shift_end_min=None,
            ),
        }))
    plan = Plan(
        jobs=result_jobs,
        engineers=engineers,
        routes=[],
        metrics=PlanMetrics(
            engineers_used=0,
            total_distance_km=0.0,
            unassigned_count=sum(1 for j in result_jobs
                                 if j.status == "unassigned"),
            per_engineer_distance_km={},
        ),
    )
    return plan


# Приоритет причин отказа: чем выше в списке, тем важнее для диспетчера.
_REASON_PRIORITY = {
    cons.NO_SKILL:       6,   # совсем нет нужного навыка
    cons.NO_VEHICLE:     5,   # совсем нет нужного транспорта
    cons.NO_EQUIPMENT:   4,   # нет оборудования
    cons.OUT_OF_WINDOW:  3,   # есть кому, но не успевает в окно
    cons.OUT_OF_SHIFT:   2,   # не укладывается в смену
    cons.NO_ENGINEER:    1,   # все заняты/недоступны — самый слабый аргумент
}


def _pick_better_reason(current, new):
    """
    Выбирает более информативную причину отказа из двух.
    current/new = (reason, arrive_min, depart_min, travel_min, km, engineer_id)
    """
    cur_reason = current[0]
    new_reason = new[0]
    if _REASON_PRIORITY.get(new_reason, 0) > _REASON_PRIORITY.get(cur_reason, 0):
        return new
    return current
