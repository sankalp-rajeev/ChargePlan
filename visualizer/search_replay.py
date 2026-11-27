"""Replay of the label-setting search, settle by settle.

``capture`` runs the real optimizer with ``trace=[]`` and returns its result
and the states in the order they were settled. ``build_payload`` turns that
into a compact JSON payload, and ``replay_html`` renders a self-contained page
(Leaflet map plus a node-by-battery grid) that plays the settle order back.

The search finishes in milliseconds, so the page slows the replay down; the
order, labels, and counts are exactly those of the run. Nothing here changes
how the optimizer searches.
"""

from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from optimizer.battery_state_optimizer import SettleRecord, _heuristic_ms, solve
from optimizer.model import discretize, edge_energy_steps

TEMPLATE_PATH = Path(__file__).with_name("search_replay.html")
LEAFLET_CSS = "https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.css"
LEAFLET_JS = "https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.js"
DARK_TILES = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"


def capture(graph: dict, config: dict, *, use_heuristic: bool = False) -> tuple[dict, list]:
    """Run the optimizer once with a trace and return ``(result, trace)``."""
    trace: list[SettleRecord] = []
    result = solve(graph, config, use_heuristic=use_heuristic, trace=trace)
    return result, trace


def column_order(graph: dict, config: dict) -> list[str]:
    """Node ids ordered by progress from the origin towards the destination."""
    nodes = {node["id"]: node for node in graph["nodes"]}
    origin, destination = nodes[config["origin"]], nodes[config["destination"]]
    scale = math.cos(math.radians((origin["lat"] + destination["lat"]) / 2))
    dx = (destination["lon"] - origin["lon"]) * scale
    dy = destination["lat"] - origin["lat"]
    length = math.hypot(dx, dy) or 1.0

    def progress(node: dict) -> float:
        if node["id"] == origin["id"]:
            return -math.inf
        if node["id"] == destination["id"]:
            return math.inf
        return (
            (node["lon"] - origin["lon"]) * scale * dx + (node["lat"] - origin["lat"]) * dy
        ) / length

    return [node["id"] for node in sorted(graph["nodes"], key=progress)]


def _short_label(node: dict, number: int) -> str:
    if node["type"] == "origin":
        return "Start"
    if node["type"] == "destination":
        return "End"
    rate = node["charge_rate_kw"]
    return f"S{number} {rate:g} kW" if rate is not None else f"S{number}"


def optimal_state_path(trace: list, goal: tuple) -> list[tuple]:
    """States of the optimal plan, start to goal, rebuilt from the trace parents."""
    parent = {record.state: record.parent for record in trace}
    path = []
    state = goal
    while state is not None:
        path.append(state)
        state = parent.get(state)
    return list(reversed(path))


def route_points(graph: dict, route_ids: list[str], legs: list | None = None) -> list[list[float]]:
    """(lat, lon) points along a route; ``legs`` may hold road geometry per leg."""
    nodes = {node["id"]: node for node in graph["nodes"]}
    points: list[list[float]] = []
    for index in range(len(route_ids) - 1):
        start, end = nodes[route_ids[index]], nodes[route_ids[index + 1]]
        leg = legs[index] if legs and index < len(legs) and legs[index] else None
        segment = leg or [(start["lat"], start["lon"]), (end["lat"], end["lon"])]
        points.extend([[round(lat, 5), round(lon, 5)] for lat, lon in segment])
    return points


def explored_links(
    graph: dict, config: dict, trace: list, *, use_heuristic: bool = False
) -> list[dict]:
    """Every road the search relaxed, with the search clock when it started and ended.

    The clock is the queue key in minutes: the label's time for label-setting,
    time plus the heuristic for A*. States are settled in nondecreasing key order,
    so drawing each relaxed road from ``start`` to ``end`` on that clock shows the
    search front as a wave. A road is listed once, at its first relaxation; the
    destination state is never expanded, so nothing leaves it.
    """
    _capacity, _initial, reserve, _delta = discretize(config)
    nodes = {node["id"]: node for node in graph["nodes"]}
    h = (
        _heuristic_ms(nodes, graph["edges"], config["destination"])
        if use_heuristic
        else dict.fromkeys(nodes, 0)
    )
    roads: dict[str, list[dict]] = {}
    for edge in graph["edges"]:
        roads.setdefault(edge["from"], []).append(edge)
    energy = {
        (e["from"], e["to"]): edge_energy_steps(e["distance_miles"], config) for e in graph["edges"]
    }
    first: dict[tuple[str, str], dict] = {}
    for record in trace:
        node, battery, _flag = record.state
        if node == config["destination"]:
            break
        depart = (record.label[0] + h[node]) / 60000
        for edge in roads.get(node, ()):
            pair = (node, edge["to"])
            if pair in first or battery - energy[pair] < reserve:
                continue
            arrive = depart + edge["drive_time_minutes"] + (h[edge["to"]] - h[node]) / 60000
            first[pair] = {
                "from": node,
                "to": edge["to"],
                "start": depart,
                "end": max(arrive, depart),
            }
    return list(first.values())


