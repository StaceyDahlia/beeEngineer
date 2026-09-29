from __future__ import annotations

import csv
import io
import json
import re
import uuid
from datetime import datetime
from typing import Any


class ScenarioImportError(ValueError):
    pass


SKILLS = {"local", "connect", "emergency"}
VEHICLE_ALIASES = {
    "car": "Автомобиль",
    "foot": "Пешеход",
    "bike": "Велосипед",
    "trans": "ОТ",
    "Общественный транспорт": "ОТ",
    "Автомобиль": "Автомобиль",
    "Пешеход": "Пешеход",
    "Велосипед": "Велосипед",
    "ОТ": "ОТ",
}


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _coords(value: Any, *, lat: Any = None, lon: Any = None) -> list[float]:
    raw = value if isinstance(value, list) else [lat, lon]
    if len(raw) != 2:
        raise ScenarioImportError("Координаты должны содержать широту и долготу.")
    try:
        result = [float(raw[0]), float(raw[1])]
    except (TypeError, ValueError) as exc:
        raise ScenarioImportError("Координаты должны быть числами.") from exc
    if not (-90 <= result[0] <= 90 and -180 <= result[1] <= 180):
        raise ScenarioImportError("Координаты находятся вне допустимого диапазона.")
    return result


def _timestamp(value: Any, planning_date: str, field: str) -> str:
    text = _text(value)
    if re.fullmatch(r"\d{2}:\d{2}", text):
        text = f"{planning_date}T{text}:00+03:00"
    try:
        result = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ScenarioImportError(f"Поле {field} должно быть временем HH:MM или ISO datetime.") from exc
    if result.tzinfo is None:
        raise ScenarioImportError(f"Поле {field} должно содержать часовой пояс.")
    return result.isoformat()


def _equipment(value: Any) -> dict[str, int]:
    if value in (None, "", [], {}):
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = [item.strip() for item in value.split("|") if item.strip()]
    if isinstance(value, list):
        result: dict[str, int] = {}
        for item in value:
            result[_text(item)] = result.get(_text(item), 0) + 1
        return result
    if isinstance(value, dict):
        try:
            return {str(key): int(amount) for key, amount in value.items() if int(amount) > 0}
        except (TypeError, ValueError) as exc:
            raise ScenarioImportError("Количество оборудования должно быть целым числом.") from exc
    raise ScenarioImportError("Оборудование должно быть списком или объектом.")


def _vehicle(value: Any, *, required: bool) -> str | None:
    text = _text(value)
    if not text and not required:
        return None
    try:
        return VEHICLE_ALIASES[text]
    except KeyError as exc:
        raise ScenarioImportError(f"Неизвестный транспорт: {text or 'не указан'}.") from exc


def _job(raw: dict[str, Any], scenario_id: str, planning_date: str, index: int) -> dict[str, Any]:
    original_id = _text(raw.get("id"))
    if not original_id:
        raise ScenarioImportError(f"Заявка в строке {index}: не указан id.")
    skill = _text(raw.get("skill") or raw.get("required_skill"))
    if skill not in SKILLS:
        raise ScenarioImportError(f"Заявка {original_id}: неизвестный навык {skill or 'не указан'}.")
    start = _timestamp(raw.get("window_start"), planning_date, "window_start")
    end = _timestamp(raw.get("window_end"), planning_date, "window_end")
    if datetime.fromisoformat(start) >= datetime.fromisoformat(end):
        raise ScenarioImportError(f"Заявка {original_id}: начало окна должно быть раньше конца.")
    try:
        duration = int(raw.get("duration_min"))
    except (TypeError, ValueError) as exc:
        raise ScenarioImportError(f"Заявка {original_id}: неверная длительность.") from exc
    if duration <= 0:
        raise ScenarioImportError(f"Заявка {original_id}: длительность должна быть больше нуля.")
    address = _text(raw.get("address"))
    coords = _coords(raw.get("coords"), lat=raw.get("lat"), lon=raw.get("lon"))
    if not address:
        raise ScenarioImportError(f"Заявка {original_id}: не указан адрес.")
    return {
        "id": f"{scenario_id}:{original_id}",
        "original_id": original_id,
        "source": "user_import",
        "input_order": index - 1,
        "type": _text(raw.get("type") or raw.get("title") or "Выездная работа"),
        "subtype": _text(raw.get("subtype")) or None,
        "skill": skill,
        "priority": _text(raw.get("priority")) or "Обычная",
        "address": address,
        "coords": coords,
        "window_start": start,
        "window_end": end,
        "duration_min": duration,
        "required_vehicle": _vehicle(raw.get("required_vehicle"), required=False),
        "required_equipment": _equipment(raw.get("required_equipment")),
        "status": _text(raw.get("status")) or "planned",
        "zone": _text(raw.get("zone")) or None,
        "provenance": {"kind": "user_import", "validated": True},
    }


