"""Canonical graph assembly and export (H3)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from data_pipeline.chargers import load_stations
from data_pipeline.export_graph import (
    build_canonical_graph,
    build_edges,
    build_edges_neighbor_limited,
    build_nodes,
    export_graph,
    validate_canonical_graph,
)
from data_pipeline.reduce_graph import DEFAULT_CONSUMPTION_KWH_PER_MILE
from optimizer.battery_state_optimizer import solve
from optimizer.model import load_graph

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "afdc"

DETROIT = {"lat": 42.3314, "lon": -83.0458}
CHICAGO = {"lat": 41.8781, "lon": -87.6298}

STATIONS = [
    {
        "id": "ann_arbor",
        "lat": 42.2808,
        "lon": -83.7430,
        "charge_rate_kw": 150.0,
    },
    {
        "id": "kalamazoo",
        "lat": 42.2917,
        "lon": -85.5872,
        "charge_rate_kw": 50.0,
    },
]


def station(station_id: str, lon: float, *, lat: float = 42.2) -> dict:
    return {
        "id": station_id,
        "lat": lat,
        "lon": lon,
        "charge_rate_kw": 150.0,
    }


def test_build_nodes_assigns_types_and_rates() -> None:
    nodes = build_nodes(DETROIT, CHICAGO, STATIONS)
    assert [node["id"] for node in nodes] == [
        "origin",
        "ann_arbor",
        "kalamazoo",
        "destination",
    ]
    assert nodes[0]["type"] == "origin"
    assert nodes[0]["charge_rate_kw"] is None
    assert nodes[1]["type"] == "charger"
    assert nodes[1]["charge_rate_kw"] == 150.0
    assert nodes[-1]["type"] == "destination"


def test_build_edges_creates_in_range_forward_pairs(tmp_path: Path) -> None:
    nodes = build_nodes(DETROIT, CHICAGO, STATIONS)
    edges = build_edges(nodes, tmp_path, road_backend="haversine")
    pairs = {(edge["from"], edge["to"]) for edge in edges}

    assert ("origin", "ann_arbor") in pairs
    assert ("ann_arbor", "kalamazoo") in pairs
    assert ("kalamazoo", "destination") in pairs
    assert ("origin", "destination") in pairs
    assert ("origin", "kalamazoo") in pairs
    assert ("ann_arbor", "destination") in pairs
    assert all(edge["distance_miles"] > 0 for edge in edges)
    assert all(
        edge["distance_miles"] * DEFAULT_CONSUMPTION_KWH_PER_MILE <= 100.0 + 1e-9
        for edge in edges
    )


def test_export_graph_writes_loadable_canonical_file(tmp_path: Path) -> None:
    nodes = build_nodes(DETROIT, CHICAGO, STATIONS)
    edges = build_edges(
        nodes,
        tmp_path / "cache",
        road_backend="haversine",
    )
    out = tmp_path / "canonical_graph.json"
    export_graph(nodes, edges, out)

    loaded = load_graph(out)
    assert len(loaded["nodes"]) == 4
    assert len(loaded["edges"]) >= 4
    validate_canonical_graph(loaded)


def test_export_graph_rejects_edge_shorter_than_straight_line(tmp_path: Path) -> None:
    nodes = build_nodes(DETROIT, CHICAGO, [])
    edges = [
        {
            "from": "origin",
            "to": "destination",
            "distance_miles": 1.0,
            "drive_time_minutes": 2.0,
        }
    ]
    with pytest.raises(ValueError, match="straight-line"):
        export_graph(nodes, edges, tmp_path / "bad.json")
    assert not (tmp_path / "bad.json").exists()


def test_build_canonical_graph_from_afdc_fixture(tmp_path: Path) -> None:
    stations = load_stations(FIXTURES / "sample_stations.json")
    graph = build_canonical_graph(
        DETROIT,
        CHICAGO,
        stations,
        tmp_path / "cache",
        max_chargers=3,
        road_backend="haversine",
    )
    validate_canonical_graph(graph)
    charger_ids = [node["id"] for node in graph["nodes"] if node["type"] == "charger"]
    assert 1 <= len(charger_ids) <= 3
    assert all(node["charge_rate_kw"] for node in graph["nodes"] if node["type"] == "charger")

    out = tmp_path / "processed" / "canonical_graph.json"
    export_graph(graph["nodes"], graph["edges"], out)
    loaded = load_graph(out)
    assert loaded["nodes"][0]["id"] == "origin"


def test_range_based_edges_include_feasible_skips_neighbor_limit_drops(
    tmp_path: Path,
) -> None:
    """Neighbor window of 3 drops long in-range skips that range connectivity keeps."""
    # Six evenly spaced corridor chargers west of Detroit toward Chicago.
    # From origin, neighbor_count=3 only reaches s1–s3; s4/s5/destination
    # require a longer (still in-budget) hop that range-based connectivity adds.
    stations = [
        station("s1", -83.5),
        station("s2", -84.0),
        station("s3", -84.5),
        station("s4", -85.0),
        station("s5", -85.5),
        station("s6", -86.0),
    ]
    nodes = build_nodes(DETROIT, CHICAGO, stations)

    limited = build_edges_neighbor_limited(
        nodes,
        tmp_path / "cache",
        neighbor_count=3,
        road_backend="haversine",
    )
    ranged = build_edges(
        nodes,
        tmp_path / "cache",
        road_backend="haversine",
    )

    limited_pairs = {(edge["from"], edge["to"]) for edge in limited}
    ranged_pairs = {(edge["from"], edge["to"]) for edge in ranged}

    assert limited_pairs < ranged_pairs
    assert ("origin", "s4") not in limited_pairs
    assert ("origin", "s4") in ranged_pairs
    assert ("s1", "s5") not in limited_pairs
    assert ("s1", "s5") in ranged_pairs

    # The recovered skip is a strictly shorter path than chaining three hops.
    by_pair = {(edge["from"], edge["to"]): edge for edge in ranged}
    direct = by_pair[("origin", "s4")]["distance_miles"]
    chained = (
        by_pair[("origin", "s1")]["distance_miles"]
        + by_pair[("s1", "s2")]["distance_miles"]
        + by_pair[("s2", "s3")]["distance_miles"]
        + by_pair[("s3", "s4")]["distance_miles"]
    )
    assert direct < chained


# One degree of longitude at the test latitude, in miles.
_LAT = 42.3
_MILES_PER_LON_DEGREE = 69.17 * math.cos(math.radians(_LAT))


def _east(miles: float) -> float:
    """Longitude ``miles`` east of the test origin."""
    return -83.0 + miles / _MILES_PER_LON_DEGREE


def _point(miles_east: float) -> dict:
    return {"lat": _LAT, "lon": _east(miles_east)}


def _backward_route_nodes() -> list[dict]:
    """Origin at 0 mi, a charger 20 mi behind it, a charger ahead, destination at 120 mi."""
    stations = [
        {"id": "behind", **_point(-20), "charge_rate_kw": 150.0},
        {"id": "ahead", **_point(60), "charge_rate_kw": 150.0},
    ]
    return build_nodes(_point(0), _point(120), stations)


def _pairs(edges: list[dict]) -> set[tuple[str, str]]:
    return {(edge["from"], edge["to"]) for edge in edges}


def test_build_edges_includes_reverse_direction_pairs(tmp_path: Path) -> None:
    edges = build_edges(
        _backward_route_nodes(), tmp_path, road_backend="haversine", max_battery_kwh=70.0
    )
    pairs = _pairs(edges)

    # Both directions between two chargers are kept.
    assert ("behind", "ahead") in pairs
    assert ("ahead", "behind") in pairs
    # A charger behind the origin is reachable from it (a backward hop).
    assert ("origin", "behind") in pairs
    assert len(pairs) == len(edges), "no duplicate directed edges"


def test_build_edges_never_enter_origin_or_leave_destination(tmp_path: Path) -> None:
    edges = build_edges(
        _backward_route_nodes(), tmp_path, road_backend="haversine", max_battery_kwh=70.0
    )

    assert not [edge for edge in edges if edge["to"] == "origin"]
    assert not [edge for edge in edges if edge["from"] == "destination"]
    # The fix must not over-prune: the endpoints still connect to chargers.
    assert ("origin", "ahead") in _pairs(edges)
    assert ("ahead", "destination") in _pairs(edges)


def test_build_edges_respects_energy_budget_in_both_directions(tmp_path: Path) -> None:
    nodes = _backward_route_nodes()
    # 70 kWh at 0.30 kWh/mi reaches about 233 road miles. 'behind' -> destination
    # is 140 straight miles (175 road), so it fits. A 40 kWh budget (133 mi) does not.
    wide = _pairs(
        build_edges(nodes, tmp_path / "wide", road_backend="haversine", max_battery_kwh=70.0)
    )
    narrow = _pairs(
        build_edges(nodes, tmp_path / "narrow", road_backend="haversine", max_battery_kwh=40.0)
    )

    assert ("behind", "destination") in wide
    assert ("behind", "destination") not in narrow
    assert ("ahead", "behind") in narrow  # 80 straight miles, 100 road, 30 kWh


def test_route_needing_a_backward_connection_is_preserved(tmp_path: Path) -> None:
    """Regression: the only feasible trip first drives *away* from the destination.

    The origin starts with 12 kWh (about 40 road miles). The charger ahead is
    75 road miles away and unreachable, and the destination is farther still.
    The only reachable charger is 20 miles behind the origin. Forward-only
    connectivity drops that origin -> behind edge and the trip becomes infeasible.
    """
    nodes = _backward_route_nodes()
    config = {
        "origin": "origin",
        "destination": "destination",
        "battery_capacity_kwh": 70.0,
        "initial_soc_kwh": 12.0,
        "reserve_kwh": 2.0,
        "consumption_kwh_per_mile": DEFAULT_CONSUMPTION_KWH_PER_MILE,
        "soc_step_kwh": 1.0,
    }

    forward_only = {
        "nodes": nodes,
        "edges": build_edges_neighbor_limited(
            nodes,
            tmp_path / "old",
            neighbor_count=3,
            road_backend="haversine",
            max_battery_kwh=70.0,
        ),
    }
    corrected = {
        "nodes": nodes,
        "edges": build_edges(
            nodes, tmp_path / "new", road_backend="haversine", max_battery_kwh=70.0
        ),
    }

    assert ("origin", "behind") not in _pairs(forward_only["edges"])
    assert solve(forward_only, config)["status"] == "INFEASIBLE"

    result = solve(corrected, config)
    assert result["status"] == "FEASIBLE"
    assert result["route_node_ids"][:2] == ["origin", "behind"]
    assert result["route_node_ids"][-1] == "destination"
    assert result["number_of_charging_stops"] >= 1
