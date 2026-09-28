from __future__ import annotations

import time
from typing import Any, Literal

from .baseline_greedy import build_baseline_plan
from .ortools_vrptw import build_ortools_plan
from .travel_matrix import StaticTravelMatrix


PlannerEngine = Literal["baseline_greedy", "ortools_vrptw"]
RULES_VERSION = "static-vrptw-priority-rules-v2"


def _summary(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "engine": plan["engine"],
        "solver_status": plan["solver_status"],
        "solver_status_detail": plan.get("solver_status_detail"),
        "solve_time_ms": plan.get("solve_time_ms", 0),
        "warnings": plan.get("warnings", []),
        "metrics": plan["metrics"],
        "travel_model": plan["travel_model"],
    }


def _delta(plan_metrics: dict[str, Any], baseline_metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "assigned_count": int(plan_metrics["assigned_count"])
        - int(baseline_metrics["assigned_count"]),
        "unassigned_count": int(plan_metrics["unassigned_count"])
        - int(baseline_metrics["unassigned_count"]),
        "used_engineers": int(plan_metrics["used_engineers"])
        - int(baseline_metrics["used_engineers"]),
        "total_distance_m": int(plan_metrics["total_distance_m"])
        - int(baseline_metrics["total_distance_m"]),
        "total_distance_km": round(
            float(plan_metrics["total_distance_km"])
            - float(baseline_metrics["total_distance_km"]),
            3,
        ),
    }


def build_plan_with_comparison(
    jobs: list[dict[str, Any]],
    engineers: list[dict[str, Any]],
    *,
    scenario: str,
    engine: PlannerEngine,
    solve_time_limit_ms: int = 5_000,
) -> dict[str, Any]:
    """Запускает выбранный движок; OR-Tools сравнивается с точным baseline."""
    shared_matrix = StaticTravelMatrix(jobs, engineers)

    baseline_started = time.perf_counter()
    baseline = build_baseline_plan(
        jobs,
        engineers,
        scenario=scenario,
        matrix=shared_matrix,
    )
    baseline["solve_time_ms"] = round((time.perf_counter() - baseline_started) * 1000)
    baseline["warnings"] = []
    baseline["solver_status_detail"] = "DETERMINISTIC_FIRST_FIT"

    if engine == "baseline_greedy":
        selected = baseline
        optimized_summary = None
    else:
        selected = build_ortools_plan(
            jobs,
            engineers,
            scenario=scenario,
            matrix=shared_matrix,
            solve_time_limit_ms=solve_time_limit_ms,
        )
        optimized_summary = _summary(selected)

    baseline_summary = _summary(baseline)
    selected_metrics = selected["metrics"]
    baseline_metrics = baseline["metrics"]
    selected["baseline_metrics"] = baseline_metrics
    selected["optimized_metrics"] = (
        selected_metrics if engine == "ortools_vrptw" else None
    )
    selected["comparison"] = {
        "scenario_id": scenario,
        "rules_version": RULES_VERSION,
        "same_input": True,
        "shared_matrix_id": shared_matrix.signature,
        "travel_model": shared_matrix.metadata,
        "baseline": baseline_summary,
        "optimized": optimized_summary,
        "plan": _summary(selected),
        "delta_plan_minus_baseline": _delta(selected_metrics, baseline_metrics),
    }
    return selected
