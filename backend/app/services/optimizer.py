# НАЗНАЧЕНИЕ: основной алгоритм распределения и маршрутизации.
#
# ВХОД:
#   List[Job], List[Engineer], текущие маршруты (если replan).
#
# ВЫХОД:
#   Plan: маршруты, метрики, объяснения по каждой заявке.
#
# СВЯЗИ:
#   - использует constraints.py, distance.py
#   - сравнивает результат с baseline.py
#   - вызывает metrics.compute и explainer.explain
#   - вызывается из api/routes_optimize.py
#
# АЛГОРИТМ (config.optimizer_engine):
#   greedy  — жадная эвристика с сортировкой по окну и приоритету
#   ortools — VRPTW: time windows, skills, vehicle types, drop-переменные
#
# ЦЕЛЕВАЯ ФУНКЦИЯ:
#   1. Минимизировать количество задействованных инженеров.
#   2. Затем — суммарный пробег.
#
# ДОПУЩЕНИЯ: не гарантируем математический оптимум (см. docs/algorithm.md).

from typing import List
from ..schemas.job import Job
from ..schemas.engineer import Engineer
from ..schemas.plan import Plan

def optimize(jobs: List[Job], engineers: List[Engineer]) -> Plan:
    ...
