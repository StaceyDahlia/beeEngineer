# НАЗНАЧЕНИЕ: HTTP-эндпоинт полного планирования.
#
# ВХОД (POST /api/v1/optimize):
#   { "jobs": [...], "engineers": [...], "metrics": {...} }  (как в frontend2)
#   или пустое тело — тогда берём из data/processed/.
#
# ВЫХОД:
#   { jobs, engineers, routes, metrics, baseline_metrics }
#
# СВЯЗИ:
#   - main.py регистрирует роутер
#   - вызывает services.loader, services.optimizer, services.baseline
#   - frontend2.txt: fetchOptimizationData()

from fastapi import APIRouter
from ..services import loader, optimizer, baseline, metrics

router = APIRouter()

@router.post("/optimize")
def optimize_endpoint(payload: dict | None = None):
    jobs = loader.load_jobs()
    engineers = loader.load_engineers()
    plan = optimizer.optimize(jobs, engineers)
    plan.baseline_metrics = baseline.build_baseline_plan(jobs, engineers).metrics
    return plan
