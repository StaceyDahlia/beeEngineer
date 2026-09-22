# НАЗНАЧЕНИЕ: сборка GeoJSON FeatureCollection из Plan для карты (Leaflet).
#
# ВАЖНО: в остальном API координаты [lat, lon], а в GeoJSON — [lon, lat]
#        (стандарт). Здесь делаем flip.
#
# ВХОД:  Plan (jobs + engineers + routes).
# ВЫХОД: dict — FeatureCollection.
# СВЯЗИ: вызывается из api/routes_map.py.

from __future__ import annotations

from typing import Any, Dict, List

from ..schemas.plan import Plan


def _flip(coords: List[float]) -> List[float]:
    """[lat, lon] -> [lon, lat]."""
    return [float(coords[1]), float(coords[0])]


def _hhmm(dt) -> str:
    return f"{dt.hour:02d}:{dt.minute:02d}"


def build_feature_collection(plan: Plan) -> Dict[str, Any]:
    features: List[Dict[str, Any]] = []

    engineers_by_id = {e.id: e for e in plan.engineers}
    jobs_by_id = {j.id: j for j in plan.jobs}

    # 1. Стартовые точки инженеров.
    for e in plan.engineers:
        if not e.start_point:
            continue
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": _flip(e.start_point),
            },
            "properties": {
                "kind": "engineer_start",
                "engineer_id": e.id,
                "name": e.name,
            },
        })

    # 2. Точки заявок.
    for j in plan.jobs:
        if not j.coords:
            continue
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": _flip(j.coords),
            },
            "properties": {
                "kind": "job",
                "job_id": j.id,
                "status": j.status,
                "assigned_engineer_id": j.assigned_engineer_id,
                "priority": j.priority,
                "window_start": _hhmm(j.window_start),
                "window_end": _hhmm(j.window_end),
            },
        })

    # 3. Линии маршрутов: start_point -> каждый стоп.
    for r in plan.routes:
        if not r.stops:
            continue
        e = engineers_by_id.get(r.engineer_id)
        if e is None or not e.start_point:
            continue

        coords: List[List[float]] = [_flip(e.start_point)]
        for stop in r.stops:
            job = jobs_by_id.get(stop.job_id)
            if job is None or not job.coords:
                continue
            coords.append(_flip(job.coords))

        if len(coords) < 2:
            continue

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coords,
            },
            "properties": {
                "kind": "route",
                "engineer_id": r.engineer_id,
                "distance_km": r.distance_km,
                "jobs_count": r.jobs_count,
            },
        })

    return {"type": "FeatureCollection", "features": features}
