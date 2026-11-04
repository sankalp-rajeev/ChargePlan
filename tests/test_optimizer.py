"""Battery-state label-setting search and route reconstruction (S2)."""

from __future__ import annotations

import json
from pathlib import Path

from optimizer.battery_state_optimizer import solve
from optimizer.cli import main
from optimizer.model import write_result
from optimizer.reconstruction import reconstruct

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


def _save(result: dict, tmp_path: Path) -> None:
    write_result(result, tmp_path / "result.json")


def test_direct_route_does_not_charge(tmp_path: Path) -> None:
    graph = _graph(
        _origin(),
        _destination(),
        edges=[_edge("origin", "destination", 4, 12)],
    )
    result = solve(graph, _config())
    _save(result, tmp_path)
    assert result["status"] == "FEASIBLE"
    assert result["algorithm"] == "label_setting"
    assert result["route_node_ids"] == ["origin", "destination"]
    assert result["number_of_charging_stops"] == 0
    assert result["events"] == [
        {
            "type": "drive",
            "from": "origin",
            "to": "destination",
            "distance_miles": 4,
            "duration_minutes": 12,
            "energy_kwh_discretized": 4,
            "soc_before_kwh": 10,
            "soc_after_kwh": 6,
        }
    ]
    assert result["total_time_minutes"] == 12
    assert result["driving_time_minutes"] == 12
    assert result["charging_time_minutes"] == 0
    assert result["total_distance_miles"] == 4
    assert result["metrics"]["states_expanded"] == 2
    assert result["metrics"]["peak_states_stored"] == 2


def test_already_at_destination() -> None:
    graph = _graph(_node("origin", "origin", None), edges=[])
    result = solve(graph, _config(destination="origin", initial_soc_kwh=5))
    assert result["status"] == "FEASIBLE"
    assert result["route_node_ids"] == ["origin"]
    assert result["events"] == []
    assert result["total_time_minutes"] == 0
    assert result["number_of_charging_stops"] == 0


def test_partial_charge_beats_filling_the_battery() -> None:
    graph = _graph(
        _origin(60),
        _destination(),
        edges=[_edge("origin", "destination", 5, 5)],
    )
    result = solve(graph, _config(initial_soc_kwh=0, battery_capacity_kwh=20))
    assert result["status"] == "FEASIBLE"
    assert result["number_of_charging_stops"] == 1
    assert result["total_time_minutes"] == 10
    charge = result["events"][0]
    assert charge["type"] == "charge"
    assert charge["soc_before_kwh"] == 0
    assert charge["soc_after_kwh"] == 5
    assert charge["duration_minutes"] == 5
    assert result["events"][1]["soc_after_kwh"] == 0


def test_does_not_charge_when_destination_is_reachable() -> None:
    graph = _graph(
        _origin(60),
        _destination(),
        edges=[_edge("origin", "destination", 5, 5)],
    )
    result = solve(graph, _config(initial_soc_kwh=10))
    assert result["number_of_charging_stops"] == 0
    assert [event["type"] for event in result["events"]] == ["drive"]
    assert result["events"][0]["soc_after_kwh"] == 5


def test_reserve_blocks_an_arrival_below_the_floor() -> None:
    graph = _graph(
        _origin(),
        _destination(),
        edges=[_edge("origin", "destination", 8, 8)],
    )
    blocked = solve(graph, _config(reserve_kwh=5))
    assert blocked["status"] == "INFEASIBLE"
    assert blocked["route_node_ids"] == []
    assert blocked["events"] == []
    assert "runtime_ms" in blocked["metrics"]

    allowed = solve(graph, _config(reserve_kwh=2))
    assert allowed["status"] == "FEASIBLE"
    assert allowed["events"][0]["soc_after_kwh"] == 2


def test_initial_soc_below_reserve_is_infeasible() -> None:
    graph = _graph(
        _origin(60),
        _destination(),
        edges=[_edge("origin", "destination", 1, 1)],
    )
    result = solve(graph, _config(initial_soc_kwh=3, reserve_kwh=5))
    assert result["status"] == "INFEASIBLE"
    assert result["metrics"]["states_expanded"] == 0


def test_charge_stops_at_capacity() -> None:
    graph = _graph(
        _origin(60),
        _destination(),
        edges=[_edge("origin", "destination", 5, 5)],
    )
    result = solve(graph, _config(initial_soc_kwh=4, battery_capacity_kwh=5))
    charge = result["events"][0]
    assert charge["soc_before_kwh"] == 4
    assert charge["soc_after_kwh"] == 5
    assert result["events"][1]["soc_after_kwh"] == 0


