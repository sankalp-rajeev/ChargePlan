"""Synthetic correctness suite T1–T16 (S4).

T1–T14 run label-setting and the exact solver and require the same
feasibility and the same lexicographic label ``(time, stops, distance)``.
T15 needs A* (S10). T16 needs the farthest-reachable-charger baseline (H4).
Those two tests record the missing dependency. They do not claim the
comparison has passed.

The 100-graph check uses ``random.Random(505)`` so a disagreement can be
rebuilt from the case index.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from optimizer.battery_state_optimizer import solve as label_solve
from optimizer.exact_solver import solve as exact_solve
from optimizer.greedy_baseline import solve as greedy_solve
from optimizer.model import discretize
from optimizer.reconstruction import reconstruct

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = ROOT / "data" / "synthetic"


def _node(node_id: str, node_type: str, rate: float | None, lat: float = 42.0) -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "lat": lat,
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


def _origin(rate: float | None = None, lat: float = 42.0) -> dict:
    return _node("origin", "origin", rate, lat)


def _destination(lat: float = 42.0) -> dict:
    return _node("destination", "destination", None, lat)


def _objective(result: dict) -> tuple:
    if result["status"] != "FEASIBLE":
        return ("INFEASIBLE",)
    return (
        "FEASIBLE",
        result["total_time_minutes"],
        result["number_of_charging_stops"],
        result["total_distance_miles"],
    )


def _check_plan(result: dict, config: dict) -> None:
    capacity, _initial, reserve, delta = discretize(config)
    if result["status"] != "FEASIBLE":
        assert result["route_node_ids"] == []
        assert result["events"] == []
        return
    assert result["route_node_ids"][0] == config["origin"]
    assert result["route_node_ids"][-1] == config["destination"]
    charges = [event for event in result["events"] if event["type"] == "charge"]
    assert result["number_of_charging_stops"] == len(charges)
    previous = None
    for event in result["events"]:
        if previous is not None:
            assert event["soc_before_kwh"] == previous
        previous = event["soc_after_kwh"]
        if event["type"] == "drive":
            assert event["soc_after_kwh"] + 1e-9 >= reserve * delta
        else:
            assert event["soc_after_kwh"] <= capacity * delta + 1e-9
    drive = result["driving_time_minutes"]
    charge = result["charging_time_minutes"]
    assert drive + charge == result["total_time_minutes"]


def _agree(graph: dict, config: dict) -> tuple[dict, dict]:
    label = label_solve(graph, config)
    exact = exact_solve(graph, config)
    assert _objective(label) == _objective(exact)
    _check_plan(label, config)
    _check_plan(exact, config)
    return label, exact


def test_t1_direct_route_does_not_charge() -> None:
    graph = _graph(_origin(), _destination(), edges=[_edge("origin", "destination", 4, 12)])
    label, exact = _agree(graph, _config())
    assert label["route_node_ids"] == ["origin", "destination"]
    assert label["number_of_charging_stops"] == 0
    assert label["events"][0]["soc_before_kwh"] == 10
    assert label["events"][0]["soc_after_kwh"] == 6
    assert exact["events"][0]["soc_after_kwh"] == 6


def test_t2_one_mandatory_stop_keeps_the_reserve() -> None:
    graph = _graph(
        _origin(),
        _node("charger", "charger", 60),
        _destination(),
        edges=[
            _edge("origin", "destination", 30, 30),
            _edge("origin", "charger", 10, 10),
            _edge("charger", "destination", 10, 10),
        ],
    )
    label, _exact = _agree(graph, _config(initial_soc_kwh=15, battery_capacity_kwh=30))
    assert label["route_node_ids"] == ["origin", "charger", "destination"]
    assert label["number_of_charging_stops"] == 1
    assert label["total_time_minutes"] == 25
    assert label["events"][-1]["soc_after_kwh"] >= 0


def test_t3_optimizer_prefers_total_time_over_the_near_slow_charger() -> None:
    """The slow charger sits closer to the destination. Greedy comparison waits on H4."""
    graph = _graph(
        _origin(lat=42.0),
        _node("slow", "charger", 10, lat=42.02),
        _node("fast", "charger", 60, lat=42.40),
        _destination(lat=42.05),
        edges=[
            _edge("origin", "slow", 5, 5),
            _edge("slow", "destination", 20, 20),
            _edge("origin", "fast", 12, 12),
            _edge("fast", "destination", 20, 20),
        ],
    )
    label, _exact = _agree(graph, _config(initial_soc_kwh=20, battery_capacity_kwh=30))
    assert label["route_node_ids"] == ["origin", "fast", "destination"]
    assert label["total_time_minutes"] == 44
    with pytest.raises(NotImplementedError):
        greedy_solve(graph, _config(initial_soc_kwh=20, battery_capacity_kwh=30))


def test_t4_partial_charge_beats_filling_the_battery() -> None:
    graph = _graph(
        _origin(60),
        _destination(),
        edges=[_edge("origin", "destination", 5, 5)],
    )
    label, _exact = _agree(graph, _config(initial_soc_kwh=0, battery_capacity_kwh=20))
    assert label["events"][0]["soc_before_kwh"] == 0
    assert label["events"][0]["soc_after_kwh"] == 5
    assert label["total_time_minutes"] == 10
    full_charge_minutes = 20 + 5
    assert label["total_time_minutes"] < full_charge_minutes


def test_t5_unreachable_destination_is_infeasible() -> None:
    graph = _graph(_origin(), _destination(), edges=[_edge("origin", "destination", 30, 30)])
    label, _exact = _agree(graph, _config(initial_soc_kwh=10, battery_capacity_kwh=10))
    assert label["status"] == "INFEASIBLE"


def test_t6_reserve_rejects_an_arrival_below_the_floor() -> None:
    graph = _graph(_origin(), _destination(), edges=[_edge("origin", "destination", 8, 8)])
    blocked, _exact = _agree(graph, _config(reserve_kwh=5))
    assert blocked["status"] == "INFEASIBLE"
    allowed, exact = _agree(graph, _config(reserve_kwh=2))
    assert allowed["status"] == "FEASIBLE"
    assert exact["events"][0]["soc_after_kwh"] == 2


def test_t7_capacity_boundary_accepts_exact_energy_only() -> None:
    accepted = _graph(_origin(), _destination(), edges=[_edge("origin", "destination", 8, 8)])
    rejected = _graph(_origin(), _destination(), edges=[_edge("origin", "destination", 9, 9)])
    config = _config(initial_soc_kwh=10, battery_capacity_kwh=10, reserve_kwh=2)
    ok, _exact = _agree(accepted, config)
    assert ok["status"] == "FEASIBLE"
    assert ok["events"][0]["soc_after_kwh"] == 2
    blocked, _other = _agree(rejected, config)
    assert blocked["status"] == "INFEASIBLE"


def test_t8_faster_charger_detour_beats_the_short_slow_one() -> None:
    graph = _graph(
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
    )
    label, _exact = _agree(graph, _config(initial_soc_kwh=20, battery_capacity_kwh=30))
    assert label["route_node_ids"] == ["origin", "fast", "destination"]
    assert label["total_distance_miles"] == 32
    assert label["total_time_minutes"] == 44


def test_t9_two_stops_keep_a_consistent_soc_chain() -> None:
    graph = _graph(
        _origin(),
        _node("charger", "charger", 60),
        _node("charger_2", "charger", 60),
        _destination(),
        edges=[
            _edge("origin", "charger", 10, 10),
            _edge("charger", "charger_2", 10, 10),
            _edge("charger_2", "destination", 10, 10),
        ],
    )
    label, _exact = _agree(graph, _config(initial_soc_kwh=10, battery_capacity_kwh=10))
    assert label["number_of_charging_stops"] == 2
    assert label["total_time_minutes"] == 50
    assert [event["type"] for event in label["events"]] == [
        "drive",
        "charge",
        "drive",
        "charge",
        "drive",
    ]


def test_t10_tie_break_prefers_fewer_stops_then_shorter_distance() -> None:
    fewer_stops = _graph(
        _origin(),
        _node("charger", "charger", 60),
        _destination(),
        edges=[
            _edge("origin", "destination", 10, 10),
            _edge("origin", "charger", 2, 4.5),
            _edge("charger", "destination", 2, 4.5),
        ],
    )
    label, _exact = _agree(
        fewer_stops,
        _config(initial_soc_kwh=1, battery_capacity_kwh=10, consumption_kwh_per_mile=0.1),
    )
    assert label["total_time_minutes"] == 10
    assert label["number_of_charging_stops"] == 0
    assert label["total_distance_miles"] == 10

    shorter = _graph(
        _origin(),
        _destination(),
        edges=[
            _edge("origin", "destination", 10, 10),
            _edge("origin", "destination", 8, 10),
        ],
    )
    tied, _other = _agree(shorter, _config(initial_soc_kwh=20, battery_capacity_kwh=20))
    assert tied["total_time_minutes"] == 10
    assert tied["number_of_charging_stops"] == 0
    assert tied["total_distance_miles"] == 8


def test_t11_low_starting_soc_charges_at_origin_or_is_infeasible() -> None:
    can_charge = _graph(
        _origin(60),
        _destination(),
        edges=[_edge("origin", "destination", 4, 4)],
    )
    label, _exact = _agree(can_charge, _config(initial_soc_kwh=0, battery_capacity_kwh=10))
    assert label["number_of_charging_stops"] == 1
    assert label["events"][0]["type"] == "charge"
    assert label["events"][0]["station"] == "origin"

    cannot = _graph(_origin(), _destination(), edges=[_edge("origin", "destination", 4, 4)])
    blocked, _other = _agree(cannot, _config(initial_soc_kwh=0, battery_capacity_kwh=10))
    assert blocked["status"] == "INFEASIBLE"


def test_t12_does_not_charge_when_the_destination_is_already_reachable() -> None:
    graph = _graph(
        _origin(),
        _node("charger", "charger", 60),
        _destination(),
        edges=[
            _edge("origin", "destination", 40, 40),
            _edge("origin", "charger", 5, 5),
            _edge("charger", "destination", 10, 10),
        ],
    )
    label, _exact = _agree(graph, _config(initial_soc_kwh=20, battery_capacity_kwh=20))
    assert label["route_node_ids"] == ["origin", "charger", "destination"]
    assert label["number_of_charging_stops"] == 0
    assert [event["type"] for event in label["events"]] == ["drive", "drive"]
    assert label["events"][0]["soc_after_kwh"] == 15
    assert label["events"][1]["soc_after_kwh"] == 5


def test_t13_conservative_rounding_on_a_whole_trip() -> None:
    graph = _graph(_origin(), _destination(), edges=[_edge("origin", "destination", 55.2, 10)])
    base = {
        "initial_soc_kwh": 20,
        "battery_capacity_kwh": 30,
        "consumption_kwh_per_mile": 0.3,
        "soc_step_kwh": 1,
    }
    exact_reserve, _exact = _agree(graph, _config(reserve_kwh=3, **base))
    assert exact_reserve["events"][0]["energy_kwh_discretized"] == 17
    assert exact_reserve["events"][0]["soc_after_kwh"] == 3
    still_ok, _other = _agree(graph, _config(reserve_kwh=2.1, **base))
    assert still_ok["status"] == "FEASIBLE"
    rejected, _blocked = _agree(graph, _config(reserve_kwh=3.1, **base))
    assert rejected["status"] == "INFEASIBLE"
    short, _none = _agree(graph, _config(**{**base, "initial_soc_kwh": 16, "reserve_kwh": 0}))
    assert short["status"] == "INFEASIBLE"


def test_t14_stop_counting_for_one_visit_two_chargers_and_two_visits() -> None:
    five_steps = _graph(
        _origin(60),
        _destination(),
        edges=[_edge("origin", "destination", 5, 5)],
    )
    label, _exact = _agree(five_steps, _config(initial_soc_kwh=0, battery_capacity_kwh=20))
    assert label["number_of_charging_stops"] == 1
    assert label["events"][0]["duration_minutes"] == 5
    assert label["events"][0]["soc_after_kwh"] == 5

    two_chargers = _graph(
        _origin(),
        _node("charger", "charger", 60),
        _node("charger_2", "charger", 60),
        _destination(),
        edges=[
            _edge("origin", "charger", 10, 10),
            _edge("charger", "charger_2", 10, 10),
            _edge("charger_2", "destination", 10, 10),
        ],
    )
    second, _other = _agree(two_chargers, _config(initial_soc_kwh=10, battery_capacity_kwh=10))
    assert second["number_of_charging_stops"] == 2

    # A second visit to the same charger is not the unique optimum when the
    # departure used on that visit is already feasible after the first fill.
    # The counting rule is checked on an explicit two-visit trace instead.
    built = reconstruct(
        _two_visit_parents(),
        ("destination", 0, 0),
        {"nodes": [], "edges": []},
        {"soc_step_kwh": 1},
    )
    charges = [event for event in built["events"] if event["type"] == "charge"]
    assert len(charges) == 2
    assert charges[0]["station"] == "charger"
    assert charges[1]["station"] == "charger"
    assert built["route_node_ids"] == ["origin", "charger", "waypoint", "charger", "destination"]


def test_t15_astar_agreement_is_blocked_on_s10() -> None:
    """T15 is not complete. A* is S10 and still raises NotImplementedError."""
    graph = _graph(_origin(), _destination(), edges=[_edge("origin", "destination", 4, 12)])
    with pytest.raises(NotImplementedError):
        label_solve(graph, _config(), use_heuristic=True)


def test_t16_greedy_failure_case_is_blocked_on_h4() -> None:
    """T16 is not complete. The instance is fixed; FRC itself is H4.

    Origin is at lon -83, the destination at lon -85, and the only useful
    charger at lon -82. The charger is farther from the destination than the
    origin is, so Section 12 returns INFEASIBLE once H4 exists. Label-setting
    can charge there and finish.
    """
    graph = _graph(
        {
            "id": "origin",
            "type": "origin",
            "lat": 42.0,
            "lon": -83.0,
            "charge_rate_kw": None,
        },
        {
            "id": "away",
            "type": "charger",
            "lat": 42.0,
            "lon": -82.0,
            "charge_rate_kw": 60,
        },
        {
            "id": "destination",
            "type": "destination",
            "lat": 42.0,
            "lon": -85.0,
            "charge_rate_kw": None,
        },
        edges=[
            _edge("origin", "destination", 110, 110),
            _edge("origin", "away", 50, 50),
            _edge("away", "destination", 160, 160),
        ],
    )
    config = _config(initial_soc_kwh=60, battery_capacity_kwh=200)
    label, _exact = _agree(graph, config)
    assert label["status"] == "FEASIBLE"
    assert label["route_node_ids"] == ["origin", "away", "destination"]
    assert label["number_of_charging_stops"] == 1
    with pytest.raises(NotImplementedError):
        greedy_solve(graph, config)


def test_edge_cases_zero_loop_exact_reserve_and_sample() -> None:
    loop = _graph(
        _origin(),
        _destination(),
        edges=[
            _edge("origin", "origin", 0, 0),
            _edge("origin", "destination", 4, 4),
        ],
    )
    label, _exact = _agree(loop, _config())
    assert label["route_node_ids"] == ["origin", "destination"]
    assert label["total_time_minutes"] == 4

    exact_floor = _graph(_origin(), _destination(), edges=[_edge("origin", "destination", 5, 5)])
    held, _other = _agree(exact_floor, _config(initial_soc_kwh=5, reserve_kwh=0))
    assert held["events"][0]["soc_after_kwh"] == 0

    graph = json.loads((SYNTHETIC / "canonical_graph.json").read_text(encoding="utf-8"))
    config = json.loads((SYNTHETIC / "trip_config.json").read_text(encoding="utf-8"))
    sample, _sample_exact = _agree(graph, config)
    assert sample["total_time_minutes"] == 322.5
    assert sample["number_of_charging_stops"] == 2


def test_one_hundred_seeded_graphs_match_the_exact_solver() -> None:
    rng = random.Random(505)
    for index in range(100):
        graph, config = _random_query(rng, index)
        label = label_solve(graph, config)
        exact = exact_solve(graph, config)
        assert _objective(label) == _objective(exact), index
        _check_plan(label, config)
        _check_plan(exact, config)


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
    nodes = [_origin(origin_rate), _destination()]
    for charger_index in range(charger_count):
        rate = 60 if rng.random() < 0.5 else 30
        nodes.append(_node(f"c{charger_index}", "charger", rate))
    ids = [node["id"] for node in nodes]
    edges = []
    for start in ids:
        for end in ids:
            if start != end and rng.random() < probability:
                miles = rng.randint(1, 4)
                edges.append(_edge(start, end, miles, rng.randint(1, 6)))
    if not any(edge["from"] == "origin" for edge in edges):
        edges.append(_edge("origin", "destination", rng.randint(1, 3), rng.randint(1, 4)))
    initial = rng.randint(0, capacity)
    reserve_cap = min(2, capacity)
    reserve = rng.randint(0, reserve_cap)
    config = _config(
        battery_capacity_kwh=capacity,
        initial_soc_kwh=initial,
        reserve_kwh=reserve,
    )
    return _graph(*nodes, edges=edges), config


def _two_visit_parents() -> dict:
    start = ("origin", 5, 0)
    first = ("charger", 0, 0)
    after_first = ("charger", 5, 1)
    between = ("waypoint", 2, 0)
    second = ("charger", 1, 0)
    after_second = ("charger", 4, 1)
    goal = ("destination", 0, 0)

    def drive(src: str, end: str, energy: int, minutes: int) -> dict:
        return {
            "type": "drive",
            "from": src,
            "to": end,
            "distance_miles": minutes,
            "duration_ms": minutes * 60_000,
            "energy_steps": energy,
            "soc_before_steps": energy,
            "soc_after_steps": 0,
        }

    def charge(before: int, after: int, duration_ms: int) -> dict:
        return {
            "type": "charge",
            "station": "charger",
            "duration_ms": duration_ms,
            "soc_before_steps": before,
            "soc_after_steps": after,
        }

    return {
        start: None,
        first: (start, drive("origin", "charger", 5, 5)),
        after_first: (first, charge(0, 5, 300_000)),
        between: (after_first, drive("charger", "waypoint", 3, 5)),
        second: (between, drive("waypoint", "charger", 1, 2)),
        after_second: (second, charge(1, 4, 180_000)),
        goal: (after_second, drive("charger", "destination", 4, 4)),
    }
