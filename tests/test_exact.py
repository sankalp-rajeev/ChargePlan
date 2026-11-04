"""Exact backtracking agreement with label-setting (S3)."""

from __future__ import annotations

import ast
import inspect
import json
import random
from pathlib import Path

from optimizer import exact_solver
from optimizer.battery_state_optimizer import solve as label_solve
from optimizer.exact_solver import solve as exact_solve
from optimizer.model import write_result

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = ROOT / "data" / "synthetic"


def _node(node_id: str, node_type: str, rate: float | None) -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "lat": 42.0,
        "lon": -83.0,
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
    return {"nodes": list(nodes), "edges": edges}


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


def _origin(rate: float | None = None) -> dict:
    return _node("origin", "origin", rate)


def _destination() -> dict:
    return _node("destination", "destination", None)


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
    label = label_solve(graph, config)
    exact = exact_solve(graph, config)
    assert exact["algorithm"] == "exact"
    assert label["algorithm"] == "label_setting"
    assert _objective(exact) == _objective(label)
    if exact["status"] == "FEASIBLE":
        assert exact["driving_time_minutes"] + exact["charging_time_minutes"] == (
            exact["total_time_minutes"]
        )
    return label, exact


def test_exact_search_does_not_reuse_label_setting() -> None:
    imported = set()
    tree = ast.parse(inspect.getsource(exact_solver))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "heapq" not in imported
    assert "optimizer.battery_state_optimizer" not in imported
    assert "optimizer.reconstruction" not in imported


def test_direct_partial_charge_and_skip_charge(tmp_path: Path) -> None:
    direct = _graph(_origin(), _destination(), edges=[_edge("origin", "destination", 4, 12)])
    _, exact = _agree(direct, _config())
    write_result(exact, tmp_path / "direct.json")
    assert exact["route_node_ids"] == ["origin", "destination"]
    assert exact["number_of_charging_stops"] == 0
    assert exact["events"][0]["soc_after_kwh"] == 6

    must_charge = _graph(
        _origin(60),
        _destination(),
        edges=[_edge("origin", "destination", 5, 5)],
    )
    _, charged = _agree(must_charge, _config(initial_soc_kwh=0, battery_capacity_kwh=20))
    assert charged["number_of_charging_stops"] == 1
    assert charged["events"][0]["type"] == "charge"
    assert charged["events"][0]["soc_before_kwh"] == 0
    assert charged["events"][0]["soc_after_kwh"] == 5
    assert charged["total_time_minutes"] == 10

    _, skipped = _agree(must_charge, _config(initial_soc_kwh=10))
    assert skipped["number_of_charging_stops"] == 0
    assert [event["type"] for event in skipped["events"]] == ["drive"]


def test_reserve_capacity_and_already_there() -> None:
    trip = _graph(_origin(), _destination(), edges=[_edge("origin", "destination", 8, 8)])
    blocked, _exact_blocked = _agree(trip, _config(reserve_kwh=5))
    assert blocked["status"] == "INFEASIBLE"
    allowed, exact_allowed = _agree(trip, _config(reserve_kwh=2))
    assert allowed["status"] == "FEASIBLE"
    assert exact_allowed["events"][0]["soc_after_kwh"] == 2

    below, exact_below = _agree(
        _graph(_origin(60), _destination(), edges=[_edge("origin", "destination", 1, 1)]),
        _config(initial_soc_kwh=3, reserve_kwh=5),
    )
    assert below["status"] == "INFEASIBLE"
    assert exact_below["metrics"]["states_expanded"] == 0

    _, capped = _agree(
        _graph(_origin(60), _destination(), edges=[_edge("origin", "destination", 5, 5)]),
        _config(initial_soc_kwh=4, battery_capacity_kwh=5),
    )
    assert capped["events"][0]["soc_after_kwh"] == 5

    _, stayed = _agree(
        _graph(_node("origin", "origin", None), edges=[]),
        _config(destination="origin", initial_soc_kwh=5),
    )
    assert stayed["route_node_ids"] == ["origin"]
    assert stayed["events"] == []
    assert stayed["total_time_minutes"] == 0


