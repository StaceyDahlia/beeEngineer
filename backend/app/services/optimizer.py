# НАЗНАЧЕНИЕ: основной алгоритм распределения и маршрутизации.
#
# ВХОД:
#   List[Job], List[Engineer]
#
# ВЫХОД:
#   Plan: routes, metrics, explanations, baseline_metrics.
#
# СВЯЗИ:
#   - использует constraints.py, distance.py, route_state.py
#   - использует metrics.compute для своих метрик
#   - вызывает baseline.build_baseline_plan для comparison
#   - вызывает explainer.py в финальном проходе
#   - вызывается из api/routes_optimize.py
#
# АЛГОРИТМ (greedy, MVP):
#   Фаза A: сортировка заявок по (priority desc, window_end asc, duration desc).
#   Фаза B: best insertion — каждая заявка вставляется в лучшую
#           (маршрут × позицию), если никуда не влезает — новый инженер.
#   Фаза C: локальные улучшения — free_engineer, 2-opt, swap —
#           пока есть улучшения и не исчерпан лимит времени.
#
# ЦЕЛЕВАЯ ФУНКЦИЯ (лексикографическая, ТЗ п.2.3):
#   1. Минимизировать количество задействованных инженеров.
#   2. При равенстве — минимизировать суммарный пробег.
#
# ГАРАНТИИ:
#   - Не хуже baseline по обеим метрикам (проверяется тестами).
#   - Математический оптимум не гарантируется — это эвристика.
#
# ДОПУЩЕНИЯ:
#   - check_equipment=False по умолчанию (см. baseline.py).
#   - Возврат в стартовую точку не требуется.
#   - duration_min — время на объекте без дороги.

from __future__ import annotations

import time
from typing import List, Optional, Tuple

from ..config import settings
from ..schemas.job import Job
from ..schemas.engineer import Engineer
from ..schemas.plan import Plan, PlanMetrics, EngineerRoute, RouteStop
from . import constraints as cons
from . import distance as dist_mod
from . import metrics as metrics_mod
from . import explainer as expl
from . import baseline as baseline_mod
from .route_state import RouteState, min_of_day, fmt_hhmm


# ---------------------------------------------------------------------------
# Приоритеты: чем больше вес, тем раньше обрабатываем
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
    Основной вход. Строит план жадным алгоритмом с локальными улучшениями.
    """
    started_at = time.time()

    # 0. Отфильтровываем не-участвующие заявки (cancelled/done).
    active_jobs = [j for j in jobs if j.status in ("planned", "assigned")]
    passive_jobs = [j for j in jobs if j.status not in ("planned", "assigned")]

    # 1. Фаза A+B: жадное построение.
    states = _build_greedy(active_jobs, engineers, check_equipment=check_equipment)

    # 2. Фаза C: локальные улучшения.
    states = _local_search(
        states,
        engineers,
        deadline=started_at + settings.optimizer_time_limit_sec,
        check_equipment=check_equipment,
    )

    # 3. Сборка плана.
    plan = _finalize(states, engineers, active_jobs, passive_jobs)

    # 4. Объяснения.
    plan = _fill_explanations(plan, engineers)

    # 5. Сравнение с baseline.
    if compare_baseline:
        baseline_plan = baseline_mod.build_baseline_plan(
            jobs, engineers, check_equipment=check_equipment,
        )
        plan = plan.model_copy(update={
            "baseline_metrics": baseline_plan.metrics,
        })

    return plan


# ---------------------------------------------------------------------------
# Фаза A+B: жадное построение
# ---------------------------------------------------------------------------

def _job_sort_key(job: Job) -> Tuple[int, int, int]:
    """
    Ключ сортировки заявок:
      1. Срочные — раньше (weight desc).
      2. Более ранний конец окна — раньше.
      3. Более длительные — раньше (их труднее вставить позже).
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
) -> List[RouteState]:
    """Жадно распределяет заявки. Возвращает состояние по каждому инженеру."""
    states: List[RouteState] = [RouteState(e) for e in engineers]
    states_by_id = {s.engineer.id: s for s in states}

    # Все заявки в отсортированном порядке.
    ordered = sorted(jobs, key=_job_sort_key)

    unassigned: List[Job] = []   # ← сюда кладём не влезшие; вернём для финализации

    for job in ordered:
        # 1. Пробуем best insertion во все существующие маршруты.
        best = _find_best_insertion(job, states, check_equipment=check_equipment)
        if best is not None:
            st, position, result = best
            _apply_insertion(st, job, position, result)
            continue

        # 2. Не влезло никуда. Пробуем открыть нового инженера.
        new_st = _open_new_engineer(job, states, check_equipment=check_equipment)
        if new_st is not None:
            _apply_insertion(new_st, job, 0, _last_check_result(new_st))
            continue

        # 3. Совсем никуда.
        unassigned.append(job)

    # Заявки, которые не влезли, помечаем unassigned прямо здесь,
    # чтобы финализация не потеряла их.
    for job in unassigned:
        job_status = "unassigned"
        # Запоминаем причину — попробуем восстановить по последней попытке.
        # Для простоты: считаем NO_ENGINEER, если никто не подошёл структурно;
        # точная причина будет заполнена в _fill_explanations.
        pass

    # Отдельно: "не влезшие" заявки будут добавлены в _finalize как есть.
    # Сохраняем их в атрибуте states-объекта, чтобы не таскать через сигнатуры.
    # (см. _finalize)
    for st in states:
        st.__dict__.setdefault("_unassigned_jobs", [])
    states[0].__dict__["_unassigned_jobs"] = unassigned
    return states


