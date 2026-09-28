from __future__ import annotations

import copy
import time
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from .travel_matrix import StaticTravelMatrix
from .validation import validate_plan

try:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
except ImportError as exc:  # pragma: no cover - проверяется через HTTP-обработчик
    pywrapcp = None
    routing_enums_pb2 = None
    ORTOOLS_IMPORT_ERROR: ImportError | None = exc
else:
    ORTOOLS_IMPORT_ERROR = None


ENGINE = "ortools_vrptw"
ACTIVE_STATUSES = {"planned", "assigned", "unassigned", "sent", "en_route"}
EXCLUDED_STATUSES = {"completed", "done", "cancelled"}
VEHICLE_ALIASES = {
    "car": "Автомобиль",
    "foot": "Пешеход",
    "bike": "Велосипед",
    "trans": "ОТ",
    "Общественный транспорт": "ОТ",
}
PRIORITY_CLASSES = (
    "additional_order",
    "repair_local",
    "connection",
    "emergency",
)
PRIORITY_LABELS = {
    "additional_order": "дозаказ",
    "repair_local": "ремонт / локальная заявка",
    "connection": "подключение",
    "emergency": "авария",
}
MAX_INT64 = 2**63 - 1


class OrToolsUnavailableError(RuntimeError):
    pass


class OrToolsSolveError(RuntimeError):
    def __init__(self, status: str, solve_time_ms: int, warnings: list[str]) -> None:
        super().__init__(f"OR-Tools не построил допустимый план: {status}.")
        self.status = status
        self.solve_time_ms = solve_time_ms
        self.warnings = warnings


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _same_vehicle(required: str | None, actual: str) -> bool:
    if not required:
        return True
    return VEHICLE_ALIASES.get(required, required) == VEHICLE_ALIASES.get(actual, actual)


def _has_equipment(job: dict[str, Any], engineer: dict[str, Any]) -> bool:
    def counts(value: Any) -> dict[str, int]:
        if isinstance(value, dict):
            return {str(key): int(amount) for key, amount in value.items()}
        result: dict[str, int] = {}
        for item in value or []:
            result[str(item)] = result.get(str(item), 0) + 1
        return result
    required, available = counts(job.get("required_equipment")), counts(engineer.get("equipment"))
    return all(available.get(item, 0) >= amount for item, amount in required.items())


def priority_class(job: dict[str, Any]) -> str:
    """Возвращает официальный класс приоритета без опоры на входной порядок."""
    job_type = str(job.get("type") or "").strip().casefold()
    skill = str(job.get("skill") or "").strip().casefold()
    if skill == "emergency" or "авари" in job_type or "глобальн" in job_type:
        return "emergency"
    if "дозаказ" in job_type or "additional" in job_type:
        return "additional_order"
    if skill == "connect" or "подключ" in job_type or "connection" in job_type:
        return "connection"
    return "repair_local"


def derive_priority_penalties(
    jobs: list[dict[str, Any]],
    *,
    distance_upper_bound: int,
    vehicle_count: int,
) -> dict[str, Any]:
    """Строит безопасные целочисленные веса из верхних границ задачи.

    Один пропуск класса всегда дороже всех возможных пропусков младших
    классов вместе с максимально возможной стоимостью машин и расстояния.
    """
    distance_bound = max(1, int(distance_upper_bound))
    vehicles = max(0, int(vehicle_count))
    vehicle_fixed_cost = distance_bound + 1
    operating_cost_upper_bound = vehicles * vehicle_fixed_cost + distance_bound
    counts = Counter(priority_class(job) for job in jobs)
    penalties: dict[str, int] = {}
    lower_priority_drop_bound = 0
    for class_name in PRIORITY_CLASSES:
        penalty = lower_priority_drop_bound + operating_cost_upper_bound + 1
        penalties[class_name] = penalty
        lower_priority_drop_bound += counts[class_name] * penalty
    objective_upper_bound = lower_priority_drop_bound + operating_cost_upper_bound
    if objective_upper_bound > MAX_INT64:
        raise ValueError(
            "Рассчитанные penalty weights превышают безопасный диапазон int64; "
            "уменьшите размер одного регионального сценария."
        )
    return {
        "penalties": penalties,
        "counts": {name: counts[name] for name in PRIORITY_CLASSES},
        "distance_upper_bound": distance_bound,
        "vehicle_fixed_cost": vehicle_fixed_cost,
        "operating_cost_upper_bound": operating_cost_upper_bound,
        "objective_upper_bound": objective_upper_bound,
        "formula": (
            "penalty[class] = max_operating_cost + "
            "sum(count[lower] * penalty[lower]) + 1"
        ),
    }


