"""S9 real-data runner: scenarios in, verified ``algorithm_result.json`` files out.

Input layout (written by H5, ``data_pipeline.scenarios``)::

    <scenarios_root>/<scenario_id>/canonical_graph.json
                                   trip_config.json
                                   scenario_meta.json   (optional fields)

For each scenario, in sorted order, this module loads and validates the graph
and config with ``optimizer.model``, runs label-setting, A*, and greedy through
``optimizer.cli.run_algorithm``, and runs the exact solver only when
``experiments.instances.exact_block_reason`` allows it (otherwise the exact row
is ``SKIPPED`` and the solver is not called). It then cross-checks the results
and, only if every check passes, writes::

    <out>/<scenario_id>/<algorithm>.json   (schema-validated)
    <out>/<scenario_id>/run_manifest.json
    <out>/real_routes.csv                  (Experiment "I", one row per algorithm)

A scenario that fails any check writes a ``run_manifest.json`` with
``"status": "FAILED"`` and the errors, removes stale ``<algorithm>.json`` files
from an earlier run so the visualizer cannot read them, contributes no CSV
rows, and makes the process exit 1.

Run it with::

    python -m experiments.real_data --scenarios data/processed --out results/real
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from experiments.instances import exact_block_reason
from experiments.metrics import metric_row, read_csv, write_csv
from experiments.plan_check import check_plan
from optimizer.cli import run_algorithm
from optimizer.model import InputError, load_config, load_graph, validate_query, write_result

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIOS = ROOT / "data" / "processed"
DEFAULT_OUT = ROOT / "results" / "real"
DEFAULT_REPEATS = 3
EXPERIMENT = "I"
ALGORITHMS = ("label_setting", "astar", "greedy", "exact")
GRAPH_FILE = "canonical_graph.json"
CONFIG_FILE = "trip_config.json"
META_FILE = "scenario_meta.json"
MANIFEST_FILE = "run_manifest.json"
CSV_FILE = "real_routes.csv"


class ScenarioError(Exception):
    """One scenario cannot be run or verified. The message names the problem."""


@dataclass
class Inputs:
    graph: dict
    config: dict
    meta: dict | None
    graph_sha256: str
    config_sha256: str


@dataclass
class Run:
    """One algorithm's result, with the median runtime over the repeats."""

    result: dict
    runtimes_ms: list[float]


