# НАЗНАЧЕНИЕ: основной алгоритм распределения и маршрутизации.
#
# ВХОД:
#   List[Job], List[Engineer]
#
# ВЫХОД:
#   Plan: routes, metrics, explanations, baseline_metrics.
#
# СВЯЗИ:
#   - использует constraints.py, distance.py, metrics.py, explainer.py
#   - baseline.py импортирует RouteState, min_of_day, fmt_hhmm ОТСЮДА.
#     Поэтому baseline нельзя импортировать на уровне модуля —
#     только ленивым импортом внутри optimize() (см. compare_baseline).
#   - вызывается из api/routes_optimize.py
#
# АЛГОРИТМ (greedy):
#   Фаза A: сортировка заявок (priority desc, window_end asc, duration desc).
#   Фаза B: best insertion — лучшая (маршрут × позиция) или новый инженер.
#   Фаза C: локальные улучшения в порядке лексикографической цели:
#           free_engineer → 2-opt → swap. Не более 50 итераций.
#
# ЦЕЛЕВАЯ ФУНКЦИЯ (лексикографическая, ТЗ п.2.3):
#   1. Минимизировать количество задействованных инженеров.
#   2. При равенстве — минимизировать суммарный пробег.
#
# ГАРАНТИИ:
#   - Не хуже baseline по обеим метрикам (проверяется тестами).
#   - Математический оптимум не гарантируется.
#
# ДОПУЩЕНИЯ:
#   - check_equipment=False по умолчанию (см. README).
#   - Возврат в стартовую точку не требуется.
#   - duration_min — время на объекте без дороги.

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from ..config import settings
from ..schemas.job import Job
from ..schemas.engineer import Engineer
from ..schemas.plan import Plan, PlanMetrics, EngineerRoute, RouteStop
from . import constraints as cons
from . import distance as dist_mod
from . import metrics as metrics_mod
from . import explainer as expl


# ---------------------------------------------------------------------------
# Общие утилиты (импортируются baseline.py)
# ---------------------------------------------------------------------------

def min_of_day(dt) -> int:
    """Минуты от начала суток для datetime."""
    return dt.hour * 60 + dt.minute


def fmt_hhmm(day_min: int) -> str:
    """Минуты от начала суток → 'HH:MM'."""
    day_min = day_min % (24 * 60)
    return f"{day_min // 60:02d}:{day_min % 60:02d}"


def hhmm_to_min(s: str) -> int:
    """'HH:MM' → минуты от начала суток."""
    h, m = s.split(":")
    return int(h) * 60 + int(m)


class RouteState:
    """
    Мутируемое состояние маршрута одного инженера.
    Живёт только во время построения плана.
    Импортируется в baseline.py.
    """

    __slots__ = (
        "engineer", "ready_min", "point", "location_id",
        "stops", "distance_km", "inventory",
    )

    def __init__(self, engineer: Engineer) -> None:
        self.engineer = engineer
        self.ready_min: int = min_of_day(engineer.shift_start)
        self.point = list(engineer.start_point)
        self.location_id: str = (
            engineer.start_location_id or f"{engineer.id}:start"
        )
        self.stops: List[RouteStop] = []
        self.distance_km: float = 0.0
        self.inventory: Dict[str, int] = dict(engineer.inventory_start)

    def to_route(self) -> EngineerRoute:
        return EngineerRoute(
            engineer_id=self.engineer.id,
            stops=self.stops,
            distance_km=round(self.distance_km, 3),
            jobs_count=len(self.stops),
        )


# ---------------------------------------------------------------------------
# Глобальный индекс заявок (на время работы optimize)
# ---------------------------------------------------------------------------

_JOBS_INDEX: Dict[str, Job] = {}


def _job_by_id(job_id: str) -> Job:
    job = _JOBS_INDEX.get(job_id)
    if job is None:
        raise KeyError(f"Заявка {job_id} не найдена в _JOBS_INDEX")
    return job


# ---------------------------------------------------------------------------
# Приоритеты
# ---------------------------------------------------------------------------

_PRIORITY_WEIGHT = {
    "Срочная": 10,
    "Обычная": 1,
}


