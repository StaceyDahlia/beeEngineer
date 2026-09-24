from __future__ import annotations

import json
from pathlib import Path
import subprocess

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services.loader import SUPPORTED_SCENARIOS, load_scenario
from backend.app.services.ortools_vrptw import (
    PRIORITY_CLASSES,
    build_ortools_plan,
    derive_priority_penalties,
)
from backend.app.services.planner import build_plan_with_comparison
from backend.app.services.replanner import build_replanned_plan


ROOT = Path(__file__).resolve().parents[1]


def _engineer() -> dict:
    return {
        "id": "east:eng-priority",
        "name": "Приоритетная бригада",
        "scenario_id": "east",
        "skills": ["connect", "local", "emergency"],
        "vehicle": "Автомобиль",
        "shift_start": "2026-08-17T08:00:00+03:00",
        "shift_end": "2026-08-17T09:30:00+03:00",
        "start_point": [55.75, 37.61],
        "status": "Доступен",
    }


def _job(job_id: str, job_type: str, skill: str, order: int) -> dict:
    return {
        "id": job_id,
        "input_order": order,
        "type": job_type,
        "subtype": job_type,
        "skill": skill,
        "priority": "Срочная" if skill == "emergency" else "Обычная",
        "address": job_id,
        "coords": [55.751, 37.611],
        "window_start": "2026-08-17T08:00:00+03:00",
        "window_end": "2026-08-17T09:30:00+03:00",
        "duration_min": 80,
        "required_vehicle": None,
        "status": "planned",
    }


def _assigned_ids(plan: dict) -> set[str]:
    return {
        str(stop["job_id"])
        for route in plan["routes"]
        for stop in route["stops"]
    }


def _solve(jobs: list[dict], solve_time_limit_ms: int = 250) -> dict:
    return build_ortools_plan(
        jobs,
        [_engineer()],
        scenario="east",
        solve_time_limit_ms=solve_time_limit_ms,
    )


def test_emergency_has_priority_over_connection() -> None:
    plan = _solve(
        [
            _job("east:connection", "Подключение", "connect", 1),
            _job("east:emergency", "Авария", "emergency", 2),
        ]
    )
    assert _assigned_ids(plan) == {"east:emergency"}
    displaced = next(job for job in plan["jobs"] if job["id"] == "east:connection")
    assert displaced["unassigned_reason"]["code"] == "DISPLACED_BY_HIGHER_PRIORITY"


def test_connection_has_priority_over_repair_and_additional_order() -> None:
    plan = _solve(
        [
            _job("east:additional", "Дозаказ", "connect", 1),
            _job("east:repair", "Локальная заявка", "local", 2),
            _job("east:connection", "Подключение", "connect", 3),
        ]
    )
    assert _assigned_ids(plan) == {"east:connection"}

    repair_vs_additional = _solve(
        [
            _job("east:additional", "Дозаказ", "connect", 1),
            _job("east:repair", "Локальная заявка", "local", 2),
        ]
    )
    assert _assigned_ids(repair_vs_additional) == {"east:repair"}


def test_emergency_penalty_dominates_all_ordinary_jobs_and_operating_cost() -> None:
    jobs = [
        _job("east:additional", "Дозаказ", "connect", 1),
        _job("east:repair", "Локальная заявка", "local", 2),
        _job("east:connection", "Подключение", "connect", 3),
        _job("east:emergency", "Авария", "emergency", 4),
    ]
    bounds = derive_priority_penalties(
        jobs,
        distance_upper_bound=100_000,
        vehicle_count=1,
    )
    penalties = bounds["penalties"]
    ordinary_skip_bound = sum(
        bounds["counts"][name] * penalties[name]
        for name in PRIORITY_CLASSES
        if name != "emergency"
    )
    assert penalties["emergency"] > ordinary_skip_bound + bounds["operating_cost_upper_bound"]
    assert _assigned_ids(_solve(jobs)) == {"east:emergency"}


