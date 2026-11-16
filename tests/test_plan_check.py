"""Unit tests for the independent plan checker in ``experiments.plan_check``."""

from __future__ import annotations

import copy
from collections.abc import Callable

import pytest

from experiments.exact_suite import build_suite
from experiments.instances import exact_block_reason, load_sample
from experiments.plan_check import check_plan
from optimizer.cli import run_algorithm


def _node(node_id: str, kind: str, lon: float, rate: float | None) -> dict:
    return {"id": node_id, "type": kind, "lat": 42.0, "lon": lon, "charge_rate_kw": rate}


def _graph() -> dict:
    return {
        "nodes": [
            _node("A", "origin", -83.0, None),
            _node("C", "charger", -83.1, 60),
            _node("B", "destination", -83.2, None),
        ],
        "edges": [
            {"from": "A", "to": "C", "distance_miles": 20, "drive_time_minutes": 20},
            {"from": "C", "to": "B", "distance_miles": 30, "drive_time_minutes": 30},
        ],
    }


def _config() -> dict:
    return {
        "origin": "A",
        "destination": "B",
        "battery_capacity_kwh": 40,
        "initial_soc_kwh": 25,
        "reserve_kwh": 5,
        "consumption_kwh_per_mile": 1,
        "soc_step_kwh": 1,
    }


def _result() -> dict:
    """Drive 20 mi (25 -> 5 kWh), charge 30 kWh at 60 kW (30 min), drive 30 mi."""
    return {
        "status": "FEASIBLE",
        "algorithm": "label_setting",
        "total_time_minutes": 80,
        "driving_time_minutes": 50,
        "charging_time_minutes": 30,
        "total_distance_miles": 50,
        "number_of_charging_stops": 1,
        "soc_step_kwh": 1,
        "route_node_ids": ["A", "C", "B"],
        "events": [
            {
                "type": "drive",
                "from": "A",
                "to": "C",
                "distance_miles": 20,
                "duration_minutes": 20,
                "energy_kwh_discretized": 20,
                "soc_before_kwh": 25,
                "soc_after_kwh": 5,
            },
            {
                "type": "charge",
                "station": "C",
                "soc_before_kwh": 5,
                "soc_after_kwh": 35,
                "duration_minutes": 30,
            },
            {
                "type": "drive",
                "from": "C",
                "to": "B",
                "distance_miles": 30,
                "duration_minutes": 30,
                "energy_kwh_discretized": 30,
                "soc_before_kwh": 35,
                "soc_after_kwh": 5,
            },
        ],
        "metrics": {"runtime_ms": 1.0, "states_expanded": 1, "peak_states_stored": 1},
    }


def test_valid_plan_has_no_problems() -> None:
    assert check_plan(_graph(), _config(), _result()) == []


def test_the_hand_built_plan_is_what_label_setting_returns() -> None:
    solved = run_algorithm("label_setting", _graph(), _config())
    assert solved["total_time_minutes"] == 80
    assert solved["number_of_charging_stops"] == 1
    assert solved["route_node_ids"] == ["A", "C", "B"]
    assert check_plan(_graph(), _config(), solved) == []


def _mutate_result(change: Callable[[dict], None]) -> tuple[dict, dict, dict]:
    result = _result()
    change(result)
    return _graph(), _config(), result


def _mutate_graph(change: Callable[[dict], None]) -> tuple[dict, dict, dict]:
    graph = _graph()
    change(graph)
    return graph, _config(), _result()


def _mutate_config(**updates: float) -> tuple[dict, dict, dict]:
    config = _config()
    config.update(updates)
    return _graph(), config, _result()


def _event(result: dict, index: int) -> dict:
    return result["events"][index]


def _drop_last_leg(result: dict) -> None:
    result["events"].pop()
    result["route_node_ids"].pop()


