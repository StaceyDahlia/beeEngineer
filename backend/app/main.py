from __future__ import annotations

from pathlib import Path
import time
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .services.loader import (
    SCENARIO_LABELS,
    SUPPORTED_SCENARIOS,
    ScenarioNotFoundError,
    list_scenarios,
    load_jobs,
    load_scenario,
    register_imported_scenario,
)
from .services.ortools_vrptw import OrToolsSolveError, OrToolsUnavailableError
from .services.plan_store import PlanStoreError, plan_store
from .services.planner import build_plan_with_comparison
from .services.replanner import ReplanValidationError, build_replanned_plan
from .services.scenario_import import ScenarioImportError, parse_scenario_file


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = PROJECT_ROOT / "frontend"


class OptimizeRequest(BaseModel):
    scenario: str = "east"
    engine: Literal["baseline_greedy", "ortools_vrptw"] = "baseline_greedy"
    solve_time_limit_ms: int = 5_000


class ReplanEventRequest(BaseModel):
    event_id: str
    type: Literal["cancel_job", "engineer_unavailable", "emergency_job", "new_normal_job"]
    event_time: datetime
    base_version: int
    job_id: str | None = None
    engineer_id: str | None = None
    status: str | None = None
    title: str | None = None
    address: str | None = None
    coords: list[float] | None = None
    required_vehicle: str | None = None
    required_skill: Literal["local", "connect", "emergency"] | None = None
    required_equipment: dict[str, int] | None = None
    duration_min: int | None = None
    window_end: datetime | None = None
    solve_time_limit_ms: int = 5_000


class ApplyPreviewRequest(BaseModel):
    preview_id: str


class ReoptimizeRequest(BaseModel):
    base_version: int
    engine: Literal["baseline_greedy", "ortools_vrptw"] = "ortools_vrptw"
    solve_time_limit_ms: int = 5_000


app = FastAPI(title="Beeline Business Route Planner", version="0.5.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "beeline-integration"}


@app.get("/data/jobs")
def get_jobs(scenario: str = Query(default="east")) -> dict:
    try:
        jobs = load_jobs(scenario)
    except ScenarioNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"scenario": scenario, "count": len(jobs), "jobs": jobs}


@app.get("/data/scenarios")
def get_scenarios() -> dict:
    return {"scenarios": list_scenarios()}


@app.post("/scenarios/import", status_code=201)
async def import_scenario(request: Request) -> dict:
    filename = request.headers.get("x-filename", "scenario.json")
    content_type = request.headers.get("content-type", "application/octet-stream")
    if not filename.lower().endswith((".json", ".csv")):
        raise HTTPException(status_code=422, detail="Поддерживаются только файлы CSV и JSON.")
    try:
        scenario_id, region, jobs, engineers = parse_scenario_file(
            await request.body(), filename=filename, content_type=content_type
        )
        return register_imported_scenario(
            scenario_id,
            region=region,
            jobs=jobs,
            engineers=engineers,
            filename=filename,
        )
    except ScenarioImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/optimize")
def optimize(request: OptimizeRequest | None = None) -> dict:
    payload = request or OptimizeRequest()
    try:
        jobs, engineers = load_scenario(payload.scenario)
    except ScenarioNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    started = time.perf_counter()
    try:
        return build_plan_with_comparison(
            jobs,
            engineers,
            scenario=payload.scenario,
            engine=payload.engine,
            solve_time_limit_ms=max(100, min(payload.solve_time_limit_ms, 30_000)),
        )
    except OrToolsUnavailableError as exc:
        return JSONResponse(
            status_code=503,
            content={
                "engine": "ortools_vrptw",
                "solver_status": "unavailable",
                "solve_time_ms": round((time.perf_counter() - started) * 1000),
                "warnings": [str(exc)],
                "error": {"message": str(exc)},
            },
        )
    except OrToolsSolveError as exc:
        return JSONResponse(
            status_code=503,
            content={
                "engine": "ortools_vrptw",
                "solver_status": exc.status,
                "solve_time_ms": exc.solve_time_ms,
                "warnings": exc.warnings,
                "error": {"message": str(exc)},
            },
        )
    except Exception as exc:
        if payload.engine != "ortools_vrptw":
            raise
        message = f"OR-Tools завершился с ошибкой: {exc}"
        return JSONResponse(
            status_code=500,
            content={
                "engine": "ortools_vrptw",
                "solver_status": "error",
                "solve_time_ms": round((time.perf_counter() - started) * 1000),
                "warnings": [message],
                "error": {"message": message},
            },
        )


