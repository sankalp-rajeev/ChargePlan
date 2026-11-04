"""A* agreement with label-setting (S10, T15).

The heuristic is the floored straight-line time at the fastest edge speed.
On every instance below, A* returns the same feasibility and the same
``(time, stops, distance)`` label as plain label-setting, and it expands no
more states. No processed real-data graph is in this repo yet (S9), so that
comparison runs only when ``data/processed`` contains a canonical graph.
T16, the greedy failure case, is still H4 and is not claimed here.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from optimizer.battery_state_optimizer import _heuristic_ms, solve
from optimizer.model import haversine_miles, minutes_to_ms

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = ROOT / "data" / "synthetic"
PROCESSED = ROOT / "data" / "processed"
_LENGTH_TOLERANCE_MILES = 1e-6


def _node(
    node_id: str,
    node_type: str,
    rate: float | None,
    lat: float = 42.0,
    lon: float = -83.0,
) -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "lat": lat,
        "lon": lon,
        "charge_rate_kw": rate,
    }


def _edge(start: str, end: str, miles: float, minutes: float) -> dict:
    return {
        "from": start,
        "to": end,
        "distance_miles": miles,
        "drive_time_minutes": minutes,
    }


def _graph(*nodes: dict, edges: list[dict]) -> dict:
    graph = {"nodes": list(nodes), "edges": edges}
    _require_straight_line(graph)
    return graph


def _require_straight_line(graph: dict) -> None:
    """Reject a fixture whose road is shorter than the frozen haversine check."""
    nodes = {node["id"]: node for node in graph["nodes"]}
    for edge in graph["edges"]:
        start = nodes[edge["from"]]
        end = nodes[edge["to"]]
        straight = haversine_miles(start["lat"], start["lon"], end["lat"], end["lon"])
        if edge["distance_miles"] + _LENGTH_TOLERANCE_MILES < straight:
            raise AssertionError(
                f"edge {edge['from']!r} -> {edge['to']!r} is shorter than the "
                f"straight-line distance ({edge['distance_miles']} < {straight})"
            )


def _config(**overrides: float | str) -> dict:
    config: dict[str, float | str] = {
        "origin": "origin",
        "destination": "destination",
        "battery_capacity_kwh": 20,
        "initial_soc_kwh": 10,
        "reserve_kwh": 0,
        "consumption_kwh_per_mile": 1,
        "soc_step_kwh": 1,
    }
    config.update(overrides)
    return config


def _objective(result: dict) -> tuple:
    if result["status"] != "FEASIBLE":
        return ("INFEASIBLE",)
    return (
        "FEASIBLE",
        result["total_time_minutes"],
        result["number_of_charging_stops"],
        result["total_distance_miles"],
    )


def _agree(graph: dict, config: dict) -> tuple[dict, dict]:
    plain = solve(graph, config, use_heuristic=False)
    guided = solve(graph, config, use_heuristic=True)
    assert plain["algorithm"] == "label_setting"
    assert guided["algorithm"] == "astar"
    assert _objective(guided) == _objective(plain)
    assert guided["metrics"]["states_expanded"] <= plain["metrics"]["states_expanded"]
    return plain, guided


def test_t15_hand_cases_and_sample_agree() -> None:
    direct = _graph(
        _node("origin", "origin", None),
        _node("destination", "destination", None),
        edges=[_edge("origin", "destination", 4, 12)],
    )
    plain, guided = _agree(direct, _config())
    assert guided["route_node_ids"] == ["origin", "destination"]
    assert guided["total_time_minutes"] == 12
    assert guided["metrics"]["states_expanded"] == plain["metrics"]["states_expanded"] == 2

    must_charge = _graph(
        _node("origin", "origin", 60),
        _node("destination", "destination", None),
        edges=[_edge("origin", "destination", 5, 5)],
    )
    _agree(must_charge, _config(initial_soc_kwh=0, battery_capacity_kwh=20))

    infeasible = _graph(
        _node("origin", "origin", None),
        _node("destination", "destination", None),
        edges=[_edge("origin", "destination", 30, 30)],
    )
    blocked, guided_blocked = _agree(infeasible, _config(battery_capacity_kwh=10))
    assert blocked["status"] == "INFEASIBLE"
    assert guided_blocked["metrics"]["states_expanded"] <= blocked["metrics"]["states_expanded"]

    graph = json.loads((SYNTHETIC / "canonical_graph.json").read_text(encoding="utf-8"))
    config = json.loads((SYNTHETIC / "trip_config.json").read_text(encoding="utf-8"))
    sample, guided_sample = _agree(graph, config)
    assert sample["total_time_minutes"] == 322.5
    assert sample["number_of_charging_stops"] == 2
    assert guided_sample["route_node_ids"] == sample["route_node_ids"]


def test_t15_one_hundred_seeded_graphs_agree() -> None:
    rng = random.Random(505)
    nonzero = 0
    for index in range(100):
        graph, config = _random_query(rng, index)
        nodes = {node["id"]: node for node in graph["nodes"]}
        heuristic = _heuristic_ms(nodes, graph["edges"], str(config["destination"]))
        assert heuristic[str(config["destination"])] == 0
        assert heuristic[str(config["origin"])] > 0
        nonzero += 1
        _agree(graph, config)
    assert nonzero == 100


def test_t15_real_data_graph_when_present() -> None:
    """S9 has not exported a real corridor yet. The check runs once that file exists."""
    graphs = sorted(PROCESSED.glob("**/canonical_graph.json")) if PROCESSED.exists() else []
    if not graphs:
        pytest.skip("no real-data canonical graph in data/processed yet (S9)")
    for path in graphs:
        graph = json.loads(path.read_text(encoding="utf-8"))
        config_path = path.with_name("trip_config.json")
        if not config_path.exists():
            pytest.skip(f"{path} has no trip_config.json beside it")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        _agree(graph, config)


def test_astar_expands_fewer_states_when_a_fast_chain_leads_away() -> None:
    step = 0.019456080139242785
    nodes = [_node("origin", "origin", None, lon=-83.0)]
    edges = []
    previous = "origin"
    for index in range(1, 9):
        node_id = f"west{index}"
        nodes.append(_node(node_id, "charger", None, lon=-83.0 - index * step))
        edges.append(_edge(previous, node_id, 1, 1))
        previous = node_id
    nodes.append(_node("destination", "destination", None, lon=-82.81498222089809))
    edges.append(_edge("origin", "destination", 10, 10))
    graph = _graph(*nodes, edges=edges)
    for edge in edges:
        start = next(node for node in nodes if node["id"] == edge["from"])
        end = next(node for node in nodes if node["id"] == edge["to"])
        straight = haversine_miles(start["lat"], start["lon"], end["lat"], end["lon"])
        assert edge["distance_miles"] + 1e-6 >= straight
    plain, guided = _agree(graph, _config(initial_soc_kwh=20, battery_capacity_kwh=20))
    assert plain["route_node_ids"] == ["origin", "destination"]
    assert plain["total_time_minutes"] == 10
    assert guided["metrics"]["states_expanded"] < plain["metrics"]["states_expanded"]


def test_heuristic_is_admissible_and_consistent_on_the_sample() -> None:
    graph = json.loads((SYNTHETIC / "canonical_graph.json").read_text(encoding="utf-8"))
    config = json.loads((SYNTHETIC / "trip_config.json").read_text(encoding="utf-8"))
    nodes = {node["id"]: node for node in graph["nodes"]}
    heuristic = _heuristic_ms(nodes, graph["edges"], config["destination"])
    assert heuristic[config["destination"]] == 0
    for edge in graph["edges"]:
        drive_ms = minutes_to_ms(edge["drive_time_minutes"])
        assert heuristic[edge["from"]] <= drive_ms + heuristic[edge["to"]]
    plain = solve(graph, config, use_heuristic=False)
    optimal_ms = minutes_to_ms(plain["total_time_minutes"])
    assert heuristic[config["origin"]] <= optimal_ms


def test_zero_time_edge_keeps_the_heuristic_admissible() -> None:
    # 0.05 degrees of longitude at this latitude is about 2.6 miles, so a
    # 5-mile road still covers the straight line. Zero drive time makes the
    # speed unbounded, which forces h to zero.
    graph = _graph(
        _node("origin", "origin", None),
        _node("spur", "charger", None, lon=-83.05),
        _node("destination", "destination", None),
        edges=[
            _edge("origin", "spur", 5, 0),
            _edge("origin", "destination", 4, 4),
        ],
    )
    nodes = {node["id"]: node for node in graph["nodes"]}
    straight = haversine_miles(
        nodes["origin"]["lat"],
        nodes["origin"]["lon"],
        nodes["spur"]["lat"],
        nodes["spur"]["lon"],
    )
    assert straight <= 5
    heuristic = _heuristic_ms(nodes, graph["edges"], "destination")
    assert heuristic == {"origin": 0, "spur": 0, "destination": 0}
    plain, guided = _agree(graph, _config())
    assert plain["total_time_minutes"] == 4
    assert guided["route_node_ids"] == ["origin", "destination"]


def _random_query(rng: random.Random, index: int) -> tuple[dict, dict]:
    if index < 80:
        charger_count = rng.randint(1, 3)
        capacity = rng.randint(4, 7)
        probability = 0.5
    elif index < 95:
        charger_count = rng.randint(4, 5)
        capacity = rng.randint(3, 5)
        probability = 0.3
    else:
        charger_count = 6
        capacity = 3
        probability = 0.25
    origin_rate = 60 if rng.random() < 0.25 else None
    nodes = [
        _node("origin", "origin", origin_rate, *_coordinate(index, 0)),
        _node("destination", "destination", None, *_coordinate(index, 1)),
    ]
    for charger_index in range(charger_count):
        rate = 60 if rng.random() < 0.5 else 30
        lat, lon = _coordinate(index, 2 + charger_index)
        nodes.append(_node(f"c{charger_index}", "charger", rate, lat, lon))
    located = {node["id"]: node for node in nodes}
    ids = list(located)
    edges = []
    for start in ids:
        for end in ids:
            if start != end and rng.random() < probability:
                edges.append(_road(rng, located, start, end))
    if not any(edge["from"] == "origin" for edge in edges):
        edges.append(_road(rng, located, "origin", "destination"))
    initial = rng.randint(0, capacity)
    reserve = rng.randint(0, min(2, capacity))
    config = _config(
        battery_capacity_kwh=capacity,
        initial_soc_kwh=initial,
        reserve_kwh=reserve,
    )
    return _graph(*nodes, edges=edges), config


def _coordinate(index: int, slot: int) -> tuple[float, float]:
    """Spread nodes by about 0.7–5 miles so the straight-line heuristic is positive."""
    lat = 42.0 + ((index * 3 + slot * 5) % 7) * 0.015
    lon = -83.0 + ((index * 5 + slot * 3) % 9) * 0.015
    return lat, lon


def _road(rng: random.Random, nodes: dict[str, dict], start: str, end: str) -> dict:
    straight = haversine_miles(
        nodes[start]["lat"],
        nodes[start]["lon"],
        nodes[end]["lat"],
        nodes[end]["lon"],
    )
    miles = float(rng.randint(1, 4))
    if miles + _LENGTH_TOLERANCE_MILES < straight:
        miles = straight
    return _edge(start, end, miles, rng.randint(1, 6))
