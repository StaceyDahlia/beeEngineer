# НАЗНАЧЕНИЕ: перепланирование при событии в течение дня.
#
# ВХОД:
#   Plan + Event.
#
# ВЫХОД:
#   Новый Plan со встроенными:
#     - changed_job_ids: список id изменившихся заявок
#     - diff_summary:    {added, removed, moved, newly_unassigned, cancelled}
#     - needs_review:    id заявок, требующих решения диспетчера
#     - warnings:        структурированные предупреждения (PlanWarning)
#
# СВЯЗИ:
#   - вызывается из api/routes_replan.py
#   - использует constraints.py, distance.py, metrics.py, explainer.py
#   - делегирует optimizer.optimize при полном пересчёте
#   - использует RouteState и служебные функции из optimizer.py
#
# ЛОГИКА:
#   1. Применяем событие к jobs/engineers.
#   2. Реконструируем состояния маршрутов из старого плана.
#   3. _prune_invalid_stops: оставляем все стопы, которые всё ещё
#      валидны по окнам/смене; удаляем только сломавшиеся.
#      Удалённые заявки идут в пул на перераспределение.
#   4. Если пул непустой — инкрементальная вставка.
#   5. Если после инкремента остались unassigned — полный пересчёт
#      optimizer'ом с заморозкой префиксов.
#   6. Собираем diff, needs_review, warnings.
#
# СТАТУСЫ ЗАЯВОК:
#   frozen   = in_progress | done      — НИКОГДА не переназначаем
#   active   = planned | assigned | unassigned
#   passive  = cancelled
#
# ОСОБЫЕ СЛУЧАИ:
#   - engineer_unavailable с in_progress-заявками: событие применяется,
#     инженер помечается недоступным; его in_progress-заявки остаются
#     на нём и попадают в needs_review + warnings.
#   - cancel_job для frozen-заявки игнорируется с warning.
#   - Событие engineer_unavailable применяется всегда, даже если у
#     инженера есть frozen-заявки (диспетчер должен знать).
#
# ДОПУЩЕНИЯ:
#   - check_equipment=False по умолчанию.
#   - Возврат в стартовую точку не требуется.

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from ..schemas.job import Job
from ..schemas.engineer import Engineer
from ..schemas.plan import (
    Plan, PlanMetrics, EngineerRoute, RouteStop, PlanWarning,
)
from ..schemas.event import (
    Event, UrgentJobPayload, CancelJobPayload, EngineerUnavailablePayload,
)
from . import constraints as cons
from . import distance as dist_mod
from . import metrics as metrics_mod
from .optimizer import (
    RouteState, optimize,
    min_of_day, fmt_hhmm, hhmm_to_min,
    _find_best_insertion, _apply_insertion, _job_by_id, _fill_explanations,
)


# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

FROZEN_STATUSES = {"in_progress", "done"}
ACTIVE_STATUSES = {"planned", "assigned", "unassigned"}
PASSIVE_STATUSES = {"cancelled"}

_PRIORITY_WEIGHT = {"Срочная": 10, "Обычная": 1}


# ---------------------------------------------------------------------------
# Публичный интерфейс
# ---------------------------------------------------------------------------