@app.post("/plans")
def create_plan(request: OptimizeRequest | None = None) -> dict:
    payload = request or OptimizeRequest()
    try:
        jobs, engineers = load_scenario(payload.scenario)
        plan = build_plan_with_comparison(
            jobs,
            engineers,
            scenario=payload.scenario,
            engine=payload.engine,
            solve_time_limit_ms=max(100, min(payload.solve_time_limit_ms, 30_000)),
        )
        return plan_store.create_plan(plan)
    except ScenarioNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OrToolsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OrToolsSolveError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "engine": "ortools_vrptw",
                "solver_status": exc.status,
                "solve_time_ms": exc.solve_time_ms,
                "warnings": exc.warnings,
            },
        ) from exc


@app.get("/plans/{plan_id}")
def get_plan(plan_id: str) -> dict:
    try:
        return plan_store.get_plan(plan_id)
    except PlanStoreError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@app.post("/plans/{plan_id}/reoptimize")
def reoptimize_plan(plan_id: str, request: ReoptimizeRequest) -> dict:
    """Rebuild the current plan data, including jobs added by applied events."""
    try:
        plan_store.ensure_current(plan_id, request.base_version)
        parent = plan_store.get_plan(plan_id)
        jobs = [
            job for job in parent["jobs"]
            if str(job.get("status") or "").lower() not in {"completed", "done", "cancelled"}
        ]
        plan = build_plan_with_comparison(
            jobs,
            parent["engineers"],
            scenario=str(parent["scenario_id"]),
            engine=request.engine,
            solve_time_limit_ms=max(100, min(request.solve_time_limit_ms, 30_000)),
        )
        return plan_store.save_revision(
            base_plan_id=plan_id, base_version=request.base_version, plan=plan
        )
    except PlanStoreError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except OrToolsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OrToolsSolveError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/plans/{plan_id}/history")
def get_plan_history(plan_id: str) -> dict:
    try:
        plans = plan_store.history(plan_id)
        return {
            "plan_id": plan_id,
            "count": len(plans),
            "plans": plans,
            "storage": "in_memory",
            "retention_message": "История хранится до перезапуска сервиса.",
        }
    except PlanStoreError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@app.post("/plans/{plan_id}/events/preview")
def preview_event(plan_id: str, request: ReplanEventRequest) -> dict:
    try:
        plan_store.ensure_current(plan_id, request.base_version)
        parent = plan_store.get_plan(plan_id)
        event = request.model_dump(mode="json", exclude={"base_version", "solve_time_limit_ms"})
        candidate, diff = build_replanned_plan(
            parent,
            event,
            solve_time_limit_ms=max(100, min(request.solve_time_limit_ms, 30_000)),
        )
        return plan_store.save_preview(
            base_plan_id=plan_id,
            base_version=request.base_version,
            event=event,
            plan=candidate,
            diff=diff,
        )
    except PlanStoreError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ReplanValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_EVENT", "message": str(exc)},
        ) from exc
    except OrToolsUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "ORTOOLS_UNAVAILABLE", "message": str(exc)},
        ) from exc
    except OrToolsSolveError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "ORTOOLS_SOLVE_ERROR",
                "message": str(exc),
                "solver_status": exc.status,
                "solve_time_ms": exc.solve_time_ms,
                "warnings": exc.warnings,
            },
        ) from exc


@app.post("/plans/{plan_id}/events/apply")
def apply_event(plan_id: str, request: ApplyPreviewRequest) -> dict:
    try:
        preview = plan_store.get_preview(request.preview_id)
        if preview["base_plan_id"] != plan_id:
            raise ReplanValidationError("Preview создан для другого плана.")
        return plan_store.apply_preview(request.preview_id)
    except PlanStoreError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ReplanValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_PREVIEW", "message": str(exc)},
        ) from exc


@app.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
