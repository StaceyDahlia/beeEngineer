from __future__ import annotations

import copy
from datetime import datetime
import json
from pathlib import Path

from backend.app.services.loader import load_scenario
from backend.app.services.planner import build_plan_with_comparison
from backend.app.services.replanner import build_replanned_plan


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "frontend" / "demo-emergency.json"


def _stop(plan: dict, job_id: str) -> dict:
    return next(
        stop
        for route in plan["routes"]
        for stop in route["stops"]
        if str(stop["job_id"]) == job_id
    )


def _ordinary_changed_ids(diff: dict, emergency_id: str) -> set[str]:
    return {
        str(item["job_id"])
        for item in diff["items"]
        if str(item["job_id"]) != emergency_id
    }


def test_demo_emergency_is_stable_feasible_and_preserves_fixed_work() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert config["scenario"] == "east"
    assert config["event"] == {
        "event_id": "demo-emergency-east-v1",
        "type": "emergency_job",
        "event_time": "2026-08-17T19:30:00+03:00",
        "title": "Аварийное восстановление узла связи",
        "address": "Город Москва, пер.Маяковского, д. 2",
        "coords": [55.7397743, 37.6599082],
        "required_vehicle": None,
        "solve_time_limit_ms": 1500,
    }

    jobs, engineers = load_scenario("east")
    parent = build_plan_with_comparison(
        jobs,
        engineers,
        scenario="east",
        engine="ortools_vrptw",
        solve_time_limit_ms=int(config["base_solve_time_limit_ms"]),
    )
    event_time = datetime.fromisoformat(config["event"]["event_time"])

    completed_stop = parent["routes"][0]["stops"][0]
    completed_id = str(completed_stop["job_id"])
    completed_job = next(job for job in parent["jobs"] if str(job["id"]) == completed_id)
    completed_job["status"] = "completed"
    completed_snapshot = copy.deepcopy(completed_job)

    current_stop = next(
        stop
        for route in parent["routes"]
        for stop in route["stops"]
        if datetime.fromisoformat(stop["service_start"])
        <= event_time
        < datetime.fromisoformat(stop["service_end"])
    )
    current_id = str(current_stop["job_id"])
    current_engineer_id = next(
        str(route["engineer_id"])
        for route in parent["routes"]
        if any(str(stop["job_id"]) == current_id for stop in route["stops"])
    )
    current_job = next(job for job in parent["jobs"] if str(job["id"]) == current_id)
    current_job["status"] = "in_progress"
    current_snapshot = copy.deepcopy(current_stop)

    results: list[tuple[dict, dict]] = []
    for _ in range(2):
        results.append(
            build_replanned_plan(
                parent,
                copy.deepcopy(config["event"]),
                solve_time_limit_ms=int(config["preview_solve_time_limit_ms"]),
            )
        )

    emergency_id = f"emergency:{config['event']['event_id']}"
    expected_changed = {
        "east:21367",
        "east:30243",
        "east:31260",
        "east:47670",
        "east:74198",
    }
    for plan, diff in results:
        emergency = next(job for job in plan["jobs"] if str(job["id"]) == emergency_id)
        assert emergency["status"] == "assigned"
        assert emergency["skill"] == "emergency"
        assert emergency["duration_min"] == 80
        assert emergency["window_start"] == config["event"]["event_time"]
        assert emergency["window_end"] == "2026-08-17T21:30:00+03:00"

        changed = _ordinary_changed_ids(diff, emergency_id)
        assert changed == expected_changed
        assert config["expected_ordinary_changes"]["min"] <= len(changed) <= config[
            "expected_ordinary_changes"
        ]["max"]

        completed_after = next(
            job for job in plan["jobs"] if str(job["id"]) == completed_id
        )
        assert completed_after["status"] == "completed"
        assert completed_after["service_start"] == completed_snapshot["service_start"]
        assert completed_after["service_end"] == completed_snapshot["service_end"]

        assert current_id in plan["replan"]["locked_job_ids"]
        current_after = _stop(plan, current_id)
        current_engineer_after = next(
            str(route["engineer_id"])
            for route in plan["routes"]
            if any(str(stop["job_id"]) == current_id for stop in route["stops"])
        )
        assert current_engineer_after == current_engineer_id
        assert current_after["service_start"] == current_snapshot["service_start"]
        assert current_after["service_end"] == current_snapshot["service_end"]

    assert _ordinary_changed_ids(results[0][1], emergency_id) == _ordinary_changed_ids(
        results[1][1], emergency_id
    )


def test_demo_ui_contract_is_wired_and_timeout_copy_is_neutral() -> None:
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    api = (ROOT / "frontend" / "src" / "api.js").read_text(encoding="utf-8")
    assert 'id="demo-emergency-button"' in html
    assert "'/static/demo-emergency.json'" in html
    assert "previewScenario({event,label:'demo-emergency east'})" in html
    assert "Найден допустимый план за" in html
    assert "оптимум не доказан" in html
    assert "лимит времени" not in html
    assert "Backend explanation" in html
    assert "Какие заявки изменились и почему" in html
    assert "solve_time_limit_ms" in api
    for button_id in (
        "baseline-button",
        "optimize-button",
        "express-button",
        "cancel-scenario-button",
        "unavailable-scenario-button",
        "urgent-scenario-button",
        "demo-emergency-button",
    ):
        assert f'id="{button_id}"' in html
        assert f"$('{button_id}').addEventListener" in html
    assert "Произвольный импорт не входит" not in html
    assert 'id="import-button"' not in html
