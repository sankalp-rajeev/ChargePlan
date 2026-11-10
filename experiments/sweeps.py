"""Experiments A, C, D, E, and J.

Each sweep changes one input. Exact search is used only by Experiment A, and
only on graphs that already satisfy the S6 bound. Timing rows store the median
of repeated solves. States must match across those repeats; if they do not,
the run stops instead of publishing a mixed measurement. Experiment J has its
own instances (``experiments.astar_suite``). It crosses four factors and does
not reuse the rows of any other experiment.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from experiments.astar_suite import SUITE_REPLICATES, build_astar_suite
from experiments.astar_summary import SUITE_NOTE, reduction_pct, summarize_astar
from experiments.correctness import ALGORITHMS, compare, failed_ids, summarize
from experiments.instances import (
    CAPACITIES,
    CHARGER_COUNTS,
    DEFAULT_SEED,
    SCALING_REPLICATES,
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


@dataclass(frozen=True)
class ExperimentA:
    """CSV rows, failing instance ids, and the supplementary summary for Experiment A."""

    rows: list[dict[str, str]]
    failed: tuple[str, ...]
    records: list[dict]
    summary: dict


def experiment_a(seed: int, count: int) -> tuple[list[dict[str, str]], tuple[str, ...]]:
    """Compare label-setting, A*, and exact on ``count`` generated graphs."""
    report = experiment_a_report(seed, count)
    return report.rows, report.failed


def experiment_a_report(seed: int, count: int) -> ExperimentA:
    """Run Experiment A and keep the per-instance comparison records.

    A mismatch between any two solvers, or between the exact solver and the
    energy-only certificate, puts the instance id in ``failed``. Nothing is
    skipped: every instance is compared on feasibility and the full
    ``(time, stops, distance)`` label.
    """
    rows: list[dict[str, str]] = []
    records: list[dict] = []
    for instance in correctness_instances(seed, count):
        results = {name: _solve(name, instance) for name in ALGORITHMS}
        record = compare(instance, results)
        ok = not record["mismatches"] and not record["certificate_conflict"]
        rows.extend(_a_rows(instance, results, ok))
        records.append(record)
    return ExperimentA(
        rows=rows,
        failed=failed_ids(records),
        records=records,
        summary=summarize(seed, records),
    )


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
    *,
    seed: int = DEFAULT_SEED,
    replicates: int = SCALING_REPLICATES,
) -> tuple[list[dict[str, str]], tuple[str, ...]]:
    """Sweep the candidate-charger count over matched subsets of seeded pools.

    Every replicate is one pool, and each count is a prefix of it. Label-setting
    and A* run on the same instance, so they see the same graph and config.
    ``min_stops`` is the energy-only certificate, a lower bound on the stops any
    route needs. ``summarize_scaling`` aggregates these rows per count.
    """
    rows: list[dict[str, str]] = []
    failed: list[str] = []
    for instance in scaling_instances(counts, seed=seed, replicates=replicates):
        note = (
            f"replicate={instance.meta['replicate']}; edges={instance.meta['edges']}; "
            f"capacity_steps={instance.meta['capacity_steps']}; "
            f"fast_share={instance.meta['fast_share']}; "
            f"min_stops={_stops(instance.meta['min_stops'])}"
        )
        pair, ok = _pair(instance, "D", repeats, note=note)
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


@dataclass(frozen=True)
class ExperimentJ:
    """CSV rows, failing instance ids, reproduction records, and the summary for Experiment J."""

    rows: list[dict[str, str]]
    failed: tuple[str, ...]
    failures: list[dict]
    summary: dict


def experiment_j(
    repeats: int = TIMING_REPEATS,
    *,
    seed: int = DEFAULT_SEED,
    replicates: int = SUITE_REPLICATES,
) -> tuple[list[dict[str, str]], tuple[str, ...]]:
    """Run the dedicated A* benchmark. Return its rows and the ids that disagreed."""
    report = experiment_j_report(repeats, seed=seed, replicates=replicates)
    return report.rows, report.failed


def experiment_j_report(
    repeats: int = TIMING_REPEATS,
    *,
    seed: int = DEFAULT_SEED,
    replicates: int = SUITE_REPLICATES,
    instances: list[Instance] | None = None,
) -> ExperimentJ:
    """Compare A* with label-setting on the instances built by ``astar_suite``.

    Both algorithms get the same graph and config objects, and their runs are
    interleaved so neither always runs on a colder or warmer interpreter. The
    two results must agree on status, time, stops, and distance. A pair that
    does not agree is written with ``agreement=false``, listed in ``failed``, and
    described in ``failures`` with the graph and config needed to reproduce it.
    Nothing is dropped. A* expanding more states than label-setting is recorded
    as a negative reduction. It is not an error.

    ``instances`` replaces the default benchmark for tests.
    """
    suite = build_astar_suite(seed, replicates) if instances is None else instances
    rows: list[dict[str, str]] = []
    failed: list[str] = []
    failures: list[dict] = []
    for instance in suite:
        before = _fingerprint(instance)
        plain, guided = _timed_pair(instance, repeats)
        if _fingerprint(instance) != before:
            raise InputError(f"{instance.instance_id}: a solver changed its input")
        ok = objective(plain) == objective(guided)
        agreement = "true" if ok else "false"
        reduction = reduction_pct(
            plain["metrics"]["states_expanded"], guided["metrics"]["states_expanded"]
        )
        meta = instance.meta
        note = (
            f"{SUITE_NOTE}; geometry={meta['geometry']}; size={meta['size']}; "
            f"speed={meta['speed']}; soc_step={format(meta['soc_step'], 'g')}; "
            f"replicate={meta['replicate']}; edges={meta['edges']}; "
            f"capacity_steps={meta['capacity_steps']}; straight_miles={meta['straight_miles']}; "
            f"h_origin_minutes={meta['h_origin_minutes']}; "
            f"min_stops={_stops(meta['min_stops'])}"
        )
        timing = f"timing=median of {repeats}" if repeats > 1 else "timing=single run"
        guided_note = (
            f"{note}; {timing}; states_reduction_pct="
            f"{'' if reduction is None else format(reduction, '.10g')}"
        )
        rows.append(_row(instance, "J", plain, agreement, f"{note}; {timing}"))
        rows.append(_row(instance, "J", guided, agreement, guided_note))
        if not ok:
            failed.append(instance.instance_id)
            failures.append(_failure(instance, plain, guided, seed))
    return ExperimentJ(
        rows=rows,
        failed=tuple(failed),
        failures=failures,
        summary=summarize_astar(rows),
    )


def _a_rows(instance: Instance, results: dict[str, dict], ok: bool) -> list[dict[str, str]]:
    agreement = "true" if ok else "false"
    note = "suite=A; timing=single run"
    category = instance.meta.get("category")
    if category:
        note = f"{note}; category={category}; variant={instance.meta.get('variant', '')}"
    return [_row(instance, "A", results[name], agreement, note) for name in ALGORITHMS]


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


def _timed_pair(instance: Instance, repeats: int) -> tuple[dict, dict]:
    """Time label-setting and A* with the repeats interleaved, alternating who goes first."""
    if repeats < 1:
        raise InputError("timing repeats must be positive")
    plain_runs: list[dict] = []
    guided_runs: list[dict] = []
    for index in range(repeats):
        if index % 2 == 0:
            plain_runs.append(_solve("label_setting", instance))
            guided_runs.append(_solve("astar", instance))
        else:
            guided_runs.append(_solve("astar", instance))
            plain_runs.append(_solve("label_setting", instance))
    return (
        _collapse("label_setting", instance, plain_runs),
        _collapse("astar", instance, guided_runs),
    )


def _fingerprint(instance: Instance) -> str:
    payload = json.dumps([instance.graph, instance.config], sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _failure(instance: Instance, plain: dict, guided: dict, seed: int) -> dict:
    """Everything needed to reproduce a label disagreement."""
    return {
        "instance_id": instance.instance_id,
        "seed": seed,
        "factors": {
            key: instance.meta.get(key)
            for key in ("geometry", "size", "speed", "soc_step", "replicate")
        },
        "label_setting": _label(plain),
        "astar": _label(guided),
        "config": instance.config,
        "graph": instance.graph,
    }


def _label(result: dict) -> dict:
    return {
        "status": result["status"],
        "total_time_minutes": result["total_time_minutes"],
        "number_of_charging_stops": result["number_of_charging_stops"],
        "total_distance_miles": result["total_distance_miles"],
        "states_expanded": result["metrics"]["states_expanded"],
    }


def _stops(minimum: int | None) -> str:
    """The certificate minimum, or ``none`` when no energy-feasible chain exists."""
    return "none" if minimum is None else str(minimum)


def _timed(algorithm: str, instance: Instance, repeats: int) -> dict:
    if repeats < 1:
        raise InputError("timing repeats must be positive")
    return _collapse(algorithm, instance, [_solve(algorithm, instance) for _ in range(repeats)])


def _collapse(algorithm: str, instance: Instance, results: list[dict]) -> dict:
    """Check that repeated solves agree, then keep one result with the median runtime."""
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
