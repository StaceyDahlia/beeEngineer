# НАЗНАЧЕНИЕ: перепланирование при событии.
#
# ВХОД:
#   Текущий Plan + Event (urgent_job | cancel_job | engineer_unavailable).
#
# ВЫХОД:
#   Новый Plan + список changed_job_ids (что изменилось).
#
# СВЯЗИ:
#   - вызывается из api/routes_replan.py
#   - использует optimizer.optimize (для полного пересчёта)
#     или локальную вставку (для скорости)
#   - использует explainer.py для объяснения изменений
#
# ЛОГИКА:
#   1. urgent_job: добавить заявку с приоритетом «Срочная»,
#      попробовать вставить в существующий маршрут без сдвига окон.
#   2. cancel_job: удалить заявку, освободить слот.
#   3. engineer_unavailable: пометить инженера недоступным,
#      перераспределить его заявки.
#   4. Вернуть diff: какие назначения/порядок/маршруты изменились.

from typing import List, Tuple
from ..schemas.plan import Plan
from ..schemas.event import Event

def replan(plan: Plan, event: Event) -> Tuple[Plan, List[str]]:
    ...