def replan(
    plan: Plan,
    event: Event,
    *,
    check_equipment: bool = False,
) -> Plan:
    """
    Обрабатывает одно событие на существующем плане.
    Возвращает новый Plan с заполненными changed_job_ids,
    diff_summary, needs_review, warnings.
    """
    # 1. Копируем входные данные.
    jobs = [j.model_copy() for j in plan.jobs]
    engineers = [e.model_copy() for e in plan.engineers]
    jobs_by_id = {j.id: j for j in jobs}
    eng_by_id = {e.id: e for e in engineers}

    # 2. Применяем событие. Возвращает вспомогательные данные.
    event_result = _apply_event(event, jobs, jobs_by_id, engineers, eng_by_id)

    # 3. Определяем множества статусов.
    frozen_ids: Set[str] = {j.id for j in jobs if j.status in FROZEN_STATUSES}
    cancelled_ids: Set[str] = {j.id for j in jobs if j.status in PASSIVE_STATUSES}
    cancelled_ids |= event_result.cancelled_ids

    # 4. Реконструируем состояния маршрутов из старого плана.
    states = _states_from_plan(plan, engineers, jobs_by_id)

    # 5. Убираем из маршрутов стопы, которые сломались.
    #    Возвращаются id заявок, попавших в пул на перераспределение.
    dropped_ids = _prune_invalid_stops(
        states, jobs_by_id, frozen_ids=frozen_ids,
    )

    # 6. Формируем пул активных заявок для перераспределения:
    #    - dropped (те, что удалили из маршрутов из-за невалидности);
    #    - active-заявки, у которых сейчас нет инженера
    #      (например, после engineer_unavailable);
    #    - срочные новые (urgent_job).
    pool_ids: Set[str] = set(dropped_ids)
    for j in jobs:
        if j.id in frozen_ids or j.id in cancelled_ids:
            continue
        if j.assigned_engineer_id is None and j.status in ACTIVE_STATUSES:
            pool_ids.add(j.id)

    # Дополнительно: если событие urgent_job добавило новую заявку.
    for jid in event_result.new_job_ids:
        pool_ids.add(jid)

    pool_jobs = [jobs_by_id[jid] for jid in pool_ids if jid in jobs_by_id]

    # 7. Инкрементальная вставка пула.
    if pool_jobs:
        still_unassigned = _incremental_insert(
            states, pool_jobs, jobs_by_id,
            check_equipment=check_equipment,
        )
    else:
        still_unassigned = []

    # 8. Собираем промежуточный план.
    new_plan = _finalize(
        states, engineers, jobs,
        frozen_ids=frozen_ids, cancelled_ids=cancelled_ids,
        still_unassigned_ids={j.id for j in still_unassigned},
    )
    new_plan = _fill_explanations(new_plan, engineers)

    # 9. При необходимости — полный пересчёт.
    if still_unassigned or _engineers_used_worse(new_plan, plan):
        optimized_plan = _full_replan(
            jobs, engineers, frozen_ids, cancelled_ids,
            check_equipment=check_equipment,
        )
        if optimized_plan is not None and _is_better(optimized_plan, new_plan):
            new_plan = _fill_explanations(optimized_plan, engineers)

    # 10. Diff, needs_review, warnings.
    changed_ids, diff_summary = _compute_diff(
        old_plan=plan, new_plan=new_plan,
        frozen_ids=frozen_ids, cancelled_ids=cancelled_ids,
    )
    warnings = list(event_result.warnings)
    # Предупреждение о том, что осталось unassigned.
    for jid in diff_summary.get("newly_unassigned", []):
        warnings.append(PlanWarning(
            code="UNASSIGNED_AFTER_REPLAN",
            job_id=jid,
            message="Не удалось перераспределить заявку при перепланировании.",
        ))

    new_plan = new_plan.model_copy(update={
        "changed_job_ids": changed_ids,
        "diff_summary": diff_summary,
        "needs_review": sorted(event_result.needs_review),
        "warnings": warnings,
    })

    return new_plan


