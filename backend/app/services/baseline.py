# НАЗНАЧЕНИЕ: базовый жадный алгоритм из ТЗ (для сравнения метрик).
#
# ВХОД:
#   List[Job] (в порядке поступления), List[Engineer] (в порядке из данных).
#
# ВЫХОД:
#   Plan с метриками (baseline_metrics).
#
# СВЯЗИ:
#   - использует constraints.can_assign
#   - использует metrics.compute
#   - вызывается из optimizer.py (для сравнения) и compare_baseline.py
#
# ЛОГИКА (строго по ТЗ п.2.3):
#   1. Заявки обрабатываются по порядку поступления.
#   2. Каждая назначается первому по порядку доступному инженеру,
#      который удовлетворяет обязательным ограничениям.
#   3. Порядок посещения = порядок назначения.
#   4. Глобальная оптимизация НЕ выполняется.

from typing import List
from ..schemas.job import Job
from ..schemas.engineer import Engineer
from ..schemas.plan import Plan

def build_baseline_plan(jobs: List[Job], engineers: List[Engineer]) -> Plan:
    ...
