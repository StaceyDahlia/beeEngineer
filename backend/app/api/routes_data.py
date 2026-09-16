# НАЗНАЧЕНИЕ: отдача справочников и исходных данных.
#
# ВХОД:
#   GET /api/v1/jobs
#   GET /api/v1/engineers
#   GET /api/v1/reference/{skills|vehicles|priorities}
#
# ВЫХОД:
#   JSON-списки.
#
# СВЯЗИ:
#   - main.py
#   - services.loader
#   - frontend: первичная загрузка списка заявок и инженеров

from fastapi import APIRouter
from ..services import loader

router = APIRouter()

@router.get("/jobs")
def get_jobs():
    return loader.load_jobs()

@router.get("/engineers")
def get_engineers():
    return loader.load_engineers()