def test_one_mandatory_stop_and_two_stops() -> None:
    charger = _node("charger", "charger", 60)
    graph = _graph(
        _origin(),
        charger,
        _destination(),
        edges=[
            _edge("origin", "destination", 30, 30),
            _edge("origin", "charger", 10, 10),
            _edge("charger", "destination", 10, 10),
        ],
    )
    one_stop = solve(graph, _config(initial_soc_kwh=15, battery_capacity_kwh=30))
    assert one_stop["route_node_ids"] == ["origin", "charger", "destination"]
    assert one_stop["number_of_charging_stops"] == 1
    assert one_stop["total_time_minutes"] == 25

    second = _node("charger_2", "charger", 60)
    two = _graph(
        _origin(),
        charger,
        second,
        _destination(),
        edges=[
            _edge("origin", "charger", 10, 10),
            _edge("charger", "charger_2", 10, 10),
            _edge("charger_2", "destination", 10, 10),
        ],
    )
    two_stops = solve(two, _config(initial_soc_kwh=10, battery_capacity_kwh=10))
    assert two_stops["number_of_charging_stops"] == 2
    assert [event["type"] for event in two_stops["events"]] == [
        "drive",
        "charge",
        "drive",
        "charge",
        "drive",
    ]
    assert two_stops["total_time_minutes"] == 50


def test_two_visits_to_one_charger_count_as_two_stops() -> None:
    start = ("origin", 5, 0)
    first = ("charger", 0, 0)
    after_first = ("charger", 5, 1)
    between = ("waypoint", 2, 0)
    second = ("charger", 1, 0)
    after_second = ("charger", 4, 1)
    goal = ("destination", 0, 0)
    parent = {
        start: None,
        first: (
            start,
            _drive("origin", "charger", 5, 5),
        ),
        after_first: (
            first,
            _charge("charger", 0, 5, 300_000),
        ),
        between: (
            after_first,
            _drive("charger", "waypoint", 3, 5),
        ),
        second: (
            between,
            _drive("waypoint", "charger", 1, 2),
        ),
        after_second: (
            second,
            _charge("charger", 1, 4, 180_000),
        ),
        goal: (
            after_second,
            _drive("charger", "destination", 4, 4),
        ),
    }
    built = reconstruct(parent, goal, {"nodes": [], "edges": []}, {"soc_step_kwh": 1})
    charges = [event for event in built["events"] if event["type"] == "charge"]
    assert len(charges) == 2
    assert charges[0]["station"] == "charger"
    assert charges[0]["soc_before_kwh"] == 0
    assert charges[0]["soc_after_kwh"] == 5
    assert charges[1]["station"] == "charger"
    assert charges[1]["soc_before_kwh"] == 1
    assert charges[1]["soc_after_kwh"] == 4
    assert built["route_node_ids"] == ["origin", "charger", "waypoint", "charger", "destination"]


def _drive(start: str, end: str, energy: int, minutes: int) -> dict:
    return {
        "type": "drive",
        "from": start,
        "to": end,
        "distance_miles": minutes,
        "duration_ms": minutes * 60_000,
        "energy_steps": energy,
        "soc_before_steps": energy,
        "soc_after_steps": 0,
    }


def _charge(station: str, before: int, after: int, duration_ms: int) -> dict:
    return {
        "type": "charge",
        "station": station,
        "duration_ms": duration_ms,
        "soc_before_steps": before,
        "soc_after_steps": after,
    }


def test_faster_charger_detour_beats_the_near_slow_charger() -> None:
    slow = _node("slow", "charger", 10)
    fast = _node("fast", "charger", 60)
    graph = _graph(
        _origin(),
        slow,
        fast,
        _destination(),
        edges=[
            _edge("origin", "slow", 5, 5),
            _edge("slow", "destination", 20, 20),
            _edge("origin", "fast", 12, 12),
            _edge("fast", "destination", 20, 20),
        ],
    )
    result = solve(graph, _config(initial_soc_kwh=20, battery_capacity_kwh=30))
    assert result["route_node_ids"] == ["origin", "fast", "destination"]
    assert result["total_time_minutes"] == 44
    assert result["number_of_charging_stops"] == 1


def test_fewer_stops_beats_a_shorter_distance() -> None:
    charger = _node("charger", "charger", 60)
    graph = _graph(
        _origin(),
        charger,
        _destination(),
        edges=[
            _edge("origin", "destination", 10, 10),
            _edge("origin", "charger", 2, 4.5),
            _edge("charger", "destination", 2, 4.5),
        ],
    )
    result = solve(
        graph,
        _config(
            initial_soc_kwh=1,
            battery_capacity_kwh=10,
            consumption_kwh_per_mile=0.1,
        ),
    )
    assert result["route_node_ids"] == ["origin", "destination"]
    assert result["number_of_charging_stops"] == 0
    assert result["total_distance_miles"] == 10
    assert result["total_time_minutes"] == 10