def apply_review_decision(
    plan: Plan,
    job_id: str,
    action: str,
    *,
    check_equipment: bool = False,
) -> Plan:
    """
    Применяет решение диспетчера по заявке из needs_review.
      keep     — оставить как есть, убрать из needs_review.
      reassign — попробовать перераспределить через optimizer,
                 заморозив всех остальных.
      cancel   — отменить заявку.
    """
    if action not in ("keep", "reassign", "cancel"):
        raise ValueError(
            f"action должен быть keep|reassign|cancel, получено {action!r}"
        )

    jobs = [j.model_copy() for j in plan.jobs]
    engineers = [e.model_copy() for e in plan.engineers]
    jobs_by_id = {j.id: j for j in jobs}

    if job_id not in jobs_by_id:
        raise KeyError(f"Заявка {job_id} не найдена в плане")

    job = jobs_by_id[job_id]

    if action == "keep":
        # Просто убираем из needs_review.
        new_review = [x for x in (plan.needs_review or []) if x != job_id]
        return plan.model_copy(update={"needs_review": new_review})

    if action == "cancel":
        # Помечаем cancelled, убираем стоп из маршрутов.
        idx = jobs.index(job)
        jobs[idx] = job.model_copy(update={
            "status": "cancelled",
            "assigned_engineer_id": None,
            "arrive_planned": None,
            "depart_planned": None,
        })
        # Удаляем стоп из маршрута.
        new_routes: List[EngineerRoute] = []
        for r in plan.routes:
            stops = [s for s in r.stops if s.job_id != job_id]
            new_routes.append(r.model_copy(update={
                "stops": stops,
                "jobs_count": len(stops),
            }))
        new_plan = plan.model_copy(update={
            "jobs": jobs, "engineers": engineers, "routes": new_routes,
        })
        new_plan = new_plan.model_copy(
            update={"metrics": metrics_mod.compute(new_plan)}
        )
        new_review = [x for x in (plan.needs_review or []) if x != job_id]
        return new_plan.model_copy(update={"needs_review": new_review})

    # action == "reassign"
    # Замораживаем всё, кроме этой заявки, и зовём optimizer.
    frozen_ids: Set[str] = {
        j.id for j in jobs
        if j.id != job_id and j.status in FROZEN_STATUSES
    }
    # Плюс все assigned, кроме проблемной — чтобы optimizer их не трогал.
    for j in jobs:
        if j.id != job_id and j.status == "assigned":
            frozen_ids.add(j.id)

    cancelled_ids: Set[str] = {j.id for j in jobs if j.status == "cancelled"}

    optimized_plan = _full_replan(
        jobs, engineers, frozen_ids, cancelled_ids,
        check_equipment=check_equipment,
    )
    if optimized_plan is None:
        # Не получилось — возвращаем как было, но убираем из needs_review.
        return plan.model_copy(update={
            "needs_review": [x for x in (plan.needs_review or [])
                             if x != job_id],
        })

    optimized_plan = _fill_explanations(optimized_plan, engineers)
    new_review = [x for x in (plan.needs_review or []) if x != job_id]
    return optimized_plan.model_copy(update={"needs_review": new_review})


# ---------------------------------------------------------------------------
# Обработка события
# ---------------------------------------------------------------------------

class _EventResult:
    __slots__ = ("cancelled_ids", "new_job_ids", "needs_review", "warnings")

    def __init__(self) -> None:
        self.cancelled_ids: Set[str] = set()
        self.new_job_ids: Set[str] = set()
        self.needs_review: List[str] = []
        self.warnings: List[PlanWarning] = []


