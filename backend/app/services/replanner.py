from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any

from .ortools_vrptw import PRIORITY_LABELS, priority_class
from .planner import build_plan_with_comparison
from .validation import validate_plan


TERMINAL_STATUSES = {"completed", "done", "cancelled"}
LOCKED_STATUSES = {"in_progress", "en_route"}


class ReplanValidationError(ValueError):
    pass


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _route_index(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(route["engineer_id"]): route for route in plan["routes"]}


def _stop_index(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(stop["job_id"]): {**stop, "engineer_id": str(route["engineer_id"])}
        for route in plan["routes"]
        for stop in route["stops"]
    }


def _find_current_stop(
    route: dict[str, Any], jobs_by_id: dict[str, dict[str, Any]], event_time: datetime
) -> dict[str, Any] | None:
    for stop in route["stops"]:
        job = jobs_by_id[str(stop["job_id"])]
        if str(job.get("status")) in LOCKED_STATUSES:
            return stop
        arrival = _dt(stop["arrival_time"])
        end = _dt(stop["service_end"])
        if arrival <= event_time < end:
            return stop
    return None


def _completed_ids(
    plan: dict[str, Any], event_time: datetime
) -> set[str]:
    jobs_by_id = {str(job["id"]): job for job in plan["jobs"]}
    completed = {
        job_id
        for job_id, job in jobs_by_id.items()
        if str(job.get("status")) in {"completed", "done"}
    }
    for route in plan["routes"]:
        for stop in route["stops"]:
            if _dt(stop["service_end"]) <= event_time:
                completed.add(str(stop["job_id"]))
    return completed


def _event_job(event: dict[str, Any], jobs: list[dict[str, Any]]) -> dict[str, Any]:
    event_time = _dt(event["event_time"])
    # ASSUMPTION MVP: подтверждённый диапазон реакции 1–2 часа моделируется
    # верхней границей в 2 часа; длительность аварийных работ — 80 минут.
    window_end = event_time + timedelta(hours=2)
    return {
        "id": f"emergency:{event['event_id']}",
        "input_order": max(
            [int(job.get("input_order", index)) for index, job in enumerate(jobs)]
            or [0]
        )
        + 1,
        "type": "Авария",
        "subtype": event.get("title") or "Срочная аварийная заявка",
        "skill": "emergency",
        "priority": "Срочная",
        "address": event.get("address") or "Адрес аварийной заявки",
        "coords": [float(value) for value in event["coords"]],
        "window_start": event_time.isoformat(),
        "window_end": window_end.isoformat(),
        "duration_min": 80,
        "required_vehicle": event.get("required_vehicle") or None,
        "status": "planned",
        "created_by_event_id": event["event_id"],
    }


def _recompute_metrics(
    routes: list[dict[str, Any]], jobs: list[dict[str, Any]]
) -> dict[str, Any]:
    total_distance_m = sum(int(route["distance_m"]) for route in routes)
    assigned_count = sum(len(route["stops"]) for route in routes)
    excluded_count = sum(
        str(job.get("status")) in TERMINAL_STATUSES for job in jobs
    )
    unassigned_count = sum(
        str(job.get("status")) == "unassigned" for job in jobs
    )
    return {
        "scope": "remaining_day",
        "used_engineers": sum(bool(route["stops"]) for route in routes),
        "engineers_used": sum(bool(route["stops"]) for route in routes),
        "total_distance_m": total_distance_m,
        "total_distance_km": round(total_distance_m / 1000.0, 3),
        "total_distance": round(total_distance_m / 1000.0, 3),
        "total_travel_min": sum(int(route["travel_min"]) for route in routes),
        "total_waiting_min": sum(int(route["waiting_min"]) for route in routes),
        "total_service_min": sum(int(route["service_min"]) for route in routes),
        "assigned_count": assigned_count,
        "unassigned_count": unassigned_count,
        "eligible_count": assigned_count + unassigned_count,
        "excluded_count": excluded_count,
        "per_engineer_distance_km": {
            str(route["engineer_id"]): round(int(route["distance_m"]) / 1000.0, 3)
            for route in routes
        },
        "per_engineer_jobs_count": {
            str(route["engineer_id"]): len(route["stops"]) for route in routes
        },
    }


