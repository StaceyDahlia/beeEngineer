# НАЗНАЧЕНИЕ: GeoJSON для карты.
#
# ВАЖНО: GET /map/geojson читает данные сценария и строит СВЕЖИЙ план
# (оптимизатор), чтобы отдать маршруты. Это не «текущий план фронта»,
# а «что бы оптимизатор нарисовал сейчас». Если фронту нужно рисовать
# свой (изменённый через /replan) Plan — понадобится POST-вариант,
# которого пока нет в контракте.
#
# ВХОД:  GET /map/geojson?scenario=east
# ВЫХОД: FeatureCollection (Point/LineString), координаты [lon, lat].
# СВЯЗИ: services/loader, services/optimizer, services/geojson.

from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from ..services import loader, optimizer, geojson


router = APIRouter()


@router.get("/map/geojson")
def get_geojson(scenario: Optional[str] = Query(default=None)):
    try:
        jobs = loader.load_jobs(scenario)
        engineers = loader.load_engineers(scenario)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    plan = optimizer.optimize(jobs, engineers, compare_baseline=False)
    return geojson.build_feature_collection(plan)
