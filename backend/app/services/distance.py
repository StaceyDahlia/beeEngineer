# НАЗНАЧЕНИЕ: расстояние и время в пути между двумя точками.
#
# ВХОД:
#   coords_from, coords_to: [lat, lon]
#   vehicle: тип транспорта (влияет на скорость)
#
# ВЫХОД:
#   (distance_km: float, travel_min: int)
#
# СВЯЗИ:
#   - вызывается из optimizer.py, baseline.py, metrics.py
#   - использует reference/vehicles.json (avg_speed_kmh)
#   - кэш матрицы: data/processed/osrm_cache/ (если OSRM)
#
# РЕЖИМЫ (config.distance_provider):
#   haversine — по формуле, время = distance / speed * 60
#   osrm      — GET /route/v1/driving/{lon1},{lat1};{lon2},{lat2}

import math, json
from pathlib import Path
from functools import lru_cache
from ..config import settings

VEHICLE_RU_TO_ID = {
    "Автомобиль": "car", "Пешеход": "foot",
    "Велосипед": "bike", "ОТ": "trans",
}
ROAD_MODES = {"car", "trans"}    # trans приближаем как наземный
TRAFFIC_AFFECTED = {"car", "trans"}

@lru_cache
def _vehicles() -> dict:
    p = Path(settings.reference_dir) / "vehicles.json"
    return {v["id"]: v for v in json.loads(p.read_text())["vehicles"]}

@lru_cache
def _traffic() -> dict:
    p = Path(settings.reference_dir) / "traffic_profiles.json"
    return json.loads(p.read_text())

def haversine_km(a, b) -> float:
    lat1, lon1 = a; lat2, lon2 = b
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1); dl = math.radians(lon2 - lon1)
    h = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(h))

def travel(a, b, vehicle_ru: str) -> tuple[float, int]:
    v_id = VEHICLE_RU_TO_ID.get(vehicle_ru, "car")
    speed = _vehicles()[v_id]["avg_speed_kmh"]
    km = haversine_km(a, b)
    # «дорожный» коэффициент на извилистость — 1.3 для дорог, 1.1 для пешехода
    km_route = km * (1.3 if v_id in ROAD_MODES else 1.1)
    base_min = km_route / speed * 60
    if v_id in TRAFFIC_AFFECTED:
        base_min *= _traffic_coef_now()   # см. ниже
    return round(km_route, 2), int(round(base_min))
