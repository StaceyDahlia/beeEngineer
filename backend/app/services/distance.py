# НАЗНАЧЕНИЕ: расстояние и время в пути между двумя точками.
#
# ВХОД:
#   a, b         — точки [lat, lon]
#   vehicle_ru   — русское название транспорта из engineers.json:
#                  "Автомобиль" | "Пешеход" | "Велосипед" | "ОТ"
#   depart_min   — время выезда в минутах от начала суток (0..1439)
#
# ВЫХОД:
#   (distance_km: float, travel_min: int)
#
# СВЯЗИ:
#   - вызывается из constraints.py, optimizer.py, baseline.py
#   - использует reference/vehicles.json (avg_speed_kmh)
#   - использует reference/traffic_profiles.json (почасовые коэффициенты)
#
# ДОПУЩЕНИЯ (обязательно упомянуть в README):
#   1. Расстояние считается по формуле Haversine (по прямой),
#      затем умножается на коэффициент извилистости дорог ROUTE_FACTOR.
#      Реальная дорожная сеть не используется.
#   2. ОТ ("trans") моделируется как единый modal split со средней
#      скоростью 40 км/ч; коэффициенты пробок применяются к нему
#      так же, как к surface_transit (см. traffic_profiles.json).
#   3. Пробки применяются только к "car" и "trans" методом
#      integrate_free_flow_minutes_across_hour_boundaries —
#      базовая поездка разбивается по границам часов.
#   4. Пешеход и велосипед от пробок не зависят.
#   5. Дата сценария фиксирована: 17.08.2026 — понедельник => weekday.

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..config import settings


# ---------------------------------------------------------------------------
# 1. Константы
# ---------------------------------------------------------------------------

EARTH_RADIUS_KM = 6371.0

VEHICLE_RU_TO_ID: Dict[str, str] = {
    "Автомобиль": "car",
    "Пешеход": "foot",
    "Велосипед": "bike",
    "ОТ": "trans",
}

# К каким режимам применяем пробки. ОТ — как surface_transit (см. допущение 2).
TRAFFIC_AFFECTED_VEHICLES = {"car", "trans"}

# Коэффициент извилистости дорог относительно Haversine.
ROUTE_FACTOR: Dict[str, float] = {
    "car": 1.30,
    "trans": 1.30,
    "bike": 1.15,
    "foot": 1.10,
}

# Дата сценария фиксирована: 17.08.2026 — понедельник.
DEFAULT_DAY_TYPE = "weekday"

# Минимум минут на поездку между разными точками (после округления).
MIN_TRAVEL_MIN = 1


# ---------------------------------------------------------------------------
# 2. Загрузка справочников (кэш на уровне процесса)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _vehicles() -> Dict[str, dict]:
    """Справочник типов транспорта: id -> {name, avg_speed_kmh}."""
    path = Path(settings.data_reference_dir) / "vehicles.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Справочник транспорта не найден: {path}. "
            f"Проверьте data_reference_dir в .env."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return {item["id"]: item for item in data["vehicles"]}


@lru_cache(maxsize=1)
def _traffic() -> dict:
    """Справочник пробок. Возвращает корневой объект."""
    path = Path(settings.data_reference_dir) / "traffic_profiles.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Справочник пробок не найден: {path}. "
            f"Проверьте data_reference_dir в .env."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_profile(name: Optional[str]) -> Tuple[str, dict]:
    """Вернуть (имя_профиля, объект профиля). Если name=None — default."""
    traffic = _traffic()
    profile_name = name or traffic.get("default_profile", "typical")
    profiles = traffic.get("profiles", {})
    if profile_name not in profiles:
        raise KeyError(
            f"Профиль пробок '{profile_name}' не найден. "
            f"Доступные: {list(profiles.keys())}"
        )
    return profile_name, profiles[profile_name]


# ---------------------------------------------------------------------------
# 3. Геометрия
# ---------------------------------------------------------------------------

def _is_same_point(a: Sequence[float], b: Sequence[float]) -> bool:
    return a[0] == b[0] and a[1] == b[1]