def _find_best_insertion(
    job: Job,
    states: List[RouteState],
    *,
    check_equipment: bool,
) -> Optional[Tuple[RouteState, int, cons.CheckResult]]:
    """
    Ищет лучшую позицию для вставки заявки во все маршруты.
    Возвращает (state, position, CheckResult) или None.
    Лучшая = минимальный прирост километров.
    """
    best: Optional[Tuple[RouteState, int, cons.CheckResult, float]] = None

    for st in states:
        if st.engineer.status != "Доступен":
            continue

        n = len(st.stops)
        for pos in range(n + 1):
            # prev_point для вставки на позицию pos
            if pos == 0:
                prev_point = st.engineer.start_point
                prev_loc_id = st.engineer.start_location_id or f"{st.engineer.id}:start"
                prev_ready = min_of_day(st.engineer.shift_start)
            else:
                prev_stop = st.stops[pos - 1]
                prev_job = _job_by_id(prev_stop.job_id, job, st)
                prev_point = prev_job.coords
                prev_loc_id = prev_job.location_id or f"{prev_job.id}:loc"
                prev_ready = _hhmm_to_min(prev_stop.departure)

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

            # Каскадная проверка: сдвинутся ли последующие заявки и не сломаются ли окна.
            if not _cascade_ok(st, job, pos, result, check_equipment=check_equipment):
                continue

            # Стоимость = прирост километров.
            delta_km = _insertion_delta_km(st, job, pos, prev_point, result)
            if best is None or delta_km < best[3]:
                best = (st, pos, result, delta_km)

    if best is None:
        return None
    return best[0], best[1], best[2]


def _insertion_delta_km(
    st: RouteState,
    job: Job,
    pos: int,
    prev_point,
    result: cons.CheckResult,
) -> float:
    """Насколько вырастет пробег при вставке."""
    if pos == len(st.stops):
        # Вставка в конец — просто добавляем поездку от prev к job.
        return result.distance_km

    next_stop = st.stops[pos]
    next_job = _job_by_id(next_stop.job_id, job, st)
    km_prev_job = result.distance_km
    km_job_next, _ = dist_mod.travel(
        job.coords, next_job.coords, st.engineer.vehicle,
        depart_min=result.depart_min,
    )
    km_prev_next = next_stop.distance_km_from_prev
    return km_prev_job + km_job_next - km_prev_next


def _cascade_ok(
    st: RouteState,
    job: Job,
    pos: int,
    first_result: cons.CheckResult,
    *,
    check_equipment: bool,
) -> bool:
    """
    Проверяет, что вставка не сломает окна последующих заявок.
    Симулирует пересчёт arrive/depart начиная с позиции pos+1.
    """
    # Если вставка в конец — нечего каскадить.
    if pos == len(st.stops):
        return True

    # Текущее время — момент выезда из вставленной заявки.
    current_ready = first_result.depart_min
    current_point = job.coords
    current_loc_id = job.location_id or f"{job.id}:loc"

    for stop in st.stops[pos:]:
        next_job = _job_by_id(stop.job_id, job, st)
        r = cons.can_assign(
            job=next_job,
            engineer=st.engineer,
            prev_point=current_point,
            ready_min=current_ready,
            window_start_min=min_of_day(next_job.window_start),
            window_end_min=min_of_day(next_job.window_end),
            shift_start_min=min_of_day(st.engineer.shift_start),
            shift_end_min=min_of_day(st.engineer.shift_end),
            inventory_remaining=st.inventory,
            check_equipment=False,   # инвентарь уже учтён ранее
        )
        if not r.ok:
            return False
        current_ready = r.depart_min
        current_point = next_job.coords
        current_loc_id = next_job.location_id or f"{next_job.id}:loc"

    return True


