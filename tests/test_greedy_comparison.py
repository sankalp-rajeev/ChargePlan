"""Experiments B, F, G, H, and I: instances, rows, invariants, summaries, and plots."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from experiments import greedy_comparison as gc
from experiments import greedy_suite as suite
from experiments.metrics import COLUMNS, read_csv, write_csv
from experiments.plots import write_h9_plots
from experiments.scaling import note_fields
from optimizer.model import InputError, load_config, load_graph, validate_query

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = ROOT / "data" / "synthetic"


def without_runtime(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{k: v for k, v in row.items() if k != "runtime_ms"} for row in rows]


@pytest.fixture(scope="module")
def small_b() -> gc.Comparison:
    return gc.experiment_b(505, repeats=1, replicates=1)


def empty_b(seed: int, repeats: int, replicates: int) -> gc.Comparison:
    del seed, repeats, replicates
    return gc.Comparison([], (), [])


def scenario(root: Path, name: str) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    shutil.copy(SYNTHETIC / "canonical_graph.json", folder / "canonical_graph.json")
    shutil.copy(SYNTHETIC / "trip_config.json", folder / "trip_config.json")
    return folder


def test_instances_pass_the_input_contract(tmp_path: Path) -> None:
    instance = suite.build_f_suite(505, 1)[2]
    graph_path = tmp_path / "graph.json"
    config_path = tmp_path / "config.json"
    graph_path.write_text(json.dumps(instance.graph), encoding="utf-8")
    config_path.write_text(json.dumps(instance.config), encoding="utf-8")
    graph = load_graph(graph_path)
    config = load_config(config_path)
    validate_query(graph, config)
    assert instance.config["initial_soc_kwh"] == pytest.approx(0.6 * 60)


def test_suite_sizes_and_unique_ids() -> None:
    b = suite.build_b_suite(505, 2)
    assert len(b) == 2 * 3 * 3 * 3 * 2
    assert len({i.instance_id for i in b}) == len(b)
    assert {i.meta["layout"] for i in b} == {"sparse", "medium", "dense"}
    assert len(suite.build_f_suite(505, 3)) == 3 * 5
    assert len(suite.build_g_suite(505, 2)) == 2 * len(suite.G_COUNTS)
    assert len(suite.build_h_suite(505, 4)) == 4 * 4
    builders = (suite.build_b_suite, suite.build_f_suite, suite.build_g_suite, suite.build_h_suite)
    for build in builders:
        with pytest.raises(InputError):
            build(505, 0)


def test_same_seed_repeats_and_a_new_seed_changes_the_layout() -> None:
    first = suite.build_g_suite(505, 2)
    again = suite.build_g_suite(505, 2)
    other = suite.build_g_suite(506, 2)
    assert [(i.graph, i.config) for i in first] == [(i.graph, i.config) for i in again]
    assert [i.graph for i in first] != [i.graph for i in other]


def test_density_counts_are_matched_prefixes_of_one_pool() -> None:
    by_count = {
        i.meta["stations"]: i for i in suite.build_g_suite(505, 1) if i.meta["replicate"] == 0
    }
    small = {n["id"]: n for n in by_count[10].graph["nodes"] if n["type"] == "charger"}
    large = {n["id"]: n for n in by_count[50].graph["nodes"] if n["type"] == "charger"}
    assert set(small) < set(large)
    assert all(large[key] == node for key, node in small.items())
    large_edges = {(e["from"], e["to"]): e for e in by_count[50].graph["edges"]}
    for edge in by_count[10].graph["edges"]:
        assert large_edges[(edge["from"], edge["to"])] == edge  # a pair's road never changes


def test_initial_soc_sweep_changes_only_the_start_charge() -> None:
    group = [i for i in suite.build_f_suite(505, 2) if i.meta["replicate"] == 1]
    assert [i.meta["soc_pct"] for i in group] == [20, 40, 60, 80, 100]
    assert len({json.dumps(i.graph, sort_keys=True) for i in group}) == 1
    starts = [i.config["initial_soc_kwh"] for i in group]
    assert starts == sorted(starts) and starts[-1] == group[0].config["battery_capacity_kwh"]
    others = {
        json.dumps({k: v for k, v in i.config.items() if k != "initial_soc_kwh"}) for i in group
    }
    assert len(others) == 1


def test_speed_profiles_share_roads_and_mixed_matches_the_uniform_mean() -> None:
    group = {i.meta["speed"]: i for i in suite.build_h_suite(505, 1)}
    assert set(group) == set(suite.H_PROFILES)
    assert len({json.dumps(i.graph["edges"]) for i in group.values()}) == 1
    positions = {
        profile: [(n["id"], n["lat"], n["lon"]) for n in i.graph["nodes"]]
        for profile, i in group.items()
    }
    assert len({json.dumps(p) for p in positions.values()}) == 1
    rates = {
        profile: [n["charge_rate_kw"] for n in i.graph["nodes"] if n["type"] == "charger"]
        for profile, i in group.items()
    }
    assert set(rates["uniform_100"]) == {100.0}
    assert set(rates["mixed_50_150"]) == {50.0, 150.0}
    mean = sum(rates["mixed_50_150"]) / len(rates["mixed_50_150"])
    assert abs(mean - 100.0) <= 100.0 / len(rates["mixed_50_150"])


def test_mixed_rates_do_not_depend_on_how_many_stations_are_kept() -> None:
    small = suite.Corridor(505, 0, 10)
    large = suite.Corridor(505, 0, 50)
    assert [small.rate("mixed_50_150", s) for s in small.pool] == [
        large.rate("mixed_50_150", s) for s in large.pool[:10]
    ]
    with pytest.raises(InputError):
        large.instance("x", 51, "uniform_50", 60, 50, {})
    with pytest.raises(InputError):
        large.instance("x", 5, "turbo", 60, 50, {})


def test_density_tiers() -> None:
    assert [suite.tier_of_count(n) for n in (2, 4, 5, 10, 11, 50)] == [
        "sparse",
        "sparse",
        "medium",
        "medium",
        "dense",
        "dense",
    ]
    with pytest.raises(InputError):
        suite.tier_of_count(51)


def test_every_instance_gets_two_rows_with_the_shared_schema() -> None:
    result = gc.experiment_g(505, repeats=1, replicates=1, counts=(3, 10))
    assert result.failed == ()
    assert len(result.rows) == 4
    for row in result.rows:
        assert tuple(row) == COLUMNS
        assert row["experiment"] == "G"
        assert row["agreement"] == "true"
    by_id: dict[str, list[dict[str, str]]] = {}
    for row in result.rows:
        by_id.setdefault(row["instance_id"], []).append(row)
    for rows in by_id.values():
        assert [r["algorithm"] for r in rows] == ["label_setting", "greedy_frc"]
        optimum, greedy = rows
        assert "improvement_pct=" in optimum["note"]
        assert "optimizer_status=" in greedy["note"]
        fields = note_fields(optimum["note"])
        assert fields["suite"] == "G"
        assert fields["tier"] in {"sparse", "medium", "dense"}
        if optimum["status"] == "FEASIBLE" and greedy["status"] == "FEASIBLE":
            gap = float(greedy["total_time_minutes"]) - float(optimum["total_time_minutes"])
            assert gap >= -1e-9
            assert float(note_fields(optimum["note"])["charging_minutes"]) >= 0.0


def test_optimizer_is_never_slower_than_frc_on_a_small_b_run(small_b: gc.Comparison) -> None:
    result = small_b
    assert result.failed == ()
    assert len(result.records) == 54
    assert all(record["dominance_ok"] for record in result.records)
    feasible = [r for r in result.records if r["outcome"] == "both_feasible"]
    assert feasible
    assert all(r["improvement_pct"] >= -1e-9 for r in feasible)
    assert any(r["improvement_pct"] > 0 for r in feasible)  # the baseline loses somewhere


def test_a_timing_repeat_changes_only_runtime() -> None:
    first = gc.experiment_h(505, repeats=1, replicates=1)
    again = gc.experiment_h(505, repeats=1, replicates=1)
    other = gc.experiment_h(506, repeats=1, replicates=1)
    assert without_runtime(first.rows) == without_runtime(again.rows)
    assert without_runtime(first.rows) != without_runtime(other.rows)


def test_bad_repeat_count_is_rejected() -> None:
    with pytest.raises(InputError):
        gc.experiment_h(505, repeats=0, replicates=1)


def test_a_baseline_that_beats_the_optimizer_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    real = gc.run_algorithm

    def cheating(name: str, graph: dict, config: dict) -> dict:
        result = real(name, graph, config)
        if name == "greedy" and result["status"] == "FEASIBLE":
            result = dict(result)
            result["total_time_minutes"] = 1.0
        return result

    monkeypatch.setattr(gc, "run_algorithm", cheating)
    result = gc.experiment_h(505, repeats=1, replicates=1)
    assert result.failed
    bad = [row for row in result.rows if row["agreement"] == "false"]
    assert bad and all("dominance=VIOLATED" in row["note"] for row in bad)


def test_an_optimizer_that_misses_a_route_frc_found_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = gc.run_algorithm

    def broken(name: str, graph: dict, config: dict) -> dict:
        result = real(name, graph, config)
        if name == "label_setting":
            result = dict(result)
            result["status"] = "INFEASIBLE"
        return result

    monkeypatch.setattr(gc, "run_algorithm", broken)
    result = gc.experiment_h(505, repeats=1, replicates=1)
    assert len(result.failed) == 4
    assert {r["outcome"] for r in result.records} == {"optimizer_infeasible"}


def test_initial_soc_rows_record_the_route_and_the_soc_profile() -> None:
    result = gc.experiment_f(505, repeats=1, replicates=1)
    optimum = [r for r in result.rows if r["algorithm"] == "label_setting"]
    assert [note_fields(r["note"])["soc_pct"] for r in optimum] == ["20", "40", "60", "80", "100"]
    for row in optimum:
        fields = note_fields(row["note"])
        route = fields["route"].split(">")
        assert route[0] == "origin" and route[-1] == "destination"
        profile = [float(v) for v in fields["soc_profile_kwh"].split(",")]
        expected = float(row["capacity_kwh"]) * int(fields["soc_pct"]) / 100
        assert profile[0] == pytest.approx(expected)
        assert len(fields["stops_at"].split(",")) == int(row["number_of_charging_stops"])


def test_real_routes_without_scenarios_are_unavailable(tmp_path: Path) -> None:
    for scenarios in (None, tmp_path / "missing"):
        result = gc.experiment_i(scenarios)
        assert [row["status"] for row in result.rows] == ["UNAVAILABLE"]
        assert result.rows[0]["experiment"] == "I"
        assert result.failed == () and result.records == []
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "notes.txt").write_text("not a scenario", encoding="utf-8")
    assert [row["status"] for row in gc.experiment_i(empty).rows] == ["UNAVAILABLE"]


def test_real_routes_run_each_scenario_and_degrade_per_folder(tmp_path: Path) -> None:
    scenario(tmp_path, "b_sample")
    good = scenario(tmp_path, "a_sample")
    (good / "scenario_meta.json").write_text(
        json.dumps(
            {"category": "demo", "road_backend": "haversine", "afdc_snapshot_date": "2026-11-09"}
        ),
        encoding="utf-8",
    )
    broken = tmp_path / "c_broken"
    broken.mkdir()
    (broken / "canonical_graph.json").write_text("{", encoding="utf-8")
    (broken / "trip_config.json").write_text("{}", encoding="utf-8")

    result = gc.experiment_i(tmp_path, repeats=1)
    assert result.failed == ()
    assert [(r["instance_id"], r["status"]) for r in result.rows] == [
        ("a_sample", "FEASIBLE"),
        ("a_sample", "FEASIBLE"),
        ("b_sample", "FEASIBLE"),
        ("b_sample", "FEASIBLE"),
        ("c_broken", "UNAVAILABLE"),
    ]
    first = result.rows[0]
    assert first["algorithm"] == "label_setting"
    assert first["total_time_minutes"] == "322.5"
    assert first["number_of_charging_stops"] == "2"
    fields = note_fields(first["note"])
    assert fields["category"] == "demo"
    assert fields["afdc_snapshot"] == "2026-11-09"
    assert fields["route"].startswith("origin>") and fields["route"].endswith(">destination")
    assert result.rows[1]["algorithm"] == "greedy_frc"
    assert "did not load" in result.rows[-1]["note"]
    assert [r["instance_id"] for r in result.records] == ["a_sample", "b_sample"]


def test_real_scenario_files_are_never_modified(tmp_path: Path) -> None:
    folder = scenario(tmp_path, "keep")
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    gc.experiment_i(tmp_path, repeats=1)
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


def test_summary_reports_outcomes_and_serializes(small_b: gc.Comparison) -> None:
    b = small_b
    f = gc.experiment_f(505, repeats=1, replicates=1)
    g = gc.experiment_g(505, repeats=1, replicates=2, counts=(2, 4, 8))
    h = gc.experiment_h(505, repeats=1, replicates=2)
    summary = gc.summarize(b, f, g, h, None)
    json.dumps(summary)
    assert "I" not in summary
    overall = summary["B"]["overall"]
    assert sum(overall["outcomes"].values()) == 54
    assert overall["improvement_pct"]["median"] > 0
    assert set(summary["B"]["by_factor"]["layout"]) == {"sparse", "medium", "dense"}
    assert set(summary["F"]["by_soc_pct"]) == {"20", "40", "60", "80", "100"}
    assert len(summary["F"]["distinct_optimizer_routes_per_replicate"]) == 1
    rates = {k: v["optimizer_feasible_rate"] for k, v in summary["G"]["by_stations"].items()}
    assert set(rates) == {"2", "4", "8"}
    assert all(0.0 <= v <= 1.0 for v in rates.values())
    assert rates["8"] >= rates["2"]
    mixed = summary["H"]["by_speed"]["mixed_50_150"]
    assert mixed["instances"] == 2
    assert summary["H"]["mixed_minus_uniform_100_minutes"]["optimizer"]["n"] == 2
    assert "B optimizer vs FRC" in gc.format_report(summary)


def test_summary_of_real_routes(tmp_path: Path) -> None:
    scenario(tmp_path, "demo")
    real = gc.experiment_i(tmp_path, repeats=1)
    summary = gc.summarize(None, None, None, None, real)
    assert set(summary) == {"note", "I"}
    assert summary["I"]["outcomes"] == {"both_feasible": 1}
    assert summary["I"]["scenarios"][0]["scenario"] == "demo"
    assert "route" not in summary["I"]["scenarios"][0]["optimizer"]


def test_standalone_run_writes_csvs_summary_and_plots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gc, "experiment_b", empty_b)
    scenarios = tmp_path / "scenarios"
    scenario(scenarios, "demo")
    results = gc.run(
        tmp_path / "out",
        repeats=1,
        greedy_replicates=1,
        density_replicates=1,
        speed_replicates=1,
        scenarios=scenarios,
        plots_dir=tmp_path / "plots",
    )
    assert results.failed == ()
    for filename in gc.FILES.values():
        assert (tmp_path / "out" / filename).is_file(), filename
    summary = json.loads((tmp_path / "out" / gc.SUMMARY_FILE).read_text(encoding="utf-8"))
    assert summary["I"]["outcomes"] == {"both_feasible": 1}
    assert {p.name for p in (tmp_path / "plots").iterdir()} == {
        "greedy_vs_optimizer.png",
        "feasibility_vs_charger_density.png",
    }


def test_cli_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gc, "experiment_b", empty_b)
    argv = [
        "--out",
        str(tmp_path / "out"),
        "--plots",
        str(tmp_path / "plots"),
        "--repeats",
        "1",
        "--greedy-replicates",
        "1",
        "--density-replicates",
        "1",
        "--speed-replicates",
        "1",
        "--scenarios",
        str(tmp_path / "none"),
    ]
    assert gc.main(argv) == 0
    assert read_csv(tmp_path / "out" / "real_routes.csv")[0]["status"] == "UNAVAILABLE"
    assert gc.main([*argv[:-2], "--repeats", "0", "--scenarios", str(tmp_path)]) == 1


def test_plots_are_written_from_rows_and_survive_empty_input(
    tmp_path: Path,
    small_b: gc.Comparison,
) -> None:
    rows = {"greedy_comparison.csv": [], "charger_density.csv": []}
    for path in write_h9_plots(rows, tmp_path / "empty"):
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    g = gc.experiment_g(505, repeats=1, replicates=1, counts=(2, 6))
    full = write_h9_plots(
        {"greedy_comparison.csv": small_b.rows, "charger_density.csv": g.rows}, tmp_path / "full"
    )
    assert [p.name for p in full] == [
        "greedy_vs_optimizer.png",
        "feasibility_vs_charger_density.png",
    ]
    assert all(p.stat().st_size > 1000 for p in full)


def test_written_rows_round_trip_through_the_csv_contract(tmp_path: Path) -> None:
    result = gc.experiment_h(505, repeats=1, replicates=1)
    path = tmp_path / "charger_speed.csv"
    write_csv(path, result.rows)
    assert read_csv(path) == result.rows
