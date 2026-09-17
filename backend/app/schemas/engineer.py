# НАЗНАЧЕНИЕ: Pydantic-модель инженера.
#
# ВХОД:  data/processed/engineers.json.
# ВЫХОД: constraints.py, optimizer.py, metrics.py.
# СВЯЗИ: reference/vehicles.json, schemas/plan.py.

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class Engineer(BaseModel):
    id: str
    name: str
    scenario_id: Optional[str] = None
    skills: List[str]
    vehicle: str                          # Автомобиль | Пешеход | Велосипед | ОТ
    shift_start: datetime
    shift_end: datetime
    start_point: List[float]              # [lat, lon]
    start_address: Optional[str] = None
    start_location_id: Optional[str] = None
    shift_profile: Optional[str] = None
    inventory_start: Dict[str, int] = Field(default_factory=dict)
    status: str = "Доступен"              # Доступен | Заболел | На перерыве
    provenance: Dict[str, Any] = Field(default_factory=dict)
