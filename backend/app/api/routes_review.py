# НАЗНАЧЕНИЕ: HTTP-эндпоинт решения диспетчера по заявке из needs_review.
#
# ВХОД:  POST /resolve_review {scenario?, plan, job_id, action}
#        action: keep|reassign|cancel
# ВЫХОД: Plan с обновлённым needs_review.
# СВЯЗИ: services/replanner.apply_review_decision.

from fastapi import APIRouter, HTTPException

from ..schemas.api import ResolveReviewRequest
from ..services import replanner


router = APIRouter()


@router.post("/resolve_review")
def resolve_review_endpoint(payload: ResolveReviewRequest):
    try:
        new_plan = replanner.apply_review_decision(
            payload.plan, payload.job_id, payload.action,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return new_plan
