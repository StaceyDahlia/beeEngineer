from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from backend.app.services.baseline_greedy import build_baseline_plan
from backend.app.services.history_repository import PlanHistoryRepository
from backend.app.services.ortools_vrptw import build_ortools_plan
from backend.app.services.plan_store import PlanStore
from backend.app.services.travel_matrix import StaticTravelMatrix, VEHICLE_PROFILE


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
HELPER = ROOT / "frontend" / "src" / "dispatcher_ux.js"


def node_json(source: str) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required")
    result = subprocess.run([node, "-e", source], cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8")
    return json.loads(result.stdout)


def test_unassigned_filter_excludes_closed_jobs() -> None:
    payload = node_json(f"""
      const ux=require({json.dumps(str(HELPER))});
      const statuses=['unassigned','completed','done','cancelled','closed'];
      console.log(JSON.stringify(Object.fromEntries(statuses.map(status=>[status,ux.visibleInMapMode({{status}},'unassigned','all')]))));
    """)
    assert payload == {"unassigned": True, "completed": False, "done": False, "cancelled": False, "closed": False}


def test_human_deltas_and_distance_are_readable() -> None:
    payload = node_json(f"""
      const ux=require({json.dumps(str(HELPER))});
      console.log(JSON.stringify({{
        jobs:ux.humanDelta(40,10,'unassigned'),
        distance:ux.humanDelta(100,64.875,'distance'),
        crews:ux.humanDelta(8,10,'crews')
      }}));
    """)
    assert payload == {
        "jobs": "Неназначенных стало на 30 меньше",
        "distance": "Пробег меньше на 35,13 км",
        "crews": "Бригад стало на 2 больше",
    }


def _engineer(equipment: list[str]) -> dict:
    return {"id":"e1","name":"Бригада 1","scenario_id":"east","skills":["connect"],"vehicle":"Автомобиль","equipment":equipment,"shift_start":"2026-08-17T08:00:00+03:00","shift_end":"2026-08-17T18:00:00+03:00","start_point":[55.75,37.61],"status":"Доступен"}


def _job(required: list[str]) -> dict:
    return {"id":"j1","input_order":1,"type":"Подключение","skill":"connect","priority":"Обычная","address":"Адрес","coords":[55.751,37.611],"window_start":"2026-08-17T09:00:00+03:00","window_end":"2026-08-17T17:00:00+03:00","duration_min":30,"required_vehicle":None,"required_equipment":required,"status":"planned"}


@pytest.mark.parametrize("builder", [build_baseline_plan, build_ortools_plan])
def test_equipment_is_a_hard_constraint(builder) -> None:
    kwargs = {"scenario": "east"}
    if builder is build_ortools_plan:
        kwargs["solve_time_limit_ms"] = 100
    plan = builder([_job(["Рефлектометр"])], [_engineer(["Мультиметр"])], **kwargs)
    assert plan["jobs"][0]["status"] == "unassigned"
    assert plan["jobs"][0]["unassigned_reason"]["code"] == "NO_EQUIPMENT"
    assert plan["jobs"][0]["unassigned_reason"]["message"] == "Нет бригады с нужным оборудованием."


def test_plan_store_implements_history_repository() -> None:
    store = PlanStore()
    assert isinstance(store, PlanHistoryRepository)
    first = store.create_plan({"scenario_id":"east","metrics":{},"jobs":[],"engineers":[],"routes":[]})
    history = store.history(first["plan_id"])
    assert [record["version"] for record in history] == [1]


def test_import_replaces_and_resets_previous_context() -> None:
    assert 'value="replace" checked' in HTML
    assert 'value="create" disabled' in HTML
    assert "state.planHistory=[]" in HTML
    assert "state.filter='all'" in HTML
    assert "state.mapLayerMode='all'" in HTML
    assert "Текущий сценарий заменён" in HTML
    payload = node_json(f"""
      const ux=require({json.dumps(str(HELPER))});
      const state={{jobs:[{{id:'old'}}],engineers:[{{id:'old-e'}}],planHistory:[{{version:1}}],lastPlan:{{}},baseline:{{}},planId:'old',version:2,selectedEngineerId:'old-e',filter:'attention',mapLayerMode:'problems'}};
      ux.replaceDataset(state,{{jobs:[{{id:'new'}}],engineers:[{{id:'new-e'}}]}},{{name:'import.csv'}});
      console.log(JSON.stringify(state));
    """)
    assert payload["jobs"] == [{"id": "new"}]
    assert payload["engineers"] == [{"id": "new-e"}]
    assert payload["planHistory"] == []
    assert payload["planId"] is None
    assert payload["filter"] == "all"
    assert payload["mapLayerMode"] == "all"


def test_emergency_demo_has_russian_user_facing_copy() -> None:
    assert '<span class="status assigned">Preview</span>' not in HTML
    for text in ("Черновик перепланирования", "Применить перепланирование", "срочная аварийная заявка", "Глобальный оптимум не подтверждён"):
        assert text in HTML


def test_real_dispatcher_controls_are_present() -> None:
    for control in ("handoff-button", "route-sheet-engineer", "map-filter-hint"):
        assert f'id="{control}"' in HTML
    assert "История хранится до перезапуска сервиса" in HTML
    assert "Расчётное время в пути" in HTML


def test_engineer_work_status_is_human_readable() -> None:
    payload = node_json(f"""
      const ux=require({json.dumps(str(HELPER))});
      const engineer={{id:'e1',status:'Доступен'}};
      console.log(JSON.stringify([
        ux.engineerWorkStatus(engineer,[]),
        ux.engineerWorkStatus(engineer,[{{engineerId:'e1',status:'en_route'}}]),
        ux.engineerWorkStatus(engineer,[{{engineerId:'e1',status:'in_progress'}}]),
        ux.engineerWorkStatus({{id:'e1',status:'Недоступен'}},[])
      ]));
    """)
    assert payload == ["Доступна", "В пути", "Выполняет работу", "Недоступна"]


def test_static_travel_time_matches_distance_and_vehicle_speed() -> None:
    engineer = _engineer(["Рефлектометр"])
    job = _job([])
    matrix = StaticTravelMatrix([job], [engineer])
    metric = matrix.get(matrix.engineer_start_id("e1"), matrix.job_point_id("j1"), "Автомобиль")
    expected = max(1, __import__("math").ceil((metric.distance_m / 1000) / VEHICLE_PROFILE["Автомобиль"]["speed_kmh"] * 60))
    assert metric.travel_min == expected
    assert matrix.metadata["provider"] == "static_haversine"
    assert matrix.metadata["traffic"] is False
