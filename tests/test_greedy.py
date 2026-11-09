"""Farthest-reachable-charger baseline tests (H4)."""

from __future__ import annotations

from optimizer.greedy_baseline import solve as greedy_solve
from optimizer.model import haversine_miles, write_result


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


def test_frc_direct_drive_when_destination_reachable() -> None:
    graph = {
        "nodes": [
            _node("origin", "origin", None),
            _node("destination", "destination", None, lon=-83.05),
        ],
        "edges": [_edge("origin", "destination", 4, 12)],
    }
    result = greedy_solve(graph, _config())
    assert result["status"] == "FEASIBLE"
    assert result["algorithm"] == "greedy_frc"
    assert result["route_node_ids"] == ["origin", "destination"]
    assert result["number_of_charging_stops"] == 0
    assert result["total_time_minutes"] == 12
    assert result["events"][0]["type"] == "drive"


def test_frc_charges_just_enough_then_drives(tmp_path) -> None:
    graph = {
        "nodes": [
            _node("origin", "origin", 60),
            _node("destination", "destination", None, lon=-83.05),
        ],
        "edges": [_edge("origin", "destination", 5, 5)],
    }
    result = greedy_solve(graph, _config(initial_soc_kwh=0, battery_capacity_kwh=20))
    assert result["status"] == "FEASIBLE"
    assert result["number_of_charging_stops"] == 1
    assert result["events"][0]["type"] == "charge"
    assert result["events"][0]["soc_before_kwh"] == 0
    assert result["events"][0]["soc_after_kwh"] == 5
    assert result["events"][1]["type"] == "drive"
    write_result(result, tmp_path / "greedy_result.json")


def test_frc_prefers_near_slow_charger_over_far_fast() -> None:
    """Section 12 picks the charger closest to the destination among progress makers."""
    graph = {
        "nodes": [
            _node("origin", "origin", None, lat=42.0, lon=-83.0),
            _node("slow", "charger", 10, lat=42.03, lon=-83.0),
            _node("fast", "charger", 60, lat=42.0, lon=-82.85),
            _node("destination", "destination", None, lat=42.05, lon=-83.0),
        ],
        "edges": [
            _edge("origin", "slow", 5, 5),
            _edge("slow", "destination", 20, 20),
            _edge("origin", "fast", 12, 12),
            _edge("fast", "destination", 20, 20),
        ],
    }
    result = greedy_solve(graph, _config(initial_soc_kwh=20, battery_capacity_kwh=30))
    assert result["status"] == "FEASIBLE"
    assert result["route_node_ids"] == ["origin", "slow", "destination"]
    origin_to_dest = haversine_miles(42.0, -83.0, 42.05, -83.0)
    slow_to_dest = haversine_miles(42.03, -83.0, 42.05, -83.0)
    fast_to_dest = haversine_miles(42.0, -82.85, 42.05, -83.0)
    assert slow_to_dest < origin_to_dest
    assert fast_to_dest > origin_to_dest


def test_frc_t16_infeasible_when_only_charger_is_farther() -> None:
    graph = {
        "nodes": [
            _node("origin", "origin", None, lat=42.0, lon=-83.0),
            _node("away", "charger", 60, lat=42.0, lon=-82.0),
            _node("destination", "destination", None, lat=42.0, lon=-85.0),
        ],
        "edges": [
            _edge("origin", "destination", 110, 110),
            _edge("origin", "away", 52, 50),
            _edge("away", "destination", 160, 160),
        ],
    }
    result = greedy_solve(graph, _config(initial_soc_kwh=60, battery_capacity_kwh=200))
    assert result["status"] == "INFEASIBLE"
    assert result["algorithm"] == "greedy_frc"
    assert result["route_node_ids"] == []
    assert result["events"] == []
