"""Experiment A, C, D, E, and J produce measured rows and plots."""

from __future__ import annotations

from pathlib import Path

import pytest

from experiments.instances import (
    CHARGER_COUNTS,
    CHARGER_SCALE_MAX,
    CORRECTNESS_COUNT,
    OMITTED_CHARGER_COUNT,
    correctness_instances,
    scaling_instances,
)
from experiments.metrics import read_csv
from experiments.plots import MEASURED_PLOTS, OMITTED_PLOTS
from experiments.run_all import run
from experiments.sweeps import experiment_a, experiment_c, experiment_e
from optimizer.model import InputError


def test_experiment_a_graphs_match_the_section_21_shape() -> None:
    first = correctness_instances(505, CORRECTNESS_COUNT)
    second = correctness_instances(505, CORRECTNESS_COUNT)
    assert len(first) == 100
    assert [item.instance_id for item in first] == [item.instance_id for item in second]
    assert first[0].graph["edges"] == second[0].graph["edges"]
    assert correctness_instances(506, 4)[0].graph["edges"] != first[0].graph["edges"]
    for instance in first:
        assert 4 <= instance.node_count <= 8
        assert 1 <= instance.charger_count <= 5
        assert instance.config["battery_capacity_kwh"] <= 6


def test_experiment_a_agrees_on_one_hundred_graphs() -> None:
    rows, failed = experiment_a(505, CORRECTNESS_COUNT)
    assert failed == ()
    exact = [row for row in rows if row["algorithm"] == "exact"]
    assert len(exact) == 100
    assert {row["agreement"] for row in rows} == {"true"}
    assert {row["status"] for row in rows} <= {"FEASIBLE", "INFEASIBLE"}


def test_charger_scaling_stops_at_one_hundred() -> None:
    assert CHARGER_COUNTS[-1] == CHARGER_SCALE_MAX
    assert OMITTED_CHARGER_COUNT not in CHARGER_COUNTS
    with pytest.raises(InputError, match="200"):
        scaling_instances((OMITTED_CHARGER_COUNT,))
    small = scaling_instances((10, 25))
    assert [item.charger_count for item in small] == [10, 25]
    assert small[0].config["battery_capacity_kwh"] == small[1].config["battery_capacity_kwh"]
    assert small[0].graph["nodes"][0]["lon"] == small[1].graph["nodes"][0]["lon"]
    assert small[0].graph["nodes"][-1]["lon"] == small[1].graph["nodes"][-1]["lon"]


def test_soc_error_is_the_gap_from_the_fine_reference() -> None:
    rows, failed = experiment_c(repeats=1)
    assert failed == ()
    sample = [
        row
        for row in rows
        if row["algorithm"] == "label_setting" and "family=sample" in row["note"]
    ]
    reference = next(row for row in sample if "role=reference" in row["note"])
    assert reference["soc_step_kwh"] == "0.1"
    assert reference["status"] == "FEASIBLE"
    assert "objective_error_minutes=0" in reference["note"]
    coarse = next(row for row in sample if row["soc_step_kwh"] == "14")
    expected = float(coarse["total_time_minutes"]) - float(reference["total_time_minutes"])
    assert f"objective_error_minutes={format(expected, '.10g')}" in coarse["note"]
    assert expected > 0
    roundup = {
        row["soc_step_kwh"]: row["status"]
        for row in rows
        if row["algorithm"] == "label_setting" and "family=roundup" in row["note"]
    }
    assert roundup["0.1"] == "FEASIBLE"
    assert roundup["6"] == "INFEASIBLE"


def test_capacity_sweep_keeps_the_roads_and_the_initial_soc() -> None:
    rows, failed = experiment_e(repeats=1)
    assert failed == ()
    by_capacity = {
        row["capacity_kwh"]: row
        for row in rows
        if row["algorithm"] == "label_setting"
    }
    assert by_capacity["40"]["status"] == "INFEASIBLE"
    assert by_capacity["100"]["status"] == "FEASIBLE"
    assert int(by_capacity["100"]["number_of_charging_stops"]) <= int(
        by_capacity["55"]["number_of_charging_stops"]
    )
    assert {row["agreement"] for row in rows} == {"true"}


def test_sweeps_write_measurements_plots_and_leave_h4_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbid_exact(graph: dict, config: dict) -> dict:
        del graph, config
        raise AssertionError("exact search is not part of the scaling sweeps")

    monkeypatch.setattr("experiments.sweeps.exact_solve", forbid_exact)
    plots = tmp_path / "plots"
    summary = run(
        tmp_path,
        seed=505,
        tiny_count=1,
        correctness_count=0,
        sweeps=True,
        timing_repeats=1,
        plots_dir=plots,
        charger_counts=(10, 25),
        capacities=(40, 70),
    )
    assert summary.disagreements == ()
    soc = read_csv(tmp_path / "soc_resolution.csv")
    assert soc
    assert {row["status"] for row in soc} <= {"FEASIBLE", "INFEASIBLE"}
    assert all(row["runtime_ms"] for row in soc)
    scaling = read_csv(tmp_path / "charger_scaling.csv")
    assert {row["chargers"] for row in scaling} == {"10", "25"}
    _assert_astar_uses_no_more_states(scaling)
    greedy = read_csv(tmp_path / "greedy_comparison.csv")
    assert greedy[0]["status"] == "UNAVAILABLE"
    assert greedy[0]["total_time_minutes"] == ""
    real = read_csv(tmp_path / "real_routes.csv")
    assert real[0]["status"] == "UNAVAILABLE"
    written = {path.name for path in plots.iterdir()}
    assert written == set(MEASURED_PLOTS)
    assert set(OMITTED_PLOTS).isdisjoint(written)
    for name in MEASURED_PLOTS:
        header = (plots / name).read_bytes()[:8]
        assert header == b"\x89PNG\r\n\x1a\n"


def _assert_astar_uses_no_more_states(rows: list[dict[str, str]]) -> None:
    grouped: dict[str, dict[str, int]] = {}
    for row in rows:
        grouped.setdefault(row["instance_id"], {})[row["algorithm"]] = int(row["states_expanded"])
    for algorithms in grouped.values():
        assert algorithms["astar"] <= algorithms["label_setting"]