CORRUPTIONS: list[tuple[str, Callable[[], tuple[dict, dict, dict]], str]] = [
    # Continuous walk from the origin to the destination.
    (
        "first drive starts away from the origin",
        lambda: _mutate_result(lambda r: _event(r, 0).update({"from": "C"})),
        "drive starts at 'C' but the walk is at 'A'",
    ),
    (
        "second drive does not start where the first ended",
        lambda: _mutate_result(lambda r: _event(r, 2).update({"from": "A"})),
        "drive starts at 'A' but the walk is at 'C'",
    ),
    (
        "charge at a node other than the current one",
        lambda: _mutate_result(lambda r: _event(r, 1).update({"station": "B"})),
        "charge at 'B' but the walk is at 'C'",
    ),
    (
        "walk ends before the destination",
        lambda: _mutate_result(_drop_last_leg),
        "walk ends at 'C', not the destination 'B'",
    ),
    (
        "drive on an edge that is not in the graph",
        lambda: _mutate_result(lambda r: _event(r, 2).update({"to": "A"})),
        "no edge 'C' -> 'A' in the graph",
    ),
    (
        "drive distance differs from the edge",
        lambda: _mutate_result(lambda r: _event(r, 0).update({"distance_miles": 19})),
        "matches no edge",
    ),
    (
        "route_node_ids differs from the walk",
        lambda: _mutate_result(lambda r: r.update({"route_node_ids": ["A", "B"]})),
        "route_node_ids does not equal the walk",
    ),
    # SOC chain.
    (
        "first soc_before is not the initial energy",
        lambda: _mutate_result(lambda r: _event(r, 0).update({"soc_before_kwh": 26})),
        "event 0: soc_before is 26 steps but the previous state left 25",
    ),
    (
        "charge does not start from the arrival SOC",
        lambda: _mutate_result(lambda r: _event(r, 1).update({"soc_before_kwh": 6})),
        "event 1: soc_before is 6 steps but the previous state left 5",
    ),
    (
        "drive soc_after is not soc_before minus the energy",
        lambda: _mutate_result(lambda r: _event(r, 0).update({"soc_after_kwh": 6})),
        "soc_after is 6 steps, expected 5",
    ),
    (
        "drive energy is not the ceiled energy",
        lambda: _mutate_result(lambda r: _event(r, 0).update({"energy_kwh_discretized": 19})),
        "drive energy is not ceil(c*d/dE) = 20 steps",
    ),
    (
        "SOC value off the step grid",
        lambda: _mutate_result(lambda r: _event(r, 1).update({"soc_after_kwh": 35.5})),
        "soc_after_kwh is not a multiple of the SOC step",
    ),
    # Reserve, capacity, chargers.
    (
        "drive arrives below the reserve",
        lambda: _mutate_config(reserve_kwh=6),
        "below the reserve of 6",
    ),
    (
        "charge above capacity",
        lambda: _mutate_result(
            lambda r: (
                _event(r, 1).update({"soc_after_kwh": 41, "duration_minutes": 36}),
                _event(r, 2).update({"soc_before_kwh": 41}),
            )
        ),
        "above capacity 40",
    ),
    (
        "capacity is floored to a whole step",
        lambda: _mutate_config(battery_capacity_kwh=34.9),
        "above capacity 34",
    ),
    (
        "charge at a node without a charger",
        lambda: _mutate_graph(lambda g: g["nodes"][1].update({"charge_rate_kw": None})),
        "'C' has no charger",
    ),
    (
        "charge adds no energy",
        lambda: _mutate_result(lambda r: _event(r, 1).update({"soc_after_kwh": 5})),
        "charge adds no energy",
    ),
    # Stop counting.
    (
        "stop count is too high",
        lambda: _mutate_result(lambda r: r.update({"number_of_charging_stops": 2})),
        "number_of_charging_stops is 2, recomputed 1",
    ),
    (
        "stop count is too low",
        lambda: _mutate_result(lambda r: r.update({"number_of_charging_stops": 0})),
        "number_of_charging_stops is 0, recomputed 1",
    ),
    # Times and distance.
    (
        "total time is off by more than a millisecond",
        lambda: _mutate_result(lambda r: r.update({"total_time_minutes": 80.001})),
        "total_time_minutes",
    ),
    (
        "driving time is wrong",
        lambda: _mutate_result(lambda r: r.update({"driving_time_minutes": 49})),
        "driving_time_minutes",
    ),
    (
        "charging time is wrong",
        lambda: _mutate_result(lambda r: r.update({"charging_time_minutes": 31})),
        "charging_time_minutes",
    ),
    (
        "charge event duration is wrong",
        lambda: _mutate_result(lambda r: _event(r, 1).update({"duration_minutes": 20})),
        "event 1: charge duration",
    ),
    (
        "drive event duration is wrong",
        lambda: _mutate_result(lambda r: _event(r, 2).update({"duration_minutes": 29})),
        "event 2: drive duration",
    ),
    (
        "distance is wrong",
        lambda: _mutate_result(lambda r: r.update({"total_distance_miles": 50.01})),
        "total_distance_miles",
    ),
    (
        "soc_step_kwh differs from the config",
        lambda: _mutate_result(lambda r: r.update({"soc_step_kwh": 2})),
        "soc_step_kwh differs",
    ),
]


