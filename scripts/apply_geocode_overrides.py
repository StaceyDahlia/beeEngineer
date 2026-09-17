from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" if (ROOT / "data").exists() else ROOT.parent / "data" / "processed"
OVERRIDES = (ROOT / "geocode_overrides.json" if (ROOT / "geocode_overrides.json").exists()
             else ROOT.parent / "data" / "reference" / "geocode_overrides.json")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    config = load(OVERRIDES)
    overrides = config["overrides"]
    applied = set()

    for scenario_dir in sorted((DATA / "scenarios").iterdir()):
        if not scenario_dir.is_dir():
            continue
        locations_path = scenario_dir / "locations.json"
        locations_doc = load(locations_path)
        locations = locations_doc["locations"]
        by_id = {item["id"]: item for item in locations}

        for location_id, override in overrides.items():
            if location_id not in by_id:
                continue
            item = by_id[location_id]
            item["coords"] = override["coords"]
            item["geocode_status"] = override["status"]
            item["geocode"] = {
                "provider": override["source"],
                "matched_address": override["matched_address"],
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "method": "manual_external_review",
                "note": override.get("note"),
            }
            applied.add(location_id)

        dump(locations_path, locations_doc)
        coord_by_location = {x["id"]: x.get("coords") for x in locations}
        status_by_location = {x["id"]: x.get("geocode_status") for x in locations}

        jobs_path = scenario_dir / "jobs.json"
        jobs_doc = load(jobs_path)
        for job in jobs_doc["jobs"]:
            location_id = job["location_id"]
            job["coords"] = coord_by_location.get(location_id)
            job.setdefault("provenance", {})["coords"] = {
                "kind": "external",
                "provider": "geocoding pipeline",
                "status": status_by_location.get(location_id),
                "location_id": location_id,
            }
        dump(jobs_path, jobs_doc)

        engineers_path = scenario_dir / "engineers.json"
        engineers_doc = load(engineers_path)
        for engineer in engineers_doc["engineers"]:
            location_id = engineer["start_location_id"]
            engineer["start_point"] = coord_by_location.get(location_id)
            engineer.setdefault("provenance", {})["start_point"] = {
                "kind": "external",
                "provider": "geocoding pipeline",
                "status": status_by_location.get(location_id),
                "location_id": location_id,
            }
        dump(engineers_path, engineers_doc)

    missing = sorted(set(overrides) - applied)
    if missing:
        raise SystemExit(f"Overrides not applied: {missing}")
    print(f"Applied {len(applied)} geocode overrides")


if __name__ == "__main__":
    main()
