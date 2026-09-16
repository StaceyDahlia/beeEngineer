# НАЗНАЧЕНИЕ: человекочитаемые объяснения решений.
#
# ВХОД:
#   Job, Engineer, Plan, причина отказа (если unassigned).
#
# ВЫХОД:
#   Строка на русском для диспетчера:
#   - почему заявка назначена этому инженеру;
#   - какие ограничения учтены;
#   - почему выбран такой маршрут;
#   - почему заявка не назначена (если применимо).
#
# СВЯЗИ:
#   - вызывается из optimizer.py, replanner.py
#   - результат кладётся в Job.explanation
#   - frontend показывает в блоке «Объяснение»
#
# ШАБЛОНЫ:
#   assigned:  «Заявка #X назначена <Инженер>, так как …»
#   unassigned: «Заявка #X не назначена: <причина из constraints>»

from ..schemas.job import Job
from ..schemas.engineer import Engineer

def explain_assignment(job: Job, engineer: Engineer) -> str: ...
def explain_unassigned(job: Job, reason_code: str) -> str: ...
