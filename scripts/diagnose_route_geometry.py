from __future__ import annotations

import argparse
import json

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print the live backend-to-map route geometry and KPI contract."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--scenario", default="east")
    parser.add_argument("--engineer-id")
    args = parser.parse_args()

    response = httpx.post(
        f"{args.base_url.rstrip('/')}/plans",
        json={
            "scenario": args.scenario,
            "engine": "ortools_vrptw",
            "solve_time_limit_ms": 1_000,
        },
        timeout=90,
    )
    response.raise_for_status()
    plan = response.json()
    candidates = [route for route in plan.get("routes", []) if route.get("stops")]
    if args.engineer_id:
        candidates = [
            route for route in candidates if str(route["engineer_id"]) == args.engineer_id
        ]
    if not candidates:
        raise SystemExit("No non-empty route matched the requested engineer.")

    route = candidates[0]
    collection = route.get("route_geometry") or {}
    segments = []
    for feature in collection.get("features") or []:
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        segments.append(
            {
                "from": properties.get("origin_id"),
                "to": properties.get("destination_id"),
                "source": properties.get("source"),
                "geometry_type": geometry.get("type"),
                "geometry_points": len(geometry.get("coordinates") or []),
                "distance_m": properties.get("distance_m"),
                "travel_min": properties.get("travel_min"),
                "matrix_id": properties.get("matrix_id"),
            }
        )
    segment_distance_m = sum(int(item["distance_m"] or 0) for item in segments)
    result = {
        "plan_id": plan.get("plan_id"),
        "engineer_id": route["engineer_id"],
        "route_geometry_present": bool(collection),
        "route_geometry_source": collection.get("source"),
        "route_geometry_matrix_id": collection.get("matrix_id"),
        "segments": segments,
        "segment_distance_sum_m": segment_distance_m,
        "route_distance_m": route.get("distance_m"),
        "route_kpi_matches_segments": segment_distance_m == int(route.get("distance_m") or 0),
        "plan_total_distance_m": plan.get("metrics", {}).get("total_distance_m"),
        "all_route_distance_sum_m": sum(
            int(item.get("distance_m") or 0) for item in plan.get("routes", [])
        ),
    }
    result["plan_kpi_matches_routes"] = (
        result["plan_total_distance_m"] == result["all_route_distance_sum_m"]
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