def haversine_km(a: Sequence[float], b: Sequence[float]) -> float:
    """Расстояние по прямой между двумя точками [lat, lon] в километрах."""
    lat1, lon1 = a[0], a[1]
    lat2, lon2 = b[0], b[1]

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    h = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    # asin(sqrt(h)) численно стабильнее, чем acos для малых расстояний.
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


# ---------------------------------------------------------------------------
# 4. Пробки: интеграция базовой поездки по границам часов
# ---------------------------------------------------------------------------

def _hour_of(day_min: int) -> int:
    """Час суток (0..23) для минуты от начала суток."""
    return (day_min // 60) % 24


def _minutes_until_next_hour(day_min: int) -> int:
    """Сколько минут осталось до следующего часа. 60, если мы ровно в :00."""
    rem = 60 - (day_min % 60)
    return rem if rem > 0 else 60


def _integrate_traffic(
    base_min: float,
    depart_min: int,
    profile_name: str,
    day_type: str,
) -> float:
    """
    Применяет почасовые коэффициенты к базовой длительности поездки.
    Реализует метод integrate_free_flow_minutes_across_hour_boundaries:
    поездка разрезается на куски по границам часов, каждый кусок
    умножается на коэффициент соответствующего часа.
    """
    _, profile_obj = _resolve_profile(profile_name)
    coefficients = profile_obj.get(day_type)
    if coefficients is None:
        raise KeyError(
            f"day_type '{day_type}' не найден в профиле '{profile_name}'. "
            f"Доступные: {list(profile_obj.keys())}"
        )

    remaining = float(base_min)
    current_min = int(depart_min) % (24 * 60)
    total = 0.0

    while remaining > 1e-9:
        hour = _hour_of(current_min)
        coef = float(coefficients[hour])
        chunk = min(remaining, _minutes_until_next_hour(current_min))
        total += chunk * coef
        current_min += int(round(chunk))
        remaining -= chunk

    return total


# ---------------------------------------------------------------------------
# 5. Публичное API
# ---------------------------------------------------------------------------

def travel(
    a: Sequence[float],
    b: Sequence[float],
    vehicle_ru: str,
    depart_min: int,
    *,
    day_type: str = DEFAULT_DAY_TYPE,
    profile: Optional[str] = None,
) -> Tuple[float, int]:
    """
    Расстояние и время в пути.

    Параметры:
        a, b        — [lat, lon]
        vehicle_ru  — "Автомобиль" | "Пешеход" | "Велосипед" | "ОТ"
        depart_min  — минуты от начала суток (0..1439)
        day_type    — "weekday" | "weekend"
        profile     — имя профиля пробок; None => default из справочника

    Возвращает:
        (distance_km, travel_min) — округление: км до 3 знаков,
        минуты до целого, минимум 1 для разных точек.
    """
    if vehicle_ru not in VEHICLE_RU_TO_ID:
        raise ValueError(
            f"Неизвестный тип транспорта: {vehicle_ru!r}. "
            f"Ожидается один из: {list(VEHICLE_RU_TO_ID.keys())}"
        )

    if _is_same_point(a, b):
        return 0.0, 0

    vehicle_id = VEHICLE_RU_TO_ID[vehicle_ru]
    vehicle = _vehicles().get(vehicle_id)
    if vehicle is None:
        raise KeyError(
            f"Транспорт '{vehicle_id}' отсутствует в vehicles.json."
        )

    speed_kmh = float(vehicle["avg_speed_kmh"])
    if speed_kmh <= 0:
        raise ValueError(f"Некорректная скорость для {vehicle_id}: {speed_kmh}")

    km_geom = haversine_km(a, b)
    km_route = km_geom * ROUTE_FACTOR[vehicle_id]

    base_min = km_route / speed_kmh * 60.0

    if vehicle_id in TRAFFIC_AFFECTED_VEHICLES:
        base_min = _integrate_traffic(
            base_min=base_min,
            depart_min=int(depart_min) % (24 * 60),
            profile_name=profile or _traffic().get("default_profile", "typical"),
            day_type=day_type,
        )

    travel_min = max(MIN_TRAVEL_MIN, int(round(base_min)))
    return round(km_route, 3), travel_min


# ---------------------------------------------------------------------------
# 6. Кэш матрицы (для optimizer и baseline)
# ---------------------------------------------------------------------------
#
# Кэшируем только «чистую» часть — км и base_min без пробок.
# Пробки зависят от depart_min, поэтому применяются поверх кэша.
# Ключ — (loc_a_id, loc_b_id, vehicle_id).

_PAIR_CACHE: Dict[Tuple[str, str, str], Tuple[float, int]] = {}


def _base_pair(
    loc_a_id: str,
    loc_b_id: str,
    vehicle_ru: str,
) -> Tuple[float, int]:
    """Км и базовые минуты без пробок. Нужны координаты через _coords_by_id."""
    raise RuntimeError(
        "_base_pair не вызывается напрямую: используйте cached_travel "
        "с явными координатами."
    )


def cached_travel(
    loc_a_id: str,
    loc_b_id: str,
    coords_a: Sequence[float],
    coords_b: Sequence[float],
    vehicle_ru: str,
    depart_min: int,
    *,
    day_type: str = DEFAULT_DAY_TYPE,
    profile: Optional[str] = None,
) -> Tuple[float, int]:
    """
    Как travel(), но кэширует (km, base_min_without_traffic) по
    (loc_a_id, loc_b_id, vehicle_id). Пробки применяются поверх.
    """
    if loc_a_id == loc_b_id or _is_same_point(coords_a, coords_b):
        return 0.0, 0

    if vehicle_ru not in VEHICLE_RU_TO_ID:
        raise ValueError(f"Неизвестный тип транспорта: {vehicle_ru!r}")
    vehicle_id = VEHICLE_RU_TO_ID[vehicle_ru]

    key = (loc_a_id, loc_b_id, vehicle_id)
    cached = _PAIR_CACHE.get(key)

    if cached is None:
        vehicle = _vehicles()[vehicle_id]
        speed_kmh = float(vehicle["avg_speed_kmh"])
        km_geom = haversine_km(coords_a, coords_b)
        km_route = km_geom * ROUTE_FACTOR[vehicle_id]
        base_min = km_route / speed_kmh * 60.0
        cached = (round(km_route, 3), base_min)   # base_min float, не округляем
        _PAIR_CACHE[key] = cached

    km_route, base_min = cached

    if vehicle_id in TRAFFIC_AFFECTED_VEHICLES:
        base_min = _integrate_traffic(
            base_min=base_min,
            depart_min=int(depart_min) % (24 * 60),
            profile_name=profile or _traffic().get("default_profile", "typical"),
            day_type=day_type,
        )

    travel_min = max(MIN_TRAVEL_MIN, int(round(base_min)))
    return km_route, travel_min


def precompute(
    points: List[Tuple[str, Sequence[float]]],
    vehicles_ru: Sequence[str],
) -> None:
    """
    Прогревает кэш для всех пар точек и указанных транспортов.
    Полезно при старте optimizer, чтобы не платить за первый проход.
    """
    speeds_vehicle_ids = set()
    for v in vehicles_ru:
        if v not in VEHICLE_RU_TO_ID:
            raise ValueError(f"Неизвестный тип транспорта: {v!r}")
        speeds_vehicle_ids.add(VEHICLE_RU_TO_ID[v])

    veh = _vehicles()
    for i, (id_a, coords_a) in enumerate(points):
        for j, (id_b, coords_b) in enumerate(points):
            if i == j:
                continue
            for vehicle_id in speeds_vehicle_ids:
                key = (id_a, id_b, vehicle_id)
                if key in _PAIR_CACHE:
                    continue
                speed_kmh = float(veh[vehicle_id]["avg_speed_kmh"])
                km_geom = haversine_km(coords_a, coords_b)
                km_route = km_geom * ROUTE_FACTOR[vehicle_id]
                base_min = km_route / speed_kmh * 60.0
                _PAIR_CACHE[key] = (round(km_route, 3), base_min)


def cache_stats() -> dict:
    return {"pairs": len(_PAIR_CACHE)}


def clear_cache() -> None:
    _PAIR_CACHE.clear()
    _vehicles.cache_clear()
    _traffic.cache_clear()