def test_stops_charger_choice_and_ties() -> None:
    charger = _node("charger", "charger", 60)
    _, one_stop = _agree(
        _graph(
            _origin(),
            charger,
            _destination(),
            edges=[
                _edge("origin", "destination", 30, 30),
                _edge("origin", "charger", 10, 10),
                _edge("charger", "destination", 10, 10),
            ],
        ),
        _config(initial_soc_kwh=15, battery_capacity_kwh=30),
    )
    assert one_stop["number_of_charging_stops"] == 1
    assert one_stop["total_time_minutes"] == 25

    _, two_stops = _agree(
        _graph(
            _origin(),
            charger,
            _node("charger_2", "charger", 60),
            _destination(),
            edges=[
                _edge("origin", "charger", 10, 10),
                _edge("charger", "charger_2", 10, 10),
                _edge("charger_2", "destination", 10, 10),
            ],
        ),
        _config(initial_soc_kwh=10, battery_capacity_kwh=10),
    )
    assert two_stops["number_of_charging_stops"] == 2
    assert two_stops["total_time_minutes"] == 50

    _, fast = _agree(
        _graph(
            _origin(),
            _node("slow", "charger", 10),
            _node("fast", "charger", 60),
            _destination(),
            edges=[
                _edge("origin", "slow", 5, 5),
                _edge("slow", "destination", 20, 20),
                _edge("origin", "fast", 12, 12),
                _edge("fast", "destination", 20, 20),
            ],
        ),
        _config(initial_soc_kwh=20, battery_capacity_kwh=30),
    )
    assert fast["route_node_ids"] == ["origin", "fast", "destination"]
    assert fast["total_time_minutes"] == 44

    _, fewer_stops = _agree(
        _graph(
            _origin(),
            charger,
            _destination(),
            edges=[
                _edge("origin", "destination", 10, 10),
                _edge("origin", "charger", 2, 4.5),
                _edge("charger", "destination", 2, 4.5),
            ],
        ),
        _config(initial_soc_kwh=1, battery_capacity_kwh=10, consumption_kwh_per_mile=0.1),
    )
    assert fewer_stops["number_of_charging_stops"] == 0
    assert fewer_stops["total_distance_miles"] == 10

    _, shorter = _agree(
        _graph(
            _origin(),
            _destination(),
            edges=[
                _edge("origin", "destination", 10, 10),
                _edge("origin", "destination", 8, 10),
            ],
        ),
        _config(initial_soc_kwh=20, battery_capacity_kwh=20),
    )
    assert shorter["total_distance_miles"] == 8
    assert shorter["total_time_minutes"] == 10


def test_rounding_and_sample_query_agree() -> None:
    rounded = _graph(
        _origin(),
        _destination(),
        edges=[_edge("origin", "destination", 55.2, 10)],
    )
    base = {
        "initial_soc_kwh": 20,
        "battery_capacity_kwh": 30,
        "consumption_kwh_per_mile": 0.3,
        "soc_step_kwh": 1,
    }
    _, exact = _agree(rounded, _config(reserve_kwh=3, **base))
    assert exact["events"][0]["energy_kwh_discretized"] == 17
    assert exact["events"][0]["soc_after_kwh"] == 3
    _agree(rounded, _config(reserve_kwh=2.1, **base))
    _agree(rounded, _config(reserve_kwh=3.1, **base))
    _agree(rounded, _config(**{**base, "initial_soc_kwh": 16, "reserve_kwh": 0}))

    graph = json.loads((SYNTHETIC / "canonical_graph.json").read_text(encoding="utf-8"))
    config = json.loads((SYNTHETIC / "trip_config.json").read_text(encoding="utf-8"))
    _, sample = _agree(graph, config)
    assert sample["status"] == "FEASIBLE"
    assert sample["route_node_ids"] == ["origin", "station_1", "station_2", "destination"]
    assert sample["total_time_minutes"] == 322.5
    assert sample["number_of_charging_stops"] == 2
    assert sample["events"][1]["soc_after_kwh"] == 70
    assert sample["events"][-1]["soc_after_kwh"] == 7


def test_random_tiny_graphs_match_label_setting() -> None:
    rng = random.Random(505)
    for _case in range(30):
        graph, config = _random_query(rng)
        _agree(graph, config)


def _random_query(rng: random.Random) -> tuple[dict, dict]:
    charger_count = rng.randint(0, 2)
    nodes = [_origin(60 if rng.random() < 0.3 else None), _destination()]
    for index in range(charger_count):
        rate = 60 if rng.random() < 0.5 else 30
        nodes.append(_node(f"c{index}", "charger", rate))
    ids = [node["id"] for node in nodes]
    edges = []
    for start in ids:
        for end in ids:
            if start != end and rng.random() < 0.45:
                miles = rng.randint(1, 4)
                edges.append(_edge(start, end, miles, rng.randint(1, 6)))
    if not any(edge["from"] == "origin" for edge in edges):
        edges.append(_edge("origin", "destination", rng.randint(1, 4), rng.randint(1, 6)))
    capacity = rng.randint(4, 8)
    initial = rng.randint(0, capacity)
    reserve = rng.randint(0, min(2, initial))
    config = _config(
        battery_capacity_kwh=capacity,
        initial_soc_kwh=initial,
        reserve_kwh=reserve,
    )
    return _graph(*nodes, edges=edges), config