def _apply_insertion(
    st: RouteState,
    job: Job,
    pos: int,
    result: cons.CheckResult,
) -> None:
    """Физически вставляет заявку в маршрут state, обновляя стопы и километры."""
    # Пересчитываем все стопы начиная с позиции pos.
    new_stop = RouteStop(
        job_id=job.id,
        arrival=fmt_hhmm(result.arrive_min),
        departure=fmt_hhmm(result.depart_min),
        travel_min_from_prev=result.travel_min,
        distance_km_from_prev=round(result.distance_km, 3),
    )

    # Собираем новый список стопов.
    new_stops = st.stops[:pos] + [new_stop] + st.stops[pos:]

    # Пересчитываем километры и время прибытия для всех стопов от pos+1.
    current_point = (
        st.engineer.start_point if pos == 0
        else _job_coords(st.stops[pos - 1].job_id, st, job)
    )
    current_ready = (
        min_of_day(st.engineer.shift_start) if pos == 0
        else _hhmm_to_min(st.stops[pos - 1].departure)
    )
    current_loc_id = (
        st.engineer.start_location_id if pos == 0
        else None
    )

    # Пробегаем заново по всем стопам, чтобы получить корректные километры.
    total_km = 0.0
    rebuilt: List[RouteStop] = []
    for i, stop in enumerate(new_stops):
        stop_job = _job_by_id(stop.job_id, job, st)
        km, _ = dist_mod.travel(
            current_point, stop_job.coords, st.engineer.vehicle,
            depart_min=current_ready,
        )
        rebuilt.append(RouteStop(
            job_id=stop.job_id,
            arrival=stop.arrival,
            departure=stop.departure,
            travel_min_from_prev=stop.travel_min_from_prev if i != pos else result.travel_min,
            distance_km_from_prev=round(km, 3),
        ))
        total_km += km
        current_point = stop_job.coords
        current_ready = _hhmm_to_min(stop.departure)

    st.stops = rebuilt
    st.distance_km = total_km
    st.ready_min = current_ready
    st.point = current_point

    if check_equipment := False:
        cons.apply_inventory_consumption(job, st.inventory)


def _open_new_engineer(
    job: Job,
    states: List[RouteState],
    *,
    check_equipment: bool,
) -> Optional[RouteState]:
    """Пытается назначить заявку инженеру, у которого ещё нет заявок."""
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
            # Сохраняем результат в state, чтобы _apply_insertion потом использовал.
            st.__dict__["_pending_first_result"] = r
            return st
    return None


def _last_check_result(st: RouteState) -> cons.CheckResult:
    return st.__dict__.pop("_pending_first_result")


# ---------------------------------------------------------------------------
# Фаза C: локальные улучшения
# ---------------------------------------------------------------------------

def _local_search(
    states: List[RouteState],
    engineers: List[Engineer],
    *,
    deadline: float,
    check_equipment: bool,
) -> List[RouteState]:
    """Пробует три типа улучшений, пока есть прогресс и есть время."""
    max_iterations = 100
    for _ in range(max_iterations):
        if time.time() > deadline:
            break
        improved = False

        # 1. Освободить инженера (приоритетная цель по ТЗ).
        if _try_free_engineer(states, check_equipment=check_equipment):
            improved = True
            continue

        # 2. 2-opt внутри маршрута.
        if _try_2opt_any(states):
            improved = True
            continue

        # 3. Swap между маршрутами.
        if _try_swap_any(states, check_equipment=check_equipment):
            improved = True
            continue

        if not improved:
            break

    return states


def _try_free_engineer(states, *, check_equipment) -> bool:
    """
    Пытается освободить инженера целиком: все его заявки relocate в другие.
    Успех = уменьшение engineers_used.
    """
    used = [s for s in states if s.stops]
    if len(used) <= 1:
        return False

    # Пробуем самого «дешёвого» для освобождения: с наименьшим числом стопов.
    used.sort(key=lambda s: len(s.stops))
    for victim in used:
        if _try_relocate_all(victim, states, check_equipment=check_equipment):
            return True
    return False


