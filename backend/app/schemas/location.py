# НАЗНАЧЕНИЕ: Pydantic-модель локации (адрес + координаты).
#
# ВХОД:  data/processed/locations.json.
# ВЫХОД: loader.load_locations, API для карты.
# СВЯЗИ: schemas/job.py (job.location_id ссылается сюда).

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class Location(BaseModel):
    id: str
    address: str
    coords: List[float]                       # [lat, lon]
    geocode_status: Optional[str] = None
    geocode: Dict[str, Any] = Field(default_factory=dict)