def _merge_locked_metrics(
    metrics: dict[str, Any], locked_routes: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    result = copy.deepcopy(metrics)
    per_engineer = copy.deepcopy(result.get("per_engineer_distance_km") or {})
    per_engineer_jobs = copy.deepcopy(result.get("per_engineer_jobs_count") or {})
    locked_count = 0
    locked_distance = 0
    locked_travel = 0
    locked_waiting = 0
    locked_service = 0
    suffix_used = {
        engineer_id
        for engineer_id, count in per_engineer_jobs.items()
        if int(count) > 0
    }
    locked_used: set[str] = set()
    for engineer_id, stops in locked_routes.items():
        if not stops:
            continue
        locked_used.add(engineer_id)
        distance = sum(int(stop["distance_m_from_prev"]) for stop in stops)
        per_engineer[engineer_id] = round(
            float(per_engineer.get(engineer_id, 0)) + distance / 1000.0, 3
        )
        per_engineer_jobs[engineer_id] = int(per_engineer_jobs.get(engineer_id, 0)) + len(stops)
        locked_count += len(stops)
        locked_distance += distance
        locked_travel += sum(int(stop["travel_min_from_prev"]) for stop in stops)
        locked_waiting += sum(int(stop["waiting_min"]) for stop in stops)
        locked_service += sum(int(stop["service_min"]) for stop in stops)
    result["assigned_count"] = int(result["assigned_count"]) + locked_count
    result["eligible_count"] = int(result["eligible_count"]) + locked_count
    result["used_engineers"] = len(suffix_used | locked_used)
    result["engineers_used"] = result["used_engineers"]
    result["total_distance_m"] = int(result["total_distance_m"]) + locked_distance
    result["total_distance_km"] = round(result["total_distance_m"] / 1000.0, 3)
    result["total_distance"] = result["total_distance_km"]
    result["total_travel_min"] = int(result["total_travel_min"]) + locked_travel
    result["total_waiting_min"] = int(result["total_waiting_min"]) + locked_waiting
    result["total_service_min"] = int(result["total_service_min"]) + locked_service
    result["per_engineer_distance_km"] = per_engineer
    result["per_engineer_jobs_count"] = per_engineer_jobs
    result["scope"] = "remaining_day_with_locked_current_stage"
    return result


def _assignment_map(
    plan: dict[str, Any], ignored: set[str]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for route in plan["routes"]:
        active_stops = [
            stop for stop in route["stops"] if str(stop["job_id"]) not in ignored
        ]
        for order, stop in enumerate(active_stops, start=1):
            result[str(stop["job_id"])] = {
                "engineer_id": str(route["engineer_id"]),
                "order": order,
                "service_start": stop["service_start"],
            }
    return result


def build_diff(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    completed_ids: set[str],
    priority_event_job_id: str | None = None,
) -> dict[str, Any]:
    after_jobs = {str(job["id"]): job for job in after["jobs"]}
    cancelled_ids = {
        job_id
        for job_id, job in after_jobs.items()
        if str(job.get("status")) == "cancelled"
    }
    ignored = completed_ids | cancelled_ids
    old = _assignment_map(before, ignored)
    new = _assignment_map(after, ignored)
    categories: dict[str, list[dict[str, Any]]] = {
        "assigned": [],
        "unassigned": [],
        "reassigned": [],
        "reordered": [],
        "delayed": [],
        "cancelled": [],
    }

    for job_id in sorted(cancelled_ids):
        categories["cancelled"].append(
            {"job_id": job_id, "message": f"Заявка #{job_id} отменена и снята с маршрута."}
        )
    for job_id in sorted(set(old) | set(new)):
        before_item, after_item = old.get(job_id), new.get(job_id)
        if before_item is None and after_item is not None:
            categories["assigned"].append(
                {
                    "job_id": job_id,
                    "to_engineer_id": after_item["engineer_id"],
                    "message": f"Заявка #{job_id} назначена инженеру {after_item['engineer_id']}.",
                }
            )
            continue
        if before_item is not None and after_item is None:
            categories["unassigned"].append(
                {
                    "job_id": job_id,
                    "from_engineer_id": before_item["engineer_id"],
                    "message": f"Заявка #{job_id} снята с назначения.",
                }
            )
            continue
        if before_item is None or after_item is None:
            continue
        if before_item["engineer_id"] != after_item["engineer_id"]:
            categories["reassigned"].append(
                {
                    "job_id": job_id,
                    "from_engineer_id": before_item["engineer_id"],
                    "to_engineer_id": after_item["engineer_id"],
                    "message": (
                        f"Заявка #{job_id} переназначена: {before_item['engineer_id']} → "
                        f"{after_item['engineer_id']}."
                    ),
                }
            )
        elif before_item["order"] != after_item["order"]:
            categories["reordered"].append(
                {
                    "job_id": job_id,
                    "from_order": before_item["order"],
                    "to_order": after_item["order"],
                    "message": (
                        f"Заявка #{job_id} переставлена в маршруте: "
                        f"{before_item['order']} → {after_item['order']}."
                    ),
                }
            )
        delay_min = int(
            (_dt(after_item["service_start"]) - _dt(before_item["service_start"])).total_seconds()
            // 60
        )
        if delay_min > 0:
            categories["delayed"].append(
                {
                    "job_id": job_id,
                    "delay_min": delay_min,
                    "message": f"Заявка #{job_id} задержана на {delay_min} мин.",
                }
            )

    priority_impacts: list[dict[str, Any]] = []
    if priority_event_job_id:
        for category in ("unassigned", "reassigned", "reordered", "delayed"):
            for item in categories[category]:
                affected_job = after_jobs.get(str(item["job_id"]))
                if affected_job is None or priority_class(affected_job) == "emergency":
                    continue
                affected_class = priority_class(affected_job)
                impact_message = (
                    f" Изменение выполнено ради аварийной заявки "
                    f"#{priority_event_job_id}; класс затронутой заявки — "
                    f"«{PRIORITY_LABELS[affected_class]}»."
                )
                item["message"] += impact_message
                item.update(
                    caused_by_priority_event=True,
                    priority_event_job_id=priority_event_job_id,
                    affected_priority_class=affected_class,
                )
                affected_job["replan_priority_impact"] = {
                    "event_job_id": priority_event_job_id,
                    "category": category,
                    "message": impact_message.strip(),
                }
                explanation = affected_job.get("assignment_explanation")
                if isinstance(explanation, dict):
                    explanation["summary"] = (
                        f"{explanation.get('summary', '')} {impact_message.strip()}"
                    ).strip()
                    affected_job["explanation"] = explanation["summary"]
                priority_impacts.append(
                    {
                        "category": category,
                        "job_id": item["job_id"],
                        "priority_class": affected_class,
                        "message": item["message"],
                    }
                )

    items = [
        {"category": category, **item}
        for category, values in categories.items()
        for item in values
    ]
    return {
        **categories,
        "items": items,
        "summary": {category: len(values) for category, values in categories.items()},
        "priority_impact": {
            "event_job_id": priority_event_job_id,
            "affected_count": len(priority_impacts),
            "items": priority_impacts,
        },
    }


def _delta(plan_metrics: dict[str, Any], baseline_metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "assigned_count": int(plan_metrics["assigned_count"])
        - int(baseline_metrics["assigned_count"]),
        "unassigned_count": int(plan_metrics["unassigned_count"])
        - int(baseline_metrics["unassigned_count"]),
        "used_engineers": int(plan_metrics["used_engineers"])
        - int(baseline_metrics["used_engineers"]),
        "total_distance_m": int(plan_metrics["total_distance_m"])
        - int(baseline_metrics["total_distance_m"]),
        "total_distance_km": round(
            float(plan_metrics["total_distance_km"])
            - float(baseline_metrics["total_distance_km"]),
            3,
        ),
    }


def build_replanned_plan(
    parent: dict[str, Any],
    event: dict[str, Any],
    *,
    solve_time_limit_ms: int = 5_000,
) -> tuple[dict[str, Any], dict[str, Any]]:
    event_time = _dt(event["event_time"])
    jobs = copy.deepcopy(parent["jobs"])
    engineers = copy.deepcopy(parent["engineers"])
    jobs_by_id = {str(job["id"]): job for job in jobs}
    routes_by_engineer = _route_index(parent)
    completed_ids = _completed_ids(parent, event_time)

    cancel_id: str | None = None
    priority_event_job_id: str | None = None
    if event["type"] == "cancel_job":
        cancel_id = str(event.get("job_id") or "")
        if cancel_id not in jobs_by_id:
            raise ReplanValidationError(f"Заявка {cancel_id} не найдена.")
        if cancel_id in completed_ids:
            raise ReplanValidationError("Нельзя отменить уже завершённую заявку.")
    elif event["type"] == "engineer_unavailable":
        engineer_id = str(event.get("engineer_id") or "")
        if engineer_id not in {str(item["id"]) for item in engineers}:
            raise ReplanValidationError(f"Инженер {engineer_id} не найден.")
    elif event["type"] == "emergency_job":
        coords = event.get("coords")
        if not isinstance(coords, list) or len(coords) != 2:
            raise ReplanValidationError("Для аварийной заявки нужны координаты [lat, lon].")
        emergency = _event_job(event, jobs)
        priority_event_job_id = str(emergency["id"])
        jobs.insert(0, emergency)
        jobs_by_id[str(emergency["id"])] = emergency
    else:
        raise ReplanValidationError(f"Неизвестный тип события: {event['type']}.")

    locked_routes: dict[str, list[dict[str, Any]]] = {}
    locked_ids: set[str] = set()
    adjusted_engineers: list[dict[str, Any]] = []
    for engineer in engineers:
        engineer_id = str(engineer["id"])
        route = routes_by_engineer.get(
            engineer_id,
            {"engineer_id": engineer_id, "stops": []},
        )
        current_stop = _find_current_stop(route, jobs_by_id, event_time)
        current_id = str(current_stop["job_id"]) if current_stop else None
        if cancel_id and current_id == cancel_id:
            raise ReplanValidationError(
                "Нельзя отменить заявку: инженер уже в пути или начал работу."
            )

        locked: list[dict[str, Any]] = []
        ready_at = max(_dt(engineer["shift_start"]), event_time)
        start_point = copy.deepcopy(engineer["start_point"])
        completed_stops = [
            stop
            for stop in route["stops"]
            if str(stop["job_id"]) in completed_ids
        ]
        if completed_stops:
            last_completed = completed_stops[-1]
            start_point = copy.deepcopy(last_completed["coords"])
        if current_stop is not None:
            locked_stop = copy.deepcopy(current_stop)
            locked_stop["sequence"] = 1
            locked.append(locked_stop)
            locked_ids.add(current_id)
            start_point = copy.deepcopy(current_stop["coords"])
            ready_at = max(ready_at, _dt(current_stop["service_end"]))
            current_job = jobs_by_id[current_id]
            current_job["status"] = (
                "in_progress"
                if event_time >= _dt(current_stop["service_start"])
                else "en_route"
            )
            current_job["assignment_status"] = "locked"
            current_job["assigned_engineer_id"] = engineer_id
            current_job["explanation"] = (
                "Текущий этап зафиксирован: работа в пути или уже начата и не прерывается."
            )
        locked_routes[engineer_id] = locked

        adjusted = copy.deepcopy(engineer)
        adjusted["start_point"] = start_point
        shift_end = _dt(engineer["shift_end"])
        if ready_at > shift_end and not locked:
            # Инженер с уже завершившейся сменой остаётся допустимым пустым
            # vehicle в модели, но не может получить хвост после события.
            adjusted["shift_start"] = shift_end.isoformat()
            adjusted["status"] = "Недоступен"
        else:
            adjusted["shift_start"] = ready_at.isoformat()
        if (
            event["type"] == "engineer_unavailable"
            and engineer_id == str(event.get("engineer_id"))
        ):
            adjusted["status"] = event.get("status") or "Недоступен"
            engineer["status"] = adjusted["status"]
        adjusted_engineers.append(adjusted)

    for job in jobs:
        job_id = str(job["id"])
        status = str(job.get("status") or "planned")
        if job_id in completed_ids or status in {"completed", "done"}:
            job.update(
                status="completed",
                assignment_status="excluded",
                exclusion_reason="Заявка завершена до события и не перепланируется.",
            )
        elif status == "cancelled" or job_id == cancel_id:
            job.update(
                status="cancelled",
                assignment_status="excluded",
                assigned_engineer_id=None,
                exclusion_reason="Заявка отменена и исключена из нового расчёта.",
            )

    remaining_jobs = [
        copy.deepcopy(job)
        for job in jobs
        if str(job["id"]) not in completed_ids | locked_ids
        and str(job.get("status")) not in TERMINAL_STATUSES
    ]
    suffix = build_plan_with_comparison(
        remaining_jobs,
        adjusted_engineers,
        scenario=str(parent["scenario_id"]),
        engine=str(parent["engine"]),
        solve_time_limit_ms=solve_time_limit_ms,
    )

    suffix_jobs = {str(job["id"]): job for job in suffix["jobs"]}
    merged_jobs: list[dict[str, Any]] = []
    for job in jobs:
        job_id = str(job["id"])
        merged_jobs.append(copy.deepcopy(suffix_jobs.get(job_id, job)))

    suffix_routes = _route_index(suffix)
    merged_routes: list[dict[str, Any]] = []
    for engineer in engineers:
        engineer_id = str(engineer["id"])
        suffix_route = copy.deepcopy(
            suffix_routes.get(
                engineer_id,
                {
                    "engineer_id": engineer_id,
                    "start_point": engineer["start_point"],
                    "stops": [],
                    "distance_m": 0,
                    "distance_km": 0.0,
                    "travel_min": 0,
                    "waiting_min": 0,
                    "service_min": 0,
                    "jobs_count": 0,
                },
            )
        )
        locked = copy.deepcopy(locked_routes.get(engineer_id, []))
        stops = locked + suffix_route["stops"]
        for sequence, stop in enumerate(stops, start=1):
            stop["sequence"] = sequence
        distance_m = sum(int(stop["distance_m_from_prev"]) for stop in stops)
        merged_routes.append(
            {
                "engineer_id": engineer_id,
                "start_point": copy.deepcopy(engineer["start_point"]),
                "stops": stops,
                "distance_m": distance_m,
                "distance_km": round(distance_m / 1000.0, 3),
                "travel_min": sum(int(stop["travel_min_from_prev"]) for stop in stops),
                "waiting_min": sum(int(stop["waiting_min"]) for stop in stops),
                "service_min": sum(int(stop["service_min"]) for stop in stops),
                "jobs_count": len(stops),
            }
        )

    plan = copy.deepcopy(suffix)
    plan.update(
        schema_version="0.3",
        jobs=merged_jobs,
        engineers=engineers,
        routes=merged_routes,
        replan={
            "event_time": event_time.isoformat(),
            "locked_job_ids": sorted(locked_ids),
            "completed_job_ids": sorted(completed_ids),
            "scope": "remaining_day",
            "priority_event_job_id": priority_event_job_id,
        },
    )
    plan["metrics"] = _recompute_metrics(merged_routes, merged_jobs)
    plan["baseline_metrics"] = _merge_locked_metrics(
        suffix["baseline_metrics"], locked_routes
    )
    plan["optimized_metrics"] = (
        plan["metrics"] if plan["engine"] == "ortools_vrptw" else None
    )
    comparison = plan.get("comparison") or {}
    if comparison.get("baseline"):
        comparison["baseline"]["metrics"] = copy.deepcopy(plan["baseline_metrics"])
    if comparison.get("optimized") and plan["engine"] == "ortools_vrptw":
        comparison["optimized"]["metrics"] = copy.deepcopy(plan["metrics"])
    if comparison.get("plan"):
        comparison["plan"]["metrics"] = copy.deepcopy(plan["metrics"])
    comparison["delta_plan_minus_baseline"] = _delta(
        plan["metrics"], plan["baseline_metrics"]
    )
    comparison["scope"] = "remaining_day_with_locked_current_stage"
    plan["comparison"] = comparison

    diff = build_diff(
        parent,
        plan,
        completed_ids=completed_ids,
        priority_event_job_id=priority_event_job_id,
    )
    plan["diff"] = diff
    validate_plan(plan)
    return plan, diff
