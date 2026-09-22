# НАЗНАЧЕНИЕ: отдача данных сценария фронту.
#
# ВХОД:  GET /data/jobs, /data/engineers, /data/events, /data/locations
#        с опциональным query-параметром ?scenario=east.
# ВЫХОД: JSON-обёртки {scenario, count, jobs|engineers|events|locations}.
# СВЯЗИ: services/loader, schemas/*.

from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from ..services import loader


router = APIRouter()


def _scenario_label(scenario: Optional[str]) -> Optional[str]:
    return scenario or loader.active_scenario()


@router.get("/data/jobs")
def get_jobs(scenario: Optional[str] = Query(default=None)):
    try:
        jobs = loader.load_jobs(scenario)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "scenario": _scenario_label(scenario),
        "count": len(jobs),
        "jobs": jobs,
    }


@router.get("/data/engineers")
def get_engineers(scenario: Optional[str] = Query(default=None)):
    try:
        engineers = loader.load_engineers(scenario)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "scenario": _scenario_label(scenario),
        "count": len(engineers),
        "engineers": engineers,
    }


@router.get("/data/events")
def get_events(scenario: Optional[str] = Query(default=None)):
    events = loader.load_events(scenario)
    return {
        "scenario": _scenario_label(scenario),
        "count": len(events),
        "events": events,
    }


@router.get("/data/locations")
def get_locations(scenario: Optional[str] = Query(default=None)):
    locations = loader.load_locations(scenario)
    return {
        "scenario": _scenario_label(scenario),
        "count": len(locations),
        "locations": locations,
    }
