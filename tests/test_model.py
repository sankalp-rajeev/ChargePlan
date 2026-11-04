"""Shared runtime: loading, Section 7.1 rounding, and result writing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from optimizer.model import (
    InputError,
    charge_step_minutes,
    discretize,
    edge_energy_kwh,
    edge_energy_steps,
    load_config,
    load_graph,
    minutes_to_ms,
    ms_to_minutes,
    validate_query,
    write_result,
)

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = ROOT / "data" / "synthetic"


def _sample_config() -> dict:
    return json.loads((SYNTHETIC / "trip_config.json").read_text(encoding="utf-8"))


def _sample_result() -> dict:
    return json.loads((SYNTHETIC / "algorithm_result.json").read_text(encoding="utf-8"))


def test_loaders_accept_synthetic_samples() -> None:
    graph = load_graph(SYNTHETIC / "canonical_graph.json")
    config = load_config(SYNTHETIC / "trip_config.json")
    validate_query(graph, config)
    assert config["origin"] == "origin"
    assert {node["id"] for node in graph["nodes"]} >= {"origin", "destination"}


def test_sample_edge_energy_rounds_up_to_17_kwh() -> None:
    config = _sample_config()
    assert edge_energy_steps(55.2, config) == 17
    assert edge_energy_kwh(55.2, config) == 17


def test_discretize_sample_config() -> None:
    assert discretize(_sample_config()) == (70, 54, 7, 1.0)


def test_discretize_floor_and_ceil() -> None:
    config = _sample_config()
    config["battery_capacity_kwh"] = 70.9
    config["initial_soc_kwh"] = 54.9
    config["reserve_kwh"] = 7.1
    assert discretize(config) == (70, 54, 8, 1.0)


def test_discretize_fractional_step() -> None:
    config = _sample_config()
    config["soc_step_kwh"] = 0.5
    config["battery_capacity_kwh"] = 70
    config["initial_soc_kwh"] = 0.4
    config["reserve_kwh"] = 0.1
    assert discretize(config) == (140, 0, 1, 0.5)


def test_exact_energy_does_not_round_up() -> None:
    config = _sample_config()
    assert edge_energy_steps(10, config) == 3


def test_fractional_step_energy_rounds_up() -> None:
    config = _sample_config()
    config["soc_step_kwh"] = 0.5
    assert edge_energy_steps(1, config) == 1
    assert edge_energy_kwh(1, config) == 0.5


def test_zero_distance_uses_no_energy() -> None:
    assert edge_energy_steps(0, _sample_config()) == 0


def test_charge_step_and_millisecond_conversion() -> None:
    assert charge_step_minutes(1.0, 150) == pytest.approx(0.4)
    assert minutes_to_ms(1) == 60_000
    assert minutes_to_ms(51.3) == 3_078_000
    assert minutes_to_ms(0.000025) == 2
    assert ms_to_minutes(60_000) == pytest.approx(1)
    assert ms_to_minutes(3_078_000) == pytest.approx(51.3)


def test_initial_soc_and_reserve_cannot_exceed_capacity() -> None:
    config = _sample_config()
    config["initial_soc_kwh"] = 70
    config["reserve_kwh"] = 70
    assert discretize(config)[:3] == (70, 70, 70)

    config["initial_soc_kwh"] = 70.1
    with pytest.raises(InputError, match="initial_soc_kwh"):
        discretize(config)

    config = _sample_config()
    config["reserve_kwh"] = 70.1
    with pytest.raises(InputError, match="reserve_kwh"):
        discretize(config)

    config = _sample_config()
    config["battery_capacity_kwh"] = 1.2
    config["initial_soc_kwh"] = 0
    config["reserve_kwh"] = 1.1
    with pytest.raises(InputError, match="discretized reserve"):
        discretize(config)


def test_load_config_rejects_soc_above_capacity(tmp_path: Path) -> None:
    config = _sample_config()
    config["initial_soc_kwh"] = 80
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(InputError, match="initial_soc_kwh"):
        load_config(path)


def test_rounding_rejects_bad_numbers() -> None:
    config = _sample_config()
    config["soc_step_kwh"] = 0
    with pytest.raises(InputError):
        discretize(config)
    with pytest.raises(InputError):
        edge_energy_steps(-1, _sample_config())
    with pytest.raises(InputError):
        charge_step_minutes(1, 0)
    with pytest.raises(InputError):
        minutes_to_ms(-0.1)
    with pytest.raises(InputError):
        ms_to_minutes(-1)


def test_load_graph_rejects_short_edge(tmp_path: Path) -> None:
    graph = json.loads((SYNTHETIC / "canonical_graph.json").read_text(encoding="utf-8"))
    graph["edges"][0]["distance_miles"] = 1.0
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(graph), encoding="utf-8")
    with pytest.raises(InputError, match="straight-line"):
        load_graph(path)


def test_load_graph_rejects_duplicate_and_unknown_nodes(tmp_path: Path) -> None:
    graph = json.loads((SYNTHETIC / "canonical_graph.json").read_text(encoding="utf-8"))
    graph["nodes"].append(dict(graph["nodes"][0]))
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(graph), encoding="utf-8")
    with pytest.raises(InputError, match="duplicate"):
        load_graph(path)

    graph = json.loads((SYNTHETIC / "canonical_graph.json").read_text(encoding="utf-8"))
    graph["edges"][0]["to"] = "missing"
    path = tmp_path / "unknown.json"
    path.write_text(json.dumps(graph), encoding="utf-8")
    with pytest.raises(InputError, match="unknown node"):
        load_graph(path)


def test_loaders_reject_schema_and_syntax_errors(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(InputError, match="invalid JSON"):
        load_config(broken)

    config = _sample_config()
    del config["reserve_kwh"]
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(InputError, match="trip_config.schema.json"):
        load_config(path)

    missing = tmp_path / "nope.json"
    with pytest.raises(InputError, match="cannot read"):
        load_graph(missing)


def test_validate_query_requires_endpoint_ids() -> None:
    graph = load_graph(SYNTHETIC / "canonical_graph.json")
    config = _sample_config()
    config["destination"] = "nowhere"
    with pytest.raises(InputError, match="destination"):
        validate_query(graph, config)


def test_write_result_round_trip_and_infeasible_rule(tmp_path: Path) -> None:
    result = _sample_result()
    path = tmp_path / "result.json"
    write_result(result, path)
    assert json.loads(path.read_text(encoding="utf-8")) == result

    infeasible = dict(result)
    infeasible["status"] = "INFEASIBLE"
    infeasible["route_node_ids"] = []
    infeasible["events"] = []
    infeasible_path = tmp_path / "infeasible.json"
    write_result(infeasible, infeasible_path)

    infeasible["route_node_ids"] = ["origin"]
    with pytest.raises(InputError, match="algorithm_result.schema.json"):
        write_result(infeasible, tmp_path / "bad.json")