def _empty_route(engineer: dict[str, Any]) -> dict[str, Any]:
    return {
        "engineer_id": str(engineer["id"]),
        "start_point": engineer["start_point"],
        "stops": [],
        "distance_m": 0,
        "distance_km": 0.0,
        "travel_min": 0,
        "waiting_min": 0,
        "service_min": 0,
        "jobs_count": 0,
    }


def _status_name(status: int) -> str:
    names = {
        0: "ROUTING_NOT_SOLVED",
        1: "ROUTING_SUCCESS",
        2: "ROUTING_PARTIAL_SUCCESS_LOCAL_OPTIMUM_NOT_REACHED",
        3: "ROUTING_FAIL",
        4: "ROUTING_FAIL_TIMEOUT",
        5: "ROUTING_INVALID",
        6: "ROUTING_INFEASIBLE",
        7: "ROUTING_OPTIMAL",
    }
    return names.get(int(status), f"ROUTING_STATUS_{status}")


def _failure_reason(
    job: dict[str, Any],
    engineers: list[dict[str, Any]],
    matrix: StaticTravelMatrix,
) -> dict[str, Any]:
    failures: list[str] = []
    directly_feasible = False
    for engineer in engineers:
        if engineer.get("status") != "Доступен":
            failures.append("NO_AVAILABLE_ENGINEER")
            continue
        if str(job.get("skill") or "") not in set(engineer.get("skills") or []):
            failures.append("NO_SKILL")
            continue
        if not _same_vehicle(job.get("required_vehicle"), str(engineer.get("vehicle") or "")):
            failures.append("NO_VEHICLE")
            continue
        if job.get("required_equipment") and not _has_equipment(job, engineer):
            failures.append("NO_EQUIPMENT")
            continue

        metric = matrix.get(
            matrix.engineer_start_id(str(engineer["id"])),
            matrix.job_point_id(str(job["id"])),
            str(engineer["vehicle"]),
        )
        arrival = _dt(engineer["shift_start"]) + timedelta(minutes=metric.travel_min)
        service_start = max(arrival, _dt(job["window_start"]))
        if service_start > _dt(job["window_end"]):
            failures.append("OUT_OF_WINDOW")
            continue
        if service_start + timedelta(minutes=int(job["duration_min"])) > _dt(
            engineer["shift_end"]
        ):
            failures.append("OUT_OF_SHIFT")
            continue
        directly_feasible = True

    unique = set(failures)
    if directly_feasible:
        code = "NOT_SELECTED_BY_SOLVER"
        message = (
            "Заявка индивидуально выполнима, но не вошла в найденный VRPTW-план "
            "при текущих ресурсах и лимите времени решения."
        )
    elif not failures or unique == {"NO_AVAILABLE_ENGINEER"}:
        code = "NO_AVAILABLE_ENGINEER"
        message = "Нет доступного инженера для назначения заявки."
    elif "NO_SKILL" in unique and unique <= {"NO_SKILL", "NO_AVAILABLE_ENGINEER"}:
        code = "NO_SKILL"
        message = "У доступных инженеров нет требуемого навыка."
    elif "NO_VEHICLE" in unique and unique <= {
        "NO_SKILL",
        "NO_VEHICLE",
        "NO_AVAILABLE_ENGINEER",
    }:
        code = "NO_VEHICLE"
        message = "Нет доступного инженера с подходящими навыком и транспортом."
    elif "NO_EQUIPMENT" in unique:
        code = "NO_EQUIPMENT"
        message = "Нет бригады с нужным оборудованием."
    elif "OUT_OF_WINDOW" in unique:
        code = "OUT_OF_WINDOW"
        message = "Подходящие инженеры не успевают начать работу в клиентском окне."
    elif "OUT_OF_SHIFT" in unique:
        code = "OUT_OF_SHIFT"
        message = "Подходящие инженеры не успевают завершить работу до конца смены."
    else:
        code = "NO_FEASIBLE_ASSIGNMENT"
        message = "Назначение не удовлетворяет обязательным ограничениям."
    return {
        "code": code,
        "message": message,
        "evidence_level": "ortools_result_and_direct_feasibility_check",
        "checked_engineers": len(engineers),
        "failure_codes": sorted(unique),
    }