# ---------------------------------------------------------------------------
# Публичный интерфейс
# ---------------------------------------------------------------------------

def optimize(
    jobs: List[Job],
    engineers: List[Engineer],
    *,
    check_equipment: bool = False,
    compare_baseline: bool = True,
) -> Plan:
    """
    Основной вход. Строит план жадно + локальные улучшения.
    """
    global _JOBS_INDEX
    _JOBS_INDEX = {j.id: j for j in jobs}

    try:
        started_at = time.time()

        # 0. Разделяем заявки: активные и «сквозные» (cancelled/done).
        active_jobs = [j for j in jobs if j.status in ("planned", "assigned")]
        passive_jobs = [j for j in jobs if j.status not in ("planned", "assigned")]

        # 1. Фаза A+B: жадное построение.
        states, unassigned_jobs = _build_greedy(
            active_jobs, engineers, check_equipment=check_equipment,
        )

        # 2. Фаза C: локальные улучшения.
        states = _local_search(
            states,
            deadline=started_at + settings.optimizer_time_limit_sec,
            check_equipment=check_equipment,
        )

        # 3. Сборка плана.
        plan = _finalize(states, engineers, active_jobs, passive_jobs)

        # 4. Объяснения.
        plan = _fill_explanations(plan, engineers)

        # 5. Сравнение с baseline (ленивый импорт — избегаем цикла).
        if compare_baseline:
            from . import baseline as baseline_mod
            baseline_plan = baseline_mod.build_baseline_plan(
                jobs, engineers, check_equipment=check_equipment,
            )
            plan = plan.model_copy(update={
                "baseline_metrics": baseline_plan.metrics,
            })

        return plan
    finally:
        _JOBS_INDEX = {}


# ---------------------------------------------------------------------------
# Фаза A+B: жадное построение
# ---------------------------------------------------------------------------

def _job_sort_key(job: Job) -> Tuple[int, int, int]:
    """
    Срочные раньше; при равенстве — более ранний конец окна;
    при равенстве — более длительная работа.
    """
    return (
        -_PRIORITY_WEIGHT.get(job.priority, 1),
        min_of_day(job.window_end),
        -job.duration_min,
    )


def _build_greedy(
    jobs: List[Job],
    engineers: List[Engineer],
    *,
    check_equipment: bool,
) -> Tuple[List[RouteState], List[Job]]:
    """Жадное распределение. Возвращает (states, unassigned_jobs)."""
    states: List[RouteState] = [RouteState(e) for e in engineers]
    ordered = sorted(jobs, key=_job_sort_key)
    unassigned: List[Job] = []

    for job in ordered:
        # 1. Best insertion.
        best = _find_best_insertion(job, states, check_equipment=check_equipment)
        if best is not None:
            st, pos, result = best
            _apply_insertion(st, job, pos, result, check_equipment=check_equipment)
            continue

        # 2. Новый инженер.
        new_st, first_result = _open_new_engineer(
            job, states, check_equipment=check_equipment,
        )
        if new_st is not None:
            _apply_insertion(
                new_st, job, 0, first_result, check_equipment=check_equipment,
            )
            continue

        # 3. Никуда.
        unassigned.append(job)

    return states, unassigned


def _find_best_insertion(
    job: Job,
    states: List[RouteState],
    *,
    check_equipment: bool,
) -> Optional[Tuple[RouteState, int, cons.CheckResult]]:
    """
    Ищет лучшую позицию по всем маршрутам.
    Возвращает (state, position, CheckResult) или None.
    Лучшая — минимальный прирост километров.
    """
    best: Optional[Tuple[RouteState, int, cons.CheckResult, float]] = None

    for st in states:
        if st.engineer.status != "Доступен":
            continue

        n = len(st.stops)
        for pos in range(n + 1):
            # prev_point и ready_min для позиции pos.
            if pos == 0:
                prev_point = st.engineer.start_point
                prev_ready = min_of_day(st.engineer.shift_start)
            else:
                prev_stop = st.stops[pos - 1]
                prev_job = _job_by_id(prev_stop.job_id)
                prev_point = prev_job.coords
                prev_ready = hhmm_to_min(prev_stop.departure)

            result = cons.can_assign(
                job=job,
                engineer=st.engineer,
                prev_point=prev_point,
                ready_min=prev_ready,
                window_start_min=min_of_day(job.window_start),
                window_end_min=min_of_day(job.window_end),
                shift_start_min=min_of_day(st.engineer.shift_start),
                shift_end_min=min_of_day(st.engineer.shift_end),
                inventory_remaining=st.inventory,
                check_equipment=check_equipment,
            )
            if not result.ok:
                continue

            # Каскадная проверка: не сломает ли вставка окна последующих.
            if not _cascade_ok(st, job, pos, result):
                continue

            delta_km = _insertion_delta_km(st, job, pos, result)
            if best is None or delta_km < best[3] - 1e-9:
                best = (st, pos, result, delta_km)

    if best is None:
        return None
    return best[0], best[1], best[2]


