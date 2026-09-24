from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services import ortools_vrptw
from backend.app.services.ortools_vrptw import build_ortools_plan
from backend.app.services.planner import build_plan_with_comparison


def _engineer(
    engineer_id: str = "eng-1",
    *,
    skills: list[str] | None = None,
    vehicle: str = "Автомобиль",
    shift_start: str = "2026-08-17T08:00:00+03:00",
    shift_end: str = "2026-08-17T18:00:00+03:00",
) -> dict:
    return {
        "id": engineer_id,
        "name": engineer_id,
        "scenario_id": "east",
        "skills": skills if skills is not None else ["connect"],
        "vehicle": vehicle,
        "shift_start": shift_start,
        "shift_end": shift_end,
        "start_point": [55.75, 37.61],
        "status": "Доступен",
    }


def _job(
    job_id: str = "job-1",
    *,
    skill: str = "connect",
    vehicle: str | None = None,
    coords: list[float] | None = None,
    window_start: str = "2026-08-17T09:00:00+03:00",
    window_end: str = "2026-08-17T17:00:00+03:00",
    duration_min: int = 30,
) -> dict:
    return {
        "id": job_id,
        "input_order": 1,
        "type": "Подключение",
        "skill": skill,
        "priority": "Обычная",
        "address": job_id,
        "coords": coords or [55.751, 37.611],
        "window_start": window_start,
        "window_end": window_end,
        "duration_min": duration_min,
        "required_vehicle": vehicle,
        "status": "planned",
    }


def _plan(jobs: list[dict], engineers: list[dict]) -> dict:
    return build_ortools_plan(
        jobs,
        engineers,
        scenario="east",
        solve_time_limit_ms=100,
    )


def test_ortools_assigns_feasible_job() -> None:
    plan = _plan([_job()], [_engineer()])
    assert plan["engine"] == "ortools_vrptw"
    assert plan["metrics"]["assigned_count"] == 1
    assert plan["routes"][0]["stops"][0]["job_id"] == "job-1"


def test_ortools_rejects_missing_skill_and_required_vehicle() -> None:
    no_skill = _plan([_job(skill="emergency")], [_engineer(skills=["connect"])])
    assert no_skill["jobs"][0]["unassigned_reason"]["code"] == "NO_SKILL"

    no_vehicle = _plan(
        [_job(vehicle="Автомобиль")],
        [_engineer(vehicle="Пешеход")],
    )
    assert no_vehicle["jobs"][0]["unassigned_reason"]["code"] == "NO_VEHICLE"


def test_ortools_rejects_window_and_shift_violations() -> None:
    missed_window = _plan(
        [
            _job(
                window_start="2026-08-17T08:00:00+03:00",
                window_end="2026-08-17T08:00:00+03:00",
            )
        ],
        [_engineer()],
    )
    assert missed_window["jobs"][0]["unassigned_reason"]["code"] == "OUT_OF_WINDOW"

    over_shift = _plan(
        [
            _job(
                window_start="2026-08-17T17:50:00+03:00",
                window_end="2026-08-17T18:00:00+03:00",
                duration_min=30,
            )
        ],
        [_engineer()],
    )
    assert over_shift["jobs"][0]["unassigned_reason"]["code"] == "OUT_OF_SHIFT"


def test_ortools_records_waiting_for_early_arrival() -> None:
    plan = _plan(
        [
            _job(
                window_start="2026-08-17T10:00:00+03:00",
                window_end="2026-08-17T11:00:00+03:00",
            )
        ],
        [_engineer()],
    )
    stop = plan["routes"][0]["stops"][0]
    assert stop["waiting_min"] > 0
    assert datetime.fromisoformat(stop["arrival_time"]) < datetime.fromisoformat(
        stop["service_start"]
    )


def test_last_service_may_finish_exactly_at_shift_end() -> None:
    plan = _plan(
        [
            _job(
                window_start="2026-08-17T17:30:00+03:00",
                window_end="2026-08-17T17:30:00+03:00",
                duration_min=30,
            )
        ],
        [_engineer()],
    )
    stop = plan["routes"][0]["stops"][0]
    assert stop["service_end"] == "2026-08-17T18:00:00+03:00"


def test_api_returns_real_ortools_engine_not_greedy_alias() -> None:
    client = TestClient(app)
    baseline = client.post(
        "/optimize",
        json={"scenario": "east", "engine": "baseline_greedy"},
    )
    optimized = client.post(
        "/optimize",
        json={
            "scenario": "east",
            "engine": "ortools_vrptw",
            "solve_time_limit_ms": 200,
        },
    )
    assert baseline.status_code == 200
    assert optimized.status_code == 200
    assert baseline.json()["engine"] == "baseline_greedy"
    assert optimized.json()["engine"] == "ortools_vrptw"
    assert optimized.json()["solver_status_detail"].startswith("ROUTING_")


def test_api_does_not_fallback_to_greedy_when_ortools_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(ortools_vrptw, "ORTOOLS_IMPORT_ERROR", ImportError("test"))
    response = TestClient(app).post(
        "/optimize",
        json={"scenario": "east", "engine": "ortools_vrptw"},
    )
    body = response.json()
    assert response.status_code == 503
    assert body["engine"] == "ortools_vrptw"
    assert body["solver_status"] == "unavailable"
    assert body["warnings"]
    assert "routes" not in body


def test_comparison_uses_same_matrix_and_service_norms() -> None:
    jobs = [_job("job-1"), _job("job-2", coords=[55.752, 37.612])]
    engineers = [_engineer()]
    plan = build_plan_with_comparison(
        jobs,
        engineers,
        scenario="east",
        engine="ortools_vrptw",
        solve_time_limit_ms=100,
    )
    comparison = plan["comparison"]
    matrix_id = comparison["shared_matrix_id"]
    assert comparison["same_input"] is True
    assert comparison["baseline"]["travel_model"]["matrix_id"] == matrix_id
    assert comparison["optimized"]["travel_model"]["matrix_id"] == matrix_id
    assert comparison["travel_model"]["service_norm_travel_added"] is False
    assert plan["travel_model"]["service_norm_travel_added"] is False


def test_closed_jobs_are_excluded_and_route_is_open() -> None:
    completed = _job("done")
    completed["status"] = "completed"
    active = _job("active")
    plan = _plan([completed, active], [_engineer()])
    assert plan["metrics"]["excluded_count"] == 1
    assert plan["jobs"][0]["assignment_status"] == "excluded"
    route = plan["routes"][0]
    assert route["jobs_count"] == 1
    assert route["distance_m"] == route["stops"][0]["distance_m_from_prev"]


def test_objective_prefers_coverage_before_engine_count() -> None:
    engineers = [
        _engineer("connect-engineer", skills=["connect"]),
        _engineer("emergency-engineer", skills=["emergency"]),
    ]
    plan = _plan(
        [_job("connect-job"), _job("emergency-job", skill="emergency")],
        engineers,
    )
    assert plan["metrics"]["assigned_count"] == 2
    assert plan["metrics"]["used_engineers"] == 2


def test_objective_prefers_fewer_engineers_then_shorter_distance() -> None:
    two_jobs = [
        _job("job-1", coords=[55.751, 37.611]),
        _job("job-2", coords=[55.752, 37.612]),
    ]
    engineers = [
        _engineer("near"),
        {**_engineer("far"), "start_point": [55.95, 37.90]},
    ]
    plan = _plan(two_jobs, engineers)
    assert plan["metrics"]["assigned_count"] == 2
    assert plan["metrics"]["used_engineers"] == 1
    assert plan["routes"][0]["jobs_count"] == 2