def build_payload(
    graph: dict,
    config: dict,
    result: dict,
    trace: list,
    *,
    greedy: dict | None = None,
    leg_geometry=None,
) -> dict:
    """Compact, JSON-ready description of one traced search.

    ``leg_geometry(start_node, end_node)`` may return road points for a leg
    (display only); when it is ``None`` or returns ``None`` legs are straight.
    """
    capacity, _initial, reserve, delta_e = discretize(config)
    order = column_order(graph, config)
    index = {node_id: i for i, node_id in enumerate(order)}
    nodes = {node["id"]: node for node in graph["nodes"]}

    use_heuristic = result.get("algorithm") == "astar"
    h_ms = (
        _heuristic_ms(nodes, graph["edges"], config["destination"])
        if use_heuristic
        else dict.fromkeys(nodes, 0)
    )
    links = explored_links(graph, config, trace, use_heuristic=use_heuristic)
    steps = []
    for record in trace:
        node_id, battery, flag = record.state
        steps.append(
            [
                index[node_id],
                battery,
                flag,
                round(record.label[0] / 60000, 2),
                record.label[1],
                index[record.parent[0]] if record.parent else -1,
                record.queue,
                record.reached,
                round(record.label[2], 1),
                round((record.label[0] + h_ms[node_id]) / 60000, 3),
            ]
        )

    # Every road link the search travels along, plus the optimal and greedy
    # routes' legs. Road shapes are fetched once, in parallel, display only.
    pairs = {(link["from"], link["to"]) for link in links}
    for route in (result.get("route_node_ids", []), (greedy or {}).get("route_node_ids", [])):
        pairs.update(zip(route, route[1:], strict=False))
    shapes: dict[tuple[str, str], list | None] = {}
    if leg_geometry is not None and pairs:
        ordered = sorted(pairs)
        with ThreadPoolExecutor(max_workers=6) as pool:
            fetched = pool.map(lambda pair: leg_geometry(nodes[pair[0]], nodes[pair[1]]), ordered)
            shapes = dict(zip(ordered, fetched, strict=True))

    def legs_for(route_ids: list[str]) -> list | None:
        if not shapes:
            return None
        return [shapes.get((route_ids[i], route_ids[i + 1])) for i in range(len(route_ids) - 1)]

    def link_points(a: str, b: str) -> list[list[float]]:
        shape = shapes.get((a, b)) or [
            (nodes[a]["lat"], nodes[a]["lon"]),
            (nodes[b]["lat"], nodes[b]["lon"]),
        ]
        return [[round(lat, 5), round(lon, 5)] for lat, lon in shape]

    feasible = result["status"] == "FEASIBLE"
    goal = trace[-1].state if feasible and trace else None
    optimal = [[index[s[0]], s[1]] for s in optimal_state_path(trace, goal)] if goal else []
    payload = {
        "algorithm": result["algorithm"],
        "feasible": feasible,
        "capacity_steps": capacity,
        "reserve_steps": reserve,
        "soc_step_kwh": float(delta_e),
        "nodes": [
            {
                "id": node_id,
                "label": _short_label(nodes[node_id], position),
                "code": f"S{position}",
                "type": nodes[node_id]["type"],
                "lat": nodes[node_id]["lat"],
                "lon": nodes[node_id]["lon"],
                "kw": nodes[node_id]["charge_rate_kw"],
            }
            for position, node_id in enumerate(order)
        ],
        "steps": steps,
        "optimal": optimal,
        "total_time_minutes": result.get("total_time_minutes"),
        "stops": result.get("number_of_charging_stops"),
        "route": [index[n] for n in result.get("route_node_ids", [])],
        "route_points": route_points(
            graph, result.get("route_node_ids", []), legs_for(result.get("route_node_ids", []))
        )
        if feasible
        else [],
        "greedy": None,
        "links": [
            {
                "a": index[link["from"]],
                "b": index[link["to"]],
                "t0": round(link["start"], 3),
                "t1": round(link["end"], 3),
                "pts": link_points(link["from"], link["to"]),
            }
            for link in links
        ],
        "final_clock": round(steps[-1][9], 3) if steps else 0,
        "start_kwh": round(_initial * float(delta_e), 2),
    }
    if greedy and greedy.get("status") == "FEASIBLE":
        payload["greedy"] = {
            "total_time_minutes": greedy["total_time_minutes"],
            "stops": greedy["number_of_charging_stops"],
            "route_points": route_points(
                graph, greedy["route_node_ids"], legs_for(greedy["route_node_ids"])
            ),
        }
    return payload


def replay_html(payload: dict, *, layout: str = "split", basemap: str = "street") -> str:
    """Self-contained HTML page that plays the payload back.

    ``layout="split"`` puts the map beside the explanation panel;
    ``layout="map"`` uses a full-width map with the panel floating over it.
    ``basemap`` is the starting map style, ``"street"`` (OpenStreetMap, as on
    the route map) or ``"dark"``; the page has a switch for both.
    """
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return (
        template.replace("__LEAFLET_CSS__", LEAFLET_CSS)
        .replace("__LEAFLET_JS__", LEAFLET_JS)
        .replace("__TILES__", DARK_TILES)
        .replace("__LAYOUT__", "maponly" if layout == "map" else "")
        .replace("__BASEMAP__", basemap if basemap in ("street", "dark") else "street")
        .replace("__PAYLOAD__", data)
    )