@pytest.mark.parametrize(
    ("name", "build", "expected"),
    CORRUPTIONS,
    ids=[name for name, _build, _expected in CORRUPTIONS],
)
def test_each_corruption_is_reported(
    name: str, build: Callable[[], tuple[dict, dict, dict]], expected: str
) -> None:
    graph, config, result = build()
    problems = check_plan(graph, config, result)
    assert problems, name
    assert any(expected in problem for problem in problems), problems


def test_infeasible_result_must_be_empty() -> None:
    empty = {
        "status": "INFEASIBLE",
        "algorithm": "label_setting",
        "total_time_minutes": 0,
        "driving_time_minutes": 0,
        "charging_time_minutes": 0,
        "total_distance_miles": 0,
        "number_of_charging_stops": 0,
        "soc_step_kwh": 1,
        "route_node_ids": [],
        "events": [],
        "metrics": {"runtime_ms": 0.0, "states_expanded": 0, "peak_states_stored": 0},
    }
    assert check_plan(_graph(), _config(), empty) == []

    with_route = copy.deepcopy(empty)
    with_route["route_node_ids"] = ["A"]
    assert "INFEASIBLE result has a route" in check_plan(_graph(), _config(), with_route)

    with_events = copy.deepcopy(empty)
    with_events["events"] = _result()["events"]
    assert "INFEASIBLE result has events" in check_plan(_graph(), _config(), with_events)


def test_rounding_follows_the_prd_rules() -> None:
    """16.56 kWh costs 17 steps; capacity and initial SOC floor; reserve ceils."""
    graph = {
        "nodes": [_node("A", "origin", -83.0, None), _node("B", "destination", -83.1, None)],
        "edges": [{"from": "A", "to": "B", "distance_miles": 55.2, "drive_time_minutes": 50}],
    }
    config = {
        "origin": "A",
        "destination": "B",
        "battery_capacity_kwh": 30.9,
        "initial_soc_kwh": 20.9,
        "reserve_kwh": 3.1,
        "consumption_kwh_per_mile": 0.3,
        "soc_step_kwh": 1,
    }
    result = {
        "status": "FEASIBLE",
        "algorithm": "label_setting",
        "total_time_minutes": 50,
        "driving_time_minutes": 50,
        "charging_time_minutes": 0,
        "total_distance_miles": 55.2,
        "number_of_charging_stops": 0,
        "soc_step_kwh": 1,
        "route_node_ids": ["A", "B"],
        "events": [
            {
                "type": "drive",
                "from": "A",
                "to": "B",
                "distance_miles": 55.2,
                "duration_minutes": 50,
                "energy_kwh_discretized": 17,
                "soc_before_kwh": 20,
                "soc_after_kwh": 3,
            }
        ],
        "metrics": {"runtime_ms": 0.0, "states_expanded": 1, "peak_states_stored": 1},
    }
    # 20 - 17 = 3, but the reserve 3.1 ceils to 4, so the drive is not allowed.
    assert any("below the reserve of 4" in p for p in check_plan(graph, config, result))
    config["reserve_kwh"] = 3.0
    assert check_plan(graph, config, result) == []
    # Flooring energy (16 steps) instead of ceiling it is caught.
    result["events"][0]["energy_kwh_discretized"] = 16
    result["events"][0]["soc_after_kwh"] = 4
    assert any("ceil(c*d/dE) = 17" in p for p in check_plan(graph, config, result))


