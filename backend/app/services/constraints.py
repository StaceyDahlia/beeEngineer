# НАЗНАЧЕНИЕ: проверка обязательных ограничений при назначении заявки инженеру.
#
# ВХОД:
#   Job, Engineer, prev_point, ready_min, границы окна и смены,
#   текущий остаток инвентаря инженера.
#
# ВЫХОД:
#   CheckResult(ok, reason, arrive_min, depart_min, travel_min)
#
# СВЯЗИ:
#   - вызывается из baseline.py, optimizer.py, replanner.py
#   - использует distance.py (travel)
#   - reason-коды читает explainer.py
#
# ТРИ ГРУППЫ ОГРАНИЧЕНИЙ:
#   1. Квалификация: job.skill in engineer.skills.
#   2. Ресурс: required_vehicle подходит; required_equipment хватает.
#   3. Время: arrive ∈ окно заявки; весь визит ∈ смена инженера.
#
# КОДЫ ПРИЧИН:
#   NO_SKILL | NO_VEHICLE | NO_EQUIPMENT | OUT_OF_WINDOW | OUT_OF_SHIFT | NO_ENGINEER
#
# ДОПУЩЕНИЯ:
#   - Оборудование расходуется в момент завершения работ.
#     Инвентарь для проверки передаёт вызывающий код (см. inventory_remaining).
#   - Возврат в стартовую точку после последней заявки не требуется.

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence

from ..schemas.job import Job
from ..schemas.engineer import Engineer
from . import distance as dist_mod


# ---------------------------------------------------------------------------
# Коды причин отказа
# ---------------------------------------------------------------------------

NO_SKILL       = "NO_SKILL"
NO_VEHICLE     = "NO_VEHICLE"
NO_EQUIPMENT   = "NO_EQUIPMENT"
OUT_OF_WINDOW  = "OUT_OF_WINDOW"
OUT_OF_SHIFT   = "OUT_OF_SHIFT"
NO_ENGINEER    = "NO_ENGINEER"

ALL_REASONS = {
    NO_SKILL, NO_VEHICLE, NO_EQUIPMENT,
    OUT_OF_WINDOW, OUT_OF_SHIFT, NO_ENGINEER,
}


# ---------------------------------------------------------------------------
# Результат
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    ok: bool
    reason: Optional[str] = None
    arrive_min: Optional[int] = None
    depart_min: Optional[int] = None
    travel_min: int = 0
    distance_km: float = 0.0


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def _min_of_day(dt) -> int:
    return dt.hour * 60 + dt.minute


def _differs(a: Sequence[float], b: Sequence[float]) -> bool:
    return a[0] != b[0] or a[1] != b[1]


# ---------------------------------------------------------------------------
# Публичный интерфейс
# ---------------------------------------------------------------------------

def can_assign(
    job: Job,
    engineer: Engineer,
    prev_point: Sequence[float],
    ready_min: int,
    *,
    window_start_min: int,
    window_end_min: int,
    shift_start_min: int,
    shift_end_min: int,
    inventory_remaining: Optional[Dict[str, int]] = None,
    check_equipment: bool = True,
) -> CheckResult:
    """
    Проверяет, можно ли назначить заявку инженеру.

    prev_point     — [lat, lon] точки, из которой инженер выезжает
                     (стартовая точка или предыдущая заявка).
    ready_min      — минуты от начала суток, когда инженер готов начать движение.
    inventory_remaining — текущий остаток оборудования (уже с учётом ранее
                     назначенных заявок этого инженера). Если None —
                     берём engineer.inventory_start.
    check_equipment — если False, оборудование не проверяется (для baseline
                     и для MVP, пока не готов учёт инвентаря).
    """

    # --- 0. Статус инженера ---
    if engineer.status != "Доступен":
        return CheckResult(False, NO_ENGINEER)

    # --- 1. Квалификация ---
    if job.skill not in engineer.skills:
        return CheckResult(False, NO_SKILL)

    # --- 2. Ресурс: транспорт ---
    if job.required_vehicle and job.required_vehicle != engineer.vehicle:
        return CheckResult(False, NO_VEHICLE)

    # --- 2b. Ресурс: оборудование ---
    if check_equipment and job.required_equipment:
        inventory = (
            inventory_remaining
            if inventory_remaining is not None
            else engineer.inventory_start
        )
        for item, need in job.required_equipment.items():
            if inventory.get(item, 0) < need:
                return CheckResult(False, NO_EQUIPMENT)

    # --- 3. Время ---
    # 3a. Рассчитываем поездку. depart_min = ready_min (момент старта).
    km, travel_min = dist_mod.travel(
        prev_point, job.coords, engineer.vehicle, depart_min=ready_min,
    )

    arrive_raw = ready_min + travel_min

    # 3b. Проверяем, что инженер вообще успевает доехать до конца смены.
    #     Это отдельный случай — смена кончается, а мы ещё в пути.
    if arrive_raw > shift_end_min:
        return CheckResult(
            False, OUT_OF_SHIFT,
            arrive_min=arrive_raw,
            travel_min=travel_min,
            distance_km=km,
        )

    # 3c. Если приехали раньше окна — ждём начала окна.
    #     Если приехали раньше смены — это невозможно (ready_min >= shift_start
    #     по построению), но на всякий случай берём max.
    arrive_min = max(arrive_raw, window_start_min)
    depart_min = arrive_min + job.duration_min

    # 3d. Проверка попадания в окно заявки.
    if arrive_min > window_end_min:
        return CheckResult(
            False, OUT_OF_WINDOW,
            arrive_min=arrive_min,
            depart_min=depart_min,
            travel_min=travel_min,
            distance_km=km,
        )

    # 3e. Проверка окончания работ до конца смены.
    if depart_min > shift_end_min:
        return CheckResult(
            False, OUT_OF_SHIFT,
            arrive_min=arrive_min,
            depart_min=depart_min,
            travel_min=travel_min,
            distance_km=km,
        )

    # Всё хорошо.
    return CheckResult(
        True, None,
        arrive_min=arrive_min,
        depart_min=depart_min,
        travel_min=travel_min,
        distance_km=km,
    )


def apply_inventory_consumption(
    job: Job,
    inventory_remaining: Dict[str, int],
) -> None:
    """
    Уменьшает остаток инвентаря на требуемое заявкой оборудование.
    Вызывается вызывающим кодом ПОСЛЕ того, как can_assign вернул ok=True.
    """
    for item, need in job.required_equipment.items():
        inventory_remaining[item] = inventory_remaining.get(item, 0) - need
