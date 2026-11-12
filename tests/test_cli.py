"""CLI dispatch, exit codes, and result-file checks for the shared runtime."""

from __future__ import annotations

import json
from pathlib import Path

from optimizer import battery_state_optimizer, exact_solver, greedy_baseline
from optimizer.cli import main

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = ROOT / "data" / "synthetic"
GRAPH = SYNTHETIC / "canonical_graph.json"
CONFIG = SYNTHETIC / "trip_config.json"


def _sample_result(algorithm: str, status: str = "FEASIBLE") -> dict:
    result = json.loads((SYNTHETIC / "algorithm_result.json").read_text(encoding="utf-8"))
    result["algorithm"] = algorithm
    result["status"] = status
    if status == "INFEASIBLE":
        result["route_node_ids"] = []
        result["events"] = []
    return result


def _run(tmp_path: Path, algorithm: str, **overrides: str) -> tuple[int, Path]:
    out = tmp_path / "out.json"
    args = [
        "--algorithm",
        algorithm,
        "--graph",
        overrides.get("graph", str(GRAPH)),
        "--config",
        overrides.get("config", str(CONFIG)),
        "--out",
        str(out),
    ]
    return main(args), out


def test_help_and_missing_arguments_exit_cleanly() -> None:
    assert main(["--help"]) == 0
    assert main([]) == 2
    assert main(["--algorithm", "nope"]) == 2


def test_invalid_inputs_exit_nonzero(tmp_path: Path) -> None:
    code, out = _run(tmp_path, "label_setting", graph=str(tmp_path / "missing.json"))
    assert code == 1
    assert not out.exists()

    bad_config = tmp_path / "config.json"
    bad_config.write_text("[]", encoding="utf-8")
    code, _out = _run(tmp_path, "exact", config=str(bad_config))
    assert code == 1


def test_each_algorithm_is_dispatched(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, bool | None]] = []

    def fake_search(graph, config, *, use_heuristic=False):
        calls.append(("search", use_heuristic))
        name = "astar" if use_heuristic else "label_setting"
        return _sample_result(name)

    def fake_greedy(graph, config):
        calls.append(("greedy", None))
        return _sample_result("greedy_frc")

    def fake_exact(graph, config):
        calls.append(("exact", None))
        return _sample_result("exact")

    monkeypatch.setattr(battery_state_optimizer, "solve", fake_search)
    monkeypatch.setattr(greedy_baseline, "solve", fake_greedy)
    monkeypatch.setattr(exact_solver, "solve", fake_exact)

    code, out = _run(tmp_path, "label_setting")
    assert code == 0
    assert json.loads(out.read_text(encoding="utf-8"))["algorithm"] == "label_setting"

    code, out = _run(tmp_path, "astar")
    assert code == 0
    assert json.loads(out.read_text(encoding="utf-8"))["algorithm"] == "astar"

    code, out = _run(tmp_path, "greedy")
    assert code == 0
    assert json.loads(out.read_text(encoding="utf-8"))["algorithm"] == "greedy_frc"

    code, out = _run(tmp_path, "exact")
    assert code == 0
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "FEASIBLE"
    assert calls == [("search", False), ("search", True), ("greedy", None), ("exact", None)]


def test_infeasible_result_exits_zero(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        exact_solver,
        "solve",
        lambda graph, config: _sample_result("exact", "INFEASIBLE"),
    )
    code, out = _run(tmp_path, "exact")
    assert code == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["status"] == "INFEASIBLE"
    assert written["route_node_ids"] == []
    assert written["events"] == []


def test_solver_must_use_the_frozen_algorithm_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        greedy_baseline,
        "solve",
        lambda graph, config: _sample_result("label_setting"),
    )
    code, out = _run(tmp_path, "greedy")
    assert code == 1
    assert not out.exists()


def test_greedy_frc_exits_zero_and_writes_result(tmp_path: Path) -> None:
    code, out = _run(tmp_path, "greedy")
    assert code == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["algorithm"] == "greedy_frc"
    assert written["status"] in {"FEASIBLE", "INFEASIBLE"}
    if written["status"] == "INFEASIBLE":
        assert written["route_node_ids"] == []
        assert written["events"] == []


def test_exact_tiny_query_exits_zero(tmp_path: Path) -> None:
    graph = tmp_path / "graph.json"
    config = tmp_path / "config.json"
    graph.write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": "origin",
                        "type": "origin",
                        "lat": 42.0,
                        "lon": -83.0,
                        "charge_rate_kw": None,
                    },
                    {
                        "id": "destination",
                        "type": "destination",
                        "lat": 42.0,
                        "lon": -83.0,
                        "charge_rate_kw": None,
                    },
                ],
                "edges": [
                    {
                        "from": "origin",
                        "to": "destination",
                        "distance_miles": 4,
                        "drive_time_minutes": 12,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config.write_text(
        json.dumps(
            {
                "origin": "origin",
                "destination": "destination",
                "battery_capacity_kwh": 20,
                "initial_soc_kwh": 10,
                "reserve_kwh": 0,
                "consumption_kwh_per_mile": 1,
                "soc_step_kwh": 1,
            }
        ),
        encoding="utf-8",
    )
    code, out = _run(tmp_path, "exact", graph=str(graph), config=str(config))
    assert code == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["algorithm"] == "exact"
    assert written["status"] == "FEASIBLE"
    assert written["route_node_ids"] == ["origin", "destination"]
    assert written["total_time_minutes"] == 12


def test_astar_sample_exits_zero(tmp_path: Path) -> None:
    code, out = _run(tmp_path, "astar")
    assert code == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["algorithm"] == "astar"
    assert written["status"] == "FEASIBLE"
    assert written["route_node_ids"] == ["origin", "station_1", "station_2", "destination"]
    assert written["total_time_minutes"] == 322.5
    assert written["number_of_charging_stops"] == 2


def test_label_setting_sample_exits_zero(tmp_path: Path) -> None:
    code, out = _run(tmp_path, "label_setting")
    assert code == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["status"] == "FEASIBLE"
    assert written["algorithm"] == "label_setting"
    assert written["route_node_ids"] == ["origin", "station_1", "station_2", "destination"]
    assert written["number_of_charging_stops"] == 2
    assert written["total_time_minutes"] == 322.5
