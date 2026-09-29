from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services.baseline_greedy import build_baseline_plan
from backend.app.services.ortools_vrptw import build_ortools_plan, zone_transition_penalty
from backend.app.services.planner import build_plan_with_comparison
from backend.app.services.plan_store import plan_store
from backend.app.services.replanner import build_replanned_plan
from backend.app.services import travel_matrix
from backend.app.services.travel_matrix import OSRMRoutingProvider, attach_route_geometry


DATE = "2026-08-17"


def engineer(*, equipment: dict[str, int] | None = None) -> dict:
    return {
        "id": "test:eng-1",
        "name": "Бригада Тест",
        "scenario_id": "test",
        "skills": ["connect", "local"],
        "vehicle": "Автомобиль",
        "equipment": equipment or {"router": 1},
        "shift_start": f"{DATE}T08:00:00+03:00",
        "shift_end": f"{DATE}T18:00:00+03:00",
        "start_point": [55.75, 37.61],
        "status": "Доступен",
    }


def job(job_id: str, start: str, end: str, *, coords: list[float], zone: str | None = None) -> dict:
    return {
        "id": f"test:{job_id}",
        "input_order": int(job_id),
        "type": "Подключение",
        "skill": "connect",
        "priority": "Обычная",
        "address": f"Москва, адрес {job_id}",
        "coords": coords,
        "window_start": f"{DATE}T{start}:00+03:00",
        "window_end": f"{DATE}T{end}:00+03:00",
        "duration_min": 30,
        "required_vehicle": None,
        "required_equipment": {},
        "status": "planned",
        "zone": zone,
    }


def test_osrm_matrix_metrics_and_geometry_use_the_same_arcs() -> None:
    calls: list[str] = []

    def fake_get(url: str, timeout: float) -> dict:
        calls.append(url)
        if "/table/" in url:
            return {
                "code": "Ok",
                "distances": [[0, 1200], [1300, 0]],
                "durations": [[0, 600], [660, 0]],
            }
        return {
            "code": "Ok",
            "routes": [
                {
                    "legs": [
                        {
                            "steps": [
                                {
                                    "geometry": {
                                        "type": "LineString",
                                        "coordinates": [
                                            [37.61, 55.75],
                                            [37.615, 55.755],
                                            [37.62, 55.76],
                                        ],
                                    }
                                }
                            ]
                        }
                    ]
                }
            ],
        }

    jobs = [job("1", "09:00", "12:00", coords=[55.76, 37.62])]
    engineers = [engineer()]
    provider = OSRMRoutingProvider(
        jobs, engineers, base_url="http://osrm.test", json_getter=fake_get
    )
    plan = build_ortools_plan(jobs, engineers, scenario="test", matrix=provider, solve_time_limit_ms=200)
    attach_route_geometry(plan, provider)
    stop = plan["routes"][0]["stops"][0]
    route = plan["routes"][0]
    feature = route["route_geometry"]["features"][0]
    assert stop["distance_m_from_prev"] == 1200
    assert stop["travel_min_from_prev"] == 10
    assert plan["metrics"]["total_distance_m"] == 1200
    assert feature["properties"]["distance_m"] == 1200
    assert feature["properties"]["travel_min"] == 10
    assert feature["properties"]["source"] == "osrm"
    assert feature["properties"]["matrix_id"] == plan["travel_model"]["matrix_id"]
    assert len(feature["geometry"]["coordinates"]) > 2
    assert route["route_geometry"]["distance_m"] == route["distance_m"]
    assert sum(
        item["properties"]["distance_m"]
        for item in route["route_geometry"]["features"]
    ) == plan["metrics"]["total_distance_m"]
    assert any("/table/" in url for url in calls)
    assert any("/route/" in url for url in calls)


def test_routing_provider_falls_back_without_fake_road_geometry(monkeypatch) -> None:
    class Unavailable:
        def __init__(self, *args, **kwargs):
            raise TimeoutError("offline")

    monkeypatch.setenv("ROUTING_PROVIDER", "auto")
    monkeypatch.setattr(travel_matrix, "OSRMRoutingProvider", Unavailable)
    provider = travel_matrix.build_routing_provider(
        [job("1", "09:00", "12:00", coords=[55.76, 37.62])], [engineer()]
    )
    assert provider.metadata["provider"] == "static_haversine"
    assert provider.metadata["fallback"] is True
    assert provider.metadata["road_geometry"] is False
    assert provider.geometry_for_route([], "Автомобиль") == []


def test_default_routing_mode_fails_closed_when_osrm_is_unavailable(monkeypatch) -> None:
    class Unavailable:
        def __init__(self, *args, **kwargs):
            raise TimeoutError("offline")

    monkeypatch.delenv("ROUTING_PROVIDER", raising=False)
    monkeypatch.setattr(travel_matrix, "OSRMRoutingProvider", Unavailable)
    with pytest.raises(TimeoutError, match="offline"):
        travel_matrix.build_routing_provider(
            [job("1", "09:00", "12:00", coords=[55.76, 37.62])], [engineer()]
        )


