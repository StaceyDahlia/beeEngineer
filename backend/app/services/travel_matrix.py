from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Protocol, Sequence

import httpx


EARTH_RADIUS_KM = 6371.0
DEFAULT_OSRM_URL = "http://router.project-osrm.org"

# ASSUMPTION: fallback only. It is deliberately identified as a calculation
# model and never exposed as road geometry.
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


class RoutingProvider(Protocol):
    signature: str

    def has_job(self, job_id: str) -> bool: ...

    def get(self, origin_id: str, destination_id: str, vehicle: str) -> TravelMetric: ...

    def geometry_for_segment(
        self, origin_id: str, destination_id: str, vehicle: str
    ) -> dict[str, Any] | None: ...

    def geometry_for_route(
        self, point_ids: list[str], vehicle: str
    ) -> list[dict[str, Any]]: ...

    @property
    def metadata(self) -> dict[str, Any]: ...


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


def _collect_points(
    jobs: Iterable[dict[str, Any]], engineers: Iterable[dict[str, Any]]
) -> tuple[dict[str, list[float]], set[str]]:
    points: dict[str, list[float]] = {}
    vehicles: set[str] = set()
    for job in jobs:
        coords = job.get("coords")
        if _valid_coords(coords):
            points[StaticRoutingProvider.job_point_id(str(job["id"]))] = [
                float(coords[0]),
                float(coords[1]),
            ]
    for engineer in engineers:
        coords = engineer.get("start_point")
        if _valid_coords(coords):
            points[StaticRoutingProvider.engineer_start_id(str(engineer["id"]))] = [
                float(coords[0]),
                float(coords[1]),
            ]
        vehicles.add(str(engineer.get("vehicle") or ""))
    unknown = sorted(vehicle for vehicle in vehicles if vehicle not in VEHICLE_PROFILE)
    if unknown:
        raise ValueError(f"Неизвестные виды транспорта: {', '.join(unknown)}")
    return dict(sorted(points.items())), vehicles


