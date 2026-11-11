"""Experiments C, D, E, and J produce measured rows and plots.

Experiment A has its own file, ``test_exact_suite.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from experiments.instances import correctness_instances
from experiments.metrics import read_csv
from experiments.plots import MEASURED_PLOTS, OMITTED_PLOTS
from experiments.run_all import run
from experiments.sweeps import experiment_a, experiment_c, experiment_e


def test_experiment_a_keeps_its_row_wrapper_and_the_section_21_shape() -> None:
    rows, failed = experiment_a(505, 40)
    assert failed == ()
    assert len([row for row in rows if row["algorithm"] == "exact"]) == 40
    assert {row["agreement"] for row in rows} == {"true"}
    for instance in correctness_instances(505, 40):
        assert 4 <= instance.node_count <= 8
        assert 1 <= instance.charger_count <= 5


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
        scaling_replicates=2,
        astar_replicates=1,
    )
    assert summary.disagreements == ()
    soc = read_csv(tmp_path / "soc_resolution.csv")
    assert soc
    assert {row["status"] for row in soc} <= {"FEASIBLE", "INFEASIBLE"}
    assert all(row["runtime_ms"] for row in soc)
    scaling = read_csv(tmp_path / "charger_scaling.csv")
    assert {row["chargers"] for row in scaling} == {"10", "25"}
    assert len(scaling) == 2 * 2 * 2  # replicates x counts x algorithms
    assert {row["agreement"] for row in scaling} == {"true"}
    assert (tmp_path / "charger_scaling_summary.json").is_file()
    comparison = read_csv(tmp_path / "astar_comparison.csv")
    dedicated = [row for row in comparison if "suite=J" in row["note"]]
    assert len(dedicated) == 36 * 2  # one replicate x 36 factor combinations x 2 algorithms
    assert {row["experiment"] for row in dedicated} == {"J"}
    assert {row["agreement"] for row in dedicated} == {"true"}
    # Experiment J measures its own instances. None of them is a D or C instance.
    d_ids = {row["instance_id"] for row in scaling}
    c_ids = {row["instance_id"] for row in soc}
    assert d_ids.isdisjoint(row["instance_id"] for row in comparison)
    assert c_ids.isdisjoint(row["instance_id"] for row in comparison)
    assert not any("comparison=J" in row["note"] for row in comparison)
    assert (tmp_path / "astar_summary.json").is_file()
    assert not (tmp_path / "astar_failures.json").exists()
    assert summary.experiment_j is not None
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