def test_shorter_distance_breaks_equal_time_and_stops() -> None:
    graph = _graph(
        _origin(),
        _destination(),
        edges=[
            _edge("origin", "destination", 10, 10),
            _edge("origin", "destination", 8, 10),
        ],
    )
    result = solve(graph, _config(initial_soc_kwh=20, battery_capacity_kwh=20))
    assert result["total_time_minutes"] == 10
    assert result["number_of_charging_stops"] == 0
    assert result["total_distance_miles"] == 8
    assert result["events"][0]["distance_miles"] == 8


def test_conservative_rounding_accepts_exact_reserve_only() -> None:
    graph = _graph(
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
    exact = solve(graph, _config(reserve_kwh=3, **base))
    assert exact["status"] == "FEASIBLE"
    assert exact["events"][0]["energy_kwh_discretized"] == 17
    assert exact["events"][0]["soc_after_kwh"] == 3

    still_ok = solve(graph, _config(reserve_kwh=2.1, **base))
    assert still_ok["status"] == "FEASIBLE"

    rejected = solve(graph, _config(reserve_kwh=3.1, **base))
    assert rejected["status"] == "INFEASIBLE"

    short = solve(graph, _config(**{**base, "initial_soc_kwh": 16, "reserve_kwh": 0}))
    assert short["status"] == "INFEASIBLE"


def test_faster_path_replaces_a_stale_label() -> None:
    midpoint = _node("mid", "charger", None)
    graph = _graph(
        _origin(),
        midpoint,
        _destination(),
        edges=[
            _edge("origin", "destination", 10, 10),
            _edge("origin", "mid", 1, 1),
            _edge("mid", "destination", 1, 1),
        ],
    )
    result = solve(graph, _config(initial_soc_kwh=20, battery_capacity_kwh=20))
    assert result["total_time_minutes"] == 2
    assert result["total_distance_miles"] == 2
    assert result["route_node_ids"] == ["origin", "mid", "destination"]


def test_reconstruct_merges_five_charge_steps_into_one_stop() -> None:
    start = ("charger", 0, 0)
    parent: dict = {start: None}
    previous = start
    for step in range(5):
        nxt = ("charger", step + 1, 1)
        parent[nxt] = (
            previous,
            {
                "type": "charge",
                "station": "charger",
                "duration_ms": 60_000,
                "soc_before_steps": step,
                "soc_after_steps": step + 1,
            },
        )
        previous = nxt
    goal = ("destination", 0, 0)
    parent[goal] = (
        previous,
        {
            "type": "drive",
            "from": "charger",
            "to": "destination",
            "distance_miles": 5,
            "duration_ms": 300_000,
            "energy_steps": 5,
            "soc_before_steps": 5,
            "soc_after_steps": 0,
        },
    )
    built = reconstruct(parent, goal, {"nodes": [], "edges": []}, {"soc_step_kwh": 1})
    assert built["route_node_ids"] == ["charger", "destination"]
    assert built["events"][0] == {
        "type": "charge",
        "station": "charger",
        "soc_before_kwh": 0,
        "soc_after_kwh": 5,
        "duration_minutes": 5,
    }
    assert built["charging_time_minutes"] == 5
    assert built["driving_time_minutes"] == 5


def test_sample_query_charges_at_the_faster_station() -> None:
    graph = json.loads((SYNTHETIC / "canonical_graph.json").read_text(encoding="utf-8"))
    config = json.loads((SYNTHETIC / "trip_config.json").read_text(encoding="utf-8"))
    result = solve(graph, config)
    assert result["status"] == "FEASIBLE"
    assert result["route_node_ids"] == ["origin", "station_1", "station_2", "destination"]
    assert result["number_of_charging_stops"] == 2
    assert result["total_time_minutes"] == 322.5
    assert result["driving_time_minutes"] == 291.3
    assert result["charging_time_minutes"] == 31.2
    assert result["total_distance_miles"] == 315.2
    assert result["events"][1]["soc_before_kwh"] == 37
    assert result["events"][1]["soc_after_kwh"] == 70
    assert result["events"][-1]["soc_after_kwh"] == 7


def test_cli_runs_a_feasible_label_setting_query(tmp_path: Path) -> None:
    graph = _graph(
        _origin(),
        _destination(),
        edges=[_edge("origin", "destination", 4, 12)],
    )
    graph_path = tmp_path / "graph.json"
    config_path = tmp_path / "config.json"
    out_path = tmp_path / "out.json"
    graph_path.write_text(json.dumps(graph), encoding="utf-8")
    config_path.write_text(json.dumps(_config()), encoding="utf-8")
    code = main(
        [
            "--algorithm",
            "label_setting",
            "--graph",
            str(graph_path),
            "--config",
            str(config_path),
            "--out",
            str(out_path),
        ]
    )
    assert code == 0
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["status"] == "FEASIBLE"
    assert written["route_node_ids"] == ["origin", "destination"]
