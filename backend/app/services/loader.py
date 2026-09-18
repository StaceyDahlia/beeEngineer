# НАЗНАЧЕНИЕ: чтение данных сценария и превращение в Pydantic-модели.
#
# ВХОД:
#   data/processed/jobs.json, engineers.json, events.json, locations.json
#   или data/processed/scenarios/{scenario}/... — если scenario задан явно.
#
# ВЫХОД:
#   List[Job], List[Engineer], List[Event], List[Location]
#
# СВЯЗИ:
#   - вызывается из api/* и scripts/*
#   - использует schemas/job.py, engineer.py, event.py, location.py
#   - данные передаются в constraints.py, optimizer.py, replanner.py
#
# ОСОБЕННОСТИ:
#   - Сценарий выбирается заранее скриптом scripts/select_scenario.py
#     и копируется в data/processed/. Backend читает data/processed/.
#   - Опционально: load_*(scenario="east") читает напрямую из
#     data/processed/scenarios/east/.
#   - Кэш через lru_cache. Файлы не меняются в рантайме.
#   - Понятные ошибки: если файла нет — подсказка запустить
#     scripts/select_scenario.py.
#   - Валидация ссылочной целостности — в dev-режиме
#     (settings.debug_validate_data=True).

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

from ..config import settings
from ..schemas.job import Job
from ..schemas.engineer import Engineer
from ..schemas.event import Event
from ..schemas.location import Location


# ---------------------------------------------------------------------------
# Разрешение пути
# ---------------------------------------------------------------------------

def _processed_dir(scenario: Optional[str] = None) -> Path:
    """
    Куда смотреть:
      scenario=None -> data/processed/
      scenario="east" -> data/processed/scenarios/east/
    """
    base = Path(settings.data_processed_dir)
    if scenario is None:
        return base
    return base / "scenarios" / scenario


def _read_json(path: Path, *, what: str, hint: str = "") -> dict:
    """Читает JSON с понятными ошибками."""
    if not path.exists():
        msg = f"{what} не найден: {path}."
        if hint:
            msg += f" {hint}"
        raise FileNotFoundError(msg)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(
            f"{what} повреждён ({path}): {e.msg} "
            f"(строка {e.lineno}, колонка {e.colno})"
        ) from e


# ---------------------------------------------------------------------------
# Публичный интерфейс
# ---------------------------------------------------------------------------

def load_jobs(scenario: Optional[str] = None) -> List[Job]:
    return _load_jobs(scenario)


def load_engineers(scenario: Optional[str] = None) -> List[Engineer]:
    return _load_engineers(scenario)


def load_events(scenario: Optional[str] = None) -> List[Event]:
    return _load_events(scenario)


def load_locations(scenario: Optional[str] = None) -> List[Location]:
    return _load_locations(scenario)


def load_all(
    scenario: Optional[str] = None,
    *,
    validate: Optional[bool] = None,
) -> Tuple[List[Job], List[Engineer], List[Event]]:
    """
    Удобный вход для API: читает jobs+engineers+events одним вызовом.
    Locations не возвращает — их обычно тянут отдельно для карты.
    """
    jobs = load_jobs(scenario)
    engineers = load_engineers(scenario)
    events = load_events(scenario)

    if validate is None:
        validate = getattr(settings, "debug_validate_data", False)
    if validate:
        _validate_references(jobs, engineers, scenario)

    return jobs, engineers, events


def load_jobs_indexed(scenario: Optional[str] = None) -> dict:
    """job.id → Job. Удобно для быстрых обращений в API и replanner."""
    return {j.id: j for j in load_jobs(scenario)}


def load_engineers_indexed(scenario: Optional[str] = None) -> dict:
    """engineer.id → Engineer."""
    return {e.id: e for e in load_engineers(scenario)}


def clear_caches() -> None:
    """Сбросить кэши. Нужно после select_scenario.py в dev-режиме."""
    _load_jobs.cache_clear()
    _load_engineers.cache_clear()
    _load_events.cache_clear()
    _load_locations.cache_clear()


