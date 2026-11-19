"""H11: the visualizer discovers and draws the result files the S9 runner writes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from experiments import real_data
from visualizer.app import uses_scenario_mode
from visualizer.comparison import render_comparison
from visualizer.data_io import load_graph, load_result, load_trip_config
from visualizer.map_view import render_map
from visualizer.scenarios import (
    OPTIMIZER_KEYS,
    discover_scenarios,
    find_entry,
    overview_rows,
    provenance_lines,
)
from visualizer.soc_chart import build_trip_summary, render_soc_chart, render_timeline

ROOT = Path(__file__).resolve().parents[1]
REAL_SCENARIOS = ROOT / "data" / "processed"
REAL_RESULTS = ROOT / "results" / "real"
H5_SCENARIOS = (
    "no_charge_detroit_ann_arbor",
    "one_stop_detroit_cleveland",
    "multi_stop_detroit_chicago",
    "mixed_speeds_detroit_chicago",
    "sparse_detroit_chicago",
)


def node(node_id: str, kind: str, lon: float, rate: float | None = None) -> dict:
    return {"id": node_id, "type": kind, "lat": 42.0, "lon": lon, "charge_rate_kw": rate}


def edge(start: str, end: str, miles: float) -> dict:
    return {"from": start, "to": end, "distance_miles": miles, "drive_time_minutes": miles}


def config(**updates: float) -> dict:
    base = {
        "origin": "A",
        "destination": "B",
        "battery_capacity_kwh": 40,
        "initial_soc_kwh": 25,
        "reserve_kwh": 5,
        "consumption_kwh_per_mile": 1,
        "soc_step_kwh": 5,
    }
    base.update(updates)
    return base


LINE = {
    "nodes": [
        node("A", "origin", -83.0),
        node("C", "charger", -83.1, 60),
        node("B", "destination", -83.2),
    ],
    "edges": [edge("A", "C", 20), edge("C", "B", 30)],
}

# The farthest charger in range is a detour that cannot reach B, so greedy fails where
# the optimizer drives straight through.
GREEDY_TRAP = {
    "nodes": [
        node("A", "origin", -83.0),
        node("C", "charger", -82.0, 60),
        node("B", "destination", -85.0),
    ],
    "edges": [edge("A", "B", 110), edge("A", "C", 52), edge("C", "B", 160)],
}

SCENARIOS = {
    "no_charge_demo": (LINE, config(initial_soc_kwh=40, consumption_kwh_per_mile=0.5)),
    "one_stop_demo": (LINE, config()),
    "greedy_trap_demo": (
        GREEDY_TRAP,
        config(battery_capacity_kwh=200, initial_soc_kwh=60, reserve_kwh=0),
    ),
    "infeasible_demo": (LINE, config(battery_capacity_kwh=10, initial_soc_kwh=10, reserve_kwh=5)),
}


def write_scenarios(root: Path) -> None:
    for name, (graph, trip) in SCENARIOS.items():
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "canonical_graph.json").write_text(json.dumps(graph), encoding="utf-8")
        (folder / "trip_config.json").write_text(json.dumps(trip), encoding="utf-8")


@pytest.fixture(scope="module")
def runner_output(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    base = tmp_path_factory.mktemp("h11")
    scenarios, results = base / "scenarios", base / "results"
    write_scenarios(scenarios)
    reports = real_data.run_scenarios(scenarios, results, repeats=1)
    assert all(report.ok for report in reports), [r.errors for r in reports]
    return scenarios, results


def test_discovers_every_scenario_and_the_runner_file_names(runner_output) -> None:
    entries = discover_scenarios(*runner_output)
    assert sorted(entry.scenario_id for entry in entries) == sorted(SCENARIOS)
    entry = find_entry(entries, "one_stop_demo")
    assert entry is not None and entry.usable
    assert entry.manifest_status == "OK"
    assert entry.optimizers == ["label_setting", "astar", "exact"]
    assert entry.greedy_path is not None and entry.greedy_path.name == "greedy.json"
    assert entry.trip_path is not None and entry.trip_path.name == "trip_config.json"
    assert find_entry(entries, "nope") is None


def test_every_scenario_result_loads_and_draws(runner_output) -> None:
    for entry in discover_scenarios(*runner_output):
        graph = load_graph(entry.graph_path)
        trip = load_trip_config(entry.trip_path)
        for key in (*OPTIMIZER_KEYS, "greedy"):
            if key not in entry.results:
                continue
            result = load_result(entry.results[key])
            assert render_map(graph, result) is not None
            assert build_trip_summary(result, trip)
            if result["events"]:
                assert render_soc_chart(result, "distance", trip) is not None
                assert render_timeline(result, trip)
        optimized = load_result(entry.results[entry.optimizers[0]])
        comparison = render_comparison(load_result(entry.greedy_path), optimized)
        assert len(comparison["rows"]) == 5


def test_greedy_trap_and_infeasible_scenarios_are_reported_not_raised(runner_output) -> None:
    entries = discover_scenarios(*runner_output)
    trap = find_entry(entries, "greedy_trap_demo")
    comparison = render_comparison(
        load_result(trap.greedy_path), load_result(trap.results["label_setting"])
    )
    assert comparison["greedy_status"] == "INFEASIBLE"
    assert comparison["severity"] == "success"

    dead = find_entry(entries, "infeasible_demo")
    assert dead.usable
    result = load_result(dead.results["label_setting"])
    assert result["status"] == "INFEASIBLE"
    assert render_map(load_graph(dead.graph_path), result) is not None
    assert render_comparison(load_result(dead.greedy_path), result)["severity"] == "info"


def test_overview_rows_summarize_each_scenario(runner_output) -> None:
    rows = {row["Scenario"]: row for row in overview_rows(discover_scenarios(*runner_output))}
    assert rows["one_stop_demo"]["Optimized"].endswith("1 stop")
    assert rows["no_charge_demo"]["Optimized"].endswith("0 stops")
    assert rows["greedy_trap_demo"]["Greedy (FRC)"] == "INFEASIBLE"
    assert rows["infeasible_demo"]["Optimized"] == "INFEASIBLE"


def test_missing_results_and_missing_directories_are_not_errors(tmp_path: Path) -> None:
    assert discover_scenarios(tmp_path / "absent", tmp_path / "absent") == []
    write_scenarios(tmp_path / "scenarios")
    entries = discover_scenarios(tmp_path / "scenarios", tmp_path / "no_results")
    assert len(entries) == len(SCENARIOS)
    for entry in entries:
        assert not entry.usable
        assert entry.optimizers == []
        assert any("experiments.real_data" in problem for problem in entry.problems)
    assert overview_rows(entries)[0]["Optimized"] == "no result"


def test_failed_manifest_is_surfaced_with_its_errors(tmp_path: Path) -> None:
    write_scenarios(tmp_path / "scenarios")
    failed = tmp_path / "results" / "one_stop_demo"
    failed.mkdir(parents=True)
    (failed / "run_manifest.json").write_text(
        json.dumps({"status": "FAILED", "errors": ["astar disagrees with label_setting"]}),
        encoding="utf-8",
    )
    entry = find_entry(
        discover_scenarios(tmp_path / "scenarios", tmp_path / "results"), "one_stop_demo"
    )
    assert entry.manifest_status == "FAILED"
    assert entry.manifest_errors == ("astar disagrees with label_setting",)
    assert not entry.usable
    assert any("FAILED" in problem for problem in entry.problems)


def test_scenario_without_a_graph_is_listed_with_a_problem(tmp_path: Path) -> None:
    folder = tmp_path / "scenarios" / "half"
    folder.mkdir(parents=True)
    (folder / "scenario_meta.json").write_text(json.dumps({"category": "sparse"}), encoding="utf-8")
    [entry] = discover_scenarios(tmp_path / "scenarios", tmp_path / "results")
    assert not entry.usable
    assert any("canonical_graph.json is missing" in problem for problem in entry.problems)


def test_scenarios_sort_by_category_then_name(tmp_path: Path) -> None:
    for name, category in [("z", "no_charge"), ("a", "sparse"), ("b", "one_stop"), ("c", "other")]:
        folder = tmp_path / name
        folder.mkdir()
        (folder / "scenario_meta.json").write_text(
            json.dumps({"category": category}), encoding="utf-8"
        )
    assert [e.scenario_id for e in discover_scenarios(tmp_path, tmp_path / "r")] == [
        "z",
        "b",
        "a",
        "c",
    ]


def test_provenance_names_the_road_backend_and_snapshot(tmp_path: Path) -> None:
    folder = tmp_path / "s"
    folder.mkdir()
    meta = {"road_backend": "haversine", "afdc_snapshot_date": "2026-11-09", "charger_count": 6}
    (folder / "scenario_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    [entry] = discover_scenarios(tmp_path, tmp_path / "r")
    lines = provenance_lines(entry)
    assert "AFDC snapshot 2026-11-09" in lines
    assert any("haversine" in line and "not OSM" in line for line in lines)


def test_corrupt_result_file_shows_as_unreadable_in_the_overview(
    runner_output, tmp_path: Path
) -> None:
    scenarios, results = runner_output
    broken = tmp_path / "results"
    for source in results.rglob("*.json"):
        target = broken / source.relative_to(results)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (broken / "one_stop_demo" / "greedy.json").write_text("not json", encoding="utf-8")
    rows = {row["Scenario"]: row for row in overview_rows(discover_scenarios(scenarios, broken))}
    assert rows["one_stop_demo"]["Greedy (FRC)"] == "unreadable"
    assert rows["one_stop_demo"]["Optimized"] != "unreadable"


def test_scenario_flags_choose_the_scenario_browser() -> None:
    from visualizer.app import parse_args

    assert not uses_scenario_mode(parse_args([]))
    assert uses_scenario_mode(parse_args(["--scenario", "x"]))
    assert uses_scenario_mode(parse_args(["--results", "r"]))
    assert not uses_scenario_mode(parse_args(["--result", "r.json"]))


def run_app(monkeypatch: pytest.MonkeyPatch, *args: str):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(sys, "argv", ["app.py", *args])
    return AppTest.from_file(str(ROOT / "visualizer" / "app.py"), default_timeout=60).run()


def scenario_args(runner_output, scenario: str, *extra: str) -> list[str]:
    scenarios, results = runner_output
    return [
        "--scenarios",
        str(scenarios),
        "--results",
        str(results),
        "--scenario",
        scenario,
        *extra,
    ]


def test_app_draws_a_real_scenario_with_the_greedy_comparison(monkeypatch, runner_output) -> None:
    app = run_app(monkeypatch, *scenario_args(runner_output, "one_stop_demo"))
    assert not app.exception
    assert not app.error
    assert app.sidebar.radio[0].value == "Real scenario results"
    assert app.sidebar.selectbox[0].value == "one_stop_demo"
    assert any(s.value == "Greedy vs optimized" for s in app.subheader)
    assert len(app.get("vega_lite_chart")) == 1
    assert any("Optimized (label_setting)" in json.dumps(t.value.to_dict()) for t in app.table)
    assert any("S9 run manifest: OK" in c.value for c in app.caption)


def test_app_optimizer_picker_switches_the_result(monkeypatch, runner_output) -> None:
    app = run_app(
        monkeypatch, *scenario_args(runner_output, "one_stop_demo", "--optimizer", "astar")
    )
    assert not app.exception
    assert app.sidebar.selectbox[1].value == "astar"
    assert any("Optimized (astar)" in json.dumps(t.value.to_dict()) for t in app.table)


def test_app_shows_greedy_failure_and_infeasible_scenarios_without_errors(
    monkeypatch, runner_output
) -> None:
    trap = run_app(monkeypatch, *scenario_args(runner_output, "greedy_trap_demo"))
    assert not trap.exception and not trap.error
    assert any("no feasible plan" in s.value for s in trap.success)

    dead = run_app(monkeypatch, *scenario_args(runner_output, "infeasible_demo"))
    assert not dead.exception and not dead.error
    assert any("found no feasible plan" in w.value for w in dead.warning)
    assert any("no drive or charge events" in i.value for i in dead.info)


def test_app_tells_the_user_to_run_the_runner_when_results_are_missing(
    monkeypatch, tmp_path: Path
) -> None:
    write_scenarios(tmp_path / "scenarios")
    app = run_app(
        monkeypatch,
        "--scenarios",
        str(tmp_path / "scenarios"),
        "--results",
        str(tmp_path / "none"),
        "--scenario",
        "one_stop_demo",
    )
    assert not app.exception
    assert any("experiments.real_data" in w.value for w in app.warning)


def test_app_with_no_scenario_folders_says_so(monkeypatch, tmp_path: Path) -> None:
    app = run_app(monkeypatch, "--scenarios", str(tmp_path / "absent"), "--results", str(tmp_path))
    assert not app.exception
    assert any("No scenario folders found" in i.value for i in app.info)


def test_app_reports_a_corrupt_main_result(monkeypatch, runner_output, tmp_path: Path) -> None:
    scenarios, results = runner_output
    broken = tmp_path / "results"
    for source in results.rglob("*.json"):
        target = broken / source.relative_to(results)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (broken / "one_stop_demo" / "label_setting.json").write_text("{", encoding="utf-8")
    app = run_app(
        monkeypatch,
        "--scenarios",
        str(scenarios),
        "--results",
        str(broken),
        "--scenario",
        "one_stop_demo",
    )
    assert not app.exception
    assert any("not valid JSON" in e.value for e in app.error)


def test_app_default_start_is_still_file_mode(monkeypatch) -> None:
    app = run_app(monkeypatch)
    assert not app.exception
    assert app.sidebar.radio[0].value == "Files"


@pytest.mark.skipif(
    not all((REAL_RESULTS / name / "label_setting.json").is_file() for name in H5_SCENARIOS),
    reason="run `make real-results` after generating the H5 scenarios in data/processed",
)
@pytest.mark.parametrize("scenario", H5_SCENARIOS)
def test_real_h5_scenarios_render_from_s9_results(monkeypatch, scenario: str) -> None:
    entry = find_entry(discover_scenarios(REAL_SCENARIOS, REAL_RESULTS), scenario)
    assert entry is not None and entry.usable
    assert entry.greedy_path is not None
    app = run_app(
        monkeypatch,
        "--scenarios",
        str(REAL_SCENARIOS),
        "--results",
        str(REAL_RESULTS),
        "--scenario",
        scenario,
    )
    assert not app.exception
    assert not app.error
    assert len(app.get("vega_lite_chart")) == 1
    assert len(app.table) == 2
