"""Run the S6 benchmarks and the S7 sweeps, and write Section 23 CSV files.

``make experiments`` calls ``python -m experiments.run_all``. Seeds, bounds,
and the sweeps that are intentionally smaller than the PRD examples are
documented in ``experiments/README.md``.

Label-setting and A* run on the Detroit–Chicago sample, the S6 tiny graphs,
and Experiments C, D, E, and J. Experiment J builds its own instances and
shares no rows with D or C. Its rows and the S6 rows both land in
``astar_comparison.csv`` and are told apart by the note (``suite=J`` or
``suite=S6``). Exact search runs on the S6 tiny graphs and
on Experiment A, and only inside the S6 bound. The greedy baseline, Experiments
F through I, and real-data routes stay ``UNAVAILABLE``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from experiments.astar_suite import SUITE_REPLICATES
from experiments.astar_summary import FAILURES_FILE as ASTAR_FAILURES_FILE
from experiments.astar_summary import SUMMARY_FILE as ASTAR_SUMMARY_FILE
from experiments.astar_summary import format_report as format_astar_report
from experiments.correctness import SUMMARY_FILE, format_report, write_summary
from experiments.instances import (
    CAPACITIES,
    CHARGER_COUNTS,
    CORRECTNESS_COUNT,
    DEFAULT_SEED,
    DEFAULT_TINY,
    SCALING_REPLICATES,
    TIMING_REPEATS,
    Instance,
    exact_block_reason,
    load_sample,
    tiny_instances,
)
from experiments.metrics import OUTPUT_FILES, metric_row, objective, write_csv
from experiments.plots import MEASURED_PLOTS, write_plots
from experiments.scaling import SUMMARY_FILE as SCALING_SUMMARY_FILE
from experiments.scaling import summarize_scaling
from experiments.sweeps import (
    experiment_a_report,
    experiment_c,
    experiment_d,
    experiment_e,
    experiment_j_report,
)
from optimizer.battery_state_optimizer import solve as label_solve
from optimizer.exact_solver import solve as exact_solve
from optimizer.model import InputError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "results"
DEFAULT_PLOTS = ROOT / "plots"

_PENDING = (
    (
        "greedy_comparison.csv",
        "B",
        "greedy_frc",
        "The FRC baseline is not implemented (H4). Experiment B was not run.",
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

# Used only by ``--no-sweeps``. The default run writes measured rows instead.
_SWEEP_PLACEHOLDERS = (
    (
        "soc_resolution.csv",
        "C",
        "",
        "SOC sweep skipped by --no-sweeps.",
    ),
    (
        "charger_scaling.csv",
        "D",
        "",
        "Charger sweep skipped by --no-sweeps.",
    ),
    (
        "battery_capacity.csv",
        "E",
        "",
        "Capacity sweep skipped by --no-sweeps.",
    ),
)


@dataclass(frozen=True)
class RunSummary:
    """CSV rows from the solvers that ran, plus any label mismatches.

    ``experiment_a`` is the supplementary summary written to
    ``correctness_summary.json``. It is ``None`` when ``--correctness 0``.
    ``experiment_j`` is the content of ``astar_summary.json``. It is ``None``
    under ``--no-sweeps``.
    """

    rows: list[dict[str, str]]
    disagreements: tuple[str, ...]
    experiment_a: dict | None = None
    experiment_j: dict | None = None


def main(argv: list[str] | None = None) -> int:
    """Write the benchmark CSVs. Exit 1 when a completed comparison disagrees."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.tiny < 1:
        print("error: --tiny must be positive", file=sys.stderr)
        return 1
    if args.correctness < 0:
        print("error: --correctness must be non-negative", file=sys.stderr)
        return 1
    if args.repeats < 1:
        print("error: --repeats must be positive", file=sys.stderr)
        return 1
    if args.scaling_replicates < 1:
        print("error: --scaling-replicates must be positive", file=sys.stderr)
        return 1
    if args.astar_replicates < 1:
        print("error: --astar-replicates must be positive", file=sys.stderr)
        return 1
    try:
        summary = run(
            args.out,
            seed=args.seed,
            tiny_count=args.tiny,
            correctness_count=args.correctness,
            sweeps=not args.no_sweeps,
            timing_repeats=args.repeats,
            plots_dir=args.plots,
            charger_counts=args.charger_counts,
            scaling_replicates=args.scaling_replicates,
            astar_replicates=args.astar_replicates,
        )
    except (InputError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {len(OUTPUT_FILES)} CSV files to {args.out}")
    if summary.experiment_a is not None:
        print(f"wrote {SUMMARY_FILE} to {args.out}")
        print(format_report(summary.experiment_a))
    if summary.experiment_j is not None:
        print(f"wrote {ASTAR_SUMMARY_FILE} to {args.out}")
        print(format_astar_report(summary.experiment_j))
    if not args.no_sweeps:
        print(f"wrote {len(MEASURED_PLOTS)} plots to {args.plots}")
    if summary.disagreements:
        joined = ", ".join(summary.disagreements)
        print(f"error: mismatch on {joined}", file=sys.stderr)
        return 1
    return 0


def run(
    output_dir: Path,
    *,
    seed: int = DEFAULT_SEED,
    tiny_count: int = DEFAULT_TINY,
    correctness_count: int = CORRECTNESS_COUNT,
    sweeps: bool = True,
    timing_repeats: int = TIMING_REPEATS,
    plots_dir: Path | None = None,
    charger_counts: tuple[int, ...] = CHARGER_COUNTS,
    capacities: tuple[int, ...] = CAPACITIES,
    scaling_replicates: int = SCALING_REPLICATES,
    astar_replicates: int = SUITE_REPLICATES,
) -> RunSummary:
    """Run the available solvers and write every Section 23 CSV.

    A run with sweeps also writes ``charger_scaling_summary.json``, the
    per-count aggregate of Experiment D, and ``astar_summary.json``, the
    aggregate of the dedicated Experiment J benchmark. If any J pair disagrees,
    ``astar_failures.json`` holds the graph and config of each such instance.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    sample = load_sample()
    tiny = tiny_instances(seed, tiny_count)
    disagreements: list[str] = []

    sample_pair, _, sample_ok = _benchmark(sample, "J", run_exact=False)
    if not sample_ok:
        disagreements.append(sample.instance_id)
    sample_exact = _exact(sample, "A")

    correctness: list[dict[str, str]] = [sample_exact]
    astar_rows = [_with_note(row, "suite=S6; set=sample") for row in sample_pair]
    for instance in tiny:
        pair, exact_row, ok = _benchmark(instance, "A", run_exact=True)
        correctness.extend(pair)
        if exact_row is not None:
            correctness.append(exact_row)
        astar_rows.extend(_with_note(_retarget(row), "suite=S6; set=tiny") for row in pair)
        if not ok:
            disagreements.append(instance.instance_id)

    measured: dict[str, list[dict[str, str]]] = {}
    astar_summary: dict | None = None
    astar_failures: list[dict] = []
    a_summary: dict | None = None
    a_records: list[dict] = []
    if correctness_count:
        report = experiment_a_report(seed, correctness_count)
        correctness.extend(report.rows)
        disagreements.extend(report.failed)
        a_summary = report.summary
        a_records = report.records
    if sweeps:
        soc_rows, soc_failed = experiment_c(timing_repeats)
        scale_rows, scale_failed = experiment_d(
            timing_repeats,
            charger_counts,
            seed=seed,
            replicates=scaling_replicates,
        )
        capacity_rows, capacity_failed = experiment_e(timing_repeats, capacities)
        astar = experiment_j_report(timing_repeats, seed=seed, replicates=astar_replicates)
        disagreements.extend(soc_failed)
        disagreements.extend(scale_failed)
        disagreements.extend(capacity_failed)
        disagreements.extend(astar.failed)
        measured["soc_resolution.csv"] = soc_rows
        measured["charger_scaling.csv"] = scale_rows
        measured["battery_capacity.csv"] = capacity_rows
        astar_rows.extend(astar.rows)
        astar_summary = astar.summary
        astar_failures = astar.failures
    else:
        for filename, experiment, algorithm, note in _SWEEP_PLACEHOLDERS:
            measured[filename] = [_unavailable(experiment, algorithm, note, seed)]

    write_csv(output_dir / "correctness.csv", correctness)
    if a_summary is not None:
        write_summary(output_dir / SUMMARY_FILE, a_summary, a_records)
    else:
        (output_dir / SUMMARY_FILE).unlink(missing_ok=True)  # skipped: no stale summary
    write_csv(output_dir / "astar_comparison.csv", astar_rows)
    for filename, rows in measured.items():
        write_csv(output_dir / filename, rows)
    if sweeps:
        summary_text = json.dumps(
            summarize_scaling(measured["charger_scaling.csv"]), indent=2, sort_keys=True
        )
        (output_dir / SCALING_SUMMARY_FILE).write_text(summary_text + "\n", encoding="utf-8")
    else:
        (output_dir / SCALING_SUMMARY_FILE).unlink(missing_ok=True)
    if astar_summary is not None:
        astar_text = json.dumps(astar_summary, indent=2, sort_keys=True)
        (output_dir / ASTAR_SUMMARY_FILE).write_text(astar_text + "\n", encoding="utf-8")
    else:
        (output_dir / ASTAR_SUMMARY_FILE).unlink(missing_ok=True)
    failures_path = output_dir / ASTAR_FAILURES_FILE
    if astar_failures:
        failures_path.write_text(json.dumps(astar_failures, indent=2) + "\n", encoding="utf-8")
    elif failures_path.exists():
        failures_path.unlink()  # a rerun that passes must not leave an old failure behind
    for filename, experiment, algorithm, note in _PENDING:
        write_csv(
            output_dir / filename,
            [_unavailable(experiment, algorithm, note, seed)],
        )
    if sweeps:
        charts = dict(measured)
        charts["astar_comparison.csv"] = astar_rows
        write_plots(charts, DEFAULT_PLOTS if plots_dir is None else plots_dir)
    return RunSummary(
        sample_pair + correctness,
        tuple(disagreements),
        a_summary,
        astar_summary,
    )


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


def _with_note(row: dict[str, str], note: str) -> dict[str, str]:
    """Copy a row and set its note. The S6 rows share a file with Experiment J's."""
    tagged = dict(row)
    tagged["note"] = f"{row['note']}; {note}" if row["note"] else note
    return tagged


def _counts(text: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in text.split(",") if part.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a comma-separated integer list: {text!r}") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run ChargePlan benchmarks and write results/.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT, help="CSV directory")
    parser.add_argument("--plots", type=Path, default=DEFAULT_PLOTS, help="PNG directory")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="seed for the random graphs")
    parser.add_argument("--tiny", type=int, default=DEFAULT_TINY, help="S6 tiny-graph count")
    parser.add_argument(
        "--correctness",
        type=int,
        default=CORRECTNESS_COUNT,
        help="Experiment A graph count; 0 skips that suite",
    )
    parser.add_argument("--repeats", type=int, default=TIMING_REPEATS, help="timing repeats")
    parser.add_argument(
        "--charger-counts",
        type=_counts,
        default=CHARGER_COUNTS,
        help="Experiment D station counts, comma separated (default 10,25,50,100; max 200)",
    )
    parser.add_argument(
        "--scaling-replicates",
        type=int,
        default=SCALING_REPLICATES,
        help="Experiment D station pools per count",
    )
    parser.add_argument(
        "--astar-replicates",
        type=int,
        default=SUITE_REPLICATES,
        help="Experiment J replicates per factor combination (36 combinations)",
    )
    parser.add_argument(
        "--no-sweeps",
        action="store_true",
        help="skip Experiments C, D, E, and J and leave those CSV files unmeasured",
    )
    return parser


if __name__ == "__main__":
    sys.exit(main())
