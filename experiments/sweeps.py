"""Experiments A, C, D, E, and J.

Each sweep changes one input. Exact search is used only by Experiment A, and
only on graphs that already satisfy the S6 bound. Timing rows store the median
of repeated solves. States must match across those repeats; if they do not,
the run stops instead of publishing a mixed measurement.
"""

from __future__ import annotations

import math

from experiments.instances import (
    CAPACITIES,
    CHARGER_COUNTS,
    SOC_ROUNDUP_STEPS,
    SOC_SAMPLE_STEPS,
    TIMING_REPEATS,
    Instance,
    capacity_instances,
    correctness_instances,
    load_sample,
    roundup_probe,
    scaling_instances,
    soc_instances,
)
from experiments.metrics import metric_row, objective
from optimizer.battery_state_optimizer import solve as label_solve
from optimizer.exact_solver import solve as exact_solve
from optimizer.model import InputError


def experiment_a(seed: int, count: int) -> tuple[list[dict[str, str]], tuple[str, ...]]:
    """Compare label-setting, A*, and exact on ``count`` tiny graphs."""
    rows: list[dict[str, str]] = []
    failed: list[str] = []
    for instance in correctness_instances(seed, count):
        measured, ok = _triple(instance, "A")
        rows.extend(measured)
        if not ok:
            failed.append(instance.instance_id)
    return rows, tuple(failed)


def experiment_c(repeats: int = TIMING_REPEATS) -> tuple[list[dict[str, str]], tuple[str, ...]]:
    """Sweep SOC step on the sample and on the rounding probe."""
    sample = load_sample()
    families = (
        ("sample", soc_instances(SOC_SAMPLE_STEPS, family="sample", base=sample), 0.1),
        ("roundup", soc_instances(SOC_ROUNDUP_STEPS, family="roundup", base=roundup_probe()), 0.1),
    )
    rows: list[dict[str, str]] = []
    failed: list[str] = []
    for family, instances, reference_step in families:
        family_rows: list[dict[str, str]] = []
        for instance in instances:
            pair, ok = _pair(instance, "C", repeats, note="")
            family_rows.extend(pair)
            if not ok:
                failed.append(instance.instance_id)
        rows.extend(_with_objective_error(family_rows, family, reference_step))
    return rows, tuple(failed)


def experiment_d(
    repeats: int = TIMING_REPEATS,
    counts: tuple[int, ...] = CHARGER_COUNTS,
) -> tuple[list[dict[str, str]], tuple[str, ...]]:
    """Sweep the number of candidate chargers on one fixed corridor."""
    rows: list[dict[str, str]] = []
    failed: list[str] = []
    for instance in scaling_instances(counts):
        pair, ok = _pair(instance, "D", repeats, note="corridor=fixed; hop=3")
        rows.extend(pair)
        if not ok:
            failed.append(instance.instance_id)
    return rows, tuple(failed)


def experiment_e(
    repeats: int = TIMING_REPEATS,
    capacities: tuple[int, ...] = CAPACITIES,
) -> tuple[list[dict[str, str]], tuple[str, ...]]:
    """Sweep battery capacity on the sample roads with initial SOC fixed."""
    rows: list[dict[str, str]] = []
    failed: list[str] = []
    for instance in capacity_instances(load_sample(), capacities):
        pair, ok = _pair(instance, "E", repeats, note="initial_soc_kwh=30; roads=sample")
        rows.extend(pair)
        if not ok:
            failed.append(instance.instance_id)
    return rows, tuple(failed)


