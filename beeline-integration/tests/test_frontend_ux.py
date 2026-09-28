from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "frontend" / "index.html"
UX_PATH = ROOT / "frontend" / "src" / "ux.js"


def _node_json(source: str) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for browser helper tests")
    result = subprocess.run(
        [node, "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


def test_metric_and_delta_formatting_is_readable() -> None:
    payload = _node_json(
        f"""
        const ux = require({json.dumps(str(UX_PATH))});
        console.log(JSON.stringify({{
          km: ux.formatKm(165.739999999),
          minutes: ux.formatMinutes(14.7),
          distanceDelta: ux.delta(-36.44, 'km'),
          jobDelta: ux.delta(15, 'jobs'),
          crewDelta: ux.delta(-2, 'crews')
        }}));
        """
    )
    assert payload == {
        "km": "165,74 км",
        "minutes": "15 мин",
        "distanceDelta": "−36,44 км",
        "jobDelta": "+15 заявок",
        "crewDelta": "−2 бригады",
    }


def test_human_diff_uses_address_and_engineer_names() -> None:
    payload = _node_json(
        f"""
        const ux = require({json.dumps(str(UX_PATH))});
        const text = ux.humanDiff(
          {{category:'reassigned', job_id:'j-1', from_engineer_id:'e-1', to_engineer_id:'e-2'}},
          [{{id:'j-1', address:'ул. Михайлова, 14'}}],
          [{{id:'e-1', name:'Бригада Зверев'}}, {{id:'e-2', name:'Бригада Перов'}}],
          {{emergency:true, emergencyId:'emergency:demo'}}
        );
        console.log(JSON.stringify({{text}}));
        """
    )
    assert payload["text"] == (
        "ул. Михайлова, 14 — переназначена с бригады «Зверев» на "
        "бригаду «Перов». Ради аварийной заявки."
    )


def test_dispatcher_controls_are_wired_without_fake_engineer_selects() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")
    assert 'id="map-layer-mode"' in html
    assert "selected_route" in html and "problems" in html and "unassigned" in html
    assert "markerClusterGroup" in html and "clusterclick" in html
    assert "data-focus-engineer-card" in html
    assert "data-focus-timeline-engineer" in html
    assert "function focusEngineerRoute" in html
    assert "function selectJob" in html
    assert "scrollIntoView" in html
    assert "data-engineer-status" not in html
    assert "data-engineer-vehicle" not in html
    assert "Предлагаемые изменения" in html
    assert "Технические детали" in html
    for filter_name in ("all", "urgent", "unassigned", "changed"):
        assert f'data-filter="{filter_name}"' in html


def test_inline_frontend_script_has_valid_javascript(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for JavaScript syntax validation")
    html = HTML_PATH.read_text(encoding="utf-8")
    inline = html.rsplit("<script>", 1)[1].split("</script>", 1)[0]
    script_path = tmp_path / "frontend-inline.js"
    script_path.write_text(inline, encoding="utf-8")
    subprocess.run([node, "--check", str(script_path)], check=True, cwd=ROOT)
