"""Metrics, CSV output, reproducibility, and error handling for the runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from experiments.instances import exact_block_reason, load_sample, tiny_instances
from experiments.metrics import COLUMNS, OUTPUT_FILES, metric_row, read_csv
from experiments.run_all import _solve as solve_named
from experiments.run_all import main, run
from optimizer.model import InputError


def test_metric_row_reads_the_result_contract() -> None:
    row = metric_row(
        experiment="J",
        instance_id="hand",
        seed=505,
        nodes=2,
        chargers=0,
        capacity_kwh=10,
        soc_step_kwh=1,
        status="",
        agreement="true",
        result={
            "algorithm": "label_setting",
            "status": "FEASIBLE",
            "total_time_minutes": 4,
            "number_of_charging_stops": 0,
            "total_distance_miles": 4,
            "metrics": {"runtime_ms": 1.25, "states_expanded": 2, "peak_states_stored": 2},
        },
    )
    assert row["algorithm"] == "label_setting"
    assert row["feasible"] == "true"
    assert row["total_time_minutes"] == "4"
    assert row["number_of_charging_stops"] == "0"
    assert row["total_distance_miles"] == "4"
    assert row["states_expanded"] == "2"
    assert row["peak_states_stored"] == "2"
    assert row["runtime_ms"] == "1.25"
    assert row["agreement"] == "true"


def test_metric_row_rejects_a_result_without_metrics() -> None:
    with pytest.raises(InputError, match="metrics"):
        metric_row(
            experiment="J",
            instance_id="hand",
            seed=None,
            nodes=None,
            chargers=None,
            capacity_kwh=None,
            soc_step_kwh=None,
            status="FEASIBLE",
            result={"algorithm": "astar", "status": "FEASIBLE"},
        )


STALE_SUMMARIES = (
    "correctness_summary.json",
    "charger_scaling_summary.json",
    "astar_summary.json",
    "astar_failures.json",
)


def _plant_stale_summaries(directory: Path) -> None:
    for name in STALE_SUMMARIES:
        (directory / name).write_text('{"stale": true}\n', encoding="utf-8")


def test_skipped_experiments_leave_no_stale_summary_files(tmp_path: Path) -> None:
    # --correctness 0 and --no-sweeps skip A, D, and J, so none of their summaries may survive.
    _plant_stale_summaries(tmp_path)
    run(tmp_path, seed=505, tiny_count=1, correctness_count=0, sweeps=False)
    assert {path.name for path in tmp_path.iterdir()} == set(OUTPUT_FILES)
    for name in STALE_SUMMARIES:
        assert not (tmp_path / name).exists(), name

    # Experiment A runs but the sweeps are skipped: only A's summary is rewritten.
    _plant_stale_summaries(tmp_path)
    run(tmp_path, seed=505, tiny_count=1, correctness_count=20, sweeps=False)
    summary = (tmp_path / "correctness_summary.json").read_text(encoding="utf-8")
    assert '"stale"' not in summary
    for name in STALE_SUMMARIES[1:]:
        assert not (tmp_path / name).exists(), name


def test_cli_removes_stale_summaries_when_experiments_are_skipped(tmp_path: Path) -> None:
    _plant_stale_summaries(tmp_path)
    code = main(
        ["--out", str(tmp_path), "--tiny", "1", "--correctness", "0", "--no-sweeps"]
    )
    assert code == 0
    assert not any((tmp_path / name).exists() for name in STALE_SUMMARIES)


def test_runner_writes_measured_rows_and_leaves_the_rest_unavailable(tmp_path: Path) -> None:
    summary = run(tmp_path, seed=505, tiny_count=2, correctness_count=0, sweeps=False)
    assert summary.disagreements == ()
    assert {path.name for path in tmp_path.iterdir()} == set(OUTPUT_FILES)

    astar = read_csv(tmp_path / "astar_comparison.csv")
    assert astar
    assert tuple(astar[0]) == COLUMNS
    sample = [row for row in astar if row["instance_id"] == "detroit-chicago"]
    assert {row["algorithm"] for row in sample} == {"label_setting", "astar"}
    assert {row["status"] for row in sample} == {"FEASIBLE"}
    assert {row["total_time_minutes"] for row in sample} == {"322.5"}
    assert {row["number_of_charging_stops"] for row in sample} == {"2"}
    assert {row["agreement"] for row in sample} == {"true"}
    _assert_pairs_agree(astar)
    assert {row["note"] for row in astar if row["instance_id"] == "detroit-chicago"} == {
        "suite=S6; set=sample"
    }
    assert all("suite=S6" in row["note"] for row in astar)
    assert not any("suite=J" in row["note"] for row in astar)  # J runs only with sweeps

    correctness = read_csv(tmp_path / "correctness.csv")
    skipped = [row for row in correctness if row["instance_id"] == "detroit-chicago"]
    assert len(skipped) == 1
    assert skipped[0]["algorithm"] == "exact"
    assert skipped[0]["status"] == "SKIPPED"
    assert skipped[0]["states_expanded"] == ""
    assert "capacity steps" in skipped[0]["note"]
    tiny_exact = [
        row
        for row in correctness
        if row["instance_id"].startswith("tiny-") and row["algorithm"] == "exact"
    ]
    assert len(tiny_exact) == 2
    assert {row["status"] for row in tiny_exact} <= {"FEASIBLE", "INFEASIBLE"}
    assert {row["agreement"] for row in tiny_exact} == {"true"}

    greedy = read_csv(tmp_path / "greedy_comparison.csv")
    assert len(greedy) == 1
    assert greedy[0]["status"] == "UNAVAILABLE"
    assert greedy[0]["algorithm"] == "greedy_frc"
    assert greedy[0]["total_time_minutes"] == ""
    assert "H4" in greedy[0]["note"]
    real = read_csv(tmp_path / "real_routes.csv")
    assert real[0]["status"] == "UNAVAILABLE"
    assert "S9" in real[0]["note"]


def test_same_seed_repeats_every_column_except_runtime(tmp_path: Path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    other_dir = tmp_path / "other"
    run(first_dir, seed=505, tiny_count=2, correctness_count=0, sweeps=False)
    run(second_dir, seed=505, tiny_count=2, correctness_count=0, sweeps=False)
    run(other_dir, seed=506, tiny_count=2, correctness_count=0, sweeps=False)
    first = _without_runtime(read_csv(first_dir / "correctness.csv"))
    second = _without_runtime(read_csv(second_dir / "correctness.csv"))
    other = _without_runtime(read_csv(other_dir / "correctness.csv"))
    assert first == second
    assert first != other


def test_tiny_graphs_are_a_function_of_the_seed() -> None:
    assert _signature(tiny_instances(505, 4)) == _signature(tiny_instances(505, 4))
    assert _signature(tiny_instances(505, 4)) != _signature(tiny_instances(506, 4))
    for instance in tiny_instances(505, 4):
        assert exact_block_reason(instance.graph, instance.config) is None


def test_exact_solver_is_not_called_outside_the_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = solve_named

    def guarded(algorithm: str, graph: dict, config: dict) -> dict:
        if algorithm == "exact" and exact_block_reason(graph, config) is not None:
            raise AssertionError("exact solver ran outside the bound")
        if algorithm == "greedy":
            raise AssertionError("greedy baseline was called")
        return real(algorithm, graph, config)

    monkeypatch.setattr("experiments.run_all._solve", guarded)
    summary = run(tmp_path, seed=505, tiny_count=1, correctness_count=0, sweeps=False)
    assert summary.disagreements == ()
    sample = load_sample()
    assert exact_block_reason(sample.graph, sample.config) is not None


def test_a_label_mismatch_is_written_and_fails_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = solve_named

    def mismatched(algorithm: str, graph: dict, config: dict) -> dict:
        result = real(algorithm, graph, config)
        if algorithm != "exact":
            return result
        forged = dict(result)
        forged["status"] = "FEASIBLE"
        forged["total_time_minutes"] = -1
        forged["number_of_charging_stops"] = 99
        forged["total_distance_miles"] = -1
        return forged

    monkeypatch.setattr("experiments.run_all._solve", mismatched)
    assert main(
        [
            "--out",
            str(tmp_path),
            "--tiny",
            "1",
            "--seed",
            "505",
            "--no-sweeps",
            "--correctness",
            "0",
        ]
    ) == 1
    rows = read_csv(tmp_path / "correctness.csv")
    failed = [
        row
        for row in rows
        if row["instance_id"] == "tiny-000" and row["algorithm"] == "exact"
    ]
    assert failed[0]["agreement"] == "false"


def test_missing_sample_and_bad_arguments_are_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(InputError, match="missing"):
        load_sample(tmp_path)
    monkeypatch.setattr(
        "experiments.run_all.load_sample",
        lambda: (_ for _ in ()).throw(InputError("benchmark sample is missing")),
    )
    assert (
        main(
            [
                "--out",
                str(tmp_path / "out"),
                "--tiny",
                "1",
                "--no-sweeps",
                "--correctness",
                "0",
            ]
        )
        == 1
    )
    assert main(["--tiny", "0"]) == 1
    with pytest.raises(InputError, match="H4"):
        solve_named("greedy", {"nodes": [], "edges": []}, {})
    with pytest.raises(InputError, match="unknown algorithm"):
        solve_named("not-a-solver", {"nodes": [], "edges": []}, {})


def _assert_pairs_agree(rows: list[dict[str, str]]) -> None:
    """A* must return label-setting's label. It need not expand fewer states."""
    by_instance: dict[str, dict[str, dict[str, str]]] = {}
    for row in rows:
        by_instance.setdefault(row["instance_id"], {})[row["algorithm"]] = row
    for algorithms in by_instance.values():
        plain, guided = algorithms["label_setting"], algorithms["astar"]
        for column in (
            "status",
            "total_time_minutes",
            "number_of_charging_stops",
            "total_distance_miles",
        ):
            assert plain[column] == guided[column]
        assert plain["agreement"] == guided["agreement"] == "true"


def _without_runtime(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{key: value for key, value in row.items() if key != "runtime_ms"} for row in rows]


def _signature(instances: list) -> list[tuple]:
    signed = []
    for instance in instances:
        edges = tuple(
            (edge["from"], edge["to"], edge["distance_miles"], edge["drive_time_minutes"])
            for edge in instance.graph["edges"]
        )
        signed.append((instance.instance_id, edges, tuple(sorted(instance.config.items()))))
    return signed
