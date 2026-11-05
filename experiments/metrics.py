"""Turn a solver result into one metrics row and write the CSV files.

The collected fields are the S6 set: runtime, states expanded, peak states
stored, feasibility, total time, charging stops, and distance. Runtime is
measured by the solver and is not part of the reproducibility check.
"""

from __future__ import annotations

import csv
from pathlib import Path

from optimizer.model import InputError

COLUMNS = (
    "experiment",
    "instance_id",
    "algorithm",
    "status",
    "feasible",
    "total_time_minutes",
    "number_of_charging_stops",
    "total_distance_miles",
    "runtime_ms",
    "states_expanded",
    "peak_states_stored",
    "seed",
    "nodes",
    "chargers",
    "capacity_kwh",
    "soc_step_kwh",
    "agreement",
    "note",
)

# Written on every ``make experiments`` run. Names are the Section 23 list.
OUTPUT_FILES = (
    "correctness.csv",
    "greedy_comparison.csv",
    "soc_resolution.csv",
    "charger_scaling.csv",
    "battery_capacity.csv",
    "initial_soc.csv",
    "charger_density.csv",
    "charger_speed.csv",
    "astar_comparison.csv",
    "real_routes.csv",
)


def objective(result: dict) -> tuple:
    """Return the feasibility bit and, when feasible, the lexicographic label."""
    status = result.get("status")
    if status != "FEASIBLE":
        return (status,)
    return (
        status,
        result.get("total_time_minutes"),
        result.get("number_of_charging_stops"),
        result.get("total_distance_miles"),
    )


def metric_row(
    *,
    experiment: str,
    instance_id: str,
    seed: int | None,
    nodes: int | None,
    chargers: int | None,
    capacity_kwh: float | None,
    soc_step_kwh: float | None,
    algorithm: str = "",
    status: str,
    result: dict | None = None,
    agreement: str = "",
    note: str = "",
) -> dict[str, str]:
    """Build one CSV row. A missing result leaves the measurement cells blank."""
    row = {column: "" for column in COLUMNS}
    row["experiment"] = experiment
    row["instance_id"] = instance_id
    row["algorithm"] = algorithm
    row["status"] = status
    row["seed"] = "" if seed is None else str(seed)
    row["nodes"] = "" if nodes is None else str(nodes)
    row["chargers"] = "" if chargers is None else str(chargers)
    row["capacity_kwh"] = _cell(capacity_kwh)
    row["soc_step_kwh"] = _cell(soc_step_kwh)
    row["agreement"] = agreement
    row["note"] = note
    if result is None:
        return row
    _require_result(result)
    row["algorithm"] = str(result["algorithm"])
    row["status"] = str(result["status"])
    row["feasible"] = "true" if result["status"] == "FEASIBLE" else "false"
    row["total_time_minutes"] = _cell(result["total_time_minutes"])
    row["number_of_charging_stops"] = str(result["number_of_charging_stops"])
    row["total_distance_miles"] = _cell(result["total_distance_miles"])
    row["runtime_ms"] = _cell(result["metrics"]["runtime_ms"])
    row["states_expanded"] = str(result["metrics"]["states_expanded"])
    row["peak_states_stored"] = str(result["metrics"]["peak_states_stored"])
    return row


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write rows with the shared header. The directory must already exist."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=COLUMNS,
            lineterminator="\n",
            extrasaction="raise",
        )
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    """Read a metrics CSV back into row dictionaries."""
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise InputError(f"{path.name} does not use the metrics columns")
        return list(reader)


def _require_result(result: dict) -> None:
    metrics = result.get("metrics")
    required = (
        "algorithm",
        "status",
        "total_time_minutes",
        "number_of_charging_stops",
        "total_distance_miles",
    )
    missing = [key for key in required if key not in result]
    if not isinstance(metrics, dict):
        missing.append("metrics")
    else:
        for key in ("runtime_ms", "states_expanded", "peak_states_stored"):
            if key not in metrics:
                missing.append(f"metrics.{key}")
    if missing:
        raise InputError("solver result is missing " + ", ".join(missing))


def _cell(value: float | None) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return format(value, ".10g")
    return str(value)
