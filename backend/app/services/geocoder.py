# НАЗНАЧЕНИЕ: адрес → координаты с кэшем.
#
# ВХОД:
#   Строка адреса (например, «Город Москва, ул.Окская, д. 32»).
#
# ВЫХОД:
#   [lat, lon] или None.
#
# СВЯЗИ:
#   - вызывается из normalizer.py
#   - кэш: data/processed/geocode_cache.json (в .gitignore)
#   - внешний сервис: Nominatim (по умолчанию) или другой
#
# ЧТО ДЕЛАЕТ:
#   1. Проверяет кэш по адресу.
#   2. Если нет — GET https://nominatim.openstreetmap.org/search
#      с User-Agent из config, rate limit 1 req/sec.
#   3. При неудаче — fallback: центроид района из справочника.
#   4. Сохраняет в кэш.

import time, json, requests
from pathlib import Path
from typing import Optional, List
from ..config import settings

def geocode(address: str) -> Optional[List[float]]:
    ...
