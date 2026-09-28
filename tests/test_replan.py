from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services.plan_store import plan_store
from backend.app.services.planner import build_plan_with_comparison
from backend.app.services.replanner import build_replanned_plan


ROOT = Path(__file__).resolve().parents[1]


def _engineer(engineer_id: str, start: list[float] | None = None) -> dict:
    return {
        "id": engineer_id,
        "name": engineer_id,
        "scenario_id": "east",
        "skills": ["connect", "emergency"],
        "vehicle": "Автомобиль",
        "shift_start": "2026-08-17T08:00:00+03:00",
        "shift_end": "2026-08-17T18:00:00+03:00",
        "start_point": start or [55.75, 37.61],
        "status": "Доступен",
    }


def _job(
    job_id: str,
    order: int,
    *,
    window_start: str,
    window_end: str,
    coords: list[float],
    duration_min: int = 60,
) -> dict:
    return {
        "id": job_id,
        "input_order": order,
        "type": "Подключение",
        "skill": "connect",
        "priority": "Обычная",
        "address": job_id,
        "coords": coords,
        "window_start": window_start,
        "window_end": window_end,
        "duration_min": duration_min,
        "required_vehicle": None,
        "status": "planned",
    }


def _parent(*, engine: str = "baseline_greedy", two_engineers: bool = False) -> dict:
    jobs = [
        _job(
            "job-a",
            1,
            window_start="2026-08-17T08:00:00+03:00",
            window_end="2026-08-17T11:00:00+03:00",
            coords=[55.751, 37.611],
        ),
        _job(
            "job-b",
            2,
            window_start="2026-08-17T12:00:00+03:00",
            window_end="2026-08-17T17:00:00+03:00",
            coords=[55.752, 37.612],
            duration_min=30,
        ),
    ]
    engineers = [_engineer("eng-1")]
    if two_engineers:
        engineers.append(_engineer("eng-2", [55.76, 37.62]))
    return build_plan_with_comparison(
        jobs,
        engineers,
        scenario="east",
        engine=engine,
        solve_time_limit_ms=200,
    )


def _route_job_ids(plan: dict) -> list[str]:
    return [str(stop["job_id"]) for route in plan["routes"] for stop in route["stops"]]


@pytest.fixture(autouse=True)
def _clean_store() -> None:
    plan_store.clear()
    yield
    plan_store.clear()


def test_cancelled_job_is_removed_from_new_route() -> None:
    parent = _parent()
    plan, diff = build_replanned_plan(
        parent,
        {
            "event_id": "cancel-1",
            "type": "cancel_job",
            "event_time": "2026-08-17T08:30:00+03:00",
            "job_id": "job-b",
        },
        solve_time_limit_ms=100,
    )
    assert "job-b" not in _route_job_ids(plan)
    assert next(job for job in plan["jobs"] if job["id"] == "job-b")["status"] == "cancelled"
    assert [item["job_id"] for item in diff["cancelled"]] == ["job-b"]


def test_completed_job_is_not_replanned() -> None:
    parent = _parent()
    next(job for job in parent["jobs"] if job["id"] == "job-a")["status"] = "completed"
    plan, _ = build_replanned_plan(
        parent,
        {
            "event_id": "unavailable-completed",
            "type": "engineer_unavailable",
            "event_time": "2026-08-17T10:00:00+03:00",
            "engineer_id": "eng-1",
            "status": "Недоступен",
        },
        solve_time_limit_ms=100,
    )
    assert "job-a" not in _route_job_ids(plan)
    completed = next(job for job in plan["jobs"] if job["id"] == "job-a")
    assert completed["status"] == "completed"
    assert completed["assignment_status"] == "excluded"


def test_unavailable_engineer_finishes_current_stage_then_loses_tail() -> None:
    parent = _parent(two_engineers=True)
    first = parent["routes"][0]["stops"][0]
    plan, _ = build_replanned_plan(
        parent,
        {
            "event_id": "unavailable-1",
            "type": "engineer_unavailable",
            "event_time": "2026-08-17T08:30:00+03:00",
            "engineer_id": "eng-1",
            "status": "Недоступен",
        },
        solve_time_limit_ms=100,
    )
    route_1 = next(route for route in plan["routes"] if route["engineer_id"] == "eng-1")
    assert [stop["job_id"] for stop in route_1["stops"]] == [first["job_id"]]
    assert any(
        stop["job_id"] == "job-b"
        for route in plan["routes"]
        if route["engineer_id"] != "eng-1"
        for stop in route["stops"]
    )