def _apply_event(
    event: Event,
    jobs: List[Job],
    jobs_by_id: Dict[str, Job],
    engineers: List[Engineer],
    eng_by_id: Dict[str, Engineer],
) -> _EventResult:
    """Применяет событие к jobs/engineers. Не трогает маршруты."""
    result = _EventResult()

    if event.type == "urgent_job":
        payload: UrgentJobPayload = event.payload  # type: ignore
        if payload.id in jobs_by_id:
            old = jobs_by_id[payload.id]
            updated = old.model_copy(update={
                "priority": "Срочная",
                "status": "planned",
            })
            idx = jobs.index(old)
            jobs[idx] = updated
            jobs_by_id[payload.id] = updated
        else:
            new_job = payload.model_copy(update={
                "priority": "Срочная",
                "status": "planned",
                "assigned_engineer_id": None,
            })
            jobs.append(new_job)
            jobs_by_id[new_job.id] = new_job
            result.new_job_ids.add(new_job.id)

    elif event.type == "cancel_job":
        payload: CancelJobPayload = event.payload  # type: ignore
        job = jobs_by_id.get(payload.job_id)
        if job is None:
            return result

        if job.status in FROZEN_STATUSES:
            result.warnings.append(PlanWarning(
                code="CANCEL_IGNORED_FROZEN",
                job_id=job.id,
                message=(
                    f"Заявка {job.id} в статусе {job.status}: "
                    f"отмена не применена."
                ),
            ))
            return result

        updated = job.model_copy(update={
            "status": "cancelled",
            "assigned_engineer_id": None,
            "arrive_planned": None,
            "depart_planned": None,
        })
        idx = jobs.index(job)
        jobs[idx] = updated
        jobs_by_id[job.id] = updated
        result.cancelled_ids.add(job.id)

    elif event.type == "engineer_unavailable":
        payload: EngineerUnavailablePayload = event.payload  # type: ignore
        e = eng_by_id.get(payload.engineer_id)
        if e is None:
            return result

        # Помечаем инженера недоступным.
        idx = engineers.index(e)
        engineers[idx] = e.model_copy(update={"status": "Заболел"})
        eng_by_id[e.id] = engineers[idx]

        # Разбираемся с его заявками.
        for j in list(jobs):
            if j.assigned_engineer_id != e.id:
                continue
            if j.status in FROZEN_STATUSES:
                # Заявка в работе — оставляем на инженере, диспетчер решит.
                result.needs_review.append(j.id)
                result.warnings.append(PlanWarning(
                    code="IN_PROGRESS_ENGINEER_UNAVAILABLE",
                    job_id=j.id,
                    engineer_id=e.id,
                    message=(
                        f"Заявка {j.id} выполняется инженером {e.id}, "
                        f"который помечен недоступным."
                    ),
                ))
                continue
            if j.status in ACTIVE_STATUSES:
                updated = j.model_copy(update={
                    "status": "unassigned",
                    "assigned_engineer_id": None,
                    "arrive_planned": None,
                    "depart_planned": None,
                })
                jdx = jobs.index(j)
                jobs[jdx] = updated
                jobs_by_id[j.id] = updated

    return result


# ---------------------------------------------------------------------------
# Реконструкция состояний из старого плана
# ---------------------------------------------------------------------------

def _states_from_plan(
    plan: Plan,
    engineers: List[Engineer],
    jobs_by_id: Dict[str, Job],
) -> List[RouteState]:
    """Превращает EngineerRoute обратно в RouteState."""
    states: List[RouteState] = []
    routes_by_id = {r.engineer_id: r for r in plan.routes}

    for e in engineers:
        st = RouteState(e)
        route = routes_by_id.get(e.id)
        if route is None or not route.stops:
            states.append(st)
            continue

        st.stops = list(route.stops)
        st.distance_km = route.distance_km

        last = st.stops[-1]
        st.ready_min = hhmm_to_min(last.departure)
        last_job = jobs_by_id.get(last.job_id)
        if last_job is not None:
            st.point = list(last_job.coords)
            st.location_id = last_job.location_id or f"{last_job.id}:loc"

        states.append(st)

    return states


# ---------------------------------------------------------------------------
# Прунинг невалидных стопов
# ---------------------------------------------------------------------------