def _insertion_delta_km(
    st: RouteState,
    job: Job,
    pos: int,
    result: cons.CheckResult,
) -> float:
    """Прирост километров при вставке на позицию pos."""
    if pos == len(st.stops):
        # Вставка в конец — стоимость = поездка от prev к job.
        return result.distance_km

    next_stop = st.stops[pos]
    next_job = _job_by_id(next_stop.job_id)
    km_job_next, _ = dist_mod.travel(
        job.coords, next_job.coords, st.engineer.vehicle,
        depart_min=result.depart_min,
    )
    return result.distance_km + km_job_next - next_stop.distance_km_from_prev


def _cascade_ok(
    st: RouteState,
    job: Job,
    pos: int,
    first_result: cons.CheckResult,
) -> bool:
    """
    Симулирует пересчёт последующих стопов.
    Возвращает False, если хотя бы одна заявка выходит за окно или смену.
    """
    if pos == len(st.stops):
        return True

    current_ready = first_result.depart_min
    current_point = job.coords

    for stop in st.stops[pos:]:
        next_job = _job_by_id(stop.job_id)
        r = cons.can_assign(
            job=next_job,
            engineer=st.engineer,
            prev_point=current_point,
            ready_min=current_ready,
            window_start_min=min_of_day(next_job.window_start),
            window_end_min=min_of_day(next_job.window_end),
            shift_start_min=min_of_day(st.engineer.shift_start),
            shift_end_min=min_of_day(st.engineer.shift_end),
            check_equipment=False,   # инвентарь уже проверен в первой вставке
        )
        if not r.ok:
            return False
        current_ready = r.depart_min
        current_point = next_job.coords

    return True


def _apply_insertion(
    st: RouteState,
    job: Job,
    pos: int,
    result: cons.CheckResult,
    *,
    check_equipment: bool,
) -> None:
    """
    Физически вставляет заявку в маршрут.
    Пересчитывает километры и время начиная с позиции вставки.
    """
    new_stop = RouteStop(
        job_id=job.id,
        arrival=fmt_hhmm(result.arrive_min),
        departure=fmt_hhmm(result.depart_min),
        travel_min_from_prev=result.travel_min,
        distance_km_from_prev=round(result.distance_km, 3),
    )
    new_stops = st.stops[:pos] + [new_stop] + st.stops[pos:]

    # Начальные условия до позиции вставки не меняются.
    if pos == 0:
        current_point = st.engineer.start_point
        current_ready = min_of_day(st.engineer.shift_start)
    else:
        prev_stop = new_stops[pos - 1]
        prev_job = _job_by_id(prev_stop.job_id)
        current_point = prev_job.coords
        current_ready = hhmm_to_min(prev_stop.departure)

    # Пересчитываем километры начиная с позиции вставки.
    rebuilt: List[RouteStop] = []
    total_km = 0.0

    # Стопы до позиции вставки переносим как есть.
    for i in range(pos):
        stop = new_stops[i]
        rebuilt.append(stop)
        total_km += stop.distance_km_from_prev

    # Стопы от позиции вставки — пересчитываем.
    for i in range(pos, len(new_stops)):
        stop = new_stops[i]
        stop_job = _job_by_id(stop.job_id)
        km, travel_min = dist_mod.travel(
            current_point, stop_job.coords, st.engineer.vehicle,
            depart_min=current_ready,
        )
        rebuilt.append(RouteStop(
            job_id=stop.job_id,
            arrival=stop.arrival,
            departure=stop.departure,
            travel_min_from_prev=travel_min,
            distance_km_from_prev=round(km, 3),
        ))
        total_km += km
        current_point = stop_job.coords
        current_ready = hhmm_to_min(stop.departure)

    st.stops = rebuilt
    st.distance_km = total_km
    st.ready_min = current_ready
    st.point = current_point

    if check_equipment:
        cons.apply_inventory_consumption(job, st.inventory)


