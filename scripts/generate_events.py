from __future__ import annotations

import copy
import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_ROOT = (SCRIPT_DIR / "data" if (SCRIPT_DIR / "data").exists()
             else SCRIPT_DIR.parent / "data" / "processed")
ROOT = DATA_ROOT / "scenarios"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    for folder in sorted(p for p in ROOT.iterdir() if p.is_dir()):
        jobs = load(folder / "jobs.json")["jobs"]
        engineers = load(folder / "engineers.json")["engineers"]
        planned = [j for j in jobs if j["status"] == "planned"]
        if len(planned) < 2 or not engineers:
            raise RuntimeError(f"Not enough records for events in {folder.name}")

        urgent = copy.deepcopy(planned[0])
        urgent["id"] = f"{folder.name}:urgent-demo-001"
        urgent["original_id"] = None
        urgent["source"] = "synthetic_event_demo"
        urgent["priority"] = "Срочная"
        urgent["status"] = "planned"
        urgent["assigned_engineer_id"] = None
        urgent["explanation"] = "Демонстрационная срочная заявка для сценария перепланирования"
        urgent.setdefault("provenance", {})["event"] = {
            "kind": "synthetic", "purpose": "replanning_demo", "version": "1.0"
        }

        events = [
            {"type": "urgent_job", "time": "2026-08-17T14:30:00+03:00", "payload": urgent},
            {"type": "cancel_job", "time": "2026-08-17T15:00:00+03:00",
             "payload": {"job_id": planned[1]["id"]}},
            {"type": "engineer_unavailable", "time": "2026-08-17T15:10:00+03:00",
             "payload": {"engineer_id": engineers[0]["id"]}},
        ]
        save(folder / "events.json", {
            "schema_version": "1.0",
            "scenario_id": folder.name,
            "kind": "synthetic_demo_events",
            "events": events,
        })
        print(folder.name, len(events))


if __name__ == "__main__":
    main()