def _metrics(routes: list[dict[str, Any]], assigned: int, unassigned: int, excluded: int) -> dict:
    total_distance_m = sum(int(route["distance_m"]) for route in routes)
    return {
        "scope": "full_day",
        "used_engineers": sum(bool(route["stops"]) for route in routes),
        "engineers_used": sum(bool(route["stops"]) for route in routes),
        "total_distance_m": total_distance_m,
        "total_distance_km": round(total_distance_m / 1000.0, 3),
        "total_distance": round(total_distance_m / 1000.0, 3),
        "total_travel_min": sum(int(route["travel_min"]) for route in routes),
        "total_waiting_min": sum(int(route["waiting_min"]) for route in routes),
        "total_service_min": sum(int(route["service_min"]) for route in routes),
        "assigned_count": assigned,
        "unassigned_count": unassigned,
        "eligible_count": assigned + unassigned,
        "excluded_count": excluded,
        "per_engineer_distance_km": {
            route["engineer_id"]: route["distance_km"] for route in routes
        },
        "per_engineer_jobs_count": {
            route["engineer_id"]: route["jobs_count"] for route in routes
        },
    }


def build_ortools_plan(
    jobs: list[dict[str, Any]],
    engineers: list[dict[str, Any]],
    *,
    scenario: str,
    matrix: StaticTravelMatrix | None = None,
    solve_time_limit_ms: int = 5_000,
) -> dict[str, Any]:
    """Строит открытый многодепотный VRPTW-план с необязательными заявками."""
    if ORTOOLS_IMPORT_ERROR is not None or pywrapcp is None or routing_enums_pb2 is None:
        raise OrToolsUnavailableError(
            "Google OR-Tools не установлен; установите зависимости из requirements.txt."
        ) from ORTOOLS_IMPORT_ERROR

    started = time.perf_counter()
    result_jobs = copy.deepcopy(jobs)
    result_engineers = copy.deepcopy(engineers)
    matrix = matrix or StaticTravelMatrix(result_jobs, result_engineers)
    routes = [_empty_route(engineer) for engineer in result_engineers]

    excluded_count = 0
    invalid_count = 0
    solver_jobs: list[dict[str, Any]] = []
    for job in result_jobs:
        job["priority_class"] = priority_class(job)
        job["priority_rank"] = PRIORITY_CLASSES.index(job["priority_class"]) + 1
        status = str(job.get("status") or "planned")
        if status in EXCLUDED_STATUSES:
            job.update(
                assignment_status="excluded",
                assigned_engineer_id=None,
                exclusion_reason="Заявка завершена или отменена и не участвует в планировании.",
            )
            excluded_count += 1
            continue
        if status not in ACTIVE_STATUSES:
            job["status"] = "unassigned"
        if not matrix.has_job(str(job["id"])):
            reason = {
                "code": "INVALID_COORDINATES",
                "message": "У заявки нет корректных координат для расчёта маршрута.",
                "evidence_level": "input_validation",
                "checked_engineers": 0,
                "failure_codes": ["INVALID_COORDINATES"],
            }
            job.update(
                status="unassigned",
                assignment_status="unassigned",
                assigned_engineer_id=None,
                unassigned_reason=reason,
                explanation=reason["message"],
            )
            invalid_count += 1
            continue
        solver_jobs.append(job)

    if not solver_jobs:
        solve_time_ms = round((time.perf_counter() - started) * 1000)
        metrics = _metrics(routes, 0, invalid_count, excluded_count)
        plan = {
            "schema_version": "0.2",
            "scenario_id": scenario,
            "planning_date": str(_dt(result_engineers[0]["shift_start"]).date()) if result_engineers else None,
            "timezone": "Europe/Moscow",
            "engine": ENGINE,
            "solver_status": "feasible",
            "solver_status_detail": "EMPTY_ACTIVE_SET",
            "solve_time_ms": solve_time_ms,
            "warnings": [],
            "jobs": result_jobs,
            "engineers": result_engineers,
            "routes": routes,
            "metrics": metrics,
            "travel_model": matrix.metadata,
            "assumptions": ["Маршруты открытые: возврат в офис не требуется."],
        }
        validate_plan(plan)
        return plan

    all_times = [
        _dt(value)
        for job in solver_jobs
        for value in (job["window_start"], job["window_end"])
    ] + [
        _dt(value)
        for engineer in result_engineers
        for value in (engineer["shift_start"], engineer["shift_end"])
    ]
    epoch = min(all_times).replace(hour=0, minute=0, second=0, microsecond=0)

    def minute(value: str | datetime) -> int:
        parsed = value if isinstance(value, datetime) else _dt(value)
        return int((parsed - epoch).total_seconds() // 60)

    job_count = len(solver_jobs)
    vehicle_count = len(result_engineers)
    starts = [job_count + i for i in range(vehicle_count)]
    ends = [job_count + vehicle_count + i for i in range(vehicle_count)]
    manager = pywrapcp.RoutingIndexManager(
        job_count + 2 * vehicle_count,
        vehicle_count,
        starts,
        ends,
    )
    routing = pywrapcp.RoutingModel(manager)

    durations = [int(job["duration_min"]) for job in solver_jobs]
    distance_callbacks: list[int] = []
    time_callbacks: list[int] = []
    max_arc_distance = 0

    for vehicle_index, engineer in enumerate(result_engineers):
        vehicle = str(engineer["vehicle"])
        start_point_id = matrix.engineer_start_id(str(engineer["id"]))
        arc_distance: dict[tuple[int, int], int] = {}
        arc_travel: dict[tuple[int, int], int] = {}
        for from_node in range(job_count + 2 * vehicle_count):
            if from_node < job_count:
                origin_id = matrix.job_point_id(str(solver_jobs[from_node]["id"]))
            else:
                origin_id = start_point_id
            for to_node in range(job_count):
                destination_id = matrix.job_point_id(str(solver_jobs[to_node]["id"]))
                metric = matrix.get(origin_id, destination_id, vehicle)
                arc_distance[(from_node, to_node)] = metric.distance_m
                arc_travel[(from_node, to_node)] = metric.travel_min
                max_arc_distance = max(max_arc_distance, metric.distance_m)

        def distance_callback(from_index: int, to_index: int, *, values=arc_distance) -> int:
            from_node = manager.IndexToNode(from_index)
            to_node = manager.IndexToNode(to_index)
            if to_node >= job_count:
                return 0
            return values[(from_node, to_node)]

        def time_callback(
            from_index: int,
            to_index: int,
            *,
            values=arc_travel,
        ) -> int:
            from_node = manager.IndexToNode(from_index)
            to_node = manager.IndexToNode(to_index)
            service = durations[from_node] if from_node < job_count else 0
            if to_node >= job_count:
                return service
            return service + values[(from_node, to_node)]

        distance_index = routing.RegisterTransitCallback(distance_callback)
        time_index = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfVehicle(distance_index, vehicle_index)
        distance_callbacks.append(distance_index)
        time_callbacks.append(time_index)

    distance_upper_bound = max(1, (job_count + vehicle_count) * max(1, max_arc_distance))
    priority_weights = derive_priority_penalties(
        solver_jobs,
        distance_upper_bound=distance_upper_bound,
        vehicle_count=vehicle_count,
    )
    vehicle_fixed_cost = int(priority_weights["vehicle_fixed_cost"])
    drop_penalties = priority_weights["penalties"]
    for vehicle_index in range(vehicle_count):
        routing.SetFixedCostOfVehicle(vehicle_fixed_cost, vehicle_index)

    horizon = max(minute(engineer["shift_end"]) for engineer in result_engineers)
    routing.AddDimensionWithVehicleTransits(
        time_callbacks,
        horizon,
        horizon,
        False,
        "Time",
    )
    time_dimension = routing.GetDimensionOrDie("Time")

    for vehicle_index, engineer in enumerate(result_engineers):
        shift_start = minute(engineer["shift_start"])
        shift_end = minute(engineer["shift_end"])
        time_dimension.CumulVar(routing.Start(vehicle_index)).SetRange(shift_start, shift_start)
        time_dimension.CumulVar(routing.End(vehicle_index)).SetRange(shift_start, shift_end)

    for job_index, job in enumerate(solver_jobs):
        index = manager.NodeToIndex(job_index)
        time_dimension.CumulVar(index).SetRange(
            minute(job["window_start"]),
            minute(job["window_end"]),
        )
        routing.AddDisjunction([index], drop_penalties[priority_class(job)])
        allowed = [
            vehicle_index
            for vehicle_index, engineer in enumerate(result_engineers)
            if engineer.get("status") == "Доступен"
            and str(job.get("skill") or "") in set(engineer.get("skills") or [])
            and _same_vehicle(job.get("required_vehicle"), str(engineer.get("vehicle") or ""))
            and (not job.get("required_equipment") or _has_equipment(job, engineer))
        ]
        if allowed:
            routing.SetAllowedVehiclesForIndex(allowed, index)
        else:
            routing.solver().Add(routing.ActiveVar(index) == 0)

    search = pywrapcp.DefaultRoutingSearchParameters()
    search.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    )
    search.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search.time_limit.FromMilliseconds(max(1, int(solve_time_limit_ms)))
    search.log_search = False

    search_started = time.perf_counter()
    solution = routing.SolveWithParameters(search)
    search_time_ms = round((time.perf_counter() - search_started) * 1000)
    solve_time_ms = round((time.perf_counter() - started) * 1000)
    raw_status = _status_name(routing.status())
    warnings: list[str] = []
    timed_out = (
        routing.status() in {2, 4}
        or search_time_ms >= max(1, int(solve_time_limit_ms)) * 0.98
    )
    if timed_out:
        warnings.append(
            "Достигнут лимит времени OR-Tools; возвращён лучший найденный допустимый план, "
            "глобальный оптимум не подтверждён."
        )
    if solution is None:
        if not warnings:
            warnings.append("OR-Tools завершился без допустимого решения; baseline не подставлялся.")
        raise OrToolsSolveError(raw_status, solve_time_ms, warnings)

    assigned_ids: set[str] = set()
    for vehicle_index, (engineer, route) in enumerate(zip(result_engineers, routes)):
        index = routing.Start(vehicle_index)
        previous_point_id = matrix.engineer_start_id(str(engineer["id"]))
        previous_end = _dt(engineer["shift_start"])
        vehicle = str(engineer["vehicle"])
        while not routing.IsEnd(solution.Value(routing.NextVar(index))):
            next_index = solution.Value(routing.NextVar(index))
            job_index = manager.IndexToNode(next_index)
            job = solver_jobs[job_index]
            job_id = str(job["id"])
            metric = matrix.get(
                previous_point_id,
                matrix.job_point_id(job_id),
                vehicle,
            )
            physical_arrival = previous_end + timedelta(minutes=metric.travel_min)
            service_start = epoch + timedelta(
                minutes=solution.Value(time_dimension.CumulVar(next_index))
            )
            service_end = service_start + timedelta(minutes=int(job["duration_min"]))
            waiting_min = max(
                0,
                int((service_start - physical_arrival).total_seconds() // 60),
            )
            stop = {
                "job_id": job_id,
                "sequence": len(route["stops"]) + 1,
                "coords": job["coords"],
                "arrival_time": physical_arrival.isoformat(),
                "service_start": service_start.isoformat(),
                "service_end": service_end.isoformat(),
                "departure_time": service_end.isoformat(),
                "arrival": service_start.strftime("%H:%M"),
                "departure": service_end.strftime("%H:%M"),
                "travel_min_from_prev": metric.travel_min,
                "distance_m_from_prev": metric.distance_m,
                "distance_km_from_prev": metric.distance_km,
                "waiting_min": waiting_min,
                "service_min": int(job["duration_min"]),
            }
            route["stops"].append(stop)
            route["distance_m"] += metric.distance_m
            route["travel_min"] += metric.travel_min
            route["waiting_min"] += waiting_min
            route["service_min"] += int(job["duration_min"])
            route["jobs_count"] += 1

            explanation = {
                "summary": (
                    f"OR-Tools включил заявку в допустимый маршрут инженера «{engineer['name']}». "
                    f"Класс приоритета — {PRIORITY_LABELS[priority_class(job)]}; "
                    "навык, транспорт, окно и смена соблюдены."
                ),
                "selection_basis": "ortools_vrptw_weighted_lexicographic",
                "priority": {
                    "class": priority_class(job),
                    "label": PRIORITY_LABELS[priority_class(job)],
                    "rank": PRIORITY_CLASSES.index(priority_class(job)) + 1,
                    "drop_penalty": drop_penalties[priority_class(job)],
                },
                "checks": {
                    "skill": {"required": job.get("skill"), "matched": True},
                    "transport": {
                        "required": job.get("required_vehicle"),
                        "actual": engineer.get("vehicle"),
                        "matched": True,
                    },
                    "equipment": {
                        "required": job.get("required_equipment") or [],
                        "available": engineer.get("equipment") or [],
                        "matched": True,
                    },
                    "time_window": {
                        "service_start": service_start.isoformat(),
                        "window_start": job["window_start"],
                        "window_end": job["window_end"],
                        "matched": True,
                    },
                    "shift": {
                        "service_end": service_end.isoformat(),
                        "shift_end": engineer["shift_end"],
                        "matched": True,
                    },
                },
                "route_impact": {
                    "incoming_distance_km": metric.distance_km,
                    "incoming_travel_min": metric.travel_min,
                    "waiting_min": waiting_min,
                },
            }
            job.update(
                status="assigned",
                assignment_status="assigned",
                assigned_engineer_id=str(engineer["id"]),
                arrive_planned=service_start.strftime("%H:%M"),
                depart_planned=service_end.strftime("%H:%M"),
                arrival_time=physical_arrival.isoformat(),
                service_start=service_start.isoformat(),
                service_end=service_end.isoformat(),
                waiting_min=waiting_min,
                assignment_explanation=explanation,
                explanation=explanation["summary"],
                unassigned_reason=None,
            )
            assigned_ids.add(job_id)
            previous_point_id = matrix.job_point_id(job_id)
            previous_end = service_end
            index = next_index

        route["distance_km"] = round(route["distance_m"] / 1000.0, 3)

    assigned_priority_classes = {
        priority_class(job)
        for job in solver_jobs
        if str(job["id"]) in assigned_ids
    }
    unassigned_count = invalid_count
    for job in solver_jobs:
        if str(job["id"]) in assigned_ids:
            continue
        reason = _failure_reason(job, result_engineers, matrix)
        job_class = priority_class(job)
        higher_assigned = [
            name
            for name in PRIORITY_CLASSES
            if name in assigned_priority_classes
            and PRIORITY_CLASSES.index(name) > PRIORITY_CLASSES.index(job_class)
        ]
        reason["priority"] = {
            "class": job_class,
            "label": PRIORITY_LABELS[job_class],
            "rank": PRIORITY_CLASSES.index(job_class) + 1,
            "drop_penalty": drop_penalties[job_class],
            "higher_priority_classes_assigned": higher_assigned,
        }
        if reason["code"] == "NOT_SELECTED_BY_SOLVER" and higher_assigned:
            labels = ", ".join(PRIORITY_LABELS[name] for name in higher_assigned)
            reason.update(
                code="DISPLACED_BY_HIGHER_PRIORITY",
                message=(
                    f"Заявка класса «{PRIORITY_LABELS[job_class]}» индивидуально выполнима, "
                    f"но снята ради заявок более высокого приоритета: {labels}."
                ),
            )
        job.update(
            status="unassigned",
            assignment_status="unassigned",
            assigned_engineer_id=None,
            arrive_planned=None,
            depart_planned=None,
            assignment_explanation=None,
            unassigned_reason=reason,
            explanation=reason["message"],
        )
        unassigned_count += 1

    metrics = _metrics(routes, len(assigned_ids), unassigned_count, excluded_count)
    solver_status = "time_limit_feasible" if timed_out else "feasible"
    plan = {
        "schema_version": "0.2",
        "scenario_id": scenario,
        "planning_date": str(epoch.date()),
        "timezone": "Europe/Moscow",
        "engine": ENGINE,
        "solver_status": solver_status,
        "solver_status_detail": raw_status,
        "solve_time_ms": solve_time_ms,
        "solver_search_time_ms": search_time_ms,
        "warnings": warnings,
        "jobs": result_jobs,
        "engineers": result_engineers,
        "routes": routes,
        "metrics": metrics,
        "travel_model": matrix.metadata,
        "objective": {
            "type": "weighted_lexicographic",
            "priority": [
                "emergency_assigned_count",
                "connection_assigned_count",
                "repair_local_assigned_count",
                "additional_order_assigned_count",
                "used_engineers",
                "total_distance_m",
            ],
            "priority_classes_high_to_low": list(reversed(PRIORITY_CLASSES)),
            "drop_penalties_by_class": drop_penalties,
            "penalty_derivation": priority_weights,
            "vehicle_fixed_cost": vehicle_fixed_cost,
            "distance_unit_cost": 1,
            "global_optimum_claimed": False,
        },
        "assumptions": [
            "Маршруты открытые: возврат в офис после последней заявки не требуется.",
            "Оборудование на этом этапе не проверяется.",
            "Расстояния и время — статическая офлайн-оценка без пробок.",
        ],
    }
    validate_plan(plan)
    return plan
