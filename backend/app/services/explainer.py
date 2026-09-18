# НАЗНАЧЕНИЕ: человекочитаемые объяснения решений для диспетчера.
#
# ВХОД:
#   explain_assignment: Job, Engineer, arrive_min, depart_min,
#                       distance_km, travel_min, new_route, baseline
#   explain_unassigned: Job, reason_code, engineers, arrive_min,
#                       window_end_min, shift_end_min
#   translate_warning:  PlanWarning
#
# ВЫХОД:
#   Строка на русском.
#
# СВЯЗИ:
#   - вызывается из optimizer.py, baseline.py, replanner.py
#   - результат кладётся в Job.explanation
#   - translate_warning вызывается в UI-слое для warnings
#   - использует reference/skills.json, reference/vehicles.json
#
# ПРИНЦИПЫ:
#   - Числа отдельными пунктами, не в основном предложении.
#   - Human-readable названия навыков и транспорта (не id).
#   - Разные шаблоны для baseline (первый подходящий) и optimizer
#     (лучшая вставка).
#   - Причина отказа — с уточнением «чего именно не хватает».
#   - Никаких технических кодов в финальном тексте.

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from ..config import settings
from ..schemas.job import Job
from ..schemas.engineer import Engineer
from ..schemas.plan import PlanWarning
from . import constraints as cons


# ---------------------------------------------------------------------------
# Справочники (человекочитаемые названия)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _skills_names() -> dict:
    """id навыка → человеческое имя из reference/skills.json."""
    path = Path(settings.data_reference_dir) / "skills.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {s["id"]: s["name"] for s in data.get("skills", [])}


@lru_cache(maxsize=1)
def _vehicles_names() -> dict:
    """Русское название → само название (для симметрии)."""
    # vehicles.json даёт {id: "car", name: "Автомобиль"}.
    # В engineers.json vehicle хранится русским словом, так что маппинг
    # нужен только если кто-то где-то использует id.
    path = Path(settings.data_reference_dir) / "vehicles.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {v["id"]: v["name"] for v in data.get("vehicles", [])}


def _skill_ru(skill_id: str) -> str:
    return _skills_names().get(skill_id, skill_id)


def _fmt_hhmm(day_min: int) -> str:
    day_min = day_min % (24 * 60)
    return f"{day_min // 60:02d}:{day_min % 60:02d}"


# ---------------------------------------------------------------------------
# Объяснение назначения
# ---------------------------------------------------------------------------

