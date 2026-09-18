# НАЗНАЧЕНИЕ: модель итогового плана и метрик.
#
# ВХОД:  optimizer.py / replanner.py.
# ВЫХОД: JSON-ответ API → frontend.
# СВЯЗИ: metrics.py, explainer.py.

from typing import List, Dict, Optional
from pydantic import BaseModel
from .job import Job
from .engineer import Engineer


class RouteStop(BaseModel):
    job_id: str
    arrival: str
    departure: str
    travel_min_from_prev: int = 0
    distance_km_from_prev: float = 0.0


class EngineerRoute(BaseModel):
    engineer_id: str
    stops: List[RouteStop]
    distance_km: float
    jobs_count: int


class PlanMetrics(BaseModel):
    engineers_used: int
    total_distance_km: float
    unassigned_count: int
    per_engineer_distance_km: Dict[str, float]
    # Доп. поля для UI (см. metrics.py).
    assigned_count: int = 0
    cancelled_count: int = 0
    total_jobs: int = 0


class PlanWarning(BaseModel):
    """
    Структурированное предупреждение для диспетчера.
    Человекочитаемый текст формирует explainer.translate_warning().
    """
    code: str                       # "IN_PROGRESS_ENGINEER_UNAVAILABLE" | ...
    job_id: Optional[str] = None
    engineer_id: Optional[str] = None
    message: str = ""               # технический короткий текст


class Plan(BaseModel):
    jobs: List[Job]
    engineers: List[Engineer]
    routes: List[EngineerRoute]
    metrics: PlanMetrics
    baseline_metrics: Optional[PlanMetrics] = None
    changed_job_ids: Optional[List[str]] = None
    diff_summary: Optional[Dict[str, List[str]]] = None
    needs_review: Optional[List[str]] = None
    warnings: Optional[List[PlanWarning]] = None
