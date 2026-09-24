from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS_ROOT = PROJECT_ROOT / "data" / "scenarios"
SUPPORTED_SCENARIOS = ("east", "southeast", "southcenter")
SCENARIO_LABELS = {
    "east": "Восток",
    "southeast": "Юго-восток",
    "southcenter": "Югоцентр",
}


class ScenarioNotFoundError(ValueError):
    pass


def _scenario_dir(scenario: str) -> Path:
    if scenario not in SUPPORTED_SCENARIOS:
        raise ScenarioNotFoundError(
            f"Неизвестный сценарий {scenario!r}. Доступные сценарии: "
            f"{', '.join(SUPPORTED_SCENARIOS)}."
        )
    return SCENARIOS_ROOT / scenario


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Файл данных не найден: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=len(SUPPORTED_SCENARIOS))
def _load_cached(scenario: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    folder = _scenario_dir(scenario)
    jobs = _read_json(folder / "jobs.json").get("jobs")
    engineers = _read_json(folder / "engineers.json").get("engineers")
    if not isinstance(jobs, list) or not isinstance(engineers, list):
        raise ValueError(
            f"Сценарий {scenario} должен содержать списки jobs и engineers."
        )
    foreign_jobs = [str(job.get("id")) for job in jobs if not str(job.get("id", "")).startswith(f"{scenario}:")]
    foreign_engineers = [
        str(engineer.get("id"))
        for engineer in engineers
        if engineer.get("scenario_id") != scenario
        or not str(engineer.get("id", "")).startswith(f"{scenario}:")
    ]
    if foreign_jobs or foreign_engineers:
        raise ValueError(
            f"Сценарий {scenario} содержит данные другого региона: "
            f"jobs={foreign_jobs[:3]}, engineers={foreign_engineers[:3]}."
        )
    return jobs, engineers


def load_scenario(scenario: str = "east") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _scenario_dir(scenario)
    jobs, engineers = _load_cached(scenario)
    # Планировщик изменяет только свои копии. Исходный сценарий остаётся неизменным.
    return copy.deepcopy(jobs), copy.deepcopy(engineers)


def load_jobs(scenario: str = "east") -> list[dict[str, Any]]:
    return load_scenario(scenario)[0]
