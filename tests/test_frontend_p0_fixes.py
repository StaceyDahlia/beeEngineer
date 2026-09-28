from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "frontend" / "index.html"
GEOCODING_PATH = ROOT / "frontend" / "src" / "geocoding.js"


def _node_json(source: str) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for frontend helper tests")
    result = subprocess.run(
        [node, "-e", source], cwd=ROOT, check=True, capture_output=True,
        text=True, encoding="utf-8",
    )
    return json.loads(result.stdout)


def test_forward_geocoding_builds_supported_request_and_parses_result() -> None:
    payload = _node_json(
        f"""
        const geo = require({json.dumps(str(GEOCODING_PATH))});
        let requested = '';
        const fetcher = async url => {{ requested = url; return {{features:[{{
          properties:{{street:'Тверская улица',housenumber:'7',city:'Москва'}},
          geometry:{{coordinates:[37.6101,55.7612]}}
        }}]}}; }};
        geo.forwardGeocode(fetcher,'https://photon.komoot.io','Москва, Тверская, 7',[55.75,37.62])
          .then(result => console.log(JSON.stringify({{requested,result}})));
        """
    )
    assert "lang=ru" not in payload["requested"]
    assert payload["result"]["candidates"][0]["coords"] == [55.7612, 37.6101]
    assert "Тверская улица, 7" in payload["result"]["candidates"][0]["display"]


def test_geocoder_error_and_empty_result_are_safe_codes() -> None:
    payload = _node_json(
        f"""
        const geo = require({json.dumps(str(GEOCODING_PATH))});
        async function message(fetcher) {{
          try {{ await geo.forwardGeocode(fetcher,'https://example.test','адрес',[55.75,37.62]); }}
          catch (error) {{ return error.message; }}
        }}
        Promise.all([
          message(async () => {{ throw new Error('GEOCODER_UNAVAILABLE'); }}),
          message(async () => ({{features:[]}}))
        ]).then(([failed,empty]) => console.log(JSON.stringify({{failed,empty}})));
        """
    )
    assert payload == {"failed": "GEOCODER_UNAVAILABLE", "empty": "GEOCODER_EMPTY"}
    html = HTML_PATH.read_text(encoding="utf-8")
    assert "Не удалось найти адрес. Укажите точку на карте." in html


def test_manual_map_point_works_without_reverse_geocoding() -> None:
    payload = _node_json(
        f"""
        const geo = require({json.dumps(str(GEOCODING_PATH))});
        const point = geo.pointSelection([55.742345,37.667891],'','map');
        console.log(JSON.stringify(point));
        """
    )
    assert payload["confirmed"] is True
    assert payload["source"] == "map"
    assert payload["coords"] == [55.742345, 37.667891]
    assert payload["display"].startswith("Точка на карте:")


def test_confirmed_coordinates_are_passed_to_emergency_event() -> None:
    payload = _node_json(
        f"""
        const geo = require({json.dumps(str(GEOCODING_PATH))});
        const event = geo.buildEmergencyEvent({{
          eventId:'ui-42', eventTime:'2025-02-18T13:30:00', title:'Авария',
          address:'ул. Михайлова, 14', coords:[55.731234,37.698765], requiredVehicle:null
        }});
        console.log(JSON.stringify(event));
        """
    )
    assert payload["type"] == "emergency_job"
    assert payload["coords"] == [55.731234, 37.698765]
    assert payload["address"] == "ул. Михайлова, 14"


def test_point_confirmation_and_comparison_panel_are_wired() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")
    assert 'id="geocode-button"' in html and "Найти адрес" in html
    assert 'id="map-pick-button"' in html and "Указать на карте" in html
    assert "map.on('click'" in html
    assert "pointConfirmed!=='true'" in html
    assert "buildEmergencyEvent" in html
    assert 'class="comparison-table"' in html
    for label in ("Показатель", "Было", "Стало", "Изменение", "Новых назначений"):
        assert label in html