def _try_relocate_all(victim, states, *, check_equipment) -> bool:
    """Пробует перенести все заявки victim в другие маршруты."""
    # Снимок состояния victim.
    victim_jobs = [_job_by_id(stop.job_id, None, victim) for stop in victim.stops]
    others = [s for s in states if s is not victim and s.engineer.status == "Доступен"]

    # Простая жадная попытка: по каждой заявке ищем вставку в others.
    # Если хотя бы одна не вставляется — откатываем.
    snapshot = _snapshot(states)

    for job in victim_jobs:
        placed = False
        for st in others:
            best = _find_best_insertion(job, [st], check_equipment=check_equipment)
            if best is not None:
                _, pos, r = best
                _apply_insertion(st, job, pos, r)
                placed = True
                break
        if not placed:
            _restore(states, snapshot)
            return False

    # Все перенеслись — освобождаем victim.
    victim.stops = []
    victim.distance_km = 0.0
    victim.ready_min = min_of_day(victim.engineer.shift_start)
    victim.point = list(victim.engineer.start_point)
    return True


def _try_2opt_any(states) -> bool:
    """Пробует 2-opt в любом маршруте, где это уменьшит пробег."""
    for st in states:
        if len(st.stops) < 3:
            continue
        if _try_2opt_one(st):
            return True
    return False


def _try_2opt_one(st) -> bool:
    """
    2-opt: переворачиваем сегмент стопов [i..j].
    Для маршрута без возврата в базу это меняет порядок посещений.
    Принимаем только если не ломает окна и уменьшает пробег.
    """
    n = len(st.stops)
    for i in range(n - 1):
        for j in range(i + 1, n):
            new_order = st.stops[:i] + list(reversed(st.stops[i:j + 1])) + st.stops[j + 1:]
            new_km, ok = _evaluate_order(st, new_order)
            if ok and new_km + 1e-6 < st.distance_km:
                st.stops = new_order
                st.distance_km = new_km
                # Обновляем ready_min и point по последнему стопу.
                last = st.stops[-1]
                st.ready_min = _hhmm_to_min(last.departure)
                # Точка — координаты последней заявки; найдём через заявку.
                # Здесь приходится доверять, что маршрут консистентен.
                return True
    return False


