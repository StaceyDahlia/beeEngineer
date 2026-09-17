"""Select one prepared scenario as the active backend dataset."""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=("east", "southeast", "southcenter"))
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    processed = args.project_root / "data" / "processed"
    source = processed / "scenarios" / args.scenario
    if not source.exists():
        raise SystemExit(f"Scenario folder not found: {source}")
    for filename in ("jobs.json", "engineers.json", "events.json", "locations.json", "inventory.json"):
        shutil.copy2(source / filename, processed / filename)
    marker = {
        "scenario_id": args.scenario,
        "selected_at": datetime.now(timezone.utc).isoformat(),
        "source": f"data/processed/scenarios/{args.scenario}",
    }
    (processed / "active_scenario.json").write_text(
        json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Active scenario: {args.scenario}")


if __name__ == "__main__":
    main()