class StaticRoutingProvider:
    """Offline calculation model used only when a road provider is unavailable."""

    provider = "static_haversine"
    version = "static-haversine-v2"

    def __init__(
        self,
        jobs: Iterable[dict[str, Any]],
        engineers: Iterable[dict[str, Any]],
        *,
        fallback_reason: str | None = None,
    ) -> None:
        self._points, vehicles = _collect_points(jobs, engineers)
        self.fallback_reason = fallback_reason
        signature_payload = {
            "points": sorted(self._points.items()),
            "profiles": {vehicle: VEHICLE_PROFILE[vehicle] for vehicle in sorted(vehicles)},
            "version": self.version,
        }
        self.signature = hashlib.sha256(
            json.dumps(signature_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        self._matrix: dict[tuple[str, str, str], TravelMetric] = {}
        for vehicle in sorted(vehicles):
            profile = VEHICLE_PROFILE[vehicle]
            for origin_id, origin in self._points.items():
                for destination_id, destination in self._points.items():
                    if origin_id == destination_id or origin == destination:
                        metric = TravelMetric(0, 0)
                    else:
                        route_km = _haversine_km(origin, destination) * profile["route_factor"]
                        metric = TravelMetric(
                            int(math.ceil(route_km * 1000.0)),
                            max(1, int(math.ceil(route_km / profile["speed_kmh"] * 60.0))),
                        )
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

    def geometry_for_segment(
        self, origin_id: str, destination_id: str, vehicle: str
    ) -> dict[str, Any] | None:
        # Static fallback intentionally has no fake road geometry.
        return None

    def geometry_for_route(
        self, point_ids: list[str], vehicle: str
    ) -> list[dict[str, Any]]:
        return []

    @property
    def metadata(self) -> dict[str, Any]:
        result = {
            "provider": self.provider,
            "version": self.version,
            "matrix_id": self.signature,
            "static": True,
            "road_geometry": False,
            "distance_unit": "m",
            "time_unit": "min",
            "traffic": False,
            "service_norm_travel_added": False,
            "display_status": "Используется расчётная модель времени в пути",
            "assumption": (
                "Haversine с коэффициентом извилистости и фиксированной "
                "скоростью транспорта; 20 минут нормативной дороги не добавляются."
            ),
        }
        if self.fallback_reason:
            result["fallback"] = True
            result["fallback_reason"] = self.fallback_reason
        return result


JsonGetter = Callable[[str, float], dict[str, Any]]
_CACHE_LOCK = threading.RLock()
_TABLE_CACHE: dict[str, dict[str, Any]] = {}
_GEOMETRY_CACHE: dict[str, dict[str, Any]] = {}


def _http_json(url: str, timeout_seconds: float) -> dict[str, Any]:
    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        response = client.get(url, headers={"Accept": "application/json"})
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Сервис маршрутизации вернул некорректный ответ.")
    return data


class OSRMRoutingProvider(StaticRoutingProvider):
    """Road matrix and matching segment geometry from the same OSRM instance."""

    provider = "osrm"
    version = "osrm-table-route-v1"

    def __init__(
        self,
        jobs: Iterable[dict[str, Any]],
        engineers: Iterable[dict[str, Any]],
        *,
        base_url: str = DEFAULT_OSRM_URL,
        timeout_seconds: float = 15.0,
        json_getter: JsonGetter | None = None,
    ) -> None:
        self._points, vehicles = _collect_points(jobs, engineers)
        if not self._points:
            raise ValueError("Нет координат для дорожной матрицы.")
        if len(self._points) > 100:
            raise ValueError("Публичный OSRM поддерживает не более 100 точек в сценарии.")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._json_getter = json_getter or _http_json
        signature_payload = {
            "points": sorted(self._points.items()),
            "base_url": self.base_url,
            "version": self.version,
        }
        self.signature = hashlib.sha256(
            json.dumps(signature_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        self._point_ids = list(self._points)
        coordinates = ";".join(
            f"{self._points[point_id][1]:.7f},{self._points[point_id][0]:.7f}"
            for point_id in self._point_ids
        )
        url = f"{self.base_url}/table/v1/driving/{coordinates}?annotations=distance,duration"
        cache_key = f"table:{self.signature}"
        with _CACHE_LOCK:
            data = _TABLE_CACHE.get(cache_key)
        if data is None:
            data = self._json_getter(url, self.timeout_seconds)
            if data.get("code") != "Ok":
                raise RuntimeError("OSRM не смог построить дорожную матрицу.")
            with _CACHE_LOCK:
                _TABLE_CACHE[cache_key] = data
        distances, durations = data.get("distances"), data.get("durations")
        size = len(self._point_ids)
        if not (
            isinstance(distances, list)
            and isinstance(durations, list)
            and len(distances) == size
            and len(durations) == size
        ):
            raise RuntimeError("OSRM вернул неполную дорожную матрицу.")
        self._matrix = {}
        for origin_index, origin_id in enumerate(self._point_ids):
            for destination_index, destination_id in enumerate(self._point_ids):
                distance = distances[origin_index][destination_index]
                duration = durations[origin_index][destination_index]
                if distance is None or duration is None:
                    continue
                metric = TravelMetric(
                    int(math.ceil(float(distance))),
                    0 if origin_id == destination_id else max(1, int(math.ceil(float(duration) / 60.0))),
                )
                # ASSUMPTION: public OSRM exposes the driving road graph only.
                # Transport compatibility remains a hard business constraint; road
                # distance/time is shared so metrics, optimization and map agree.
                for vehicle in vehicles:
                    self._matrix[(origin_id, destination_id, vehicle)] = metric

    def geometry_for_segment(
        self, origin_id: str, destination_id: str, vehicle: str
    ) -> dict[str, Any] | None:
        if origin_id == destination_id:
            return None
        origin = self._points[origin_id]
        destination = self._points[destination_id]
        cache_key = f"route:{self.signature}:{origin_id}:{destination_id}"
        with _CACHE_LOCK:
            cached = _GEOMETRY_CACHE.get(cache_key)
        if cached is not None:
            return json.loads(json.dumps(cached))
        coordinates = (
            f"{origin[1]:.7f},{origin[0]:.7f};"
            f"{destination[1]:.7f},{destination[0]:.7f}"
        )
        url = (
            f"{self.base_url}/route/v1/driving/{coordinates}"
            "?overview=full&geometries=geojson&steps=false"
        )
        data = self._json_getter(url, self.timeout_seconds)
        route = (data.get("routes") or [None])[0]
        if data.get("code") != "Ok" or not isinstance(route, dict):
            raise RuntimeError("OSRM не смог построить геометрию дорожного сегмента.")
        geometry = route.get("geometry")
        if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
            raise RuntimeError("OSRM не вернул дорожную геометрию сегмента.")
        if len(geometry.get("coordinates") or []) <= 2:
            raise RuntimeError("OSRM вернул только двухточечную линию вместо дорожной геометрии.")
        matrix_metric = self.get(origin_id, destination_id, vehicle)
        result = {
            "type": "Feature",
            "properties": {
                "origin_id": origin_id,
                "destination_id": destination_id,
                "distance_m": matrix_metric.distance_m,
                "travel_min": matrix_metric.travel_min,
                "matrix_id": self.signature,
                "source": self.provider,
                "provider": self.provider,
            },
            "geometry": geometry,
        }
        with _CACHE_LOCK:
            _GEOMETRY_CACHE[cache_key] = result
        return json.loads(json.dumps(result))

    def geometry_for_route(
        self, point_ids: list[str], vehicle: str
    ) -> list[dict[str, Any]]:
        if len(point_ids) < 2:
            return []
        cache_key = f"ordered-route:{self.signature}:{':'.join(point_ids)}"
        with _CACHE_LOCK:
            cached = _GEOMETRY_CACHE.get(cache_key)
        if cached is not None:
            return json.loads(json.dumps(cached["features"]))
        coordinates = ";".join(
            f"{self._points[point_id][1]:.7f},{self._points[point_id][0]:.7f}"
            for point_id in point_ids
        )
        url = (
            f"{self.base_url}/route/v1/driving/{coordinates}"
            "?overview=false&geometries=geojson&steps=true"
        )
        data = self._json_getter(url, self.timeout_seconds)
        route = (data.get("routes") or [None])[0]
        legs = route.get("legs") if isinstance(route, dict) else None
        if data.get("code") != "Ok" or not isinstance(legs, list):
            raise RuntimeError("OSRM не смог построить геометрию дорожного маршрута.")
        if len(legs) != len(point_ids) - 1:
            raise RuntimeError("OSRM вернул неполный набор дорожных сегментов.")
        features: list[dict[str, Any]] = []
        for index, leg in enumerate(legs):
            coordinates_for_leg: list[list[float]] = []
            for step in leg.get("steps") or []:
                values = (step.get("geometry") or {}).get("coordinates") or []
                if coordinates_for_leg and values:
                    values = values[1:]
                coordinates_for_leg.extend(values)
            if len(coordinates_for_leg) < 2:
                raise RuntimeError("OSRM не вернул геометрию дорожного сегмента.")
            origin_id, destination_id = point_ids[index], point_ids[index + 1]
            if len(coordinates_for_leg) <= 2:
                # A two-point feature is visually indistinguishable from a straight
                # stop-to-stop chord. Retry the exact arc with full overview and
                # never label a two-point chord as OSRM road geometry.
                detailed = self.geometry_for_segment(origin_id, destination_id, vehicle)
                coordinates_for_leg = detailed["geometry"]["coordinates"]
            metric = self.get(origin_id, destination_id, vehicle)
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "origin_id": origin_id,
                        "destination_id": destination_id,
                        "distance_m": metric.distance_m,
                        "travel_min": metric.travel_min,
                        "matrix_id": self.signature,
                        "source": self.provider,
                        "provider": self.provider,
                    },
                    "geometry": {"type": "LineString", "coordinates": coordinates_for_leg},
                }
            )
        with _CACHE_LOCK:
            _GEOMETRY_CACHE[cache_key] = {"features": features}
        return json.loads(json.dumps(features))

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "version": self.version,
            "matrix_id": self.signature,
            "static": False,
            "road_geometry": True,
            "distance_unit": "m",
            "time_unit": "min",
            "traffic": False,
            "service_norm_travel_added": False,
            "display_status": "Дорожная матрица и геометрия OSRM",
            "assumption": (
                "Используется дорожный граф OSRM без данных о пробках. "
                "Транспорт остаётся жёстким ограничением назначения."
            ),
        }


