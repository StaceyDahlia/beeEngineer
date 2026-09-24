from __future__ import annotations

from datetime import datetime
from typing import Any


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


def validate_plan(plan: dict[str, Any]) -> None:
    """Проверяет инварианты результата независимо от алгоритма."""
    jobs = {str(job["id"]): job for job in plan["jobs"]}
    engineers = {str(engineer["id"]): engineer for engineer in plan["engineers"]}
    if len(jobs) != len(plan["jobs"]):
        raise AssertionError("В плане обнаружены повторяющиеся job.id.")

    seen: set[str] = set()
    distance_m = 0
    used = 0
    for route in plan["routes"]:
        engineer_id = str(route["engineer_id"])
        if engineer_id not in engineers:
            raise AssertionError(f"Неизвестный инженер маршрута {engineer_id}.")
        engineer = engineers[engineer_id]
        if route["stops"]:
            used += 1
        route_distance = 0
        previous_end: datetime | None = None
        for expected_sequence, stop in enumerate(route["stops"], start=1):
            job_id = str(stop["job_id"])
            if job_id in seen:
                raise AssertionError(f"Заявка {job_id} назначена более одного раза.")
            seen.add(job_id)
            if stop["sequence"] != expected_sequence:
                raise AssertionError(f"Нарушен порядок остановок для {job_id}.")

            job = jobs[job_id]
            if str(job.get("assigned_engineer_id")) != engineer_id:
                raise AssertionError(f"Назначение заявки {job_id} не совпадает с маршрутом.")
            if str(job.get("skill") or "") not in set(engineer.get("skills") or []):
                raise AssertionError(f"У инженера нет навыка для заявки {job_id}.")
            if not _same_vehicle(
                job.get("required_vehicle"), str(engineer.get("vehicle") or "")
            ):
                raise AssertionError(f"Транспорт не подходит для заявки {job_id}.")
            if not _has_equipment(job, engineer):
                raise AssertionError(f"Оборудование не подходит для заявки {job_id}.")
            arrival = _dt(stop["arrival_time"])
            start = _dt(stop["service_start"])
            end = _dt(stop["service_end"])
            if not (arrival <= start <= end):
                raise AssertionError(f"Некорректное расписание заявки {job_id}.")
            if start < _dt(job["window_start"]) or start > _dt(job["window_end"]):
                raise AssertionError(f"Старт заявки {job_id} вне клиентского окна.")
            if start < _dt(engineer["shift_start"]) or end > _dt(engineer["shift_end"]):
                raise AssertionError(f"Заявка {job_id} не помещается в смену.")
            duration_min = int((end - start).total_seconds() // 60)
            if duration_min != int(job["duration_min"]):
                raise AssertionError(f"Неверная длительность заявки {job_id}.")
            waiting_min = int((start - arrival).total_seconds() // 60)
            if waiting_min != int(stop["waiting_min"]):
                raise AssertionError(f"Неверное ожидание для заявки {job_id}.")
            if previous_end is not None and arrival < previous_end:
                raise AssertionError(f"Маршрут инженера пересекается по времени на {job_id}.")
            previous_end = end
            route_distance += int(stop["distance_m_from_prev"])

        if route_distance != int(route["distance_m"]):
            raise AssertionError(f"Не сходится расстояние маршрута {route['engineer_id']}.")
        distance_m += route_distance

    routed_statuses = {"assigned", "in_progress", "en_route"}
    assigned = {
        str(job["id"])
        for job in plan["jobs"]
        if str(job.get("status")) in routed_statuses
    }
    if assigned != seen:
        raise AssertionError("Статусы заявок и остановки маршрутов расходятся.")
    if int(plan["metrics"]["used_engineers"]) != used:
        raise AssertionError("Метрика used_engineers не совпадает с маршрутами.")
    if int(plan["metrics"]["total_distance_m"]) != distance_m:
        raise AssertionError("Метрика total_distance_m не совпадает с маршрутами.")
