# НАЗНАЧЕНИЕ: HTTP-эндпоинт перепланирования.
#
# ВХОД:  POST /replan {scenario?, plan, event}
# ВЫХОД: Plan с changed_job_ids / diff_summary / needs_review / warnings.
# СВЯЗИ: services/replanner.

from fastapi import APIRouter, HTTPException

from ..schemas.api import ReplanRequest
from ..services import replanner


router = APIRouter()


@router.post("/replan")
def replan_endpoint(payload: ReplanRequest):
    try:
        new_plan = replanner.replan(payload.plan, payload.event)
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return new_plan
