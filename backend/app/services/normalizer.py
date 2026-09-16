# НАЗНАЧЕНИЕ: приводит сырые данные к единому виду (типы, справочники, координаты).
#
# ВХОД:
#   Строки из CSV (dict) или промежуточные dict.
#   data/reference/skills.json, vehicles.json, priorities.json.
#
# ВЫХОД:
#   Готовые dict для Job/Engineer.
#
# СВЯЗИ:
#   - вызывается из scripts/prepare_data.py и loader.py
#   - использует geocoder.py для адрес → coords
#
# ЧТО ДЕЛАЕТ:
#   1. Парсит «Начало»/«Окончание» из 'DD.MM.YYYY HH:MM' → datetime.
#   2. Маппит русские названия навыков на канонические из skills.json.
#   3. Определяет required_vehicle (в CSV почти нет — вернуть None).
#   4. Вызывает geocoder.geocode(address) → [lat, lon].
#   5. Возвращает нормализованный dict.

from datetime import datetime
from typing import Optional, Dict, Any

def parse_dt(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%d.%m.%Y %H:%M")

def normalize_job(row: Dict[str, Any]) -> Dict[str, Any]:
    # ... маппинг полей, навыков, координат
    ...
