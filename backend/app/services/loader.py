# НАЗНАЧЕНИЕ: читает CSV/JSON и превращает в Pydantic-модели.
#
# ВХОД:
#   data/*.csv  (напрямую, если prepare_data.py не запускался)
#   data/processed/jobs.json, engineers.json, events.json
#   data/reference/*.json
#
# ВЫХОД:
#   List[Job], List[Engineer], List[Event]
#
# СВЯЗИ:
#   - вызывается из app/main.py (startup) и scripts/run_demo.py
#   - использует app/schemas/*.py
#   - передаёт данные в constraints.py, optimizer.py
#
# ЧТО ДЕЛАЕТ:
#   1. read_csv (sep=';', encoding='utf-8-sig') или read_json.
#   2. Пропускает пустые строки и строку «Адрес Офиса».
#   3. Маппит «Тип заявки BK» → skill через reference/skills.json.
#   4. Маппит «Статус BK» → status (Отменена/Выполнена → cancelled/done).
#   5. Возвращает модели.

import json
from pathlib import Path
from typing import List
from ..schemas.job import Job
from ..schemas.engineer import Engineer
from ..schemas.event import Event
from ..config import settings

def load_jobs() -> List[Job]:
    path = Path(settings.data_processed_dir) / "jobs.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Job(**item) for item in data["jobs"]]

def load_engineers() -> List[Engineer]:
    path = Path(settings.data_processed_dir) / "engineers.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Engineer(**item) for item in data["engineers"]]

def load_events() -> List[Event]:
    path = Path(settings.data_processed_dir) / "events.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Event(**item) for item in data["events"]]