def journal_rows(
    scaling: list[dict[str, str]],
    soc: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Copy the measured scaling and SOC rows into Experiment J.

    Real-data routes are not added. S9 has not produced one.
    """
    copied = []
    for row in scaling + soc:
        if row["algorithm"] not in {"label_setting", "astar"}:
            continue
        if row["status"] not in {"FEASIBLE", "INFEASIBLE"}:
            continue
        updated = dict(row)
        updated["experiment"] = "J"
        note = updated["note"]
        suffix = "comparison=J"
        updated["note"] = f"{note}; {suffix}" if note else suffix
        copied.append(updated)
    return copied


def _triple(instance: Instance, experiment: str) -> tuple[list[dict[str, str]], bool]:
    plain = _solve("label_setting", instance)
    guided = _solve("astar", instance)
    exact = _solve("exact", instance)
    ok = objective(plain) == objective(guided) == objective(exact)
    agreement = "true" if ok else "false"
    note = "suite=A; timing=single run"
    return [
        _row(instance, experiment, plain, agreement, note),
        _row(instance, experiment, guided, agreement, note),
        _row(instance, experiment, exact, agreement, note),
    ], ok


def _pair(
    instance: Instance,
    experiment: str,
    repeats: int,
    *,
    note: str,
) -> tuple[list[dict[str, str]], bool]:
    plain = _timed("label_setting", instance, repeats)
    guided = _timed("astar", instance, repeats)
    ok = objective(plain) == objective(guided)
    agreement = "true" if ok else "false"
    timing = f"timing=median of {repeats}" if repeats > 1 else "timing=single run"
    full_note = f"{note}; {timing}" if note else timing
    return [
        _row(instance, experiment, plain, agreement, full_note),
        _row(instance, experiment, guided, agreement, full_note),
    ], ok


def _timed(algorithm: str, instance: Instance, repeats: int) -> dict:
    if repeats < 1:
        raise InputError("timing repeats must be positive")
    results = [_solve(algorithm, instance) for _ in range(repeats)]
    first = objective(results[0])
    states = results[0]["metrics"]["states_expanded"]
    stored = results[0]["metrics"]["peak_states_stored"]
    for result in results[1:]:
        if objective(result) != first:
            raise InputError(f"{instance.instance_id} {algorithm} changed objective across repeats")
        if result["metrics"]["states_expanded"] != states:
            raise InputError(f"{instance.instance_id} {algorithm} changed states across repeats")
        if result["metrics"]["peak_states_stored"] != stored:
            raise InputError(
                f"{instance.instance_id} {algorithm} changed stored states across repeats"
            )
    chosen = dict(results[0])
    chosen["metrics"] = dict(results[0]["metrics"])
    chosen["metrics"]["runtime_ms"] = _median(
        [result["metrics"]["runtime_ms"] for result in results]
    )
    return chosen


def _solve(algorithm: str, instance: Instance) -> dict:
    if algorithm == "label_setting":
        return label_solve(instance.graph, instance.config, use_heuristic=False)
    if algorithm == "astar":
        return label_solve(instance.graph, instance.config, use_heuristic=True)
    if algorithm == "exact":
        return exact_solve(instance.graph, instance.config)
    raise InputError(f"unknown algorithm {algorithm!r}")


def _row(
    instance: Instance,
    experiment: str,
    result: dict,
    agreement: str,
    note: str,
) -> dict[str, str]:
    return metric_row(
        experiment=experiment,
        instance_id=instance.instance_id,
        seed=instance.seed,
        nodes=instance.node_count,
        chargers=instance.charger_count,
        capacity_kwh=instance.capacity_kwh,
        soc_step_kwh=instance.soc_step_kwh,
        status=str(result["status"]),
        result=result,
        agreement=agreement,
        note=note,
    )


def _with_objective_error(
    rows: list[dict[str, str]],
    family: str,
    reference_step: float,
) -> list[dict[str, str]]:
    reference = [
        row
        for row in rows
        if row["algorithm"] == "label_setting" and math.isclose(
            float(row["soc_step_kwh"]), reference_step
        )
    ]
    if len(reference) != 1:
        raise InputError(f"{family} SOC sweep is missing its {reference_step} kWh reference")
    baseline = reference[0]
    annotated = []
    for row in rows:
        updated = dict(row)
        role = "reference" if math.isclose(float(row["soc_step_kwh"]), reference_step) else "coarse"
        extra = f"family={family}; role={role}; reference_step_kwh={format(reference_step, '.10g')}"
        if baseline["status"] == "FEASIBLE" and row["status"] == "FEASIBLE":
            error = float(row["total_time_minutes"]) - float(baseline["total_time_minutes"])
            extra = f"{extra}; objective_error_minutes={format(error, '.10g')}"
        elif row["status"] != "FEASIBLE":
            extra = f"{extra}; objective_error_minutes="
        updated["note"] = f"{updated['note']}; {extra}" if updated["note"] else extra
        annotated.append(updated)
    return annotated


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2
