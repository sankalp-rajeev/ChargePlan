"""Write a ``canonical_graph.json`` file that matches the frozen schema.

Owned by Shannon (H3). Nodes use ids, types, coordinates, and
``charge_rate_kw``. Edges are directed and store distance and driving time only.

``build_canonical_graph`` wires H1/H2 outputs into a reduced optimizer graph:
corridor candidates become charger nodes, road metrics fill directed edges to
the next ``neighbor_count`` stations along the OD corridor (same pattern as
Experiment D), and edges whose energy exceeds the experiment battery budget
are omitted. ``export_graph`` validates the frozen contract before writing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import jsonschema

from data_pipeline.reduce_graph import (
    DEFAULT_CONSUMPTION_KWH_PER_MILE,
    DEFAULT_CORRIDOR_WIDTH_MILES,
    DEFAULT_MAX_BATTERY_KWH,
    filter_edges_by_battery,
    point_to_segment,
    select_candidates,
    to_local_miles,
)
from data_pipeline.road_graph import road_metrics
from optimizer.model import haversine_miles, load_graph

_ROOT = Path(__file__).resolve().parents[1]
_GRAPH_SCHEMA = _ROOT / "contracts" / "canonical_graph.schema.json"
_LENGTH_TOLERANCE_MILES = 1e-6

# Sparse forward connectivity: each node links to the next k along the corridor.
DEFAULT_NEIGHBOR_COUNT = 3


def export_graph(nodes: list[dict], edges: list[dict], path: str | Path) -> None:
    """Validate one canonical graph and write it to ``path``.

    Validation covers the frozen JSON schema plus the structural rules in
    ``optimizer.model.load_graph`` (unique ids, edge endpoints, and
    ``distance_miles`` at least the straight-line length).
    """
    graph = {"nodes": list(nodes), "edges": list(edges)}
    validate_canonical_graph(graph)

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")

    # Round-trip through the shared loader so Shannon and Sankalp agree.
    load_graph(destination)


def validate_canonical_graph(graph: dict) -> None:
    """Raise ``ValueError`` if ``graph`` fails the frozen canonical contract."""
    schema = json.loads(_GRAPH_SCHEMA.read_text(encoding="utf-8"))
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    errors = sorted(validator_cls(schema).iter_errors(graph), key=lambda err: list(err.path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "<root>"
        raise ValueError(f"canonical_graph.schema.json: {location}: {first.message}")

    nodes_by_id = {node["id"]: node for node in graph["nodes"]}
    if len(nodes_by_id) != len(graph["nodes"]):
        raise ValueError("canonical graph has duplicate node ids")

    for edge in graph["edges"]:
        start_id = edge["from"]
        end_id = edge["to"]
        if start_id not in nodes_by_id or end_id not in nodes_by_id:
            raise ValueError(f"edge {start_id!r} -> {end_id!r} names an unknown node")
        start = nodes_by_id[start_id]
        end = nodes_by_id[end_id]
        straight = haversine_miles(start["lat"], start["lon"], end["lat"], end["lon"])
        if edge["distance_miles"] + _LENGTH_TOLERANCE_MILES < straight:
            raise ValueError(
                f"edge {start_id!r} -> {end_id!r} is shorter than the straight-line distance"
            )


def build_nodes(
    origin: dict,
    destination: dict,
    stations: list[dict],
    *,
    origin_id: str = "origin",
    destination_id: str = "destination",
) -> list[dict]:
    """Assemble schema nodes from an OD pair and corridor charger records."""
    if origin_id == destination_id:
        raise ValueError("origin_id and destination_id must differ")

    nodes = [
        _endpoint_node(origin, node_id=origin_id, node_type="origin"),
        _endpoint_node(destination, node_id=destination_id, node_type="destination"),
    ]
    used_ids = {origin_id, destination_id}
    for station in stations:
        station_id = str(station.get("id", "")).strip()
        if not station_id:
            raise ValueError("charger stations must provide a non-empty id")
        if station_id in used_ids:
            # Skip a station that collides with OD ids or an earlier charger.
            continue
        rate = station.get("charge_rate_kw")
        if rate is None:
            raise ValueError(f"charger {station_id!r} is missing charge_rate_kw")
        rate_f = float(rate)
        if rate_f <= 0:
            raise ValueError(f"charger {station_id!r} charge_rate_kw must be positive")
        nodes.insert(
            -1,
            {
                "id": station_id,
                "type": "charger",
                "lat": float(station["lat"]),
                "lon": float(station["lon"]),
                "charge_rate_kw": rate_f,
            },
        )
        used_ids.add(station_id)
    return nodes


def build_edges(
    nodes: list[dict],
    cache_dir: str | Path,
    *,
    neighbor_count: int = DEFAULT_NEIGHBOR_COUNT,
    road_backend: Literal["auto", "osmnx", "haversine"] = "auto",
    max_battery_kwh: float = DEFAULT_MAX_BATTERY_KWH,
    consumption_kwh_per_mile: float = DEFAULT_CONSUMPTION_KWH_PER_MILE,
    origin_id: str = "origin",
    destination_id: str = "destination",
) -> list[dict]:
    """Build directed forward edges along the corridor using ``road_metrics``.

    Nodes are ordered by progress from origin to destination. Each node links
    to the next ``neighbor_count`` nodes when the hop is within the battery
    energy budget. A direct origin→destination edge is added when feasible.
    """
    if neighbor_count < 1:
        raise ValueError("neighbor_count must be at least 1")

    by_id = {node["id"]: node for node in nodes}
    if origin_id not in by_id or destination_id not in by_id:
        raise ValueError("nodes must include the origin and destination ids")

    ordered = _order_nodes_along_corridor(nodes, origin_id, destination_id)
    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for index, start in enumerate(ordered):
        for offset in range(1, neighbor_count + 1):
            end_index = index + offset
            if end_index >= len(ordered):
                break
            end = ordered[end_index]
            edge = _road_edge(start, end, cache_dir, road_backend)
            key = (edge["from"], edge["to"])
            if key not in seen:
                edges.append(edge)
                seen.add(key)

    direct = _road_edge(by_id[origin_id], by_id[destination_id], cache_dir, road_backend)
    key = (direct["from"], direct["to"])
    if key not in seen:
        edges.append(direct)

    return filter_edges_by_battery(
        edges,
        max_battery_kwh=max_battery_kwh,
        consumption_kwh_per_mile=consumption_kwh_per_mile,
    )


def build_canonical_graph(
    origin: dict,
    destination: dict,
    stations: list[dict],
    cache_dir: str | Path,
    *,
    corridor_width_miles: float = DEFAULT_CORRIDOR_WIDTH_MILES,
    neighbor_count: int = DEFAULT_NEIGHBOR_COUNT,
    max_chargers: int | None = None,
    road_backend: Literal["auto", "osmnx", "haversine"] = "auto",
    max_battery_kwh: float = DEFAULT_MAX_BATTERY_KWH,
    consumption_kwh_per_mile: float = DEFAULT_CONSUMPTION_KWH_PER_MILE,
    origin_id: str = "origin",
    destination_id: str = "destination",
) -> dict:
    """Select corridor candidates and assemble a validated canonical graph dict."""
    candidates = select_candidates(
        stations,
        origin,
        destination,
        corridor_width_miles=corridor_width_miles,
    )
    if max_chargers is not None:
        candidates = _subsample_along_corridor(
            candidates,
            origin,
            destination,
            max_chargers,
        )

    nodes = build_nodes(
        origin,
        destination,
        candidates,
        origin_id=origin_id,
        destination_id=destination_id,
    )
    edges = build_edges(
        nodes,
        cache_dir,
        neighbor_count=neighbor_count,
        road_backend=road_backend,
        max_battery_kwh=max_battery_kwh,
        consumption_kwh_per_mile=consumption_kwh_per_mile,
        origin_id=origin_id,
        destination_id=destination_id,
    )
    graph = {"nodes": nodes, "edges": edges}
    validate_canonical_graph(graph)
    return graph


def _endpoint_node(point: dict[str, Any], *, node_id: str, node_type: str) -> dict:
    lat = float(point["lat"])
    lon = float(point["lon"])
    if "charge_rate_kw" not in point or point["charge_rate_kw"] is None:
        rate: float | None = None
    else:
        rate = float(point["charge_rate_kw"])
        if rate <= 0:
            raise ValueError(f"{node_type} charge_rate_kw must be positive when set")
    return {
        "id": node_id,
        "type": node_type,
        "lat": lat,
        "lon": lon,
        "charge_rate_kw": rate,
    }


def _road_edge(
    start: dict,
    end: dict,
    cache_dir: str | Path,
    road_backend: Literal["auto", "osmnx", "haversine"],
) -> dict:
    metrics = road_metrics(start, end, cache_dir, backend=road_backend)
    return {
        "from": start["id"],
        "to": end["id"],
        "distance_miles": float(metrics["distance_miles"]),
        "drive_time_minutes": float(metrics["drive_time_minutes"]),
    }


def _order_nodes_along_corridor(
    nodes: list[dict],
    origin_id: str,
    destination_id: str,
) -> list[dict]:
    origin = next(node for node in nodes if node["id"] == origin_id)
    destination = next(node for node in nodes if node["id"] == destination_id)
    scored: list[tuple[float, dict]] = []
    for node in nodes:
        progress = _along_track_miles(
            node["lat"],
            node["lon"],
            origin["lat"],
            origin["lon"],
            destination["lat"],
            destination["lon"],
        )
        scored.append((progress, node))
    scored.sort(key=lambda item: (item[0], item[1]["id"]))
    return [node for _progress, node in scored]


def _subsample_along_corridor(
    stations: list[dict],
    origin: dict,
    destination: dict,
    max_chargers: int,
) -> list[dict]:
    if max_chargers < 0:
        raise ValueError("max_chargers must be non-negative")
    if len(stations) <= max_chargers:
        return list(stations)
    if max_chargers == 0:
        return []

    scored = sorted(
        stations,
        key=lambda station: (
            _along_track_miles(
                float(station["lat"]),
                float(station["lon"]),
                float(origin["lat"]),
                float(origin["lon"]),
                float(destination["lat"]),
                float(destination["lon"]),
            ),
            str(station.get("id", "")),
        ),
    )
    if max_chargers == 1:
        return [scored[len(scored) // 2]]

    indexes = {
        round(i * (len(scored) - 1) / (max_chargers - 1)) for i in range(max_chargers)
    }
    return [scored[index] for index in sorted(indexes)]


def _along_track_miles(
    lat: float,
    lon: float,
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
) -> float:
    """Signed progress from origin toward destination in local miles."""
    origin_xy = (0.0, 0.0)
    dest_xy = to_local_miles(dest_lat, dest_lon, origin_lat, origin_lon)
    point_xy = to_local_miles(lat, lon, origin_lat, origin_lon)
    _distance, along = point_to_segment(point_xy, origin_xy, dest_xy)
    return along
