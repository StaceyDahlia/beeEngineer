from __future__ import annotations

from copy import deepcopy

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services.baseline_greedy import build_baseline_plan


def _engineer(engineer_id: str, skills: list[str], vehicle: str = "Автомобиль") -> dict:
    return {
        "id": engineer_id,
        "name": engineer_id,
        "scenario_id": "east",
        "skills": skills,
        "vehicle": vehicle,
        "shift_start": "2026-08-17T08:00:00+03:00",
        "shift_end": "2026-08-17T18:00:00+03:00",
        "start_point": [55.75, 37.61],
        "status": "Доступен",
    }


def _job(job_id: str, order: int, *, skill: str = "connect", vehicle: str | None = None) -> dict:
    return {
        "id": job_id,
        "input_order": order,
        "type": "Подключение",
        "skill": skill,
        "priority": "Обычная",
        "address": job_id,
        "coords": [55.751 + order * 0.001, 37.611],
        "window_start": "2026-08-17T08:00:00+03:00",
        "window_end": "2026-08-17T17:00:00+03:00",
        "duration_min": 30,
        "required_vehicle": vehicle,
        "status": "planned",
    }


def test_first_eligible_engineer_and_input_order() -> None:
    jobs = [_job("second", 2), _job("first", 1)]
    engineers = [_engineer("eng-1", ["connect"]), _engineer("eng-2", ["connect"])]
    plan = build_baseline_plan(deepcopy(jobs), deepcopy(engineers), scenario="east")

    assert [stop["job_id"] for stop in plan["routes"][0]["stops"]] == ["first", "second"]
    assert plan["routes"][1]["stops"] == []
    assert plan["metrics"]["used_engineers"] == 1


def test_unassigned_reason_for_skill_mismatch() -> None:
    plan = build_baseline_plan(
        [_job("emergency", 0, skill="emergency")],
        [_engineer("eng-1", ["connect"])],
        scenario="east",
    )
    job = plan["jobs"][0]
    assert job["status"] == "unassigned"
    assert job["unassigned_reason"]["code"] == "NO_SKILL"
    assert job["explanation"]


def test_transport_is_hard_constraint() -> None:
    plan = build_baseline_plan(
        [_job("car-only", 0, vehicle="Автомобиль")],
        [_engineer("eng-1", ["connect"], vehicle="Пешеход")],
        scenario="east",
    )
    assert plan["jobs"][0]["unassigned_reason"]["code"] == "NO_VEHICLE"


def test_time_window_and_shift_are_hard_constraints() -> None:
    late_job = _job("late", 0)
    late_job["window_start"] = "2026-08-17T19:00:00+03:00"
    late_job["window_end"] = "2026-08-17T20:00:00+03:00"
    plan = build_baseline_plan(
        [late_job],
        [_engineer("eng-1", ["connect"])],
        scenario="east",
    )
    assert plan["jobs"][0]["status"] == "unassigned"
    assert plan["jobs"][0]["unassigned_reason"]["code"] == "OUT_OF_SHIFT"


def test_service_duration_does_not_include_extra_normative_travel() -> None:
    plan = build_baseline_plan(
        [_job("job", 0)],
        [_engineer("eng-1", ["connect"])],
        scenario="east",
    )
    stop = plan["routes"][0]["stops"][0]
    assert stop["service_min"] == 30
    assert plan["metrics"]["total_service_min"] == 30
    assert plan["travel_model"]["service_norm_travel_added"] is False


def test_http_vertical_slice() -> None:
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    jobs = client.get("/data/jobs", params={"scenario": "east"})
    assert jobs.status_code == 200
    assert jobs.json()["count"] == 66
    response = client.post(
        "/optimize",
        json={"scenario": "east", "engine": "baseline_greedy"},
    )
    assert response.status_code == 200
    plan = response.json()
    assert plan["engine"] == "baseline_greedy"
    assert plan["metrics"]["assigned_count"] + plan["metrics"]["unassigned_count"] == 66