def _prune_invalid_stops(
    states: List[RouteState],
    jobs_by_id: Dict[str, Job],
    *,
    frozen_ids: Set[str],
) -> List[str]:
    """
    Проходит по каждому маршруту слева направо. Стопы:
      - frozen — всегда оставляем;
      - остальные — проверяем can_assign с текущим ready_min/point;
        если окна/смена соблюдаются — оставляем;
        если нет — удаляем этот стоп и заявку возвращаем в пул.
    Возвращает список id заявок, попавших в пул.
    """
    dropped: List[str] = []

    for st in states:
        if not st.stops:
            continue

        kept: List[RouteStop] = []
        current_point = st.engineer.start_point
        current_ready = min_of_day(st.engineer.shift_start)
        total_km = 0.0

        for stop in st.stops:
            job = jobs_by_id.get(stop.job_id)
            if job is None:
                # Заявки нет — стоп невалиден, удаляем.
                dropped.append(stop.job_id)
                continue

            if job.id in frozen_ids:
                # Frozen оставляем как есть; обновляем state по нему.
                kept.append(stop)
                total_km += stop.distance_km_from_prev
                current_point = list(job.coords)
                current_ready = hhmm_to_min(stop.departure)
                continue

            # Проверяем можно ли оставить этот стоп.
            r = cons.can_assign(
                job=job,
                engineer=st.engineer,
                prev_point=current_point,
                ready_min=current_ready,
                window_start_min=min_of_day(job.window_start),
                window_end_min=min_of_day(job.window_end),
                shift_start_min=min_of_day(st.engineer.shift_start),
                shift_end_min=min_of_day(st.engineer.shift_end),
                check_equipment=False,
            )

            if r.ok:
                # Оставляем, пересчитываем километры/время под текущий контекст.
                kept.append(RouteStop(
                    job_id=stop.job_id,
                    arrival=fmt_hhmm(r.arrive_min),
                    departure=fmt_hhmm(r.depart_min),
                    travel_min_from_prev=r.travel_min,
                    distance_km_from_prev=round(r.distance_km, 3),
                ))
                total_km += r.distance_km
                current_point = list(job.coords)
                current_ready = r.depart_min
            else:
                # Удаляем, заявка идёт в пул.
                dropped.append(stop.job_id)

        st.stops = kept
        st.distance_km = total_km
        st.ready_min = current_ready
        st.point = current_point
        if not kept:
            st.ready_min = min_of_day(st.engineer.shift_start)
            st.point = list(st.engineer.start_point)
            st.distance_km = 0.0
            st.inventory = dict(st.engineer.inventory_start)

    return dropped


# ---------------------------------------------------------------------------
# Инкрементальная вставка
# ---------------------------------------------------------------------------

def _incremental_insert(
    states: List[RouteState],
    pool_jobs: List[Job],
    jobs_by_id: Dict[str, Job],
    *,
    check_equipment: bool,
) -> List[Job]:
    """
    Пробует вставить pool_jobs в существующие маршруты.
    Возвращает список заявок, которые никуда не влезли.
    """
    ordered = sorted(
        pool_jobs,
        key=lambda j: (
            -_PRIORITY_WEIGHT.get(j.priority, 1),
            min_of_day(j.window_end),
            -j.duration_min,
        ),
    )

    still_unassigned: List[Job] = []

    for job in ordered:
        best = _find_best_insertion(job, states, check_equipment=check_equipment)
        if best is not None:
            st, pos, r = best
            _apply_insertion(st, job, pos, r, check_equipment=check_equipment)
            continue

        if _try_open_new(job, states, check_equipment=check_equipment):
            continue

        still_unassigned.append(job)

    return still_unassigned


def _try_open_new(
    job: Job,
    states: List[RouteState],
    *,
    check_equipment: bool,
) -> bool:
    """Пробует незадействованного инженера (без стопов)."""
    for st in states:
        if st.stops:
            continue
        if st.engineer.status != "Доступен":
            continue
        r = cons.can_assign(
            job=job, engineer=st.engineer,
            prev_point=st.engineer.start_point,
            ready_min=min_of_day(st.engineer.shift_start),
            window_start_min=min_of_day(job.window_start),
            window_end_min=min_of_day(job.window_end),
            shift_start_min=min_of_day(st.engineer.shift_start),
            shift_end_min=min_of_day(st.engineer.shift_end),
            inventory_remaining=st.inventory,
            check_equipment=check_equipment,
        )
        if r.ok:
            _apply_insertion(st, job, 0, r, check_equipment=check_equipment)
            return True
    return False


# ---------------------------------------------------------------------------
# Полный пересчёт с заморозкой
# ---------------------------------------------------------------------------

