"""Discover the real-data scenarios and the result files the S9 runner wrote for them.

Owned by Shannon (H11). H5 writes one folder per scenario under ``data/processed``
(``canonical_graph.json``, ``trip_config.json``, ``scenario_meta.json``). The S9
runner (``python -m experiments.real_data``) writes one folder per scenario under
``results/real`` with ``label_setting.json``, ``astar.json``, ``greedy.json`` (the
``greedy_frc`` baseline), ``exact.json`` when the exact solver ran, and
``run_manifest.json``.

This module only lists and reads those files. It never runs an optimizer, and it
never raises for a missing or broken scenario: it records the problem on the entry so
the app can say what to run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from visualizer.data_io import VisualizerInputError, load_result
from visualizer.soc_chart import UNAVAILABLE

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIOS_DIR = ROOT / "data" / "processed"
DEFAULT_RESULTS_DIR = ROOT / "results" / "real"

GRAPH_FILE = "canonical_graph.json"
TRIP_FILE = "trip_config.json"
META_FILE = "scenario_meta.json"
MANIFEST_FILE = "run_manifest.json"

GREEDY_KEY = "greedy"
OPTIMIZER_KEYS = ("label_setting", "astar", "exact")
RESULT_KEYS = (*OPTIMIZER_KEYS, GREEDY_KEY)

# The H5 scenario categories in presentation order. Unknown categories sort after.
CATEGORY_ORDER = ("no_charge", "one_stop", "multi_stop", "mixed_speeds", "sparse")


@dataclass(frozen=True)
class ScenarioEntry:
    """One scenario folder and the result files found for it."""

    scenario_id: str
    graph_path: Path
    trip_path: Path | None
    results: dict[str, Path]
    meta: dict = field(default_factory=dict)
    manifest_status: str | None = None
    manifest_errors: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def category(self) -> str:
        return str(self.meta.get("category", ""))

    @property
    def description(self) -> str:
        return str(self.meta.get("description", ""))

    @property
    def optimizers(self) -> list[str]:
        """Result keys of the optimizer files that exist, in preference order."""
        return [key for key in OPTIMIZER_KEYS if key in self.results]

    @property
    def greedy_path(self) -> Path | None:
        return self.results.get(GREEDY_KEY)

    @property
    def usable(self) -> bool:
        """True when the scenario has a graph and at least one optimizer result."""
        return not self.problems and bool(self.optimizers)


def discover_scenarios(
    scenarios_dir: str | Path = DEFAULT_SCENARIOS_DIR,
    results_dir: str | Path = DEFAULT_RESULTS_DIR,
) -> list[ScenarioEntry]:
    """List every scenario folder under ``scenarios_dir`` with its result files.

    A scenario with no results, a ``FAILED`` manifest, or no graph still appears,
    with ``problems`` saying why it cannot be drawn. A missing ``scenarios_dir`` gives
    an empty list.
    """
    root = Path(scenarios_dir)
    if not root.is_dir():
        return []
    entries = [
        build_entry(folder, Path(results_dir) / folder.name)
        for folder in root.iterdir()
        if folder.is_dir() and any((folder / name).is_file() for name in (GRAPH_FILE, META_FILE))
    ]
    return sorted(entries, key=sort_key)


def build_entry(folder: Path, results_folder: Path) -> ScenarioEntry:
    problems: list[str] = []
    graph_path = folder / GRAPH_FILE
    if not graph_path.is_file():
        problems.append(f"{GRAPH_FILE} is missing in {folder}")
    trip_path = folder / TRIP_FILE
    meta = read_json_object(folder / META_FILE) or {}

    results = {
        key: results_folder / f"{key}.json"
        for key in RESULT_KEYS
        if (results_folder / f"{key}.json").is_file()
    }
    manifest = read_json_object(results_folder / MANIFEST_FILE)
    status = None
    errors: tuple[str, ...] = ()
    if manifest is not None:
        status = str(manifest.get("status", "")) or None
        errors = tuple(str(error) for error in manifest.get("errors") or [])
        manifest_meta = manifest.get("scenario_meta")
        meta = {**(manifest_meta if isinstance(manifest_meta, dict) else {}), **meta}
    if status == "FAILED":
        problems.append(f"the S9 runner marked this scenario FAILED in {results_folder}")
    elif not any(key in results for key in OPTIMIZER_KEYS):
        problems.append(
            f"no optimizer result files in {results_folder}; "
            "run `python -m experiments.real_data` first"
        )
    return ScenarioEntry(
        scenario_id=folder.name,
        graph_path=graph_path,
        trip_path=trip_path if trip_path.is_file() else None,
        results=results,
        meta=meta,
        manifest_status=status,
        manifest_errors=errors,
        problems=tuple(problems),
    )


def sort_key(entry: ScenarioEntry) -> tuple[int, str]:
    try:
        rank = CATEGORY_ORDER.index(entry.category)
    except ValueError:
        rank = len(CATEGORY_ORDER)
    return rank, entry.scenario_id


def read_json_object(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def find_entry(entries: list[ScenarioEntry], scenario_id: str) -> ScenarioEntry | None:
    return next((entry for entry in entries if entry.scenario_id == scenario_id), None)


def provenance_lines(entry: ScenarioEntry) -> list[str]:
    """Short data-provenance facts worth showing next to a real-data result."""
    meta = entry.meta
    lines = []
    if meta.get("afdc_snapshot_date"):
        lines.append(f"AFDC snapshot {meta['afdc_snapshot_date']}")
    backend = meta.get("road_backend")
    if backend == "haversine":
        lines.append("road backend: haversine (straight-line distances, not OSM roads)")
    elif backend:
        lines.append(f"road backend: {backend}")
    if meta.get("charger_count") is not None:
        lines.append(f"{meta['charger_count']} chargers in the corridor")
    return lines


def overview_rows(entries: list[ScenarioEntry]) -> list[dict[str, str]]:
    """One row per scenario comparing greedy and the preferred optimizer result.

    Files that cannot be read show ``n/a`` instead of stopping the table.
    """
    rows = []
    for entry in entries:
        row = {"Scenario": entry.scenario_id, "Category": entry.category or UNAVAILABLE}
        optimized = entry.results[entry.optimizers[0]] if entry.optimizers else None
        row["Optimized"] = summarize(optimized)
        row["Greedy (FRC)"] = summarize(entry.greedy_path)
        rows.append(row)
    return rows


def summarize(path: Path | None) -> str:
    if path is None:
        return "no result"
    try:
        result = load_result(path)
    except VisualizerInputError:
        return "unreadable"
    if result["status"] != "FEASIBLE":
        return "INFEASIBLE"
    stops = result["number_of_charging_stops"]
    return f"{result['total_time_minutes']:.1f} min, {stops} stop{'' if stops == 1 else 's'}"
