# НАЗНАЧЕНИЕ: проверка трёх групп обязательных ограничений.
#
# ВХОД:
#   Job, Engineer, текущее время прибытия (для цепочки).
#
# ВЫХОД:
#   bool + причина отказа (если не подходит).
#
# СВЯЗИ:
#   - вызывается из baseline.py, optimizer.py, replanner.py
#   - использует distance.py для travel time
#
# ТРИ ГРУППЫ:
#   1. Квалификация: job.skill in engineer.skills.
#   2. Время: arrive ∈ job.window; весь маршрут ∈ engineer.shift.
#   3. Ресурс: job.required_vehicle is None or == engineer.vehicle.
#
# ВАЖНО: возвращать не просто False, а код причины:
#   NO_SKILL | NO_VEHICLE | OUT_OF_WINDOW | OUT_OF_SHIFT | NO_ENGINEER
#   — чтобы explainer.py мог объяснить неназначенные заявки.

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Tuple
from ..schemas.job import Job
from ..schemas.engineer import Engineer
from . import distance as dist_mod

# --- коды причин ---
NO_SKILL      = "NO_SKILL"
NO_VEHICLE    = "NO_VEHICLE"
OUT_OF_WINDOW = "OUT_OF_WINDOW"
OUT_OF_SHIFT  = "OUT_OF_SHIFT"
NO_ENGINEER   = "NO_ENGINEER"

@dataclass
class CheckResult:
    ok: bool
    reason: Optional[str] = None
    arrive_min: Optional[int] = None      # абсолютные минуты от начала суток
    depart_min: Optional[int] = None
    travel_min: int = 0

def _min_of_day(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute

def can_assign(
    job: Job,
    engineer: Engineer,
    prev_point: list,              # [lat, lon]
    ready_min: int,                # когда инженер готов начать движение
    window_start_min: int,         # окно заявки в минутах
    window_end_min: int,
    shift_start_min: int,
    shift_end_min: int,
) -> CheckResult:
    # 1) Квалификация
    if job.skill not in engineer.skills:
        return CheckResult(False, NO_SKILL)

    # 2) Ресурс
    if job.required_vehicle and job.required_vehicle != engineer.vehicle:
        return CheckResult(False, NO_VEHICLE)

    # 3) Время: считаем приезд
    km, travel_min = dist_mod.travel(prev_point, job.coords, engineer.vehicle)
    arrive_min = ready_min + travel_min

    # окно заявки
    service_min = job.duration_min
    if arrive_min < window_start_min:
        arrive_min = window_start_min      # ждём начала окна
    depart_min = arrive_min + service_min

    if arrive_min > window_end_min:
        return CheckResult(False, OUT_OF_WINDOW, travel_min=travel_min)
    if depart_min > shift_end_min:
        return CheckResult(False, OUT_OF_SHIFT, travel_min=travel_min)
    if arrive_min < shift_start_min:
        return CheckResult(False, OUT_OF_SHIFT, travel_min=travel_min)

    return CheckResult(True, None, arrive_min, depart_min, travel_min)