def build_routing_provider(
    jobs: list[dict[str, Any]], engineers: list[dict[str, Any]]
) -> RoutingProvider:
    # Fail closed by default: a road-routing MVP must not silently downgrade
    # to a straight-line calculation model. Static/auto are explicit modes.
    mode = os.getenv("ROUTING_PROVIDER", "osrm").strip().lower()
    if mode == "static":
        return StaticRoutingProvider(jobs, engineers)
    try:
        return OSRMRoutingProvider(
            jobs,
            engineers,
            base_url=os.getenv("OSRM_BASE_URL", DEFAULT_OSRM_URL),
            timeout_seconds=float(os.getenv("OSRM_TIMEOUT_SECONDS", "15")),
        )
    except Exception as exc:
        if mode == "osrm":
            raise
        return StaticRoutingProvider(
            jobs,
            engineers,
            fallback_reason=f"OSRM недоступен: {exc.__class__.__name__}",
        )


def attach_route_geometry(plan: dict[str, Any], provider: RoutingProvider) -> None:
    """Attach only geometries for the exact matrix arcs used by the plan."""
    road = bool(provider.metadata.get("road_geometry"))
    complete = road
    engineers = {str(item["id"]): item for item in plan.get("engineers") or []}
    for route in plan.get("routes") or []:
        engineer_id = str(route["engineer_id"])
        vehicle = str(engineers[engineer_id].get("vehicle") or "")
        point_ids = [StaticRoutingProvider.engineer_start_id(engineer_id)] + [
            StaticRoutingProvider.job_point_id(str(stop["job_id"]))
            for stop in route.get("stops") or []
        ]
        features: list[dict[str, Any]] = []
        if road and len(point_ids) > 1:
            try:
                features = provider.geometry_for_route(point_ids, vehicle)
                stops = route.get("stops") or []
                if len(features) != len(stops):
                    raise RuntimeError("Число дорожных сегментов не совпадает с числом переездов.")
                for feature, stop in zip(features, stops):
                    properties = feature.get("properties") or {}
                    geometry = feature.get("geometry") or {}
                    coordinates = geometry.get("coordinates") or []
                    if (
                        feature.get("type") != "Feature"
                        or geometry.get("type") != "LineString"
                        or len(coordinates) <= 2
                        or properties.get("source") != "osrm"
                        or properties.get("matrix_id") != provider.signature
                        or int(properties.get("distance_m") or -1)
                        != int(stop.get("distance_m_from_prev") or 0)
                        or int(properties.get("travel_min") or -1)
                        != int(stop.get("travel_min_from_prev") or 0)
                    ):
                        raise RuntimeError("OSRM-сегмент не согласован с матрицей маршрута.")
                if sum(int(item["properties"]["distance_m"]) for item in features) != int(
                    route.get("distance_m") or 0
                ):
                    raise RuntimeError("Сумма дорожных сегментов не совпадает с KPI маршрута.")
            except Exception as exc:
                complete = False
                features = []
                route["geometry_error"] = str(exc)
        for index, stop in enumerate(route.get("stops") or []):
            feature = features[index] if index < len(features) else None
            stop["route_geometry_from_prev"] = feature
            if not feature and int(stop.get("distance_m_from_prev") or 0) > 0:
                complete = False
        route_geometry = (
            {
                "type": "FeatureCollection",
                "source": provider.metadata["provider"],
                "matrix_id": provider.signature,
                "distance_m": sum(int(item["properties"]["distance_m"]) for item in features),
                "travel_min": sum(int(item["properties"]["travel_min"]) for item in features),
                "features": features,
            }
            if features and len(features) == len(route.get("stops") or [])
            else None
        )
        route["route_geometry"] = route_geometry
        # Backward-compatible alias for previously persisted plan versions.
        route["geometry"] = route_geometry
        route["geometry_source"] = provider.metadata["provider"] if route_geometry else None
        route["geometry_matrix_id"] = provider.signature if route_geometry else None
    plan["travel_model"]["geometry_complete"] = complete
    if not complete and road:
        plan.setdefault("warnings", []).append(
            "Часть дорожной геометрии недоступна; расчётные связи не выдаются за дороги."
        )


