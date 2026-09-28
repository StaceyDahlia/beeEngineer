from __future__ import annotations

import math
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Sequence


EARTH_RADIUS_KM = 6371.0

# ASSUMPTION: для первого вертикального среза используется статическая
# офлайн-оценка пути. Она едина для baseline, расписания и метрик.
VEHICLE_PROFILE = {
    "Автомобиль": {"speed_kmh": 50.0, "route_factor": 1.30},
    "ОТ": {"speed_kmh": 40.0, "route_factor": 1.30},
    "Общественный транспорт": {"speed_kmh": 40.0, "route_factor": 1.30},
    "Велосипед": {"speed_kmh": 15.0, "route_factor": 1.15},
    "Пешеход": {"speed_kmh": 5.0, "route_factor": 1.10},
}


@dataclass(frozen=True)
class TravelMetric:
    distance_m: int
    travel_min: int

    @property
    def distance_km(self) -> float:
        return round(self.distance_m / 1000.0, 3)


def _valid_coords(value: Any) -> bool:
    if not isinstance(value, list) or len(value) != 2:
        return False
    try:
        lat, lon = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return False
    return -90 <= lat <= 90 and -180 <= lon <= 180


def _haversine_km(a: Sequence[float], b: Sequence[float]) -> float:
    lat1, lon1 = float(a[0]), float(a[1])
    lat2, lon2 = float(b[0]), float(b[1])
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    h = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


class StaticTravelMatrix:
    """Полная неизменяемая матрица для точек сценария и видов транспорта."""

    provider = "static_haversine"
    version = "static-haversine-v1"

    def __init__(
        self,
        jobs: Iterable[dict[str, Any]],
        engineers: Iterable[dict[str, Any]],
    ) -> None:
        points: dict[str, list[float]] = {}
        vehicles: set[str] = set()

        for job in jobs:
            coords = job.get("coords")
            if _valid_coords(coords):
                points[self.job_point_id(str(job["id"]))] = [float(coords[0]), float(coords[1])]

        for engineer in engineers:
            coords = engineer.get("start_point")
            if _valid_coords(coords):
                points[self.engineer_start_id(str(engineer["id"]))] = [
                    float(coords[0]),
                    float(coords[1]),
                ]
            vehicles.add(str(engineer.get("vehicle") or ""))

        unknown = sorted(vehicle for vehicle in vehicles if vehicle not in VEHICLE_PROFILE)
        if unknown:
            raise ValueError(f"Неизвестные виды транспорта: {', '.join(unknown)}")

        self._points = points
        signature_payload = {
            "points": sorted(points.items()),
            "profiles": {vehicle: VEHICLE_PROFILE[vehicle] for vehicle in sorted(vehicles)},
            "version": self.version,
        }
        self.signature = hashlib.sha256(
            json.dumps(signature_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        self._matrix: dict[tuple[str, str, str], TravelMetric] = {}
        for vehicle in sorted(vehicles):
            profile = VEHICLE_PROFILE[vehicle]
            for origin_id, origin in points.items():
                for destination_id, destination in points.items():
                    if origin_id == destination_id or origin == destination:
                        metric = TravelMetric(0, 0)
                    else:
                        route_km = _haversine_km(origin, destination) * profile["route_factor"]
                        distance_m = int(math.ceil(route_km * 1000.0))
                        travel_min = max(
                            1,
                            int(math.ceil(route_km / profile["speed_kmh"] * 60.0)),
                        )
                        metric = TravelMetric(distance_m, travel_min)
                    self._matrix[(origin_id, destination_id, vehicle)] = metric

    @staticmethod
    def job_point_id(job_id: str) -> str:
        return f"job:{job_id}"

    @staticmethod
    def engineer_start_id(engineer_id: str) -> str:
        return f"engineer:{engineer_id}:start"

    def has_job(self, job_id: str) -> bool:
        return self.job_point_id(job_id) in self._points

    def get(self, origin_id: str, destination_id: str, vehicle: str) -> TravelMetric:
        try:
            return self._matrix[(origin_id, destination_id, vehicle)]
        except KeyError as exc:
            raise ValueError(
                f"В матрице нет пути {origin_id} -> {destination_id} для {vehicle}."
            ) from exc

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "version": self.version,
            "matrix_id": self.signature,
            "static": True,
            "distance_unit": "m",
            "time_unit": "min",
            "traffic": False,
            "service_norm_travel_added": False,
            "assumption": (
                "Haversine с коэффициентом извилистости и фиксированной "
                "скоростью транспорта; 20 минут нормативной дороги не добавляются."
            ),
        }
