# НАЗНАЧЕНИЕ: HTTP-эндпоинт перепланирования.
#
# ВХОД (POST /api/v1/replan):
#   { "plan": {...}, "event": { "type": "...", "time": "...", "payload": {...} } }
#
# ВЫХОД:
#   { plan, changed_job_ids, explanation }
#
# СВЯЗИ:
#   - main.py
#   - services.replanner, services.explainer
#   - frontend: кнопки «Срочная заявка», «Отменить», «Инженер недоступен»

from fastapi import APIRouter
from ..schemas.event import Event
from ..services import replanner

router = APIRouter()

@router.post("/replan")
def replan_endpoint(payload: dict):
    plan = payload["plan"]
    event = Event(**payload["event"])
    new_plan, changed = replanner.replan(plan, event)
    return {"plan": new_plan, "changed_job_ids": changed}