def _full_replan(
    jobs: List[Job],
    engineers: List[Engineer],
    frozen_ids: Set[str],
    cancelled_ids: Set[str],
    *,
    check_equipment: bool,
) -> Optional[Plan]:
    """
    Полный пересчёт через optimizer.optimize с заморозкой префиксов.
    Frozen-заявки фиксируются в маршрутах своих инженеров.
    """
    jobs_by_id = {j.id: j for j in jobs}

    # 1. Собираем frozen-заявки по инженерам.
    frozen_by_engineer: Dict[str, List[Job]] = {e.id: [] for e in engineers}
    for jid in frozen_ids:
        j = jobs_by_id.get(jid)
        if j is None or j.assigned_engineer_id is None:
            continue
        frozen_by_engineer[j.assigned_engineer_id].append(j)

    for eid in frozen_by_engineer:
        frozen_by_engineer[eid].sort(
            key=lambda j: hhmm_to_min(j.depart_planned or "00:00")
        )

    # 2. Виртуальные Engineer-копии с подменённым стартом.
    virtual_engineers: List[Engineer] = []
    for e in engineers:
        frozen = frozen_by_engineer.get(e.id, [])
        if not frozen:
            virtual_engineers.append(e)
            continue
        last = frozen[-1]
        last_job = jobs_by_id[last.id]
        virtual = e.model_copy(update={
            "start_point": list(last_job.coords),
            "start_location_id": last_job.location_id,
            "shift_start": last.depart_planned,
            "inventory_start": dict(e.inventory_start),
        })
        virtual_engineers.append(virtual)

    # 3. Активные заявки для optimizer.
    active = [
        j for j in jobs
        if j.id not in frozen_ids and j.id not in cancelled_ids
    ]

    # 4. Optimizer.
    optimized = optimize(
        active, virtual_engineers,
        check_equipment=check_equipment,
        compare_baseline=False,
    )

    # 5. Склеиваем frozen-префиксы и оптимизированные суффиксы.
    new_routes: List[EngineerRoute] = []
    for e in engineers:
        frozen = frozen_by_engineer.get(e.id, [])
        opt_route = next(
            (r for r in optimized.routes if r.engineer_id == e.id), None,
        )

        prefix_stops: List[RouteStop] = []
        for j in frozen:
            prefix_stops.append(RouteStop(
                job_id=j.id,
                arrival=j.arrive_planned or "00:00",
                departure=j.depart_planned or "00:00",
                travel_min_from_prev=0,
                distance_km_from_prev=0.0,
            ))
        suffix_stops = list(opt_route.stops) if opt_route else []
        all_stops = prefix_stops + suffix_stops

        total_km = _recompute_route_km(e, all_stops, jobs_by_id)

        new_routes.append(EngineerRoute(
            engineer_id=e.id,
            stops=all_stops,
            distance_km=round(total_km, 3),
            jobs_count=len(all_stops),
        ))

    # 6. Собираем итоговый список заявок.
    opt_by_id = {j.id: j for j in optimized.jobs}
    final_jobs: List[Job] = []
    for j in jobs:
        if j.id in frozen_ids:
            final_jobs.append(j)
        elif j.id in cancelled_ids:
            final_jobs.append(j.model_copy(update={
                "status": "cancelled", "assigned_engineer_id": None,
            }))
        elif j.id in opt_by_id:
            final_jobs.append(opt_by_id[j.id])
        else:
            final_jobs.append(j)

    plan = Plan(
        jobs=final_jobs,
        engineers=engineers,
        routes=new_routes,
        metrics=PlanMetrics(
            engineers_used=0, total_distance_km=0.0,
            unassigned_count=0, per_engineer_distance_km={},
        ),
    )
    return plan.model_copy(update={"metrics": metrics_mod.compute(plan)})


def _recompute_route_km(
    engineer: Engineer,
    stops: List[RouteStop],
    jobs_by_id: Dict[str, Job],
) -> float:
    """Пересчитывает километры по всему маршруту от start_point инженера."""
    current_point = engineer.start_point
    current_ready = min_of_day(engineer.shift_start)
    total = 0.0

    for stop in stops:
        job = jobs_by_id.get(stop.job_id)
        if job is None:
            continue
        km, _ = dist_mod.travel(
            current_point, job.coords, engineer.vehicle,
            depart_min=current_ready,
        )
        total += km
        current_point = job.coords
        current_ready = hhmm_to_min(stop.departure)

    return total


# ---------------------------------------------------------------------------
# Финализация
# ---------------------------------------------------------------------------

