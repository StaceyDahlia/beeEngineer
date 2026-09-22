# НАЗНАЧЕНИЕ: схемы тел запросов API (не путать со схемами домена).
#
# ВХОД:  тела POST /optimize, /replan, /resolve_review.
# ВЫХОД: используются в api/routes_*.py.
# СВЯЗИ: schemas/plan.py, schemas/event.py.

from typing import Literal, Optional
from pydantic import BaseModel

from .plan import Plan
from .event import Event


class OptimizeRequest(BaseModel):
    scenario: Optional[str] = None       # east|southeast|southcenter|None=активный
    engine: Literal["greedy", "ortools"] = "greedy"


class ReplanRequest(BaseModel):
    scenario: Optional[str] = None
    plan: Plan
    event: Event


class ResolveReviewRequest(BaseModel):
    scenario: Optional[str] = None
    plan: Plan
    job_id: str
    action: Literal["keep", "reassign", "cancel"]
