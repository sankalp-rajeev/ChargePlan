"""Experiment A: label-setting and A* against the exact solver.

Owned by Sankalp (S7). ``experiments.run_all`` writes ``results/correctness.csv``
for the S6 tiny graphs and for the Experiment A suite built in
``experiments.exact_suite``. This module compares the three solvers on one
instance, checks them against the energy-only certificate in
``experiments.certificate``, and rolls the results into the supplementary
``correctness_summary.json``. It is not a second runner.

A comparison is the full lexicographic objective, not only the total time: the
feasibility status, then ``total_time_minutes``, ``number_of_charging_stops``,
and ``total_distance_miles``, exactly as ``metrics.objective`` reads them.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from decimal import Decimal
from pathlib import Path

from experiments.certificate import min_charging_stops
from experiments.instances import (
    EXACT_MAX_CAPACITY_STEPS,
    EXACT_MAX_CHARGERS,
    EXACT_MAX_NODES,
    Instance,
    exact_block_reason,
)
from experiments.metrics import objective
from optimizer.model import discretize

SUMMARY_FILE = "correctness_summary.json"
ALGORITHMS = ("label_setting", "astar", "exact")
_PAIRS = (
    ("label_setting", "exact"),
    ("astar", "exact"),
    ("label_setting", "astar"),
)
_FIELDS = (
    "status",
    "total_time_minutes",
    "number_of_charging_stops",
    "total_distance_miles",
)


def run() -> None:
    """The benchmark entry point is ``python -m experiments.run_all``."""
    raise NotImplementedError("use python -m experiments.run_all")


def compare(instance: Instance, results: dict[str, dict]) -> dict:
    """Compare the three solver results on one instance.

    ``results`` maps each name in ``ALGORITHMS`` to its ``algorithm_result``.
    The returned record holds only values that repeat for a fixed seed. Runtime
    is kept apart under ``runtime_ms``.
    """
    mismatches: list[str] = []
    for left, right in _PAIRS:
        for field_name in _differences(results[left], results[right]):
            mismatches.append(f"{left}!={right}:{field_name}")
    exact = results["exact"]
    certificate = min_charging_stops(instance.graph, instance.config)
    conflict = _certificate_conflict(certificate, exact)
    feasible = exact["status"] == "FEASIBLE"
    profile = _charge_profile(exact, instance.config) if feasible else (0, 0)
    config = instance.config
    capacity_steps, _initial, reserve_steps, _step = discretize(config)
    return {
        "instance_id": instance.instance_id,
        "category": instance.meta.get("category", ""),
        "variant": instance.meta.get("variant", ""),
        "status": exact["status"],
        "stops": exact["number_of_charging_stops"] if feasible else None,
        "certificate_min_stops": certificate,
        "partial_charge_events": profile[0],
        "full_charge_events": profile[1],
        "nodes": instance.node_count,
        "chargers": instance.charger_count,
        "capacity_steps": capacity_steps,
        "reserve_steps": reserve_steps,
        "soc_step_kwh": float(config["soc_step_kwh"]),
        "consumption_kwh_per_mile": float(config["consumption_kwh_per_mile"]),
        "exact_block_reason": exact_block_reason(instance.graph, config),
        "mismatches": mismatches,
        "certificate_conflict": conflict,
        "time_difference_minutes": _spread(results, "total_time_minutes"),
        "distance_difference_miles": _spread(results, "total_distance_miles"),
        "stop_difference": _spread(results, "number_of_charging_stops"),
        "runtime_ms": {name: results[name]["metrics"]["runtime_ms"] for name in ALGORITHMS},
    }


def summarize(seed: int, records: list[dict]) -> dict:
    """Roll per-instance records into the distributions the report quotes."""
    feasible = [record for record in records if record["status"] == "FEASIBLE"]
    stops = Counter(record["stops"] for record in feasible)
    needing = [record for record in feasible if (record["certificate_min_stops"] or 0) >= 1]
    charged = [
        record
        for record in feasible
        if record["partial_charge_events"] + record["full_charge_events"] > 0
    ]
    partial = [record for record in charged if record["partial_charge_events"] > 0]
    mismatched = [record["instance_id"] for record in records if record["mismatches"]]
    conflicts = [record["instance_id"] for record in records if record["certificate_conflict"]]
    blocked = [record["instance_id"] for record in records if record["exact_block_reason"]]
    return {
        "seed": seed,
        "instances": len(records),
        "feasible": len(feasible),
        "infeasible": len(records) - len(feasible),
        "charging_stops_in_optimal_plan": _ordered(stops),
        "feasible_requiring_charging": len(needing),
        "feasible_requiring_multiple_stops": sum(
            1 for record in feasible if (record["certificate_min_stops"] or 0) >= 2
        ),
        "feasible_optimal_plan_charges": len(charged),
        "feasible_optimal_plan_multi_stop": sum(1 for record in feasible if record["stops"] >= 2),
        "certificate_minimum_stops": _ordered(
            Counter(record["certificate_min_stops"] for record in feasible)
        ),
        "partial_charging": {
            "instances_with_partial_charge": len(partial),
            "instances_with_any_charge": len(charged),
            "partial_charge_events": sum(r["partial_charge_events"] for r in records),
            "full_charge_events": sum(r["full_charge_events"] for r in records),
        },
        "by_category": _by_category(records),
        "by_variant": _ordered(
            Counter(f"{record['category']}/{record['variant']}" for record in records)
        ),
        "soc_step_kwh": _ordered(Counter(record["soc_step_kwh"] for record in records)),
        "consumption_kwh_per_mile": _ordered(
            Counter(record["consumption_kwh_per_mile"] for record in records)
        ),
        "capacity_steps": _ordered(Counter(record["capacity_steps"] for record in records)),
        "reserve_steps": _ordered(Counter(record["reserve_steps"] for record in records)),
        "nodes": _ordered(Counter(record["nodes"] for record in records)),
        "chargers": _ordered(Counter(record["chargers"] for record in records)),
        "agreement": {
            "instances_compared": len(records),
            "objective_mismatches": len(mismatched),
            "mismatched_instances": mismatched,
            "pair_mismatches": {
                f"{left}_vs_{right}": sum(
                    1
                    for record in records
                    if any(item.startswith(f"{left}!={right}:") for item in record["mismatches"])
                )
                for left, right in _PAIRS
            },
            "feasibility_mismatches": sum(
                1 for record in records if any(":status" in item for item in record["mismatches"])
            ),
            "max_time_difference_minutes": max(
                (record["time_difference_minutes"] for record in records), default=0.0
            ),
            "max_distance_difference_miles": max(
                (record["distance_difference_miles"] for record in records), default=0.0
            ),
            "max_stop_difference": max(
                (record["stop_difference"] for record in records), default=0
            ),
        },
        "certificate": {
            "conflicts": len(conflicts),
            "conflicting_instances": conflicts,
        },
        "exact_bounds": {
            "max_nodes_allowed": EXACT_MAX_NODES,
            "max_chargers_allowed": EXACT_MAX_CHARGERS,
            "max_capacity_steps_allowed": EXACT_MAX_CAPACITY_STEPS,
            "max_nodes_used": max((r["nodes"] for r in records), default=0),
            "max_chargers_used": max((r["chargers"] for r in records), default=0),
            "max_capacity_steps_used": max((r["capacity_steps"] for r in records), default=0),
            "instances_outside_bound": len(blocked),
        },
        "runtime_ms": _runtime(records),
    }


def failed_ids(records: list[dict]) -> tuple[str, ...]:
    """Instance ids where a solver pair or the certificate disagreed."""
    return tuple(
        record["instance_id"]
        for record in records
        if record["mismatches"] or record["certificate_conflict"]
    )


def write_summary(path: Path, summary: dict, records: list[dict]) -> None:
    """Write the summary and the per-instance records as sorted, readable JSON.

    Everything except the ``runtime_ms`` entries repeats for a fixed seed.
    """
    payload = {"summary": summary, "instances": records}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def deterministic_part(payload: dict) -> dict:
    """Return the payload without any wall-clock measurement."""
    summary = {key: value for key, value in payload["summary"].items() if key != "runtime_ms"}
    instances = [
        {key: value for key, value in record.items() if key != "runtime_ms"}
        for record in payload["instances"]
    ]
    return {"summary": summary, "instances": instances}


def format_report(summary: dict) -> str:
    """Return a short plain-text view of the summary for the console."""
    agreement = summary["agreement"]
    lines = [
        f"Experiment A: {summary['instances']} instances, seed {summary['seed']}",
        f"  feasible {summary['feasible']}, infeasible {summary['infeasible']}",
        f"  stops in the optimal plans {summary['charging_stops_in_optimal_plan']}",
        f"  feasible requiring charging {summary['feasible_requiring_charging']}, "
        f"requiring 2+ stops {summary['feasible_requiring_multiple_stops']}",
        f"  partial charging in {summary['partial_charging']['instances_with_partial_charge']} "
        f"of {summary['partial_charging']['instances_with_any_charge']} charging plans",
        f"  objective mismatches {agreement['objective_mismatches']}, "
        f"certificate conflicts {summary['certificate']['conflicts']}",
        f"  instances outside the exact bound {summary['exact_bounds']['instances_outside_bound']}",
    ]
    return "\n".join(lines)


def _differences(left: dict, right: dict) -> list[str]:
    """Name the objective fields that differ between two results."""
    if left.get("status") != right.get("status"):
        return ["status"]
    if left.get("status") != "FEASIBLE":
        return []
    wanted = objective(left)
    other = objective(right)
    return [name for name, a, b in zip(_FIELDS, wanted, other, strict=True) if a != b]


def _certificate_conflict(certificate: int | None, exact: dict) -> bool:
    """The certificate must agree on feasibility, and no plan may use fewer stops."""
    if (certificate is None) != (exact["status"] != "FEASIBLE"):
        return True
    return certificate is not None and exact["number_of_charging_stops"] < certificate


def _charge_profile(exact: dict, config: dict) -> tuple[int, int]:
    """Count charge events that end below a full battery, and those that end full."""
    capacity_steps, _initial, _reserve, _step = discretize(config)
    step = Decimal(str(config["soc_step_kwh"]))
    full = Decimal(capacity_steps) * step
    partial = 0
    topped = 0
    for event in exact["events"]:
        if event["type"] != "charge":
            continue
        if Decimal(str(event["soc_after_kwh"])) < full:
            partial += 1
        else:
            topped += 1
    return partial, topped


def _spread(results: dict[str, dict], key: str) -> float | int:
    """Largest gap between solvers on one numeric field, among feasible results."""
    values = [
        results[name][key] for name in ALGORITHMS if results[name]["status"] == "FEASIBLE"
    ]
    if not values:
        return 0
    return round(max(values) - min(values), 9)


def _by_category(records: list[dict]) -> dict:
    grouped: dict[str, list[dict]] = {}
    for record in records:
        grouped.setdefault(record["category"], []).append(record)
    table = {}
    for category, group in sorted(grouped.items()):
        table[category] = {
            "instances": len(group),
            "feasible": sum(1 for r in group if r["status"] == "FEASIBLE"),
            "infeasible": sum(1 for r in group if r["status"] != "FEASIBLE"),
            "stops_in_optimal_plan": _ordered(
                Counter(r["stops"] for r in group if r["status"] == "FEASIBLE")
            ),
        }
    return table


def _runtime(records: list[dict]) -> dict:
    table = {}
    for name in ALGORITHMS:
        values = [record["runtime_ms"][name] for record in records]
        table[name] = {
            "total_ms": round(sum(values), 3),
            "median_ms": round(statistics.median(values), 4) if values else 0.0,
            "max_ms": round(max(values), 4) if values else 0.0,
        }
    return table


def _ordered(counter: Counter) -> dict[str, int]:
    return {str(key): counter[key] for key in sorted(counter, key=_sort_key)}


def _sort_key(value: object) -> tuple[int, float, str]:
    if isinstance(value, (int, float)):
        return (0, float(value), "")
    return (1, 0.0, str(value))
