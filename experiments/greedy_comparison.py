"""Optimizer versus the FRC baseline, and the real-data routes (H9).

Owned by Shannon. Experiment B compares the optimizer with the
farthest-reachable-charger baseline over charger layout, charger speed,
initial SOC, and battery capacity. F, G, and H change one of those inputs at a
time (initial SOC, charger density, charger-speed mix). I runs the H5 trip
scenarios under ``data/processed/``.

Algorithms are invoked through the shared dispatcher ``optimizer.cli.run_algorithm``
and the search is never imported. Every instance runs ``label_setting`` and
``greedy`` on the same graph and config objects. The FRC plan is a legal plan
under the same discretization, so the optimizer's total time can never exceed
it, and the optimizer must find a route whenever FRC does. A violation is
written with ``agreement=false`` and fails the run, like a label mismatch in
the algorithm experiments.

``python -m experiments.greedy_comparison`` runs B, F, G, H, and I on their own.
``python -m experiments.run_all`` runs them with the rest.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from experiments.greedy_suite import (
    B_CAPACITIES,
    B_LAYOUTS,
    B_SOC_PCT,
    B_SPEEDS,
    DENSITY_REPLICATES,
    F_REPLICATES,
    F_SOC_PCT,
    FAST_KW,
    G_COUNTS,
    G_TIERS,
    GREEDY_REPLICATES,
    H_PROFILES,
    H_REPLICATES,
    build_b_suite,
    build_f_suite,
    build_g_suite,
    build_h_suite,
)
from experiments.instances import DEFAULT_SEED, TIMING_REPEATS, Instance
from experiments.metrics import metric_row, write_csv
from experiments.scaling import quartiles
from experiments.sweeps import _collapse, _fingerprint
from optimizer.cli import run_algorithm
from optimizer.model import InputError, load_config, load_graph, validate_query

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "results"
DEFAULT_PLOTS = ROOT / "plots"
DEFAULT_SCENARIOS = ROOT / "data" / "processed"
SUMMARY_FILE = "greedy_summary.json"
FILES = {
    "B": "greedy_comparison.csv",
    "F": "initial_soc.csv",
    "G": "charger_density.csv",
    "H": "charger_speed.csv",
    "I": "real_routes.csv",
}
GRAPH_FILE = "canonical_graph.json"
CONFIG_FILE = "trip_config.json"
META_FILE = "scenario_meta.json"
TIE = 1e-9
FEASIBLE = "FEASIBLE"


@dataclass(frozen=True)
class Comparison:
    """CSV rows, the ids that broke the dominance check, and one record per instance."""

    rows: list[dict[str, str]]
    failed: tuple[str, ...]
    records: list[dict]


def experiment_b(
    seed: int = DEFAULT_SEED,
    repeats: int = TIMING_REPEATS,
    replicates: int = GREEDY_REPLICATES,
) -> Comparison:
    """Layout x speed x initial SOC x capacity, optimizer against FRC."""
    return compare_all(build_b_suite(seed, replicates), "B", repeats)


def experiment_f(
    seed: int = DEFAULT_SEED,
    repeats: int = TIMING_REPEATS,
    replicates: int = F_REPLICATES,
) -> Comparison:
    """The same layout and roads at 20%, 40%, 60%, 80%, and 100% initial SOC."""
    return compare_all(build_f_suite(seed, replicates), "F", repeats, with_route=True)


def experiment_g(
    seed: int = DEFAULT_SEED,
    repeats: int = TIMING_REPEATS,
    replicates: int = DENSITY_REPLICATES,
    counts: tuple[int, ...] = G_COUNTS,
) -> Comparison:
    """Station count from sparse to dense over matched prefixes of seeded pools."""
    return compare_all(build_g_suite(seed, replicates, counts), "G", repeats)


def experiment_h(
    seed: int = DEFAULT_SEED,
    repeats: int = TIMING_REPEATS,
    replicates: int = H_REPLICATES,
) -> Comparison:
    """Identical charger speeds against a mixed network of the same mean rate."""
    return compare_all(build_h_suite(seed, replicates), "H", repeats)


def experiment_i(
    scenarios: Path | None,
    repeats: int = TIMING_REPEATS,
) -> Comparison:
    """Run every H5 scenario under ``scenarios``. Absent data gives one ``UNAVAILABLE`` row.

    A folder needs ``canonical_graph.json`` and ``trip_config.json``. A folder
    whose files do not load becomes an ``UNAVAILABLE`` row that names the error,
    and the other folders still run. ``scenario_meta.json`` is optional and only
    adds its category, road backend, and snapshot date to the note.
    """
    if scenarios is None:
        return unavailable_comparison("No scenario directory was given. Experiment I was not run.")
    root = Path(scenarios)
    if not root.is_dir():
        return unavailable_comparison(
            f"No scenario directory at {root.name}/ (H5 output, gitignored). "
            "Generate it with the H5 scenario builder, or use the S9 runner "
            "(python -m experiments.real_data) when it is available. Experiment I was not run."
        )
    folders = sorted(
        folder
        for folder in root.iterdir()
        if (folder / GRAPH_FILE).is_file() and (folder / CONFIG_FILE).is_file()
    )
    if not folders:
        return unavailable_comparison(
            f"No scenario folder with {GRAPH_FILE} and {CONFIG_FILE} under {root.name}/. "
            "Experiment I was not run."
        )
    rows: list[dict[str, str]] = []
    failed: list[str] = []
    records: list[dict] = []
    for folder in folders:
        try:
            instance = load_scenario(folder)
        except InputError as exc:
            note = f"scenario {folder.name} did not load: {exc}"
            rows.append(unavailable_row(note, folder.name))
            continue
        pair, record, ok = compare(instance, "I", repeats, with_route=True)
        rows.extend(pair)
        records.append(record)
        if not ok:
            failed.append(instance.instance_id)
    return Comparison(rows, tuple(failed), records)


def unavailable_rows(experiment: str, note: str) -> list[dict[str, str]]:
    """The single placeholder row for an experiment that was not run."""
    algorithm = "greedy_frc" if experiment == "B" else ""
    return [unavailable_row(note, "", experiment=experiment, algorithm=algorithm)]


def compare_all(
    suite: list[Instance],
    experiment: str,
    repeats: int,
    *,
    with_route: bool = False,
) -> Comparison:
    rows: list[dict[str, str]] = []
    failed: list[str] = []
    records: list[dict] = []
    for instance in suite:
        pair, record, ok = compare(instance, experiment, repeats, with_route=with_route)
        rows.extend(pair)
        records.append(record)
        if not ok:
            failed.append(instance.instance_id)
    return Comparison(rows, tuple(failed), records)


def compare(
    instance: Instance,
    experiment: str,
    repeats: int,
    *,
    with_route: bool,
) -> tuple[list[dict[str, str]], dict, bool]:
    """Run both algorithms on one instance and return its two rows and its record."""
    if repeats < 1:
        raise InputError("timing repeats must be positive")
    before = _fingerprint(instance)
    optimum = timed("label_setting", instance, repeats)
    greedy = timed("greedy", instance, repeats)
    if _fingerprint(instance) != before:
        raise InputError(f"{instance.instance_id}: a solver changed its input")

    rates = {node["id"]: node["charge_rate_kw"] for node in instance.graph["nodes"]}
    record = report(instance, experiment, optimum, greedy, rates)
    ok = record["dominance_ok"]
    agreement = "true" if ok else "false"
    timing = f"timing=median of {repeats}" if repeats > 1 else "timing=single run"
    base = base_note(experiment, instance)
    shared = f"outcome={record['outcome']}; dominance={'ok' if ok else 'VIOLATED'}"
    notes = []
    for who, result in (("opt", optimum), ("greedy", greedy)):
        parts = [base, result_note(result, instance, rates, with_route)]
        if who == "opt":
            improvement = record["improvement_pct"]
            parts.append(f"greedy_status={greedy['status']}")
            shown = "" if improvement is None else format(improvement, ".10g")
            parts.append(f"improvement_pct={shown}")
        else:
            parts.append(f"optimizer_status={optimum['status']}")
        parts.extend((shared, timing))
        notes.append("; ".join(parts))
    rows = [
        row(instance, experiment, optimum, agreement, notes[0]),
        row(instance, experiment, greedy, agreement, notes[1]),
    ]
    return rows, record, ok


def report(
    instance: Instance,
    experiment: str,
    optimum: dict,
    greedy: dict,
    rates: dict[str, float | None],
) -> dict:
    opt_ok = optimum["status"] == FEASIBLE
    greedy_ok = greedy["status"] == FEASIBLE
    improvement = None
    dominance = True
    if greedy_ok and not opt_ok:
        dominance = False
    if opt_ok and greedy_ok:
        opt_time = float(optimum["total_time_minutes"])
        greedy_time = float(greedy["total_time_minutes"])
        if opt_time > greedy_time + TIE:
            dominance = False
        if greedy_time > 0:
            improvement = 100.0 * (greedy_time - opt_time) / greedy_time
    if opt_ok and greedy_ok:
        outcome = "both_feasible"
    elif opt_ok:
        outcome = "greedy_infeasible"
    elif greedy_ok:
        outcome = "optimizer_infeasible"
    else:
        outcome = "both_infeasible"
    return {
        "experiment": experiment,
        "instance_id": instance.instance_id,
        "meta": dict(instance.meta),
        "outcome": outcome,
        "dominance_ok": dominance,
        "improvement_pct": improvement,
        "optimizer": label(optimum, rates),
        "greedy": label(greedy, rates),
    }


def label(result: dict, rates: dict[str, float | None]) -> dict:
    feasible = result["status"] == FEASIBLE
    stops = [event["station"] for event in result["events"] if event["type"] == "charge"]
    stop_rates = [rates[station] for station in stops]
    fast = sum(1 for rate in stop_rates if rate is not None and rate >= FAST_KW)
    return {
        "status": result["status"],
        "total_time_minutes": result["total_time_minutes"] if feasible else None,
        "charging_time_minutes": result["charging_time_minutes"] if feasible else None,
        "stops": result["number_of_charging_stops"] if feasible else None,
        "states_expanded": result["metrics"]["states_expanded"],
        "route": list(result["route_node_ids"]) if feasible else [],
        "fast_stop_share": fast / len(stops) if stops else None,
    }


def base_note(experiment: str, instance: Instance) -> str:
    fields = [f"suite={experiment}"]
    fields.extend(f"{key}={value}" for key, value in instance.meta.items() if key != "count")
    return "; ".join(fields)


def result_note(
    result: dict,
    instance: Instance,
    rates: dict[str, float | None],
    with_route: bool,
) -> str:
    feasible = result["status"] == FEASIBLE
    stops = [event["station"] for event in result["events"] if event["type"] == "charge"]
    charging = result["charging_time_minutes"] if feasible else ""
    parts = [
        f"charging_minutes={charging}",
        "stop_rates_kw=" + ",".join(format(float(rates[s]), "g") for s in stops),
    ]
    if with_route:
        parts.append("route=" + ">".join(result["route_node_ids"]))
        parts.append("stops_at=" + ",".join(stops))
        parts.append("soc_profile_kwh=" + ",".join(soc_profile(result, instance)))
    return "; ".join(parts)


def soc_profile(result: dict, instance: Instance) -> list[str]:
    """SOC at the start, then after each drive and each charge event."""
    if result["status"] != FEASIBLE:
        return []
    profile = [format(float(instance.config["initial_soc_kwh"]), "g")]
    profile.extend(format(float(event["soc_after_kwh"]), "g") for event in result["events"])
    return profile


def timed(algorithm: str, instance: Instance, repeats: int) -> dict:
    results = [run_algorithm(algorithm, instance.graph, instance.config) for _ in range(repeats)]
    return _collapse(algorithm, instance, results)


def row(
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


def load_scenario(folder: Path) -> Instance:
    graph = load_graph(folder / GRAPH_FILE)
    config = load_config(folder / CONFIG_FILE)
    validate_query(graph, config)
    meta: dict = {}
    meta_path = folder / META_FILE
    if meta_path.is_file():
        try:
            found = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InputError(f"{META_FILE} is unreadable: {exc}") from exc
        if isinstance(found, dict):
            for source, name in (
                ("category", "category"),
                ("road_backend", "road_backend"),
                ("afdc_snapshot_date", "afdc_snapshot"),
            ):
                if source in found:
                    meta[name] = found[source]
    meta["edges"] = len(graph["edges"])
    return Instance(folder.name, graph, config, None, meta)


def unavailable_row(
    note: str,
    instance_id: str,
    *,
    experiment: str = "I",
    algorithm: str = "",
) -> dict[str, str]:
    return metric_row(
        experiment=experiment,
        instance_id=instance_id,
        seed=None,
        nodes=None,
        chargers=None,
        capacity_kwh=None,
        soc_step_kwh=None,
        algorithm=algorithm,
        status="UNAVAILABLE",
        note=note,
    )


def unavailable_comparison(note: str) -> Comparison:
    return Comparison([unavailable_row(note, "")], (), [])


def summarize(
    b: Comparison | None,
    f: Comparison | None,
    g: Comparison | None,
    h: Comparison | None,
    i: Comparison | None,
) -> dict:
    """Aggregate the per-instance records. Values are descriptions of this run only."""
    summary: dict = {"note": "Synthetic corridor family; see experiments/README.md."}
    if b is not None:
        summary["B"] = summarize_b(b.records)
    if f is not None:
        summary["F"] = summarize_f(f.records)
    if g is not None:
        summary["G"] = summarize_g(g.records)
    if h is not None:
        summary["H"] = summarize_h(h.records)
    if i is not None:
        summary["I"] = summarize_i(i.records)
    return summary


def summarize_b(records: list[dict]) -> dict:
    factors = {
        "layout": [name for name, _ in B_LAYOUTS],
        "speed": list(B_SPEEDS),
        "soc_pct": list(B_SOC_PCT),
        "capacity_kwh": list(B_CAPACITIES),
    }
    return {
        "instances": len(records),
        "overall": comparison_stats(records),
        "by_factor": {
            name: {str(level): comparison_stats(where(records, name, level)) for level in levels}
            for name, levels in factors.items()
        },
    }


def summarize_f(records: list[dict]) -> dict:
    by_level = {}
    for level in F_SOC_PCT:
        group = where(records, "soc_pct", level)
        by_level[str(level)] = {
            **comparison_stats(group),
            "optimizer_stops": counts([r["optimizer"]["stops"] for r in group]),
        }
    replicates = sorted({r["meta"]["replicate"] for r in records})
    distinct = []
    for replicate in replicates:
        routes = {
            tuple(r["optimizer"]["route"])
            for r in records
            if r["meta"]["replicate"] == replicate and r["optimizer"]["route"]
        }
        distinct.append(len(routes))
    return {
        "instances": len(records),
        "by_soc_pct": by_level,
        "distinct_optimizer_routes_per_replicate": distinct,
    }


def summarize_g(records: list[dict]) -> dict:
    by_count = {}
    for count in sorted({r["meta"]["stations"] for r in records}):
        by_count[str(count)] = density_stats(where(records, "stations", count))
    by_tier = {
        name: density_stats(where(records, "tier", name)) for name, _ in G_TIERS
    }
    return {"instances": len(records), "by_stations": by_count, "by_tier": by_tier}


def density_stats(group: list[dict]) -> dict:
    opt_states = [r["optimizer"]["states_expanded"] for r in group]
    return {
        **comparison_stats(group),
        "optimizer_feasible_rate": rate(group, "optimizer"),
        "greedy_feasible_rate": rate(group, "greedy"),
        "optimizer_states_expanded": spread(opt_states),
    }


def summarize_h(records: list[dict]) -> dict:
    by_speed = {}
    for profile in H_PROFILES:
        group = where(records, "speed", profile)
        by_speed[profile] = {
            **comparison_stats(group),
            "optimizer_fast_stop_share": spread(
                [r["optimizer"]["fast_stop_share"] for r in group]
            ),
            "greedy_fast_stop_share": spread([r["greedy"]["fast_stop_share"] for r in group]),
        }
    paired = {}
    for who in ("optimizer", "greedy"):
        gaps = []
        for replicate in sorted({r["meta"]["replicate"] for r in records}):
            mixed = single(records, replicate, "mixed_50_150", who)
            flat = single(records, replicate, "uniform_100", who)
            if mixed is not None and flat is not None:
                gaps.append(mixed - flat)
        paired[who] = spread(gaps)
    return {
        "instances": len(records),
        "by_speed": by_speed,
        "mixed_minus_uniform_100_minutes": paired,
    }


def single(records: list[dict], replicate: int, speed: str, who: str) -> float | None:
    for record in records:
        meta = record["meta"]
        if meta["replicate"] == replicate and meta["speed"] == speed:
            return record[who]["total_time_minutes"]
    return None


def summarize_i(records: list[dict]) -> dict:
    scenarios = []
    for record in records:
        scenarios.append(
            {
                "scenario": record["instance_id"],
                "category": record["meta"].get("category"),
                "outcome": record["outcome"],
                "improvement_pct": record["improvement_pct"],
                "optimizer": {k: v for k, v in record["optimizer"].items() if k != "route"},
                "greedy": {k: v for k, v in record["greedy"].items() if k != "route"},
            }
        )
    return {"scenarios": scenarios, "outcomes": counts([r["outcome"] for r in records])}


def comparison_stats(group: list[dict]) -> dict:
    both = [r for r in group if r["outcome"] == "both_feasible"]
    improvements = [r["improvement_pct"] for r in both if r["improvement_pct"] is not None]
    greedy_total = sum(r["greedy"]["total_time_minutes"] for r in both)
    optimum_total = sum(r["optimizer"]["total_time_minutes"] for r in both)
    pooled = 100.0 * (greedy_total - optimum_total) / greedy_total if greedy_total > 0 else None
    return {
        "instances": len(group),
        "outcomes": counts([r["outcome"] for r in group]),
        "improvement_pct": spread(improvements),
        "pooled_improvement_pct": pooled,
        "optimizer_strictly_faster": sum(1 for r in both if (r["improvement_pct"] or 0) > TIE),
        "total_time_minutes": {
            "optimizer": spread([r["optimizer"]["total_time_minutes"] for r in group]),
            "greedy": spread([r["greedy"]["total_time_minutes"] for r in group]),
        },
        "charging_minutes": {
            "optimizer": spread([r["optimizer"]["charging_time_minutes"] for r in group]),
            "greedy": spread([r["greedy"]["charging_time_minutes"] for r in group]),
        },
        "stops": {
            "optimizer": spread([r["optimizer"]["stops"] for r in group]),
            "greedy": spread([r["greedy"]["stops"] for r in group]),
        },
    }


def where(records: list[dict], key: str, level: object) -> list[dict]:
    return [r for r in records if r["meta"].get(key) == level]


def rate(group: list[dict], who: str) -> float | None:
    if not group:
        return None
    return sum(1 for r in group if r[who]["status"] == FEASIBLE) / len(group)


def counts(values: list) -> dict[str, int]:
    return {str(key): count for key, count in sorted(Counter(values).items(), key=str)}


def spread(values: list[float | int | None]) -> dict | None:
    """Quartile summary of the numbers in ``values``. ``None`` entries are dropped."""
    numbers = [float(v) for v in values if v is not None]
    if not numbers:
        return None
    low, middle, high = quartiles(numbers)
    return {
        "n": len(numbers),
        "min": min(numbers),
        "q1": low,
        "median": middle,
        "q3": high,
        "max": max(numbers),
        "mean": statistics.fmean(numbers),
    }


def format_report(summary: dict) -> str:
    """A few headline lines from ``summarize``, for the console."""
    lines = []
    block = summary.get("B")
    if block is not None:
        overall = block["overall"]
        spread = overall["improvement_pct"]
        pooled = overall["pooled_improvement_pct"]
        median = "n/a" if spread is None else format(spread["median"], ".1f")
        pooled_text = "n/a" if pooled is None else format(pooled, ".1f")
        lines.append(
            f"B optimizer vs FRC: {block['instances']} instances, outcomes {overall['outcomes']}, "
            f"median improvement {median}% (pooled {pooled_text}%)"
        )
    if "G" in summary:
        rates = {
            count: stats["optimizer_feasible_rate"]
            for count, stats in summary["G"]["by_stations"].items()
        }
        lines.append(f"G optimizer feasible rate by station count: {rates}")
    if "I" in summary:
        lines.append(f"I real routes: {summary['I']['outcomes']}")
    return "\n".join(lines)


@dataclass(frozen=True)
class Results:
    """The five comparisons of one run."""

    b: Comparison
    f: Comparison
    g: Comparison
    h: Comparison
    i: Comparison

    @property
    def files(self) -> dict[str, list[dict[str, str]]]:
        return {
            FILES["B"]: self.b.rows,
            FILES["F"]: self.f.rows,
            FILES["G"]: self.g.rows,
            FILES["H"]: self.h.rows,
            FILES["I"]: self.i.rows,
        }

    @property
    def failed(self) -> tuple[str, ...]:
        return self.b.failed + self.f.failed + self.g.failed + self.h.failed + self.i.failed


def run(
    output_dir: Path = DEFAULT_OUTPUT,
    *,
    seed: int = DEFAULT_SEED,
    repeats: int = TIMING_REPEATS,
    greedy_replicates: int = GREEDY_REPLICATES,
    density_replicates: int = DENSITY_REPLICATES,
    speed_replicates: int = H_REPLICATES,
    scenarios: Path | None = DEFAULT_SCENARIOS,
    plots_dir: Path | None = None,
) -> Results:
    """Run Experiments B, F, G, H, and I, and write their CSVs and summary.

    ``plots_dir`` set also draws ``greedy_vs_optimizer.png`` and
    ``feasibility_vs_charger_density.png``.
    """
    from experiments.plots import write_h9_plots

    results = Results(
        experiment_b(seed, repeats, greedy_replicates),
        experiment_f(seed, repeats, greedy_replicates),
        experiment_g(seed, repeats, density_replicates),
        experiment_h(seed, repeats, speed_replicates),
        experiment_i(scenarios, repeats),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, rows in results.files.items():
        write_csv(output_dir / filename, rows)
    summary = summarize(results.b, results.f, results.g, results.h, results.i)
    (output_dir / SUMMARY_FILE).write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if plots_dir is not None:
        write_h9_plots(results.files, plots_dir)
    return results


def main(argv: list[str] | None = None) -> int:
    """Run B, F, G, H, and I. Exit 1 when the optimizer is worse than FRC anywhere."""
    parser = argparse.ArgumentParser(description="Run Experiments B, F, G, H, and I.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT, help="CSV directory")
    parser.add_argument("--plots", type=Path, default=DEFAULT_PLOTS, help="PNG directory")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--repeats", type=int, default=TIMING_REPEATS)
    parser.add_argument("--greedy-replicates", type=int, default=GREEDY_REPLICATES)
    parser.add_argument("--density-replicates", type=int, default=DENSITY_REPLICATES)
    parser.add_argument("--speed-replicates", type=int, default=H_REPLICATES)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    args = parser.parse_args(argv)
    try:
        results = run(
            args.out,
            seed=args.seed,
            repeats=args.repeats,
            greedy_replicates=args.greedy_replicates,
            density_replicates=args.density_replicates,
            speed_replicates=args.speed_replicates,
            scenarios=args.scenarios,
            plots_dir=args.plots,
        )
    except (InputError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(format_report(summarize(results.b, results.f, results.g, results.h, results.i)))
    if results.failed:
        print(f"error: optimizer worse than FRC on {', '.join(results.failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
