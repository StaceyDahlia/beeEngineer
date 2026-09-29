from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "frontend" / "src" / "plan_adapter.js"
HTML = ROOT / "frontend" / "index.html"


def _node_json(source: str) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for frontend contract tests")
    result = subprocess.run(
        [node, "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


def _route(coordinates: list[list[float]]) -> dict:
    return {
        "engineer_id": "eng-1",
        "distance_m": 1200,
        "geometry_source": "osrm",
        "geometry_matrix_id": "matrix-1",
        "stops": [
            {
                "job_id": "job-1",
                "distance_m_from_prev": 1200,
                "travel_min_from_prev": 10,
            }
        ],
        "route_geometry": {
            "type": "FeatureCollection",
            "source": "osrm",
            "matrix_id": "matrix-1",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "source": "osrm",
                        "distance_m": 1200,
                        "travel_min": 10,
                        "matrix_id": "matrix-1",
                    },
                    "geometry": {"type": "LineString", "coordinates": coordinates},
                }
            ],
        },
    }


def test_frontend_rejects_two_point_line_claiming_to_be_osrm() -> None:
    route = _route([[37.61, 55.75], [37.62, 55.76]])
    payload = _node_json(
        f"""
        global.window = global;
        require({json.dumps(str(ADAPTER))});
        try {{
          BeelinePlanAdapter.validateRouteGeometry(
            {json.dumps(route, ensure_ascii=False)},
            {{provider:'osrm', geometry_complete:true}}
          );
          console.log(JSON.stringify({{rejected:false}}));
        }} catch (error) {{
          console.log(JSON.stringify({{rejected:true,message:error.message}}));
        }}
        """
    )
    assert payload["rejected"] is True
    assert "промежуточными точками" in payload["message"]


def test_frontend_accepts_multi_point_backend_feature_collection() -> None:
    route = _route(
        [[37.61, 55.75], [37.613, 55.753], [37.617, 55.758], [37.62, 55.76]]
    )
    payload = _node_json(
        f"""
        global.window = global;
        require({json.dumps(str(ADAPTER))});
        const geometry = BeelinePlanAdapter.validateRouteGeometry(
          {json.dumps(route, ensure_ascii=False)},
          {{provider:'osrm', geometry_complete:true}}
        );
        console.log(JSON.stringify({{
          type:geometry.type,
          points:geometry.features[0].geometry.coordinates.length
        }}));
        """
    )
    assert payload == {"type": "FeatureCollection", "points": 4}


def test_map_uses_backend_features_and_marks_calculation_fallback() -> None:
    adapter = ADAPTER.read_text(encoding="utf-8")
    html = HTML.read_text(encoding="utf-8")
    assert "route.route_geometry || null" in adapter
    assert "route.geometry || null" not in adapter
    assert "L.geoJSON(r.routeGeo" in html
    assert "route-line--osrm" in html
    assert "route-line--calculated" in html
    assert "Дорожная геометрия недоступна — расчётная связь" in html
    assert "fetchRouteGeometry" not in html
    assert "beeline_route_mvp_api_v2" in html
    assert "PERSISTENCE_SCHEMA_VERSION = 7" in html
    assert 'plan_adapter.js?v=7' in html