def _engineer(
    raw: dict[str, Any], scenario_id: str, planning_date: str, index: int
) -> dict[str, Any]:
    original_id = _text(raw.get("id"))
    if not original_id:
        raise ScenarioImportError(f"Бригада в строке {index}: не указан id.")
    raw_skills = raw.get("skills")
    if isinstance(raw_skills, str):
        raw_skills = [item.strip() for item in re.split(r"[|;,]", raw_skills) if item.strip()]
    skills = [str(item) for item in (raw_skills or [])]
    if not skills or any(item not in SKILLS for item in skills):
        raise ScenarioImportError(f"Бригада {original_id}: укажите навыки local/connect/emergency.")
    shift_start = _timestamp(raw.get("shift_start"), planning_date, "shift_start")
    shift_end = _timestamp(raw.get("shift_end"), planning_date, "shift_end")
    if datetime.fromisoformat(shift_start) >= datetime.fromisoformat(shift_end):
        raise ScenarioImportError(f"Бригада {original_id}: начало смены должно быть раньше конца.")
    return {
        "id": f"{scenario_id}:{original_id}",
        "original_id": original_id,
        "name": _text(raw.get("name")) or f"Бригада {original_id}",
        "scenario_id": scenario_id,
        "skills": skills,
        "vehicle": _vehicle(raw.get("vehicle"), required=True),
        "shift_start": shift_start,
        "shift_end": shift_end,
        "start_address": _text(raw.get("start_address")),
        "start_point": _coords(
            raw.get("start_point"), lat=raw.get("start_lat"), lon=raw.get("start_lon")
        ),
        "status": _text(raw.get("status")) or "Доступен",
        "equipment": _equipment(raw.get("equipment")),
        "equipment_source": "user_import",
        "zone": _text(raw.get("zone")) or None,
        "provenance": {"kind": "user_import", "validated": True},
    }


def _parse_json(text: str) -> tuple[str, str, list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ScenarioImportError(f"JSON повреждён: строка {exc.lineno}, столбец {exc.colno}.") from exc
    if not isinstance(payload, dict):
        raise ScenarioImportError("JSON должен быть объектом с region, planning_date, jobs и engineers.")
    region = _text(payload.get("region"))
    planning_date = _text(payload.get("planning_date"))
    jobs, engineers = payload.get("jobs"), payload.get("engineers")
    if not isinstance(jobs, list) or not isinstance(engineers, list):
        raise ScenarioImportError("JSON должен содержать массивы jobs и engineers.")
    return region, planning_date, jobs, engineers


def _parse_csv(text: str) -> tuple[str, str, list[dict[str, Any]], list[dict[str, Any]]]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"
    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    if not rows:
        raise ScenarioImportError("CSV не содержит строк данных.")
    regions = {_text(row.get("region")) for row in rows}
    dates = {_text(row.get("planning_date")) for row in rows}
    if len(regions) != 1 or len(dates) != 1:
        raise ScenarioImportError("Один CSV должен содержать ровно один region и planning_date.")
    jobs = [row for row in rows if _text(row.get("record_type")).lower() == "job"]
    engineers = [row for row in rows if _text(row.get("record_type")).lower() == "engineer"]
    unknown = [row for row in rows if _text(row.get("record_type")).lower() not in {"job", "engineer"}]
    if unknown:
        raise ScenarioImportError("Колонка record_type принимает только job или engineer.")
    return regions.pop(), dates.pop(), jobs, engineers


def parse_scenario_file(
    content: bytes, *, filename: str, content_type: str | None = None
) -> tuple[str, str, list[dict[str, Any]], list[dict[str, Any]]]:
    if not content:
        raise ScenarioImportError("Файл пуст.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ScenarioImportError("Файл должен быть в UTF-8.") from exc
    is_json = filename.lower().endswith(".json") or "json" in (content_type or "")
    region, planning_date, raw_jobs, raw_engineers = (
        _parse_json(text) if is_json else _parse_csv(text)
    )
    if not region:
        raise ScenarioImportError("Не указан регион. Один файл должен описывать один регион.")
    try:
        datetime.fromisoformat(planning_date)
    except ValueError as exc:
        raise ScenarioImportError("planning_date должен иметь формат YYYY-MM-DD.") from exc
    if not raw_jobs or not raw_engineers:
        raise ScenarioImportError("В сценарии нужна минимум одна заявка и одна бригада.")
    slug = re.sub(r"[^a-z0-9]+", "-", region.lower()).strip("-") or "region"
    scenario_id = f"import-{slug[:24]}-{uuid.uuid4().hex[:8]}"
    jobs = [_job(raw, scenario_id, planning_date, index) for index, raw in enumerate(raw_jobs, 1)]
    engineers = [
        _engineer(raw, scenario_id, planning_date, index)
        for index, raw in enumerate(raw_engineers, 1)
    ]
    ids = [job["original_id"] for job in jobs]
    if len(ids) != len(set(ids)):
        raise ScenarioImportError("ID заявок должны быть уникальны.")
    engineer_ids = [item["original_id"] for item in engineers]
    if len(engineer_ids) != len(set(engineer_ids)):
        raise ScenarioImportError("ID бригад должны быть уникальны.")
    return scenario_id, region, jobs, engineers
