from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services.loader import load_scenario
from backend.app.services.plan_store import plan_store
from backend.app.services.planner import build_plan_with_comparison
from backend.app.services.replanner import build_replanned_plan


CONFIG_PATH = ROOT / "frontend" / "demo-emergency.json"


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _metrics_row(label: str, metrics: dict, solve_time_ms: int) -> str:
    return (
        f"{label:<18} | {metrics['assigned_count']:>3} | "
        f"{metrics['used_engineers']:>3} | {metrics['total_distance_km']:>9.3f} | "
        f"{solve_time_ms:>8}"
    )


def _print_comparison(plan: dict) -> None:
    comparison = plan["comparison"]
    print("Движок            | Наз | Инж |        Км | Время, мс")
    print("-------------------+-----+-----+-----------+----------")
    print(
        _metrics_row(
            "baseline_greedy",
            comparison["baseline"]["metrics"],
            comparison["baseline"]["solve_time_ms"],
        )
    )
    print(
        _metrics_row(
            "ortools_vrptw",
            comparison["optimized"]["metrics"],
            comparison["optimized"]["solve_time_ms"],
        )
    )


def _group_changes(diff: dict, emergency_id: str) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for item in diff.get("items", []):
        job_id = str(item["job_id"])
        if job_id == emergency_id:
            continue
        grouped.setdefault(job_id, []).append(str(item["category"]))
    return grouped


def run_demo(*, apply: bool = False) -> tuple[dict, dict, dict | None]:
    config = load_config()
    jobs, engineers = load_scenario(config["scenario"])
    parent = build_plan_with_comparison(
        jobs,
        engineers,
        scenario=config["scenario"],
        engine=config["base_engine"],
        solve_time_limit_ms=int(config["base_solve_time_limit_ms"]),
    )
    plan_store.clear()
    stored_parent = plan_store.create_plan(parent)
    event = copy.deepcopy(config["event"])
    event.pop("solve_time_limit_ms", None)
    candidate, diff = build_replanned_plan(
        stored_parent,
        event,
        solve_time_limit_ms=int(config["preview_solve_time_limit_ms"]),
    )
    preview = plan_store.save_preview(
        base_plan_id=stored_parent["plan_id"],
        base_version=stored_parent["version"],
        event=event,
        plan=candidate,
        diff=diff,
    )
    applied = plan_store.apply_preview(preview["preview_id"]) if apply else None
    return stored_parent, preview, applied


def main() -> None:
    parser = argparse.ArgumentParser(description="Воспроизвести east demo-emergency офлайн.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="После preview применить его и создать v2.",
    )
    args = parser.parse_args()
    parent, preview, applied = run_demo(apply=args.apply)
    _print_comparison(parent)

    event = preview["event"]
    emergency_id = f"emergency:{event['event_id']}"
    emergency = next(job for job in preview["plan"]["jobs"] if job["id"] == emergency_id)
    grouped = _group_changes(preview["diff"], emergency_id)
    print(
        f"\nDemo: {event['event_time']} · {event['address']} · "
        f"80 мин · skill={emergency['skill']}"
    )
    print(
        f"Preview {preview['preview_id']}: v{preview['base_version']} → "
        f"v{preview['proposed_version']}; авария={emergency['status']}; "
        f"изменено обычных заявок={len(grouped)}"
    )
    for job_id, categories in grouped.items():
        print(f"- {job_id}: {', '.join(categories)}")
    if applied:
        print(
            f"Применено: {applied['plan_id']} · v{applied['version']} · "
            f"parent={applied['parent_plan_id']}"
        )
    else:
        print("Preview не применён. Для создания v2 запустите с --apply.")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    main()
