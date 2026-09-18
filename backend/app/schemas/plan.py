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
    arrival: str          # HH:MM
    departure: str        # HH:MM
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

    assigned_count: int = 0
    cancelled_count: int = 0
    total_jobs: int = 0

class Plan(BaseModel):
    jobs: List[Job]
    engineers: List[Engineer]
    routes: List[EngineerRoute]
    metrics: PlanMetrics
    baseline_metrics: Optional[PlanMetrics] = None
    changed_job_ids: Optional[List[str]] = None   # для подсветки после replan
