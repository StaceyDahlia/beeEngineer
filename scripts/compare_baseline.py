from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services.loader import SUPPORTED_SCENARIOS, load_scenario
from backend.app.services.planner import build_plan_with_comparison


def _print_table(rows: list[dict[str, object]]) -> None:
    headers = ("Регион", "Движок", "Назначено", "Инженеров", "Км", "Время, мс")
    values = [
        (
            str(row["scenario"]),
            str(row["engine"]),
            str(row["assigned"]),
            str(row["engineers"]),
            f"{float(row['distance_km']):.3f}",
            str(row["solve_time_ms"]),
        )
        for row in rows
    ]
    widths = [max(len(headers[i]), *(len(row[i]) for row in values)) for i in range(6)]
    print(" | ".join(headers[i].ljust(widths[i]) for i in range(6)))
    print("-+-".join("-" * width for width in widths))
    for row in values:
        print(" | ".join(row[i].ljust(widths[i]) for i in range(6)))


def compare(scenarios: tuple[str, ...], solve_time_limit_ms: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for scenario in scenarios:
        jobs, engineers = load_scenario(scenario)
        plan = build_plan_with_comparison(
            jobs,
            engineers,
            scenario=scenario,
            engine="ortools_vrptw",
            solve_time_limit_ms=solve_time_limit_ms,
        )
        comparison = plan["comparison"]
        for name, summary in (
            ("baseline_greedy", comparison["baseline"]),
            ("ortools_vrptw", comparison["optimized"]),
        ):
            metrics = summary["metrics"]
            rows.append(
                {
                    "scenario": scenario,
                    "engine": name,
                    "assigned": metrics["assigned_count"],
                    "engineers": metrics["used_engineers"],
                    "distance_km": metrics["total_distance_km"],
                    "solve_time_ms": summary["solve_time_ms"],
                }
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Сравнить baseline и OR-Tools офлайн.")
    parser.add_argument(
        "--solve-time-ms",
        type=int,
        default=3000,
        help="Лимит OR-Tools на регион (по умолчанию 3000 мс).",
    )
    parser.add_argument(
        "--scenario",
        choices=SUPPORTED_SCENARIOS,
        action="append",
        help="Ограничить сравнение регионом; параметр можно повторять.",
    )
    args = parser.parse_args()
    scenarios = tuple(args.scenario or SUPPORTED_SCENARIOS)
    rows = compare(scenarios, max(100, args.solve_time_ms))
    _print_table(rows)
    print("\nOR-Tools time-limited: показан лучший найденный допустимый план; оптимум не доказан.")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    main()
