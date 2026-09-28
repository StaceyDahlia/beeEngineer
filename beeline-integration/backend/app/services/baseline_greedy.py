from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any

from .travel_matrix import StaticTravelMatrix
from .validation import validate_plan


ACTIVE_STATUSES = {"planned", "assigned", "unassigned", "sent", "en_route"}
EXCLUDED_STATUSES = {"completed", "done", "cancelled"}

VEHICLE_ALIASES = {
    "car": "Автомобиль",
    "foot": "Пешеход",
    "bike": "Велосипед",
    "trans": "ОТ",
    "Общественный транспорт": "ОТ",
}


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


def _format_reason(codes: list[str]) -> dict[str, Any]:
    unique = set(codes)
    if not codes or unique == {"NO_AVAILABLE_ENGINEER"}:
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
        "evidence_level": "baseline_first_fit_check",
        "checked_engineers": len(codes),
        "failure_codes": sorted(unique),
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


def build_baseline_plan(
    jobs: list[dict[str, Any]],
    engineers: list[dict[str, Any]],
    *,
    scenario: str,
    matrix: StaticTravelMatrix | None = None,
) -> dict[str, Any]:
    """Официальный baseline: входной порядок и первый допустимый инженер."""
    result_jobs = copy.deepcopy(jobs)
    result_engineers = copy.deepcopy(engineers)
    matrix = matrix or StaticTravelMatrix(result_jobs, result_engineers)

    routes = [_empty_route(engineer) for engineer in result_engineers]
    states: list[dict[str, Any]] = []
    for engineer, route in zip(result_engineers, routes):
        states.append(
            {
                "engineer": engineer,
                "route": route,
                "ready_at": _dt(engineer["shift_start"]),
                "point_id": matrix.engineer_start_id(str(engineer["id"])),
            }
        )

    indexed_jobs = list(enumerate(result_jobs))
    indexed_jobs.sort(key=lambda pair: (int(pair[1].get("input_order", pair[0])), pair[0]))

    assigned_count = 0
    unassigned_count = 0
    excluded_count = 0

    for _, job in indexed_jobs:
        status = str(job.get("status") or "planned")
        if status in EXCLUDED_STATUSES:
            job["assignment_status"] = "excluded"
            job["exclusion_reason"] = "Заявка завершена или отменена и не участвует в планировании."
            job["assigned_engineer_id"] = None
            excluded_count += 1
            continue
        if status not in ACTIVE_STATUSES:
            job["status"] = "unassigned"

        job_id = str(job["id"])
        if not matrix.has_job(job_id):
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
            unassigned_count += 1
            continue

        failures: list[str] = []
        assigned = False
        for state in states:
            engineer = state["engineer"]
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
                state["point_id"],
                matrix.job_point_id(job_id),
                str(engineer["vehicle"]),
            )
            physical_arrival = state["ready_at"] + timedelta(minutes=metric.travel_min)
            window_start = _dt(job["window_start"])
            window_end = _dt(job["window_end"])
            shift_end = _dt(engineer["shift_end"])
            service_start = max(physical_arrival, window_start)
            service_end = service_start + timedelta(minutes=int(job["duration_min"]))
            if service_start > window_end:
                failures.append("OUT_OF_WINDOW")
                continue
            if service_end > shift_end:
                failures.append("OUT_OF_SHIFT")
                continue

            waiting_min = int((service_start - physical_arrival).total_seconds() // 60)
            route = state["route"]
            sequence = len(route["stops"]) + 1
            stop = {
                "job_id": job_id,
                "sequence": sequence,
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
                    f"Назначена первому допустимому инженеру «{engineer['name']}» "
                    "в порядке входных данных. Навык, транспорт, окно и смена соблюдены."
                ),
                "selection_basis": "baseline_first_eligible_engineer",
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
            state["ready_at"] = service_end
            state["point_id"] = matrix.job_point_id(job_id)
            assigned_count += 1
            assigned = True
            break

        if not assigned:
            reason = _format_reason(failures)
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

    total_distance_m = 0
    total_travel_min = 0
    total_waiting_min = 0
    total_service_min = 0
    per_engineer_distance_km: dict[str, float] = {}
    used_engineers = 0
    for route in routes:
        route["distance_km"] = round(route["distance_m"] / 1000.0, 3)
        per_engineer_distance_km[route["engineer_id"]] = route["distance_km"]
        total_distance_m += route["distance_m"]
        total_travel_min += route["travel_min"]
        total_waiting_min += route["waiting_min"]
        total_service_min += route["service_min"]
        if route["stops"]:
            used_engineers += 1

    metrics = {
        "scope": "full_day",
        "used_engineers": used_engineers,
        "engineers_used": used_engineers,
        "total_distance_m": total_distance_m,
        "total_distance_km": round(total_distance_m / 1000.0, 3),
        "total_distance": round(total_distance_m / 1000.0, 3),
        "total_travel_min": total_travel_min,
        "total_waiting_min": total_waiting_min,
        "total_service_min": total_service_min,
        "assigned_count": assigned_count,
        "unassigned_count": unassigned_count,
        "eligible_count": assigned_count + unassigned_count,
        "excluded_count": excluded_count,
        "per_engineer_distance_km": per_engineer_distance_km,
        "per_engineer_jobs_count": {
            route["engineer_id"]: route["jobs_count"] for route in routes
        },
    }
    plan = {
        "schema_version": "0.1",
        "scenario_id": scenario,
        "planning_date": "2026-08-17",
        "timezone": "Europe/Moscow",
        "engine": "baseline_greedy",
        "solver_status": "feasible",
        "jobs": result_jobs,
        "engineers": result_engineers,
        "routes": routes,
        "metrics": metrics,
        "baseline_metrics": copy.deepcopy(metrics),
        "travel_model": matrix.metadata,
        "assumptions": [
            "Маршруты открытые: возврат в офис после последней заявки не требуется.",
            "Оборудование на этом этапе не проверяется.",
            "Расстояния и время — статическая офлайн-оценка без пробок.",
        ],
    }
    validate_plan(plan)
    return plan