def test_emergency_replan_keeps_current_stage_and_marks_priority_displacement() -> None:
    engineer = _engineer()
    engineer["shift_end"] = "2026-08-17T10:00:00+03:00"
    ordinary = _job("east:ordinary", "Подключение", "connect", 1)
    ordinary["window_end"] = "2026-08-17T10:00:00+03:00"
    parent = build_plan_with_comparison(
        [ordinary],
        [engineer],
        scenario="east",
        engine="ortools_vrptw",
        solve_time_limit_ms=250,
    )
    current = parent["routes"][0]["stops"][0]
    current_job = next(job for job in parent["jobs"] if job["id"] == current["job_id"])
    current_job["status"] = "in_progress"

    plan, diff = build_replanned_plan(
        parent,
        {
            "event_id": "priority-emergency",
            "type": "emergency_job",
            "event_time": current["service_start"],
            "title": "Аварийное восстановление",
            "address": "Тестовый адрес",
            "coords": [55.752, 37.612],
            "required_vehicle": None,
        },
        solve_time_limit_ms=300,
    )
    assert plan["routes"][0]["stops"][0]["job_id"] == current["job_id"]
    assert plan["routes"][0]["stops"][0]["service_end"] == current["service_end"]
    # Текущая работа занимает ресурс, поэтому авария может остаться неназначенной;
    # это проверяет неизменность зафиксированного этапа, а не обещает покрытие.
    assert plan["replan"]["locked_job_ids"] == [current["job_id"]]
    assert diff["priority_impact"]["event_job_id"] == "emergency:priority-emergency"


def test_emergency_replan_explains_lower_priority_job_removed_for_emergency() -> None:
    ordinary = _job("east:ordinary", "Подключение", "connect", 1)
    parent = build_plan_with_comparison(
        [ordinary],
        [_engineer()],
        scenario="east",
        engine="ortools_vrptw",
        solve_time_limit_ms=250,
    )
    plan, diff = build_replanned_plan(
        parent,
        {
            "event_id": "displace-ordinary",
            "type": "emergency_job",
            "event_time": "2026-08-17T08:00:00+03:00",
            "title": "Аварийное восстановление",
            "address": "Тестовый адрес",
            "coords": [55.751, 37.611],
            "required_vehicle": None,
        },
        solve_time_limit_ms=300,
    )
    assert _assigned_ids(plan) == {"emergency:displace-ordinary"}
    removed = next(item for item in diff["unassigned"] if item["job_id"] == "east:ordinary")
    assert removed["caused_by_priority_event"] is True
    assert removed["affected_priority_class"] == "connection"
    assert "ради аварийной заявки" in removed["message"]
    ordinary_after = next(job for job in plan["jobs"] if job["id"] == "east:ordinary")
    assert ordinary_after["unassigned_reason"]["code"] == "DISPLACED_BY_HIGHER_PRIORITY"
    assert ordinary_after["replan_priority_impact"]["category"] == "unassigned"


def test_scenarios_are_allowlisted_and_never_mix_regional_ids() -> None:
    assert SUPPORTED_SCENARIOS == ("east", "southeast", "southcenter")
    all_job_ids: list[set[str]] = []
    all_engineer_ids: list[set[str]] = []
    for scenario in SUPPORTED_SCENARIOS:
        jobs, engineers = load_scenario(scenario)
        job_ids = {str(job["id"]) for job in jobs}
        engineer_ids = {str(engineer["id"]) for engineer in engineers}
        assert job_ids and all(value.startswith(f"{scenario}:") for value in job_ids)
        assert engineer_ids and all(value.startswith(f"{scenario}:") for value in engineer_ids)
        assert all(engineer["scenario_id"] == scenario for engineer in engineers)
        all_job_ids.append(job_ids)
        all_engineer_ids.append(engineer_ids)
    assert not any(
        left & right
        for index, left in enumerate(all_job_ids)
        for right in all_job_ids[index + 1 :]
    )
    assert not any(
        left & right
        for index, left in enumerate(all_engineer_ids)
        for right in all_engineer_ids[index + 1 :]
    )


def test_unknown_scenario_returns_clear_allowlist_error() -> None:
    client = TestClient(app)
    for response in (
        client.get("/data/jobs?scenario=unknown"),
        client.post(
            "/optimize",
            json={"scenario": "unknown", "engine": "ortools_vrptw"},
        ),
    ):
        assert response.status_code == 404
        assert "Неизвестный сценарий" in response.json()["detail"]
        assert "east, southeast, southcenter" in response.json()["detail"]


def test_ui_scenario_switch_discards_old_plan_id_and_history() -> None:
    module_path = ROOT / "frontend" / "src" / "scenario_state.js"
    script = f"""
const stateApi = require({json.dumps(str(module_path))});
const state = {{scenario:'east', planId:'plan-old', parentPlanId:'parent-old', version:7,
  planHistory:[{{version:7}}], lastPlan:{{assigned:1}}, jobs:[{{id:'east:1'}}],
  engineers:[{{id:'east:eng-1'}}], dataRevision:4}};
stateApi.reset(state, 'southeast');
process.stdout.write(JSON.stringify(state));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    state = json.loads(result.stdout)
    assert state["scenario"] == "southeast"
    assert state["planId"] is None and state["parentPlanId"] is None
    assert state["version"] == 0 and state["planHistory"] == []
    assert state["lastPlan"] is None and state["jobs"] == [] and state["engineers"] == []