def rebuild_route_geometry_from_stops(route: dict[str, Any], matrix_id: str) -> None:
    stops = route.get("stops") or []
    features = [
        stop.get("route_geometry_from_prev")
        for stop in stops
        if stop.get("route_geometry_from_prev")
    ]
    valid = len(features) == len(stops)
    if valid:
        for feature, stop in zip(features, stops):
            properties = feature.get("properties") or {}
            geometry = feature.get("geometry") or {}
            valid = valid and (
                feature.get("type") == "Feature"
                and geometry.get("type") == "LineString"
                and len(geometry.get("coordinates") or []) > 2
                and properties.get("source") == "osrm"
                and properties.get("matrix_id") == matrix_id
                and int(properties.get("distance_m") or -1)
                == int(stop.get("distance_m_from_prev") or 0)
                and int(properties.get("travel_min") or -1)
                == int(stop.get("travel_min_from_prev") or 0)
            )
    if valid:
        valid = sum(int(item["properties"]["distance_m"]) for item in features) == int(
            route.get("distance_m") or 0
        )
    if not valid:
        features = []
        if stops:
            route["geometry_error"] = (
                "Сегменты перепланирования относятся к разным матрицам или не содержат дорожную геометрию."
            )
    route_geometry = (
        {
            "type": "FeatureCollection",
            "source": "osrm",
            "matrix_id": matrix_id,
            "distance_m": sum(int(item["properties"]["distance_m"]) for item in features),
            "travel_min": sum(int(item["properties"]["travel_min"]) for item in features),
            "features": features,
        }
        if features and len(features) == len(stops)
        else None
    )
    route["route_geometry"] = route_geometry
    route["geometry"] = route_geometry
    route["geometry_source"] = "osrm" if route_geometry else None
    route["geometry_matrix_id"] = matrix_id if route_geometry else None


# Backward-compatible name used by existing tests and callers.
StaticTravelMatrix = StaticRoutingProvider