def _evaluate_order(st, new_stops) -> Tuple[float, bool]:
    """Считает пробег и проверяет окна для нового порядка."""
    current_point = st.engineer.start_point
    current_ready = min_of_day(st.engineer.shift_start)
    total_km = 0.0

    for stop in new_stops:
        job = _job_by_id(stop.job_id, None, st)
        r = cons.can_assign(
            job=job, engineer=st.engineer,
            prev_point=current_point, ready_min=current_ready,
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


def _try_swap_any(states, *, check_equipment) -> bool:
    """Пробует поменять две заявки между двумя маршрутами, если это уменьшит пробег."""
    used = [s for s in states if s.stops]
    for i, a in enumerate(used):
        for b in used[i + 1:]:
            for ia, sa in enumerate(a.stops):
                for ib, sb in enumerate(b.stops):
                    if _try_swap_pair(a, ia, b, ib, check_equipment=check_equipment):
                        return True
    return False


def _try_swap_pair(a, ia, b, ib, *, check_equipment) -> bool:
    """Пробует поменять заявки a.stops[ia] и b.stops[ib] местами."""
    job_a = _job_by_id(a.stops[ia].job_id, None, a)
    job_b = _job_by_id(b.stops[ib].job_id, None, b)

    # Проверяем, что каждый инженер подходит для «чужой» заявки.
    if job_b.skill not in a.engineer.skills or job_a.skill not in b.engineer.skills:
        return False
    if job_b.required_vehicle and job_b.required_vehicle != a.engineer.vehicle:
        return False
    if job_a.required_vehicle and job_a.required_vehicle != b.engineer.vehicle:
        return False

    # Снимок и пробная замена.
    old_km_a, old_km_b = a.distance_km, b.distance_km
    old_stops_a, old_stops_b = list(a.stops), list(b.stops)

    new_stops_a = list(a.stops)
    new_stops_b = list(b.stops)
    new_stops_a[ia] = old_stops_b[ib]
    new_stops_b[ib] = old_stops_a[ia]

    new_km_a, ok_a = _evaluate_order(a, new_stops_a)
    new_km_b, ok_b = _evaluate_order(b, new_stops_b)

    if not (ok_a and ok_b):
        return False
    if new_km_a + new_km_b + 1e-6 >= old_km_a + old_km_b:
        return False

    a.stops = new_stops_a
    b.stops = new_stops_b
    a.distance_km = new_km_a
    b.distance_km = new_km_b
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
    """Собирает Plan из состояний, назначает статусы заявок."""
    # Карта: job_id → engineer_id.
    assigned: dict = {}
    for st in states:
        for stop in st.stops:
            assigned[stop.job_id] = st.engineer.id

    # Обновляем заявки.
    result_jobs: List[Job] = []
    for job in active_jobs:
        eid = assigned.get(job.id)
        if eid is None:
            result_jobs.append(job.model_copy(update={
                "status": "unassigned",
                "assigned_engineer_id": None,
            }))
            continue
        # Найдём стоп, чтобы взять arrive/depart.
        stop = next(
            s for st in states for s in st.stops if s.job_id == job.id
        )
        result_jobs.append(job.model_copy(update={
            "status": "assigned",
            "assigned_engineer_id": eid,
            "arrive_planned": stop.arrival,
            "depart_planned": stop.departure,
        }))

    # Passive jobs (cancelled/done) — как есть.
    result_jobs.extend(passive_jobs)

    # Собираем EngineerRoute.
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

    updated_jobs: List[Job] = []
    for job in plan.jobs:
        if job.status == "assigned" and job.assigned_engineer_id:
            e = eng_by_id.get(job.assigned_engineer_id)
            if e is None:
                updated_jobs.append(job)
                continue
            arrive_min = _hhmm_to_min(job.arrive_planned or "00:00")
            depart_min = _hhmm_to_min(job.depart_planned or "00:00")
            # Пробег: найдём стоп.
            stop = next(
                (s for r in plan.routes for s in r.stops if s.job_id == job.id),
                None,
            )
            distance_km = stop.distance_km_from_prev if stop else 0.0
            travel_min = stop.travel_min_from_prev if stop else 0
            new_route = (
                stop is not None
                and plan.routes
                and any(r.stops and r.stops[0].job_id == job.id
                        for r in plan.routes if r.engineer_id == e.id)
            )
            explanation = expl.explain_assignment(
                job=job, engineer=e,
                arrive_min=arrive_min, depart_min=depart_min,
                distance_km=distance_km, travel_min=travel_min,
                new_route=new_route, baseline=False,
            )
            updated_jobs.append(job.model_copy(update={"explanation": explanation}))
        elif job.status == "unassigned":
            reason = _infer_unassigned_reason(job, engineers)
            explanation = expl.explain_unassigned(
                job=job, reason_code=reason, engineers=engineers,
                arrive_min=None,
                window_end_min=min_of_day(job.window_end),
                shift_end_min=None,
            )
            updated_jobs.append(job.model_copy(update={"explanation": explanation}))
        else:
            updated_jobs.append(job)

    return plan.model_copy(update={"jobs": updated_jobs})


def _infer_unassigned_reason(job: Job, engineers: List[Engineer]) -> str:
    """Восстанавливает причину отказа для explainer."""
    if not any(job.skill in e.skills for e in engineers):
        return cons.NO_SKILL
    if job.required_vehicle and not any(
        job.required_vehicle == e.vehicle for e in engineers
        if job.skill in e.skills
    ):
        return cons.NO_VEHICLE
    # По умолчанию — не уложилась во время.
    return cons.OUT_OF_WINDOW


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def _hhmm_to_min(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _job_by_id(job_id: str, fallback: Optional[Job], st: RouteState) -> Job:
    """
    Возвращает Job по id. В текущей архитектуре optimizer получает jobs
    снаружи, но внутри state st заявок нет. Поэтому используем fallback,
    а если его нет — берём из глобального кэша _JOBS_INDEX.
    """
    if job_id in _JOBS_INDEX:
        return _JOBS_INDEX[job_id]
    if fallback is not None and fallback.id == job_id:
        return fallback
    raise KeyError(f"Заявка {job_id} не найдена в _JOBS_INDEX")


def _job_coords(job_id: str, st: RouteState, fallback: Optional[Job]):
    return _job_by_id(job_id, fallback, st).coords


_JOBS_INDEX: dict = {}


def _snapshot(states: List[RouteState]) -> list:
    """Сохраняет состояние маршрутов для отката."""
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