@dataclass
class ScenarioReport:
    scenario_id: str
    errors: list[str] = field(default_factory=list)
    nodes: int = 0
    edges: int = 0
    chargers: int = 0
    rows: list[dict[str, str]] = field(default_factory=list)
    table: list[dict[str, str]] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def main(argv: list[str] | None = None) -> int:
    """Run every selected scenario. Exit 0 only if every scenario passes."""
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else 1
    try:
        reports = run_scenarios(
            args.scenarios, args.out, only=args.only, repeats=args.repeats
        )
    except (InputError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for report in reports:
        _print_table(report)
    failed = [report for report in reports if not report.ok]
    for report in failed:
        for error in report.errors:
            print(f"FAILED {report.scenario_id}: {error}", file=sys.stderr)
    print(
        f"{len(reports)} scenario(s), {len(reports) - len(failed)} passed, "
        f"{len(failed)} failed -> {args.out}"
    )
    return 1 if failed else 0


def run_scenarios(
    scenarios_root: str | Path,
    out: str | Path,
    *,
    only: list[str] | None = None,
    repeats: int = DEFAULT_REPEATS,
) -> list[ScenarioReport]:
    """Run scenarios in sorted order and update ``real_routes.csv`` in ``out``."""
    if repeats < 1:
        raise InputError("--repeats must be at least 1")
    root = Path(scenarios_root)
    if not root.is_dir():
        raise InputError(f"scenarios directory not found: {root}")
    folders = _select(root, only)
    if not folders:
        raise InputError(f"no scenario folders found under {root}")
    out_root = Path(out)
    out_root.mkdir(parents=True, exist_ok=True)
    commit = _git_commit()

    reports = [_run_scenario(folder, out_root, repeats, commit) for folder in folders]
    _update_csv(out_root / CSV_FILE, reports)
    return reports


def _select(root: Path, only: list[str] | None) -> list[Path]:
    if only:
        missing = sorted(name for name in set(only) if not (root / name).is_dir())
        if missing:
            raise InputError(f"unknown scenario(s) under {root}: {', '.join(missing)}")
        return [root / name for name in sorted(set(only))]
    return sorted(
        path
        for path in root.iterdir()
        if path.is_dir()
        and any((path / name).exists() for name in (GRAPH_FILE, CONFIG_FILE, META_FILE))
    )


def _run_scenario(folder: Path, out_root: Path, repeats: int, commit: str) -> ScenarioReport:
    report = ScenarioReport(folder.name)
    out_dir = out_root / folder.name
    out_dir.mkdir(parents=True, exist_ok=True)
    inputs: Inputs | None = None
    runs: dict[str, Run] = {}
    skipped: dict[str, str] = {}
    checks: dict[str, dict] = {}
    try:
        inputs = _read_inputs(folder)
        report.nodes = len(inputs.graph["nodes"])
        report.edges = len(inputs.graph["edges"])
        report.chargers = _charger_count(inputs.graph)
        runs, skipped = _run_algorithms(inputs, repeats)
        checks, failures = _cross_check(inputs, runs)
        report.errors.extend(failures)
    except (ScenarioError, InputError) as exc:
        report.errors.append(str(exc))

    if report.ok:
        try:
            _write_results(out_dir, runs)
        except InputError as exc:
            report.errors.append(f"result file failed schema validation: {exc}")

    manifest = _manifest(folder, inputs, runs, skipped, checks, report, repeats, commit)
    if report.ok and inputs is not None:
        report.rows = _csv_rows(folder.name, inputs, runs, skipped, checks)
        report.table = _table_rows(runs)
        report.skipped = skipped
    else:
        for name in ALGORITHMS:
            (out_dir / f"{name}.json").unlink(missing_ok=True)
    _write_json(out_dir / MANIFEST_FILE, manifest)
    return report


def _write_results(out_dir: Path, runs: dict[str, Run]) -> None:
    """Validate and write every ``<algorithm>.json``, or write none of them.

    Each file is staged as ``.tmp`` through ``write_result`` (which validates the
    schema) and renamed only after all of them passed.
    """
    staged: list[tuple[Path, Path]] = []
    try:
        for name, run in runs.items():
            final = out_dir / f"{name}.json"
            temporary = out_dir / f"{name}.json.tmp"
            staged.append((temporary, final))
            write_result(_with_median_runtime(run), temporary)
    except InputError:
        for temporary, _final in staged:
            temporary.unlink(missing_ok=True)
        raise
    for temporary, final in staged:
        temporary.replace(final)


def _read_inputs(folder: Path) -> Inputs:
    graph_path = folder / GRAPH_FILE
    config_path = folder / CONFIG_FILE
    for path in (graph_path, config_path):
        if not path.is_file():
            raise ScenarioError(f"{path.name} is missing in {folder}")
    graph_sha = _sha256(graph_path)
    config_sha = _sha256(config_path)
    meta = _read_meta(folder / META_FILE)
    if meta is not None:
        _require_hash(meta, "graph_sha256", graph_sha, GRAPH_FILE)
        _require_hash(meta, "config_sha256", config_sha, CONFIG_FILE)
    graph = load_graph(graph_path)
    config = load_config(config_path)
    validate_query(graph, config)
    if _sha256(graph_path) != graph_sha or _sha256(config_path) != config_sha:
        raise ScenarioError(f"{folder.name}: an input file changed while it was being read")
    return Inputs(graph, config, meta, graph_sha, config_sha)


def _read_meta(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioError(f"cannot read {META_FILE}: {exc}") from exc
    if not isinstance(meta, dict):
        raise ScenarioError(f"{META_FILE} must contain a JSON object")
    return meta


def _require_hash(meta: dict, key: str, actual: str, filename: str) -> None:
    if key not in meta:
        return
    declared = meta[key]
    if not isinstance(declared, str):
        raise ScenarioError(f"{META_FILE} {key} must be a hex string, got {declared!r}")
    normalized = declared.strip().lower().removeprefix("sha256:")
    if normalized != actual:
        raise ScenarioError(
            f"{META_FILE} {key} {normalized} does not match the SHA-256 of "
            f"{filename} as read ({actual}); regenerate the scenario or its meta file"
        )


def _run_algorithms(inputs: Inputs, repeats: int) -> tuple[dict[str, Run], dict[str, str]]:
    runs: dict[str, Run] = {}
    skipped: dict[str, str] = {}
    for name in ALGORITHMS:
        if name == "exact":
            reason = exact_block_reason(inputs.graph, inputs.config)
            if reason is not None:
                skipped[name] = reason
                continue
        runs[name] = _repeat(name, inputs, repeats)
    return runs, skipped


def _repeat(name: str, inputs: Inputs, repeats: int) -> Run:
    first: dict | None = None
    runtimes: list[float] = []
    for index in range(repeats):
        try:
            result = run_algorithm(name, inputs.graph, inputs.config)
        except InputError as exc:
            raise ScenarioError(f"{name} failed: {exc}") from exc
        runtimes.append(float(result["metrics"]["runtime_ms"]))
        if first is None:
            first = result
        elif _stable(result) != _stable(first):
            raise ScenarioError(
                f"{name} is not deterministic: repeat {index + 1} differs from repeat 1 "
                f"(label, route, events, or states)"
            )
    assert first is not None
    return Run(first, runtimes)


def _stable(result: dict) -> str:
    """Everything in a result except the measured runtime."""
    copy = json.loads(json.dumps(result))
    copy["metrics"].pop("runtime_ms", None)
    return json.dumps(copy, sort_keys=True)


def _with_median_runtime(run: Run) -> dict:
    result = json.loads(json.dumps(run.result))
    result["metrics"]["runtime_ms"] = float(statistics.median(run.runtimes_ms))
    return result


def _label(result: dict) -> tuple:
    return (
        result["total_time_minutes"],
        result["number_of_charging_stops"],
        result["total_distance_miles"],
    )


def _cross_check(inputs: Inputs, runs: dict[str, Run]) -> tuple[dict[str, dict], list[str]]:
    """Return per-algorithm check records and a list of failure messages."""
    failures: list[str] = []
    checks: dict[str, dict] = {name: {"plan_check": None, "cross_checks": []} for name in runs}
    label_setting = runs["label_setting"].result

    for name, run in runs.items():
        problems = check_plan(inputs.graph, inputs.config, run.result)
        checks[name]["plan_check"] = {"passed": not problems, "problems": problems}
        for problem in problems:
            failures.append(f"{name} plan check: {problem}")

    for name in ("astar", "exact"):
        if name not in runs:
            continue
        other = runs[name].result
        same = other["status"] == label_setting["status"] and _label(other) == _label(
            label_setting
        )
        detail = (
            f"{other['status']} {_label(other)}"
            if same
            else f"{name} {other['status']} {_label(other)} != label_setting "
            f"{label_setting['status']} {_label(label_setting)}"
        )
        checks[name]["cross_checks"].append(
            {"name": "matches_label_setting", "passed": same, "detail": detail}
        )
        if not same:
            failures.append(f"{name} disagrees with label_setting: {detail}")

    greedy = runs["greedy"].result
    outcome, passed, detail = _compare_greedy(label_setting, greedy)
    checks["greedy"]["cross_checks"].append(
        {"name": "not_better_than_label_setting", "passed": passed, "outcome": outcome,
         "detail": detail}
    )
    if not passed:
        failures.append(f"greedy {detail}")

    astar_states = runs["astar"].result["metrics"]["states_expanded"]
    plain_states = label_setting["metrics"]["states_expanded"]
    checks["astar"]["states_expanded_vs_label_setting"] = {
        "astar": astar_states,
        "label_setting": plain_states,
        "astar_not_greater": astar_states <= plain_states,
    }
    return checks, failures


def _compare_greedy(optimal: dict, greedy: dict) -> tuple[str, bool, str]:
    """Greedy may tie or lose to the optimizer. It must never win."""
    if greedy["status"] == "INFEASIBLE":
        if optimal["status"] == "FEASIBLE":
            return (
                "infeasible_while_optimal_feasible",
                True,
                "greedy is INFEASIBLE while label_setting is FEASIBLE (allowed)",
            )
        return "both_infeasible", True, "both INFEASIBLE"
    if optimal["status"] == "INFEASIBLE":
        return (
            "better",
            False,
            "is FEASIBLE but label_setting is INFEASIBLE, so greedy is better than the optimizer",
        )
    if _label(greedy) < _label(optimal):
        return (
            "better",
            False,
            f"{_label(greedy)} is strictly better than label_setting {_label(optimal)}",
        )
    if _label(greedy) == _label(optimal):
        return "equal", True, f"greedy matches label_setting {_label(optimal)}"
    return "worse", True, f"greedy {_label(greedy)} vs label_setting {_label(optimal)}"


def _manifest(
    folder: Path,
    inputs: Inputs | None,
    runs: dict[str, Run],
    skipped: dict[str, str],
    checks: dict[str, dict],
    report: ScenarioReport,
    repeats: int,
    commit: str,
) -> dict:
    algorithms: dict[str, dict] = {}
    for name in ALGORITHMS:
        if name in skipped:
            algorithms[name] = {"status": "SKIPPED", "reason": skipped[name], "cross_checks": []}
            continue
        run = runs.get(name)
        if run is None:
            continue
        result = run.result
        entry = {
            "algorithm": result["algorithm"],
            "status": result["status"],
            "total_time_minutes": result["total_time_minutes"],
            "number_of_charging_stops": result["number_of_charging_stops"],
            "total_distance_miles": result["total_distance_miles"],
            "runtime_ms": float(statistics.median(run.runtimes_ms)),
            "states_expanded": result["metrics"]["states_expanded"],
            "peak_states_stored": result["metrics"]["peak_states_stored"],
            "result_file": f"{name}.json" if report.ok else None,
        }
        entry.update(checks.get(name, {}))
        algorithms[name] = entry
    return {
        "scenario_id": folder.name,
        "status": "OK" if report.ok else "FAILED",
        "errors": list(report.errors),
        "graph_sha256": inputs.graph_sha256 if inputs else None,
        "config_sha256": inputs.config_sha256 if inputs else None,
        "scenario_meta": inputs.meta if inputs else None,
        "graph": {"nodes": report.nodes, "edges": report.edges, "chargers": report.chargers},
        "git_commit": commit,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repeats": repeats,
        "algorithms": algorithms,
    }


def _csv_rows(
    scenario_id: str,
    inputs: Inputs,
    runs: dict[str, Run],
    skipped: dict[str, str],
    checks: dict[str, dict],
) -> list[dict[str, str]]:
    meta = inputs.meta or {}
    tags = []
    if meta.get("road_backend") is not None:
        tags.append(f"road_backend={_plain(meta['road_backend'])}")
    category = meta.get("category", meta.get("scenario_category"))
    if category is not None:
        tags.append(f"category={_plain(category)}")
    common = {
        "experiment": EXPERIMENT,
        "instance_id": scenario_id,
        "seed": None,
        "nodes": len(inputs.graph["nodes"]),
        "chargers": _charger_count(inputs.graph),
        "capacity_kwh": float(inputs.config["battery_capacity_kwh"]),
        "soc_step_kwh": float(inputs.config["soc_step_kwh"]),
    }
    agreement = "true"  # label_setting, A*, and exact (when run) passed the match check
    rows = []
    for name in ALGORITHMS:
        if name in skipped:
            rows.append(
                metric_row(
                    **common,
                    algorithm="exact",
                    status="SKIPPED",
                    note="; ".join([skipped[name], *tags]),
                )
            )
            continue
        run = runs[name]
        notes = list(tags)
        label_agreement = agreement if name != "greedy" else ""
        if name == "greedy":
            greedy_check = checks["greedy"]["cross_checks"][0]
            if greedy_check["outcome"] == "infeasible_while_optimal_feasible":
                notes.append("greedy INFEASIBLE while label_setting FEASIBLE")
        rows.append(
            metric_row(
                **common,
                status=str(run.result["status"]),
                result=_with_median_runtime(run),
                agreement=label_agreement,
                note="; ".join(notes),
            )
        )
    return rows


def _update_csv(path: Path, reports: list[ScenarioReport]) -> None:
    """Replace the rows of every scenario in this run and keep the rest.

    Running one scenario with ``--only`` therefore leaves the other rows alone,
    and running a scenario twice never duplicates its rows. A scenario that
    failed contributes no rows, and its old rows are dropped.
    """
    kept: list[dict[str, str]] = []
    if path.exists():
        rerun = {report.scenario_id for report in reports}
        kept = [row for row in read_csv(path) if row["instance_id"] not in rerun]
    new = [row for report in reports for row in report.rows]
    order = {name: index for index, name in enumerate(ALGORITHMS)}
    algorithm_rank = {"greedy_frc": order["greedy"]}
    merged = sorted(
        kept + new,
        key=lambda row: (
            row["instance_id"],
            algorithm_rank.get(row["algorithm"], order.get(row["algorithm"], len(order))),
        ),
    )
    write_csv(path, merged)


def _table_rows(runs: dict[str, Run]) -> list[dict[str, str]]:
    rows = []
    for name in ALGORITHMS:
        run = runs.get(name)
        if run is None:
            continue
        result = run.result
        feasible = result["status"] == "FEASIBLE"
        rows.append(
            {
                "algorithm": result["algorithm"],
                "status": result["status"],
                "time": f"{result['total_time_minutes']:.2f}" if feasible else "-",
                "stops": str(result["number_of_charging_stops"]) if feasible else "-",
                "miles": f"{result['total_distance_miles']:.1f}" if feasible else "-",
                "runtime_ms": f"{statistics.median(run.runtimes_ms):.2f}",
                "states": str(result["metrics"]["states_expanded"]),
            }
        )
    return rows


def _print_table(report: ScenarioReport) -> None:
    verdict = "OK" if report.ok else "FAILED"
    print(
        f"\n{report.scenario_id}  [{verdict}]  nodes={report.nodes} edges={report.edges} "
        f"chargers={report.chargers}"
    )
    if not report.table:
        return
    headers = ("algorithm", "status", "time", "stops", "miles", "runtime_ms", "states")
    cells = [[row[h] for h in headers] for row in report.table]
    widths = [max(len(h), *(len(c[i]) for c in cells)) for i, h in enumerate(headers)]
    print("  " + "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    for data in cells:
        print("  " + "  ".join(c.ljust(widths[i]) for i, c in enumerate(data)))
    for name, reason in report.skipped.items():
        print(f"  {name} SKIPPED: {reason}")


def _charger_count(graph: dict) -> int:
    return sum(1 for node in graph["nodes"] if node["charge_rate_kw"] is not None)


def _plain(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ScenarioError(f"cannot read {path}: {exc.strerror}") from exc


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _git_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    commit = completed.stdout.strip()
    return commit if completed.returncode == 0 and commit else "unknown"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m experiments.real_data",
        description="Run every algorithm on real-data scenarios, verify, and write results.",
    )
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=DEFAULT_SCENARIOS,
        help="folder of <scenario_id>/ directories (default data/processed)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="output folder for <scenario_id>/*.json and real_routes.csv (default results/real)",
    )
    parser.add_argument("--only", nargs="+", metavar="ID", help="run only these scenario ids")
    parser.add_argument(
        "--repeats",
        type=int,
        default=DEFAULT_REPEATS,
        help="runs per algorithm; runtime is the median, labels and states must repeat",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
