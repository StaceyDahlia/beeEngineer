# НАЗНАЧЕНИЕ: Pydantic-модель инженера.
#
# ВХОД:  data/processed/engineers.json.
# ВЫХОД: constraints.py, optimizer.py, metrics.py.
# СВЯЗИ: reference/vehicles.json, schemas/plan.py.

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel

class Engineer(BaseModel):
    id: str
    name: str
    skills: List[str]
    vehicle: str                          # Автомобиль | Пешеход | Велосипед | ОТ
    shift_start: datetime
    shift_end: datetime
    start_point: List[float]              # [lat, lon]
    start_address: Optional[str] = None
    status: str = "Доступен"              # Доступен | Заболел | На перерыве
