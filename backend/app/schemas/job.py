# НАЗНАЧЕНИЕ: Pydantic-модель заявки. Единый контракт между loader, optimizer, API.
#
# ВХОД:  data/processed/jobs.json, CSV через loader.
# ВЫХОД: используется в constraints.py, optimizer.py, metrics.py, API.
# СВЯЗИ: schemas/engineer.py (через plan.py), reference/skills.json.

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field

class Job(BaseModel):
    id: str
    source: Optional[str] = None          # east_control / southeast_synthetic / ...
    type: str                             # Подключение / Дозаказ / Локальная / Глобальная
    skill: str                            # требуемый навык
    priority: str = "Обычная"             # Обычная | Срочная
    district: Optional[str] = None
    address: str
    coords: Optional[List[float]] = None  # [lat, lon]
    window_start: datetime
    window_end: datetime
    duration_min: int = 60
    required_vehicle: Optional[str] = None
    status: str = "planned"               # planned | assigned | unassigned | done | cancelled
    assigned_engineer_id: Optional[str] = None
    explanation: Optional[str] = None