def explain_assignment(
    job: Job,
    engineer: Engineer,
    arrive_min: int,
    depart_min: int,
    distance_km: float,
    travel_min: int,
    new_route: bool,
    baseline: bool,
) -> str:
    """
    Возвращает объяснение, почему заявка назначена этому инженеру.
    Формат — многострочный, с пунктами.
    """
    lines: List[str] = []

    # Заголовок.
    if baseline:
        header = (
            f"Заявка #{job.original_id or job.id} назначена "
            f"«{engineer.name}» ({engineer.id}) — первому подходящему "
            f"по обязательным ограничениям."
        )
    else:
        header = (
            f"Заявка #{job.original_id or job.id} назначена "
            f"«{engineer.name}» ({engineer.id})."
        )
    lines.append(header)
    lines.append("")

    # Пункты с причинами.
    reasons: List[str] = []

    # Квалификация.
    skill_ru = _skill_ru(job.skill)
    reasons.append(
        f"Навык «{skill_ru}» входит в компетенции бригады."
    )

    # Транспорт.
    if job.required_vehicle:
        reasons.append(
            f"Требуется транспорт «{job.required_vehicle}» — "
            f"у бригады он есть."
        )
    else:
        reasons.append(
            f"Тип транспорта «{engineer.vehicle}» подходит "
            f"(в заявке ограничений нет)."
        )

    # Окно.
    ws = _fmt_hhmm(_min_of_day(job.window_start))
    we = _fmt_hhmm(_min_of_day(job.window_end))
    reasons.append(
        f"Прибытие в {_fmt_hhmm(arrive_min)} попадает в окно "
        f"{ws}–{we}."
    )

    # Пробег и время в пути.
    reasons.append(
        f"Пробег до заявки: {distance_km:.1f} км, "
        f"время в пути: {travel_min} мин."
    )

    lines.append("Причины:")
    for r in reasons:
        lines.append(f"  • {r}")
    lines.append("")

    # Строка про вставку в маршрут.
    if new_route:
        lines.append(
            "Заявка открыла новый маршрут у этой бригады — "
            "до неё бригада не была задействована."
        )
    else:
        lines.append(
            "Заявка встроена в существующий маршрут бригады."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Объяснение отказа
# ---------------------------------------------------------------------------

def explain_unassigned(
    job: Job,
    reason_code: str,
    engineers: List[Engineer],
    arrive_min: Optional[int],
    window_end_min: Optional[int],
    shift_end_min: Optional[int],
) -> str:
    """
    Объясняет, почему заявку не удалось назначить.
    Формат: одна короткая фраза + уточнение.
    """
    job_label = f"#{job.original_id or job.id}"
    skill_ru = _skill_ru(job.skill)

    if reason_code == cons.NO_SKILL:
        # Проверяем, есть ли хоть у кого-то этот навык.
        has_skill = [e for e in engineers if job.skill in e.skills]
        if not has_skill:
            return (
                f"Заявка {job_label} не назначена: ни у одного инженера "
                f"в смене нет навыка «{skill_ru}»."
            )
        names = ", ".join(f"«{e.name}»" for e in has_skill[:3])
        more = "" if len(has_skill) <= 3 else f" и ещё {len(has_skill) - 3}"
        return (
            f"Заявка {job_label} не назначена: навык «{skill_ru}» есть "
            f"только у {names}{more}, но они не подходят по времени или "
            f"транспорту."
        )

    if reason_code == cons.NO_VEHICLE:
        req = job.required_vehicle or "—"
        return (
            f"Заявка {job_label} не назначена: требуется транспорт "
            f"«{req}», но ни у одного инженера с нужным навыком "
            f"«{skill_ru}» его нет."
        )

    if reason_code == cons.NO_EQUIPMENT:
        return (
            f"Заявка {job_label} не назначена: у подходящих инженеров "
            f"не хватает оборудования для этой заявки."
        )

    if reason_code == cons.OUT_OF_WINDOW:
        ws = _fmt_hhmm(_min_of_day(job.window_start))
        we = _fmt_hhmm(_min_of_day(job.window_end))
        if arrive_min is not None:
            return (
                f"Заявка {job_label} не назначена: ближайшее возможное "
                f"прибытие {_fmt_hhmm(arrive_min)} позже окончания окна "
                f"{we} (окно {ws}–{we})."
            )
        return (
            f"Заявка {job_label} не назначена: ни один инженер не успевает "
            f"приехать в окно {ws}–{we} с учётом времени в пути."
        )

    if reason_code == cons.OUT_OF_SHIFT:
        if depart_min_hint := _shift_hint(engineers):
            return (
                f"Заявка {job_label} не назначена: работа не укладывается "
                f"в смену (смены заканчиваются в {depart_min_hint})."
            )
        return (
            f"Заявка {job_label} не назначена: работа не укладывается "
            f"в смену ни одного инженера."
        )

    if reason_code == cons.NO_ENGINEER:
        return (
            f"Заявка {job_label} не назначена: все подходящие инженеры "
            f"уже заняты или недоступны."
        )

    # Дефолт — не должны сюда попасть, но пусть будет.
    return (
        f"Заявка {job_label} не назначена: "
        f"причина не определена ({reason_code})."
    )


def _shift_hint(engineers: List[Engineer]) -> Optional[str]:
    """Возвращает самое позднее время окончания смены среди инженеров."""
    if not engineers:
        return None
    late = max(_min_of_day(e.shift_end) for e in engineers)
    return _fmt_hhmm(late)


# ---------------------------------------------------------------------------
# Перевод warnings
# ---------------------------------------------------------------------------

def translate_warning(warning: PlanWarning) -> str:
    """
    Превращает PlanWarning в человекочитаемую строку на русском.
    """
    code = warning.code
    jid = warning.job_id
    eid = warning.engineer_id

    if code == "IN_PROGRESS_ENGINEER_UNAVAILABLE":
        return (
            f"Заявка {jid} уже выполняется инженером {eid}, "
            f"который отмечен как недоступный. Требуется решение "
            f"диспетчера: оставить (инженер доработает), переназначить "
            f"или отменить."
        )

    if code == "CANCEL_IGNORED_FROZEN":
        return (
            f"Заявку {jid} нельзя отменить: она уже выполняется или "
            f"выполнена. Если отмена всё же нужна — сделайте это вручную."
        )

    if code == "UNASSIGNED_AFTER_REPLAN":
        return (
            f"Заявка {jid} осталась неназначенной после перепланирования: "
            f"ни один инженер не может её взять с учётом текущих окон "
            f"и загрузки."
        )

    if code == "OPTIMIZER_TIMEOUT":
        return (
            f"Полный пересчёт не успел завершиться за отведённое время. "
            f"План может быть неоптимальным."
        )

    # Дефолт.
    if warning.message:
        return warning.message
    return f"Предупреждение {code} по заявке {jid or '—'}."


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------

def _min_of_day(dt) -> int:
    return dt.hour * 60 + dt.minute


def clear_caches() -> None:
    """Сбросить кэши справочников (для тестов)."""
    _skills_names.cache_clear()
    _vehicles_names.cache_clear()