def test_emergency_does_not_interrupt_in_progress_and_gets_event_window() -> None:
    parent = _parent(engine="ortools_vrptw")
    current_stop = parent["routes"][0]["stops"][0]
    current_id = str(current_stop["job_id"])
    next(job for job in parent["jobs"] if str(job["id"]) == current_id)["status"] = "in_progress"
    event_time = "2026-08-17T08:30:00+03:00"
    plan, diff = build_replanned_plan(
        parent,
        {
            "event_id": "emergency-1",
            "type": "emergency_job",
            "event_time": event_time,
            "title": "Аварийное восстановление",
            "address": "Тестовый адрес",
            "coords": [55.753, 37.613],
            "required_vehicle": None,
        },
        solve_time_limit_ms=300,
    )
    route = plan["routes"][0]
    assert route["stops"][0]["job_id"] == current_id
    assert route["stops"][0]["service_end"] == current_stop["service_end"]

    emergency = next(job for job in plan["jobs"] if job["id"] == "emergency:emergency-1")
    assert emergency["skill"] == "emergency"
    assert emergency["duration_min"] == 80
    assert emergency["window_start"] == event_time
    assert emergency["window_end"] == "2026-08-17T10:30:00+03:00"
    emergency_stop = next(
        stop for route in plan["routes"] for stop in route["stops"]
        if stop["job_id"] == emergency["id"]
    )
    assert datetime.fromisoformat(emergency_stop["service_start"]) >= datetime.fromisoformat(
        current_stop["service_end"]
    )
    assert any(item["job_id"] == emergency["id"] for item in diff["assigned"])


def _create_east_baseline(client: TestClient) -> dict:
    response = client.post(
        "/plans",
        json={"scenario": "east", "engine": "baseline_greedy"},
    )
    assert response.status_code == 200
    return response.json()


def _cancel_event(plan: dict, event_id: str, job_id: str) -> dict:
    return {
        "event_id": event_id,
        "type": "cancel_job",
        "event_time": "2026-08-17T07:00:00+03:00",
        "base_version": plan["version"],
        "job_id": job_id,
        "solve_time_limit_ms": 100,
    }


def test_same_event_or_preview_is_not_applied_twice() -> None:
    client = TestClient(app)
    plan = _create_east_baseline(client)
    job_ids = _route_job_ids(plan)
    preview = client.post(
        f"/plans/{plan['plan_id']}/events/preview",
        json=_cancel_event(plan, "event-once", job_ids[0]),
    )
    assert preview.status_code == 200
    preview_id = preview.json()["preview_id"]
    applied = client.post(
        f"/plans/{plan['plan_id']}/events/apply",
        json={"preview_id": preview_id},
    )
    assert applied.status_code == 200
    assert applied.json()["version"] == 2

    repeated_apply = client.post(
        f"/plans/{plan['plan_id']}/events/apply",
        json={"preview_id": preview_id},
    )
    assert repeated_apply.status_code == 409

    duplicate_event = client.post(
        f"/plans/{applied.json()['plan_id']}/events/preview",
        json=_cancel_event(applied.json(), "event-once", job_ids[1]),
    )
    assert duplicate_event.status_code == 409
    history = client.get(f"/plans/{applied.json()['plan_id']}/history").json()
    assert history["count"] == 2


def test_stale_preview_is_rejected() -> None:
    client = TestClient(app)
    plan = _create_east_baseline(client)
    job_ids = _route_job_ids(plan)
    first = client.post(
        f"/plans/{plan['plan_id']}/events/preview",
        json=_cancel_event(plan, "event-first", job_ids[0]),
    ).json()
    stale = client.post(
        f"/plans/{plan['plan_id']}/events/preview",
        json=_cancel_event(plan, "event-stale", job_ids[1]),
    ).json()
    assert client.post(
        f"/plans/{plan['plan_id']}/events/apply",
        json={"preview_id": first["preview_id"]},
    ).status_code == 200
    rejected = client.post(
        f"/plans/{plan['plan_id']}/events/apply",
        json={"preview_id": stale["preview_id"]},
    )
    assert rejected.status_code == 409


def test_ui_contract_receives_diff_and_applies_new_version() -> None:
    client = TestClient(app)
    plan = _create_east_baseline(client)
    target = _route_job_ids(plan)[0]
    preview_response = client.post(
        f"/plans/{plan['plan_id']}/events/preview",
        json=_cancel_event(plan, "ui-diff", target),
    )
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["diff"]["cancelled"][0]["job_id"] == target
    assert preview["proposed_version"] == 2

    applied = client.post(
        f"/plans/{plan['plan_id']}/events/apply",
        json={"preview_id": preview["preview_id"]},
    ).json()
    assert applied["version"] == 2
    assert applied["parent_plan_id"] == plan["plan_id"]
    assert applied["diff"]["cancelled"][0]["job_id"] == target

    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    api = (ROOT / "frontend" / "src" / "api.js").read_text(encoding="utf-8")
    assert "BeelineApi.previewEvent" in html
    assert "response.diff" in html
    assert "BeelineApi.applyEvent" in html
    assert "Backend diff" in html
    assert "state.planHistory" in html
    assert "/events/preview" in api and "/events/apply" in api