def _finalize(
    states: List[RouteState],
    engineers: List[Engineer],
    jobs: List[Job],
    *,
    frozen_ids: Set[str],
    cancelled_ids: Set[str],
    still_unassigned_ids: Set[str],
) -> Plan:
    """Собирает Plan: назначения из states, статусы — как получилось."""
    # job_id → (engineer_id, stop).
    assignment: Dict[str, Tuple[str, RouteStop]] = {}
    for st in states:
        for stop in st.stops:
            assignment[stop.job_id] = (st.engineer.id, stop)

    final_jobs: List[Job] = []
    for j in jobs:
        if j.id in frozen_ids:
            final_jobs.append(j)
            continue
        if j.id in cancelled_ids:
            final_jobs.append(j.model_copy(update={
                "status": "cancelled",
                "assigned_engineer_id": None,
            }))
            continue
        if j.id in still_unassigned_ids:
            final_jobs.append(j.model_copy(update={
                "status": "unassigned",
                "assigned_engineer_id": None,
                "arrive_planned": None,
                "depart_planned": None,
            }))
            continue
        if j.id in assignment:
            eid, stop = assignment[j.id]
            final_jobs.append(j.model_copy(update={
                "status": "assigned",
                "assigned_engineer_id": eid,
                "arrive_planned": stop.arrival,
                "depart_planned": stop.departure,
            }))
        else:
            # Заявка активна, но её нигде нет.
            final_jobs.append(j.model_copy(update={
                "status": "unassigned",
                "assigned_engineer_id": None,
            }))

    routes = [st.to_route() for st in states]

    plan = Plan(
        jobs=final_jobs,
        engineers=engineers,
        routes=routes,
        metrics=PlanMetrics(
            engineers_used=0, total_distance_km=0.0,
            unassigned_count=0, per_engineer_distance_km={},
        ),
    )
    return plan.model_copy(update={"metrics": metrics_mod.compute(plan)})


# ---------------------------------------------------------------------------
# Сравнение и diff
# ---------------------------------------------------------------------------

def _engineers_used_worse(new_plan: Plan, old_plan: Plan) -> bool:
    return new_plan.metrics.engineers_used > old_plan.metrics.engineers_used


def _is_better(new: Plan, old: Plan) -> bool:
    """Лексикографическое сравнение по ТЗ."""
    a = (new.metrics.unassigned_count,
         new.metrics.engineers_used,
         new.metrics.total_distance_km)
    b = (old.metrics.unassigned_count,
         old.metrics.engineers_used,
         old.metrics.total_distance_km)
    return a < b


def _compute_diff(
    old_plan: Plan,
    new_plan: Plan,
    frozen_ids: Set[str],
    cancelled_ids: Set[str],
) -> Tuple[List[str], Dict[str, List[str]]]:
    """Возвращает (changed_job_ids, diff_summary)."""
    old_by_id = {j.id: j for j in old_plan.jobs}
    changed: List[str] = []
    summary: Dict[str, List[str]] = {
        "added": [], "removed": [], "moved": [],
        "newly_unassigned": [], "cancelled": sorted(cancelled_ids),
    }

    for j_new in new_plan.jobs:
        if j_new.id in frozen_ids or j_new.id in cancelled_ids:
            continue

        j_old = old_by_id.get(j_new.id)
        if j_old is None:
            # Новая заявка (urgent_job).
            if j_new.status == "assigned":
                summary["added"].append(j_new.id)
            else:
                summary["newly_unassigned"].append(j_new.id)
            changed.append(j_new.id)
            continue

        # Смена инженера.
        if j_old.assigned_engineer_id != j_new.assigned_engineer_id:
            if j_new.status == "unassigned":
                summary["newly_unassigned"].append(j_new.id)
            elif j_old.assigned_engineer_id is None:
                summary["added"].append(j_new.id)
            elif j_new.assigned_engineer_id is None:
                summary["removed"].append(j_new.id)
            else:
                summary["moved"].append(j_new.id)
            changed.append(j_new.id)
            continue

        # Тот же инженер, но изменилось время.
        if (j_old.arrive_planned != j_new.arrive_planned
                or j_old.depart_planned != j_new.depart_planned):
            summary["moved"].append(j_new.id)
            changed.append(j_new.id)

    return changed, summary
