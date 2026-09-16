# НАЗНАЧЕНИЕ: модель события перепланирования.
#
# ВХОД:  data/processed/events.json, POST /replan.
# ВЫХОД: replanner.py.
# СВЯЗИ: schemas/job.py (для urgent_job).

from datetime import datetime
from typing import Literal, Optional, Union
from pydantic import BaseModel
from .job import Job

class CancelJobPayload(BaseModel):
    job_id: str

class EngineerUnavailablePayload(BaseModel):
    engineer_id: str

class UrgentJobPayload(Job):
    pass

class Event(BaseModel):
    type: Literal["urgent_job", "cancel_job", "engineer_unavailable"]
    time: datetime
    payload: Union[UrgentJobPayload, CancelJobPayload, EngineerUnavailablePayload]
