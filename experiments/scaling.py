"""Scaling experiments.

Owned by Sankalp (S7). Experiment D varies the number of charging stations.
Experiment E varies battery capacity. Experiment J compares states expanded
by A* and by plain label-setting. ``experiments.run_all`` writes the CSVs.
This module is not a second runner.

It also aggregates the Experiment D rows per charger count. The aggregation
reads only the CSV rows, so it describes exactly what was written.
"""

from __future__ import annotations

import math
import statistics

SUMMARY_FILE = "charger_scaling_summary.json"
_MEASURED = {"FEASIBLE", "INFEASIBLE"}
_ALGORITHMS = ("label_setting", "astar")


def run() -> None:
    """The benchmark entry point is ``python -m experiments.run_all``."""
    raise NotImplementedError("use python -m experiments.run_all")


def note_fields(note: str) -> dict[str, str]:
    """Parse the ``key=value; key=value`` note of a CSV row."""
    found = {}
    for part in note.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        found[key.strip()] = value.strip()
    return found


def quartiles(values: list[float]) -> tuple[float, float, float]:
    """Return the 25th percentile, median, and 75th percentile (inclusive method)."""
    if not values:
        raise ValueError("quartiles need at least one value")
    if len(values) == 1:
        return values[0], values[0], values[0]
    low, middle, high = statistics.quantiles(values, n=4, method="inclusive")
    return low, middle, high


def summarize_scaling(rows: list[dict[str, str]]) -> dict:
    """Aggregate Experiment D rows into one record per charger count.

    A row is one algorithm on one instance. The record for a count holds the
    instance count, how many were feasible, and the median and interquartile
    range of states expanded, peak states stored, and runtime for each
    algorithm. It also holds the median edge count, the state-space ceiling
    ``2 * nodes * (capacity_steps + 1)``, and size-normalized ratios.
    ``growth`` gives the local log-log slope between consecutive counts. It is a
    description of two measured medians, not an asymptotic fit.
    """
    by_count: dict[int, dict[str, dict[str, dict[str, str]]]] = {}
    for row in rows:
        if row["experiment"] != "D" or row["status"] not in _MEASURED:
            continue
        if row["algorithm"] not in _ALGORITHMS:
            continue
        count = int(row["chargers"])
        by_count.setdefault(count, {}).setdefault(row["instance_id"], {})[row["algorithm"]] = row
    counts = [_count_record(count, by_count[count]) for count in sorted(by_count)]
    return {
        "counts": counts,
        "growth": _growth(counts),
        "instances": sum(record["instances"] for record in counts),
    }


def _count_record(count: int, instances: dict[str, dict[str, dict[str, str]]]) -> dict:
    pairs = []
    for instance_id in sorted(instances):
        pair = instances[instance_id]
        if set(pair) != set(_ALGORITHMS):
            raise ValueError(f"{instance_id} is missing a label_setting or astar row")
        pairs.append(pair)
    label = [pair["label_setting"] for pair in pairs]
    notes = [note_fields(row["note"]) for row in label]
    nodes = [int(row["nodes"]) for row in label]
    steps = {int(info["capacity_steps"]) for info in notes}
    if len(steps) != 1:
        raise ValueError(f"{count} chargers mixes SOC capacities {sorted(steps)}")
    capacity_steps = steps.pop()
    ceilings = [2 * node_count * (capacity_steps + 1) for node_count in nodes]
    edges = [float(info["edges"]) for info in notes]
    record: dict = {
        "chargers": count,
        "instances": len(pairs),
        "feasible": sum(1 for row in label if row["status"] == "FEASIBLE"),
        "infeasible": sum(1 for row in label if row["status"] == "INFEASIBLE"),
        "nodes": statistics.median(nodes),
        "capacity_steps": capacity_steps,
        "edges": _spread(edges),
        "state_ceiling": statistics.median(ceilings),
        "fast_charger_share": _spread([float(info["fast_share"]) for info in notes]),
        "min_stops": _spread(
            [float(info["min_stops"]) for info in notes if info["min_stops"] != "none"]
        ),
        "stops": _spread(
            [
                float(row["number_of_charging_stops"])
                for row in label
                if row["status"] == "FEASIBLE"
            ]
        ),
        "trip_minutes": _spread(
            [float(row["total_time_minutes"]) for row in label if row["status"] == "FEASIBLE"]
        ),
    }
    for algorithm in _ALGORITHMS:
        chosen = [pair[algorithm] for pair in pairs]
        states = [float(row["states_expanded"]) for row in chosen]
        record[algorithm] = {
            "states_expanded": _spread(states),
            "peak_states_stored": _spread([float(row["peak_states_stored"]) for row in chosen]),
            "runtime_ms": _spread([float(row["runtime_ms"]) for row in chosen]),
            "states_over_ceiling": _spread(
                [state / ceiling for state, ceiling in zip(states, ceilings, strict=True)]
            ),
            "runtime_us_per_edge_node_step": _spread(
                [
                    float(row["runtime_ms"])
                    * 1000.0
                    / ((float(info["edges"]) + int(row["nodes"])) * capacity_steps)
                    for row, info in zip(chosen, notes, strict=True)
                ]
            ),
        }
    record["astar_over_label_states"] = _spread(
        [
            float(pair["astar"]["states_expanded"])
            / float(pair["label_setting"]["states_expanded"])
            for pair in pairs
            if float(pair["label_setting"]["states_expanded"]) > 0
        ]
    )
    return record


def _spread(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    low, middle, high = quartiles(values)
    return {"q1": low, "median": middle, "q3": high, "min": min(values), "max": max(values)}


def _growth(counts: list[dict]) -> list[dict]:
    slopes = []
    for first, second in zip(counts, counts[1:], strict=False):
        ratio = math.log(second["chargers"] / first["chargers"])
        slopes.append(
            {
                "from": first["chargers"],
                "to": second["chargers"],
                "edges": _slope(first["edges"], second["edges"], ratio),
                "states_expanded": _slope(
                    first["label_setting"]["states_expanded"],
                    second["label_setting"]["states_expanded"],
                    ratio,
                ),
                "peak_states_stored": _slope(
                    first["label_setting"]["peak_states_stored"],
                    second["label_setting"]["peak_states_stored"],
                    ratio,
                ),
                "runtime_ms": _slope(
                    first["label_setting"]["runtime_ms"],
                    second["label_setting"]["runtime_ms"],
                    ratio,
                ),
            }
        )
    return slopes


def _slope(first: dict | None, second: dict | None, ratio: float) -> float | None:
    if not first or not second or first["median"] <= 0 or second["median"] <= 0:
        return None
    return math.log(second["median"] / first["median"]) / ratio
