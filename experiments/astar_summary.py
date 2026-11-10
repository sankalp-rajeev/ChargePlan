"""Experiment J metrics and aggregates.

Everything here is computed from the rows of ``astar_comparison.csv``, so the
summary describes exactly what was written. Only rows whose note carries
``suite=J`` belong to Experiment J. The S6 sample and tiny rows in the same file
carry ``suite=S6`` and are ignored.

The headline number for an instance is the relative reduction in states
expanded, ``100 * (label_setting - astar) / label_setting``. It is undefined
when label-setting expanded nothing. It is negative when A* expanded more. The
summary reports the median and interquartile range of these per-instance
numbers. It also reports a pooled reduction, ``100 * (sum label_setting - sum
astar) / sum label_setting``, which weights each instance by its size. It never
averages the percentages. Sizes differ by an order of magnitude, so a mean of
percentages would let small instances count as much as large ones.
"""

from __future__ import annotations

import math

from experiments.scaling import note_fields, quartiles

SUMMARY_FILE = "astar_summary.json"
FAILURES_FILE = "astar_failures.json"
SUITE_NOTE = "suite=J"
_MEASURED = {"FEASIBLE", "INFEASIBLE"}
_LABEL_FIELDS = ("status", "total_time_minutes", "number_of_charging_stops", "total_distance_miles")


def reduction_pct(baseline: float, other: float) -> float | None:
    """Return ``100 * (baseline - other) / baseline``, or ``None`` when ``baseline`` is 0.

    A negative value means ``other`` is larger than the baseline.
    """
    if baseline == 0:
        return None
    return 100.0 * (baseline - other) / baseline


def is_dedicated(row: dict[str, str]) -> bool:
    """True for a row written by the dedicated Experiment J benchmark."""
    return row["experiment"] == "J" and note_fields(row["note"]).get("suite") == "J"


def instance_pairs(rows: list[dict[str, str]]) -> list[dict]:
    """Pair the label-setting and A* row of every dedicated J instance.

    A missing partner is an error: a half-measured instance must not shrink the
    sample silently.
    """
    grouped: dict[str, dict[str, dict[str, str]]] = {}
    for row in rows:
        if not is_dedicated(row) or row["status"] not in _MEASURED:
            continue
        if row["algorithm"] in {"label_setting", "astar"}:
            grouped.setdefault(row["instance_id"], {})[row["algorithm"]] = row
    pairs = []
    for instance_id in sorted(grouped):
        pair = grouped[instance_id]
        if set(pair) != {"label_setting", "astar"}:
            raise ValueError(f"{instance_id} is missing a label_setting or astar row")
        plain, guided = pair["label_setting"], pair["astar"]
        info = note_fields(plain["note"])
        feasible = plain["status"] == "FEASIBLE"
        record = {
            "instance_id": instance_id,
            "geometry": info["geometry"],
            "size": int(info["size"]),
            "speed": info["speed"],
            "soc_step": info["soc_step"],
            "replicate": int(info["replicate"]),
            "feasible": feasible,
            "match": all(plain[field] == guided[field] for field in _LABEL_FIELDS),
            "ls_states": int(plain["states_expanded"]),
            "astar_states": int(guided["states_expanded"]),
            "ls_peak": int(plain["peak_states_stored"]),
            "astar_peak": int(guided["peak_states_stored"]),
            "ls_ms": float(plain["runtime_ms"]),
            "astar_ms": float(guided["runtime_ms"]),
            "heuristic_ratio": None,
            "route_ratio": None,
        }
        if feasible:
            total = float(plain["total_time_minutes"])
            straight = float(info["straight_miles"])
            if total > 0:
                record["heuristic_ratio"] = float(info["h_origin_minutes"]) / total
            if straight > 0:
                record["route_ratio"] = float(plain["total_distance_miles"]) / straight
        record["reduction"] = reduction_pct(record["ls_states"], record["astar_states"])
        record["peak_reduction"] = reduction_pct(record["ls_peak"], record["astar_peak"])
        pairs.append(record)
    return pairs


def summarize_astar(rows: list[dict[str, str]]) -> dict:
    """Aggregate the dedicated Experiment J rows. Raises if there are none."""
    pairs = instance_pairs(rows)
    if not pairs:
        raise ValueError("no dedicated Experiment J rows to summarize")
    feasible = [p for p in pairs if p["feasible"]]
    mismatched = [p["instance_id"] for p in pairs if not p["match"]]
    negative = [
        p["instance_id"] for p in pairs if p["reduction"] is not None and p["reduction"] < 0
    ]
    return {
        "instances": len(pairs),
        "feasible": len(feasible),
        "infeasible": len(pairs) - len(feasible),
        "objective_mismatches": len(mismatched),
        "mismatched_instances": mismatched,
        "negative_reduction_instances": negative,
        "overall": _group(pairs),
        "by_size": _by(pairs, lambda p: str(p["size"]), key=int),
        "by_geometry": _by(pairs, lambda p: p["geometry"]),
        "by_soc_step": _by(pairs, lambda p: p["soc_step"], key=float),
        "by_speed": _by(pairs, lambda p: p["speed"]),
        "by_geometry_speed": _by(pairs, lambda p: f"{p['geometry']}/{p['speed']}"),
        "design": _design(pairs),
        "rank_correlation": {
            "reduction_vs_heuristic_ratio": _rank_correlation(
                feasible, "heuristic_ratio"
            ),
            "reduction_vs_route_ratio": _rank_correlation(feasible, "route_ratio"),
        },
    }


