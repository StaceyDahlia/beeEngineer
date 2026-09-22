# НАЗНАЧЕНИЕ: HTTP-эндпоинт полного планирования.
#
# ВХОД:  POST /optimize {scenario?, engine?}
# ВЫХОД: Plan (со заполненным baseline_metrics).
# СВЯЗИ: services/loader, services/optimizer.

from typing import Optional
from fastapi import APIRouter, HTTPException

from ..schemas.api import OptimizeRequest
from ..services import loader, optimizer


router = APIRouter()


@router.post("/optimize")
def optimize_endpoint(payload: Optional[OptimizeRequest] = None):
    req = payload or OptimizeRequest()

    try:
        jobs = loader.load_jobs(req.scenario)
        engineers = loader.load_engineers(req.scenario)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # engine="ortools" — заглушка: пока маршрутизация одна (greedy).
    # Поле валидируется Literal-ом в OptimizeRequest, здесь просто
    # не переключаем движок.
    plan = optimizer.optimize(jobs, engineers, compare_baseline=True)
    return plan