def active_scenario() -> Optional[str]:
    """
    Возвращает имя активного сценария, если backend читает из
    data/processed/ (не из scenarios/). Определяется по наличию файла
    data/processed/scenario.txt или по содержимому jobs.json (поле scenario_id).
    """
    base = Path(settings.data_processed_dir)
    jobs_path = base / "jobs.json"
    if not jobs_path.exists():
        return None
    try:
        data = json.loads(jobs_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    # Если есть top-level scenario_id — берём.
    sid = data.get("scenario_id")
    if sid:
        return sid
    # Иначе — из первой заявки.
    jobs = data.get("jobs") or []
    if jobs:
        jid = jobs[0].get("id") or ""
        if ":" in jid:
            return jid.split(":", 1)[0]
    return None


# ---------------------------------------------------------------------------
# Кэшированные загрузчики
# ---------------------------------------------------------------------------

@lru_cache(maxsize=8)
def _load_jobs(scenario: Optional[str]) -> List[Job]:
    path = _processed_dir(scenario) / "jobs.json"
    data = _read_json(
        path, what="Файл заявок",
        hint="Запустите: python scripts/select_scenario.py east",
    )
    items = data.get("jobs")
    if items is None:
        raise ValueError(f"В {path} нет ключа 'jobs'.")
    return [Job(**item) for item in items]


@lru_cache(maxsize=8)
def _load_engineers(scenario: Optional[str]) -> List[Engineer]:
    path = _processed_dir(scenario) / "engineers.json"
    data = _read_json(
        path, what="Файл инженеров",
        hint="Запустите: python scripts/select_scenario.py east",
    )
    items = data.get("engineers")
    if items is None:
        raise ValueError(f"В {path} нет ключа 'engineers'.")
    return [Engineer(**item) for item in items]


@lru_cache(maxsize=8)
def _load_events(scenario: Optional[str]) -> List[Event]:
    path = _processed_dir(scenario) / "events.json"
    if not path.exists():
        # События опциональны — не падаем.
        return []
    data = _read_json(path, what="Файл событий")
    items = data.get("events") or []
    return [Event(**item) for item in items]


@lru_cache(maxsize=8)
def _load_locations(scenario: Optional[str]) -> List[Location]:
    path = _processed_dir(scenario) / "locations.json"
    if not path.exists():
        return []
    data = _read_json(path, what="Файл локаций")
    items = data.get("locations") or []
    return [Location(**item) for item in items]


# ---------------------------------------------------------------------------
# Валидация ссылочной целостности (dev-режим)
# ---------------------------------------------------------------------------

def _validate_references(
    jobs: List[Job],
    engineers: List[Engineer],
    scenario: Optional[str],
) -> None:
    """
    Проверяет, что все ссылки в данных консистентны.
    Падает с ValueError при первой проблеме.
    """
    eng_ids = {e.id for e in engineers}
    locations = load_locations(scenario)
    loc_ids = {loc.id for loc in locations}

    seen_job_ids: set = set()
    for j in jobs:
        if j.id in seen_job_ids:
            raise ValueError(f"Дубликат id заявки: {j.id}")
        seen_job_ids.add(j.id)

        if j.assigned_engineer_id and j.assigned_engineer_id not in eng_ids:
            raise ValueError(
                f"Заявка {j.id}: assigned_engineer_id="
                f"{j.assigned_engineer_id} не найден среди инженеров."
            )
        if j.location_id and loc_ids and j.location_id not in loc_ids:
            raise ValueError(
                f"Заявка {j.id}: location_id={j.location_id} "
                f"не найден в locations.json."
            )

    seen_eng_ids: set = set()
    for e in engineers:
        if e.id in seen_eng_ids:
            raise ValueError(f"Дубликат id инженера: {e.id}")
        seen_eng_ids.add(e.id)


# ---------------------------------------------------------------------------
# Утилита для scripts и тестов
# ---------------------------------------------------------------------------

def find_scenarios() -> List[str]:
    """
    Возвращает список доступных сценариев в data/processed/scenarios/.
    Удобно для дебага и для CLI.
    """
    base = Path(settings.data_processed_dir) / "scenarios"
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir())
