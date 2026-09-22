# НАЗНАЧЕНИЕ: Pydantic-модель заявки. Единый контракт между loader, optimizer, API.
#
# ВХОД:  data/processed/jobs.json, CSV через loader.
# ВЫХОД: используется в constraints.py, optimizer.py, metrics.py, API.
# СВЯЗИ: schemas/engineer.py (через plan.py), reference/skills.json.

from datetime import datetime
from typing import Any, Dict, Optional, List
from pydantic import BaseModel, Field


class Job(BaseModel):
    id: str
    original_id: Optional[str] = None
    source: Optional[str] = None
    source_file: Optional[str] = None
    source_row: Optional[int] = None

    input_order: int = 0                    # из baseline.py, optimizer.py

    type: str                               # Подключение / Дозаказ / Локальная / Глобальная
    subtype: Optional[str] = None
    skill: str
    priority: str = "Обычная"               # Обычная | Срочная

    district: Optional[str] = None
    district_raw: Optional[str] = None

    address: str
    location_id: Optional[str] = None
    coords: Optional[List[float]] = None    # [lat, lon]

    window_start: datetime
    window_end: datetime
    duration_min: int = 60
    norm_id: Optional[str] = None

    required_vehicle: Optional[str] = None
    required_equipment: Dict[str, int] = Field(default_factory=dict)

    connection_type: Optional[str] = None
    gigabit: bool = False

    status: str = "planned"                 # planned|assigned|unassigned|in_progress|done|cancelled
    assigned_engineer_id: Optional[str] = None
    arrive_planned: Optional[str] = None    # "HH:MM"
    depart_planned: Optional[str] = None    # "HH:MM"
    explanation: Optional[str] = None
    provenance: Dict[str, Any] = Field(default_factory=dict)