def format_report(summary: dict) -> str:
    """A few lines for the console."""
    overall = summary["overall"]["feasible_only"]
    reduction = overall["states_reduction_pct"]
    return "\n".join(
        [
            f"Experiment J: {summary['instances']} instances "
            f"(feasible {summary['feasible']}, infeasible {summary['infeasible']})",
            f"  objective mismatches {summary['objective_mismatches']}, "
            f"A* expanded more states on {len(summary['negative_reduction_instances'])}",
            "  state reduction (feasible): median "
            f"{reduction['median']:.1f}% (IQR {reduction['q1']:.1f}-{reduction['q3']:.1f}, "
            f"min {reduction['min']:.1f}, max {reduction['max']:.1f}), pooled "
            f"{overall['pooled_states_reduction_pct']:.1f}%",
        ]
    )


def _design(pairs: list[dict]) -> dict:
    cells: dict[tuple, int] = {}
    for p in pairs:
        key = (p["geometry"], p["size"], p["speed"], p["soc_step"])
        cells[key] = cells.get(key, 0) + 1
    return {
        "cells": len(cells),
        "replicates_per_cell_min": min(cells.values()),
        "replicates_per_cell_max": max(cells.values()),
        "geometries": sorted({p["geometry"] for p in pairs}),
        "sizes": sorted({p["size"] for p in pairs}),
        "speeds": sorted({p["speed"] for p in pairs}),
        "soc_steps": sorted({float(p["soc_step"]) for p in pairs}),
    }


def _by(pairs: list[dict], label, *, key=str) -> dict[str, dict]:
    groups: dict[str, list[dict]] = {}
    for p in pairs:
        groups.setdefault(label(p), []).append(p)
    order = sorted(groups) if key is str else sorted(groups, key=key)
    return {name: _group(groups[name]) for name in order}


def _group(pairs: list[dict]) -> dict:
    feasible = [p for p in pairs if p["feasible"]]
    infeasible = [p for p in pairs if not p["feasible"]]
    return {
        "instances": len(pairs),
        "feasible": len(feasible),
        "infeasible": len(infeasible),
        "all": _scope(pairs),
        "feasible_only": _scope(feasible),
        "infeasible_only": _scope(infeasible),
    }


def _scope(pairs: list[dict]) -> dict | None:
    if not pairs:
        return None
    defined = [p for p in pairs if p["reduction"] is not None]
    peak_defined = [p for p in pairs if p["peak_reduction"] is not None]
    timed = [p for p in pairs if p["ls_ms"] > 0]
    total_ls = sum(p["ls_states"] for p in pairs)
    total_peak = sum(p["ls_peak"] for p in pairs)
    total_ms = sum(p["ls_ms"] for p in pairs)
    return {
        "instances": len(pairs),
        "undefined_reduction": len(pairs) - len(defined),
        "states_reduction_pct": _spread([p["reduction"] for p in defined]),
        "pooled_states_reduction_pct": reduction_pct(
            total_ls, sum(p["astar_states"] for p in pairs)
        ),
        "fraction_zero": _fraction(defined, lambda p: p["reduction"] == 0),
        "fraction_negative": _fraction(defined, lambda p: p["reduction"] < 0),
        "fraction_no_improvement": _fraction(defined, lambda p: p["reduction"] <= 0),
        "peak_reduction_pct": _spread([p["peak_reduction"] for p in peak_defined]),
        "pooled_peak_reduction_pct": reduction_pct(
            total_peak, sum(p["astar_peak"] for p in pairs)
        ),
        "fraction_peak_not_lower": _fraction(peak_defined, lambda p: p["peak_reduction"] <= 0),
        "runtime_ratio_astar_over_ls": _spread([p["astar_ms"] / p["ls_ms"] for p in timed]),
        "pooled_runtime_ratio_astar_over_ls": (
            sum(p["astar_ms"] for p in pairs) / total_ms if total_ms > 0 else None
        ),
        "fraction_astar_slower": _fraction(timed, lambda p: p["astar_ms"] > p["ls_ms"]),
        "ls_states": _spread([float(p["ls_states"]) for p in pairs]),
        "astar_states": _spread([float(p["astar_states"]) for p in pairs]),
        "heuristic_ratio": _spread(
            [p["heuristic_ratio"] for p in pairs if p["heuristic_ratio"] is not None]
        ),
        "route_ratio": _spread([p["route_ratio"] for p in pairs if p["route_ratio"] is not None]),
    }


def _spread(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    low, middle, high = quartiles(values)
    return {"q1": low, "median": middle, "q3": high, "min": min(values), "max": max(values)}


def _fraction(pairs: list[dict], test) -> float | None:
    if not pairs:
        return None
    return sum(1 for p in pairs if test(p)) / len(pairs)


def _rank_correlation(pairs: list[dict], field: str) -> dict:
    """Spearman correlation of state reduction with ``field``, over defined pairs."""
    chosen = [p for p in pairs if p["reduction"] is not None and p[field] is not None]
    if len(chosen) < 3:
        return {"n": len(chosen), "rho": None}
    first = _ranks([p["reduction"] for p in chosen])
    second = _ranks([p[field] for p in chosen])
    return {"n": len(chosen), "rho": _pearson(first, second)}


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        for position in range(start, end + 1):
            ranks[order[position]] = (start + end) / 2 + 1
        start = end + 1
    return ranks


def _pearson(first: list[float], second: list[float]) -> float | None:
    count = len(first)
    mean_a, mean_b = sum(first) / count, sum(second) / count
    cov = sum((a - mean_a) * (b - mean_b) for a, b in zip(first, second, strict=True))
    var_a = sum((a - mean_a) ** 2 for a in first)
    var_b = sum((b - mean_b) ** 2 for b in second)
    if var_a == 0 or var_b == 0:
        return None
    return cov / math.sqrt(var_a * var_b)
