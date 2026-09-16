# НАЗНАЧЕНИЕ: расчёт обязательных метрик.
#
# ВХОД:
#   Plan (маршруты, назначения).
#
# ВЫХОД:
#   PlanMetrics: engineers_used, total_distance_km,
#                per_engineer_distance_km, unassigned_count.
#
# СВЯЗИ:
#   - вызывается из optimizer.py, baseline.py
#   - используется в api/routes_optimize.py
#   - frontend показывает на верхних плашках
#
# ФОРМУЛЫ (docs/metrics.md):
#   engineers_used = len({job.assigned_engineer_id} - {None})
#   per_engineer_distance_km = сумма distance между stops
#   total_distance_km = сумма по инженерам

from typing import List
from ..schemas.plan import Plan, PlanMetrics

def compute(plan: Plan) -> PlanMetrics: ...