def _open_new_engineer(
    job: Job,
    states: List[RouteState],
    *,
    check_equipment: bool,
) -> Tuple[Optional[RouteState], Optional[cons.CheckResult]]:
    """
    Ищет незадействованного инженера, который может взять заявку.
    Возвращает (state, CheckResult) или (None, None).
    """
    for st in states:
        if st.stops:
            continue
        if st.engineer.status != "Доступен":
            continue
        r = cons.can_assign(
            job=job,
            engineer=st.engineer,
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
            return st, r
    return None, None


# ---------------------------------------------------------------------------
# Фаза C: локальные улучшения
# ---------------------------------------------------------------------------

_LOCAL_SEARCH_MAX_ITER = 50


def _local_search(
    states: List[RouteState],
    *,
    deadline: float,
    check_equipment: bool,
) -> List[RouteState]:
    """
    Локальные улучшения в порядке лексикографической цели:
      1. free_engineer — освободить целого инженера.
      2. 2-opt — уменьшить пробег внутри маршрута.
      3. swap — обмен заявок между маршрутами.
    Останавливается, когда нет улучшений или кончилось время.
    """
    for _ in range(_LOCAL_SEARCH_MAX_ITER):
        if time.time() > deadline:
            break

        if _try_free_engineer(states, check_equipment=check_equipment):
            continue
        if _try_2opt_any(states):
            continue
        if _try_swap_any(states, check_equipment=check_equipment):
            continue
        break

    return states


def _try_free_engineer(states: List[RouteState], *, check_equipment: bool) -> bool:
    """
    Пытается освободить одного инженера: все его заявки relocate в другие.
    Возвращает True, если удалось.
    """
    used = [s for s in states if s.stops]
    if len(used) <= 1:
        return False

    # Пробуем сначала самых «дешёвых» — с наименьшим числом стопов.
    used_sorted = sorted(used, key=lambda s: len(s.stops))
    for victim in used_sorted:
        if _try_relocate_all(victim, states, check_equipment=check_equipment):
            return True
    return False


def _try_relocate_all(
    victim: RouteState,
    states: List[RouteState],
    *,
    check_equipment: bool,
) -> bool:
    """Пробует перенести все заявки victim. Если хотя бы одна не влезла — откат."""
    victim_jobs = [_job_by_id(stop.job_id) for stop in victim.stops]
    others = [
        s for s in states
        if s is not victim and s.engineer.status == "Доступен"
    ]

    snapshot = _snapshot(states)

    for job in victim_jobs:
        placed = False
        for st in others:
            best = _find_best_insertion(job, [st], check_equipment=check_equipment)
            if best is not None:
                _, pos, r = best
                _apply_insertion(st, job, pos, r, check_equipment=check_equipment)
                placed = True
                break
        if not placed:
            _restore(states, snapshot)
            return False

    # Все перенеслись — обнуляем victim.
    victim.stops = []
    victim.distance_km = 0.0
    victim.ready_min = min_of_day(victim.engineer.shift_start)
    victim.point = list(victim.engineer.start_point)
    victim.inventory = dict(victim.engineer.inventory_start)
    return True


def _try_2opt_any(states: List[RouteState]) -> bool:
    """2-opt в любом маршруте, где это уменьшает пробег."""
    for st in states:
        if len(st.stops) < 3:
            continue
        if _try_2opt_one(st):
            return True
    return False


def _try_2opt_one(st: RouteState) -> bool:
    """
    2-opt: переворачиваем сегмент [i..j]. Принимаем только если:
      - окна не ломаются;
      - пробег уменьшается.
    При применении обновляем ready_min и point по последнему стопу.
    """
    n = len(st.stops)
    for i in range(n - 1):
        for j in range(i + 1, n):
            candidate = (
                st.stops[:i]
                + list(reversed(st.stops[i:j + 1]))
                + st.stops[j + 1:]
            )
            new_km, ok = _evaluate_order(st, candidate)
            if not ok:
                continue
            if new_km + 1e-6 >= st.distance_km:
                continue

            st.stops = candidate
            st.distance_km = new_km
            last_job = _job_by_id(candidate[-1].job_id)
            st.ready_min = hhmm_to_min(candidate[-1].departure)
            st.point = list(last_job.coords)
            return True
    return False


def _evaluate_order(
    st: RouteState,
    new_stops: List[RouteStop],
) -> Tuple[float, bool]:
    """
    Считает пробег и проверяет ограничения для нового порядка стопов.
    Возвращает (km, ok).
    """
    current_point = st.engineer.start_point
    current_ready = min_of_day(st.engineer.shift_start)
    total_km = 0.0

    for stop in new_stops:
        job = _job_by_id(stop.job_id)
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
        if not r.ok:
            return 0.0, False
        total_km += r.distance_km
        current_point = job.coords
        current_ready = r.depart_min

    return total_km, True


def _try_swap_any(states: List[RouteState], *, check_equipment: bool) -> bool:
    """Swap между двумя маршрутами, если это уменьшает суммарный пробег."""
    used = [s for s in states if s.stops]
    for i, a in enumerate(used):
        for b in used[i + 1:]:
            for ia in range(len(a.stops)):
                for ib in range(len(b.stops)):
                    if _try_swap_pair(a, ia, b, ib):
                        return True
    return False


def _try_swap_pair(
    a: RouteState,
    ia: int,
    b: RouteState,
    ib: int,
) -> bool:
    """Пробует поменять a.stops[ia] и b.stops[ib]. Меняет состояние при успехе."""
    job_a = _job_by_id(a.stops[ia].job_id)
    job_b = _job_by_id(b.stops[ib].job_id)

    # Проверяем, что навыки и транспорт подходят для «чужой» заявки.
    if job_b.skill not in a.engineer.skills:
        return False
    if job_a.skill not in b.engineer.skills:
        return False
    if job_b.required_vehicle and job_b.required_vehicle != a.engineer.vehicle:
        return False
    if job_a.required_vehicle and job_a.required_vehicle != b.engineer.vehicle:
        return False

    old_km_a, old_km_b = a.distance_km, b.distance_km

    new_stops_a = list(a.stops)
    new_stops_b = list(b.stops)
    new_stops_a[ia] = b.stops[ib]
    new_stops_b[ib] = a.stops[ia]

    new_km_a, ok_a = _evaluate_order(a, new_stops_a)
    if not ok_a:
        return False
    new_km_b, ok_b = _evaluate_order(b, new_stops_b)
    if not ok_b:
        return False

    if new_km_a + new_km_b + 1e-6 >= old_km_a + old_km_b:
        return False

    a.stops = new_stops_a
    b.stops = new_stops_b
    a.distance_km = new_km_a
    b.distance_km = new_km_b

    # Обновляем ready_min и point.
    last_a = _job_by_id(new_stops_a[-1].job_id)
    a.ready_min = hhmm_to_min(new_stops_a[-1].departure)
    a.point = list(last_a.coords)

    last_b = _job_by_id(new_stops_b[-1].job_id)
    b.ready_min = hhmm_to_min(new_stops_b[-1].departure)
    b.point = list(last_b.coords)

    return True


# ---------------------------------------------------------------------------
# Финализация
# ---------------------------------------------------------------------------

def _finalize(
    states: List[RouteState],
    engineers: List[Engineer],
    active_jobs: List[Job],
    passive_jobs: List[Job],
) -> Plan:
    """Собирает Plan: назначает статусы, формирует EngineerRoute, считает метрики."""
    # Карта: job_id → (engineer_id, stop).
    assignment: Dict[str, Tuple[str, RouteStop]] = {}
    for st in states:
        for stop in st.stops:
            assignment[stop.job_id] = (st.engineer.id, stop)

    result_jobs: List[Job] = []

    for job in active_jobs:
        if job.id in assignment:
            eid, stop = assignment[job.id]
            result_jobs.append(job.model_copy(update={
                "status": "assigned",
                "assigned_engineer_id": eid,
                "arrive_planned": stop.arrival,
                "depart_planned": stop.departure,
            }))
        else:
            result_jobs.append(job.model_copy(update={
                "status": "unassigned",
                "assigned_engineer_id": None,
            }))

    result_jobs.extend(passive_jobs)

    routes = [st.to_route() for st in states]

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
    plan = plan.model_copy(update={"metrics": metrics_mod.compute(plan)})
    return plan


# ---------------------------------------------------------------------------
# Объяснения
# ---------------------------------------------------------------------------

def _fill_explanations(plan: Plan, engineers: List[Engineer]) -> Plan:
    """Финальный проход: заполняет explanation каждой заявки."""
    eng_by_id = {e.id: e for e in engineers}

    # Индекс: job_id → RouteStop (для доступа к пробегу и времени).
    stop_index: Dict[str, RouteStop] = {}
    route_for_job: Dict[str, str] = {}
    first_in_route: set = set()
    for r in plan.routes:
        for idx, stop in enumerate(r.stops):
            stop_index[stop.job_id] = stop
            route_for_job[stop.job_id] = r.engineer_id
            if idx == 0:
                first_in_route.add(stop.job_id)

    updated_jobs: List[Job] = []

    for job in plan.jobs:
        if job.status == "assigned" and job.assigned_engineer_id:
            e = eng_by_id.get(job.assigned_engineer_id)
            if e is None:
                updated_jobs.append(job)
                continue

            stop = stop_index.get(job.id)
            arrive_min = hhmm_to_min(job.arrive_planned) if job.arrive_planned else 0
            depart_min = hhmm_to_min(job.depart_planned) if job.depart_planned else 0
            explanation = expl.explain_assignment(
                job=job,
                engineer=e,
                arrive_min=arrive_min,
                depart_min=depart_min,
                distance_km=stop.distance_km_from_prev if stop else 0.0,
                travel_min=stop.travel_min_from_prev if stop else 0,
                new_route=job.id in first_in_route,
                baseline=False,
            )
            updated_jobs.append(job.model_copy(update={"explanation": explanation}))

        elif job.status == "unassigned":
            reason = _infer_unassigned_reason(job, engineers)
            explanation = expl.explain_unassigned(
                job=job,
                reason_code=reason,
                engineers=engineers,
                arrive_min=None,
                window_end_min=min_of_day(job.window_end),
                shift_end_min=None,
            )
            updated_jobs.append(job.model_copy(update={"explanation": explanation}))

        else:
            updated_jobs.append(job)

    return plan.model_copy(update={"jobs": updated_jobs})


def _infer_unassigned_reason(job: Job, engineers: List[Engineer]) -> str:
    """Восстанавливает наиболее вероятную причину отказа для explainer."""
    if not any(job.skill in e.skills for e in engineers):
        return cons.NO_SKILL
    if job.required_vehicle and not any(
        job.required_vehicle == e.vehicle
        for e in engineers if job.skill in e.skills
    ):
        return cons.NO_VEHICLE
    if not any(e.status == "Доступен" for e in engineers):
        return cons.NO_ENGINEER
    return cons.OUT_OF_WINDOW


# ---------------------------------------------------------------------------
# Снимок/откат для relocate
# ---------------------------------------------------------------------------

def _snapshot(states: List[RouteState]) -> list:
    """Снимок состояния маршрутов для отката."""
    snap = []
    for st in states:
        snap.append({
            "stops": list(st.stops),
            "distance_km": st.distance_km,
            "ready_min": st.ready_min,
            "point": list(st.point),
            "inventory": dict(st.inventory),
        })
    return snap


def _restore(states: List[RouteState], snapshot: list) -> None:
    for st, s in zip(states, snapshot):
        st.stops = s["stops"]
        st.distance_km = s["distance_km"]
        st.ready_min = s["ready_min"]
        st.point = s["point"]
        st.inventory = s["inventory"]