def test_import_creates_isolated_scenario_then_optimizes_it() -> None:
    plan_store.clear()
    payload = {
        "region": "Тестовый регион",
        "planning_date": DATE,
        "jobs": [
            {
                "id": "job-1",
                "type": "Подключение",
                "skill": "connect",
                "address": "Москва, Тверская улица, 1",
                "coords": [55.757, 37.615],
                "window_start": "09:00",
                "window_end": "12:00",
                "duration_min": 30,
                "required_vehicle": "Автомобиль",
                "required_equipment": {"router": 1},
            }
        ],
        "engineers": [
            {
                "id": "eng-1",
                "name": "Импортированная бригада",
                "skills": ["connect"],
                "vehicle": "Автомобиль",
                "shift_start": "08:00",
                "shift_end": "18:00",
                "start_point": [55.75, 37.61],
                "equipment": {"router": 1},
            }
        ],
    }
    client = TestClient(app)
    imported = client.post(
        "/scenarios/import",
        content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Filename": "scenario.json"},
    )
    assert imported.status_code == 201
    metadata = imported.json()
    assert metadata["isolated"] is True
    assert metadata["scenario_id"].startswith("import-")
    planned = client.post(
        "/plans",
        json={"scenario": metadata["scenario_id"], "engine": "ortools_vrptw", "solve_time_limit_ms": 300},
    )
    assert planned.status_code == 200
    result = planned.json()
    assert result["scenario_id"] == metadata["scenario_id"]
    assert result["metrics"]["assigned_count"] == 1
    assert result["jobs"][0]["source"] == "user_import"
    assert result["jobs"][0]["assigned_engineer_id"].startswith(metadata["scenario_id"])


def _parent_plan() -> dict:
    jobs = [
        job("1", "09:00", "10:00", coords=[55.751, 37.611]),
        job("2", "14:00", "15:00", coords=[55.752, 37.612]),
    ]
    return build_plan_with_comparison(
        jobs, [engineer()], scenario="test", engine="baseline_greedy"
    )


def normal_event(*, equipment: dict[str, int] | None = None) -> dict:
    return {
        "event_id": "normal-1",
        "type": "new_normal_job",
        "event_time": f"{DATE}T10:30:00+03:00",
        "window_end": f"{DATE}T12:30:00+03:00",
        "title": "Обычная заявка",
        "address": "Москва, новый адрес",
        "coords": [55.7515, 37.6115],
        "required_skill": "connect",
        "required_vehicle": "Автомобиль",
        "required_equipment": equipment or {},
        "duration_min": 30,
    }


def test_normal_job_is_inserted_without_reordering_existing_route() -> None:
    parent = _parent_plan()
    before = {stop["job_id"]: stop["service_start"] for stop in parent["routes"][0]["stops"]}
    result, diff = build_replanned_plan(parent, normal_event())
    route = result["routes"][0]
    existing = [stop["job_id"] for stop in route["stops"] if not stop["job_id"].startswith("normal:")]
    assert existing == ["test:1", "test:2"]
    assert {stop["job_id"]: stop["service_start"] for stop in route["stops"] if stop["job_id"] in before} == before
    inserted = next(item for item in result["jobs"] if item["id"] == "normal:normal-1")
    assert inserted["status"] == "assigned"
    assert inserted["assignment_explanation"]["selection_basis"] == "local_free_interval_insertion"
    assert diff["summary"]["assigned"] == 1


def test_normal_job_reports_equipment_block_and_keeps_route_unchanged() -> None:
    parent = _parent_plan()
    original = [stop["job_id"] for stop in parent["routes"][0]["stops"]]
    result, _ = build_replanned_plan(parent, normal_event(equipment={"fiber_splicer": 1}))
    inserted = next(item for item in result["jobs"] if item["id"] == "normal:normal-1")
    assert inserted["status"] == "unassigned"
    assert inserted["unassigned_reason"]["code"] == "NO_EQUIPMENT"
    assert [stop["job_id"] for stop in result["routes"][0]["stops"]] == original


def test_event_comparison_has_one_temporal_horizon() -> None:
    parent = _parent_plan()
    result, _ = build_replanned_plan(parent, normal_event())
    scope = result["comparison"]["event_scope"]
    assert scope["same_horizon"] is True
    assert scope["before"]["horizon_start"] == scope["after"]["horizon_start"]
    assert scope["horizon_start"] == f"{DATE}T10:30:00+03:00"
    assert scope["label"] == "Изменения в оставшейся части смены"


def test_zone_penalty_is_soft_and_discourages_repeated_crossings() -> None:
    assert zone_transition_penalty("Москва", "Москва") == 0
    assert zone_transition_penalty("Москва", "Домодедово") > 0
    jobs = [
        job("1", "09:00", "17:00", coords=[55.751, 37.611], zone="Москва"),
        job("2", "09:00", "17:00", coords=[55.752, 37.612], zone="Домодедово"),
        job("3", "09:00", "17:00", coords=[55.753, 37.613], zone="Москва"),
    ]
    result = build_ortools_plan(jobs, [engineer()], scenario="test", solve_time_limit_ms=300)
    ordered = [
        next(item for item in result["jobs"] if item["id"] == stop["job_id"])["zone"]
        for stop in result["routes"][0]["stops"]
    ]
    transitions = sum(left != right for left, right in zip(ordered, ordered[1:]))
    assert result["metrics"]["assigned_count"] == 3
    assert transitions <= 1
