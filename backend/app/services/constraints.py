# НАЗНАЧЕНИЕ: проверка трёх групп обязательных ограничений.
#
# ВХОД:
#   Job, Engineer, текущее время прибытия (для цепочки).
#
# ВЫХОД:
#   bool + причина отказа (если не подходит).
#
# СВЯЗИ:
#   - вызывается из baseline.py, optimizer.py, replanner.py
#   - использует distance.py для travel time
#
# ТРИ ГРУППЫ:
#   1. Квалификация: job.skill in engineer.skills.
#   2. Время: arrive ∈ job.window; весь маршрут ∈ engineer.shift.
#   3. Ресурс: job.required_vehicle is None or == engineer.vehicle.
#
# ВАЖНО: возвращать не просто False, а код причины:
#   NO_SKILL | NO_VEHICLE | OUT_OF_WINDOW | OUT_OF_SHIFT | NO_ENGINEER
#   — чтобы explainer.py мог объяснить неназначенные заявки.

from typing import Optional, Tuple
from ..schemas.job import Job
from ..schemas.engineer import Engineer

def can_assign(job: Job, engineer: Engineer, arrive_time) -> Tuple[bool, Optional[str]]:
    ...
