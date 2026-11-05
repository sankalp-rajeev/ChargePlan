"""Run the S6 benchmarks and write Section 23 CSV files under ``results/``.

``make experiments`` calls ``python -m experiments.run_all``. The run is
fixed by the seed and the tiny-instance count documented in
``experiments/README.md``.

Label-setting and A* run on the Detroit–Chicago sample and on every tiny
graph. The exact solver runs only when ``exact_block_reason`` is ``None``.
The greedy baseline and the real-data pipeline are not available: their CSV
files record ``UNAVAILABLE`` and contain no invented measurements. The SOC,
scaling, and battery sweeps are Experiments C, D, and E (S7) and are recorded
the same way.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from experiments.instances import (
    DEFAULT_SEED,
    DEFAULT_TINY,
    Instance,
    exact_block_reason,
    load_sample,
    tiny_instances,
)
from experiments.metrics import OUTPUT_FILES, metric_row, objective, write_csv
from optimizer.battery_state_optimizer import solve as label_solve
from optimizer.exact_solver import solve as exact_solve
from optimizer.model import InputError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "results"

_UNAVAILABLE = (
    (
        "greedy_comparison.csv",
        "B",
        "greedy_frc",
        "The FRC baseline is not implemented (H4). Experiment B was not run.",
    ),
    (
        "soc_resolution.csv",
        "C",
        "",
        "The SOC-resolution sweep is Experiment C (S7). This runner does not vary the step.",
    ),
    (
        "charger_scaling.csv",
        "D",
        "",
        "The charger-count sweep is Experiment D (S7). This runner does not grow the graph.",
    ),
    (
        "battery_capacity.csv",
        "E",
        "",
        "The battery-capacity sweep is Experiment E (S7). This runner does not vary capacity.",
    ),
    (
        "initial_soc.csv",
        "F",
        "",
        "The initial-SOC sweep is Experiment F (H9). No comparison was run.",
    ),
    (
        "charger_density.csv",
        "G",
        "",
        "The charger-density sweep is Experiment G (H9). No comparison was run.",
    ),
    (
        "charger_speed.csv",
        "H",
        "",
        "The charger-speed sweep is Experiment H (H9). No comparison was run.",
    ),
    (
        "real_routes.csv",
        "I",
        "",
        "No real-data benchmark is configured (S9, H5). Experiment I was not run.",
    ),
)


@dataclass(frozen=True)
class RunSummary:
    """CSV rows from the solvers that ran, plus any label mismatches."""

    rows: list[dict[str, str]]
    disagreements: tuple[str, ...]


def main(argv: list[str] | None = None) -> int:
    """Write the benchmark CSVs. Exit 1 when a completed comparison disagrees."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.tiny < 1:
        print("error: --tiny must be positive", file=sys.stderr)
        return 1
    try:
        summary = run(args.out, seed=args.seed, tiny_count=args.tiny)
    except (InputError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {len(OUTPUT_FILES)} CSV files to {args.out}")
    if summary.disagreements:
        joined = ", ".join(summary.disagreements)
        print(f"error: label mismatch on {joined}", file=sys.stderr)
        return 1
    return 0


def run(
    output_dir: Path,
    *,
    seed: int = DEFAULT_SEED,
    tiny_count: int = DEFAULT_TINY,
) -> RunSummary:
    """Run the available solvers and write every Section 23 CSV."""
    output_dir.mkdir(parents=True, exist_ok=True)
    sample = load_sample()
    tiny = tiny_instances(seed, tiny_count)
    disagreements: list[str] = []

    sample_pair, _, sample_ok = _benchmark(sample, "J", run_exact=False)
    if not sample_ok:
        disagreements.append(sample.instance_id)
    sample_exact = _exact(sample, "A")

    correctness: list[dict[str, str]] = [sample_exact]
    astar_rows = list(sample_pair)
    for instance in tiny:
        pair, exact_row, ok = _benchmark(instance, "A", run_exact=True)
        correctness.extend(pair)
        if exact_row is not None:
            correctness.append(exact_row)
        astar_rows.extend(_retarget(row) for row in pair)
        if not ok:
            disagreements.append(instance.instance_id)

    write_csv(output_dir / "correctness.csv", correctness)
    write_csv(output_dir / "astar_comparison.csv", astar_rows)
    for filename, experiment, algorithm, note in _UNAVAILABLE:
        write_csv(
            output_dir / filename,
            [_unavailable(experiment, algorithm, note, seed)],
        )
    return RunSummary(sample_pair + correctness, tuple(disagreements))


def _benchmark(
    instance: Instance,
    experiment: str,
    *,
    run_exact: bool,
) -> tuple[list[dict[str, str]], dict[str, str] | None, bool]:
    """Run label-setting and A*, and exact when the instance is inside the bound."""
    plain = _solve("label_setting", instance.graph, instance.config)
    guided = _solve("astar", instance.graph, instance.config)
    labels_match = objective(plain) == objective(guided)
    exact_row: dict[str, str] | None = None
    exact_matches = True
    if run_exact:
        reason = exact_block_reason(instance.graph, instance.config)
        if reason is None:
            exact_result = _solve("exact", instance.graph, instance.config)
            exact_matches = objective(exact_result) == objective(plain)
            agreement = "true" if labels_match and exact_matches else "false"
            exact_row = _measured(instance, experiment, exact_result, agreement)
        else:
            exact_row = _skipped(instance, experiment, reason)
            agreement = "true" if labels_match else "false"
    else:
        agreement = "true" if labels_match else "false"
    pair = [
        _measured(instance, experiment, plain, agreement),
        _measured(instance, experiment, guided, agreement),
    ]
    return pair, exact_row, labels_match and exact_matches


def _exact(instance: Instance, experiment: str) -> dict[str, str]:
    reason = exact_block_reason(instance.graph, instance.config)
    if reason is None:
        result = _solve("exact", instance.graph, instance.config)
        return _measured(instance, experiment, result, "")
    return _skipped(instance, experiment, reason)


def _solve(algorithm: str, graph: dict, config: dict) -> dict:
    if algorithm == "label_setting":
        return label_solve(graph, config, use_heuristic=False)
    if algorithm == "astar":
        return label_solve(graph, config, use_heuristic=True)
    if algorithm == "exact":
        return exact_solve(graph, config)
    if algorithm == "greedy":
        raise InputError("the FRC baseline is not implemented (H4)")
    raise InputError(f"unknown algorithm {algorithm!r}")


def _measured(instance: Instance, experiment: str, result: dict, agreement: str) -> dict[str, str]:
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
    )


def _skipped(instance: Instance, experiment: str, reason: str) -> dict[str, str]:
    return metric_row(
        experiment=experiment,
        instance_id=instance.instance_id,
        seed=instance.seed,
        nodes=instance.node_count,
        chargers=instance.charger_count,
        capacity_kwh=instance.capacity_kwh,
        soc_step_kwh=instance.soc_step_kwh,
        algorithm="exact",
        status="SKIPPED",
        note=reason,
    )


def _unavailable(experiment: str, algorithm: str, note: str, seed: int) -> dict[str, str]:
    return metric_row(
        experiment=experiment,
        instance_id="",
        seed=seed,
        nodes=None,
        chargers=None,
        capacity_kwh=None,
        soc_step_kwh=None,
        algorithm=algorithm,
        status="UNAVAILABLE",
        note=note,
    )


def _retarget(row: dict[str, str]) -> dict[str, str]:
    retargeted = dict(row)
    retargeted["experiment"] = "J"
    return retargeted


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run ChargePlan benchmarks and write results/.")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="directory for the CSV files",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="seed for the tiny graphs")
    parser.add_argument("--tiny", type=int, default=DEFAULT_TINY, help="number of tiny graphs")
    return parser


if __name__ == "__main__":
    sys.exit(main())