def test_consecutive_charges_at_one_node_are_one_stop() -> None:
    result = _result()
    charge = _event(result, 1)
    first = {**charge, "soc_after_kwh": 20, "duration_minutes": 15}
    second = {**charge, "soc_before_kwh": 20, "duration_minutes": 15}
    result["events"][1:2] = [first, second]
    assert check_plan(_graph(), _config(), result) == []


def _drive(start: str, end: str, miles: float, before: int, after: int) -> dict:
    return {
        "type": "drive",
        "from": start,
        "to": end,
        "distance_miles": miles,
        "duration_minutes": miles,
        "energy_kwh_discretized": before - after,
        "soc_before_kwh": before,
        "soc_after_kwh": after,
    }


def _charge(station: str, before: int, after: int) -> dict:
    return {
        "type": "charge",
        "station": station,
        "soc_before_kwh": before,
        "soc_after_kwh": after,
        "duration_minutes": after - before,
    }


def test_separate_visits_to_one_charger_are_separate_stops() -> None:
    """A, C and A again each charge once: three stops, T14 style."""
    graph = _graph()
    graph["nodes"][0]["charge_rate_kw"] = 60
    graph["edges"] += [
        {"from": "C", "to": "A", "distance_miles": 20, "drive_time_minutes": 20},
        {"from": "A", "to": "B", "distance_miles": 30, "drive_time_minutes": 30},
    ]
    result = {
        "status": "FEASIBLE",
        "algorithm": "label_setting",
        "total_time_minutes": 120,
        "driving_time_minutes": 70,
        "charging_time_minutes": 50,
        "total_distance_miles": 70,
        "number_of_charging_stops": 3,
        "soc_step_kwh": 1,
        "route_node_ids": ["A", "C", "A", "B"],
        "events": [
            _charge("A", 25, 30),
            _drive("A", "C", 20, 30, 10),
            _charge("C", 10, 30),
            _drive("C", "A", 20, 30, 10),
            _charge("A", 10, 35),
            _drive("A", "B", 30, 35, 5),
        ],
        "metrics": {"runtime_ms": 0.0, "states_expanded": 1, "peak_states_stored": 1},
    }
    assert check_plan(graph, _config(), result) == []
    result["number_of_charging_stops"] = 2
    assert any("recomputed 3" in problem for problem in check_plan(graph, _config(), result))


@pytest.mark.parametrize(
    "bad",
    [
        {"events": "not a list"},
        {"events": [123]},
        {"events": [{"type": "drive"}]},
        {"events": [{"type": "teleport", "soc_before_kwh": 25, "soc_after_kwh": 25}]},
        {"status": "MAYBE"},
    ],
)
def test_malformed_results_are_reported_not_raised(bad: dict) -> None:
    result = {**_result(), **bad}
    assert check_plan(_graph(), _config(), result)


def _solver_outputs() -> list[tuple[str, dict, dict, dict]]:
    outputs = []
    instances = [load_sample(), *build_suite(505, 50)]
    for instance in instances:
        names = ["label_setting", "astar", "greedy"]
        if exact_block_reason(instance.graph, instance.config) is None:
            names.append("exact")
        for name in names:
            result = run_algorithm(name, instance.graph, instance.config)
            label = f"{instance.instance_id}/{name}"
            outputs.append((label, instance.graph, instance.config, result))
    return outputs


def test_checker_accepts_every_solver_output_on_the_sample_and_fifty_seeded_graphs() -> None:
    checked = 0
    feasible = 0
    for label, graph, config, result in _solver_outputs():
        assert check_plan(graph, config, result) == [], label
        checked += 1
        feasible += result["status"] == "FEASIBLE"
    assert checked > 150
    assert feasible > 100


def test_checker_rejects_a_slightly_wrong_solver_output() -> None:
    graph, config, result = None, None, None
    for _label, g, c, r in _solver_outputs():
        if r["status"] == "FEASIBLE" and r["number_of_charging_stops"] >= 1:
            graph, config, result = g, c, copy.deepcopy(r)
            break
    assert result is not None
    result["total_time_minutes"] += 0.5
    assert check_plan(graph, config, result)
