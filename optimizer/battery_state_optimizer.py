"""Label-setting search over battery-expanded states, with an A* flag.

Owned by Sankalp. ``use_heuristic=False`` is plain label-setting (S2).
``use_heuristic=True`` is the A* variant (S10). Labels are
``(time_ms, stops, distance)`` and compare lexicographically. One minute is
60,000 ms. The queue orders A* by ``(time_ms + h, stops, distance)``.

``h(v)`` is the straight-line distance to the destination divided by the
fastest edge speed, then floored to milliseconds. Speed uses the same integer
edge times as the labels. Flooring keeps ``h`` admissible and consistent:
any road is at least the straight-line length, no edge is faster than that
speed, and an integer edge cost preserves the inequality after flooring.
``h`` is zero at the destination, so the first popped destination state still
has the lexicographically best label. A zero-time edge with positive length
makes the speed unbounded, and then ``h`` is zero everywhere.

The search is implemented in this module with ``heapq``. It does not call
NetworkX or another shortest-path library.
"""

from __future__ import annotations

import heapq
import time
from collections import defaultdict
from decimal import ROUND_FLOOR, Decimal

from optimizer.model import (
    InputError,
    charge_step_minutes,
    discretize,
    edge_energy_steps,
    haversine_miles,
    minutes_to_ms,
    ms_to_minutes,
)
from optimizer.reconstruction import reconstruct

State = tuple[str, int, int]
Label = tuple[int, int, float]


def solve(graph: dict, config: dict, *, use_heuristic: bool = False) -> dict:
    """Return an ``algorithm_result`` dict for label-setting or A*.

    ``use_heuristic=False`` reports ``algorithm`` = ``label_setting``.
    ``use_heuristic=True`` reports ``algorithm`` = ``astar`` and orders the
    queue with the Section 11.3 heuristic. Both return the same label.
    """
    algorithm = "astar" if use_heuristic else "label_setting"
    started = time.perf_counter()
    capacity, initial, reserve, delta_e = discretize(config)
    if initial < reserve:
        return _infeasible(config, started, algorithm, expanded=0, stored=0)

    nodes = {node["id"]: node for node in graph["nodes"]}
    origin = config["origin"]
    destination = config["destination"]
    if origin not in nodes or destination not in nodes:
        raise InputError("origin and destination must be nodes in the graph")

    outgoing: dict[str, list[dict]] = defaultdict(list)
    for edge in graph["edges"]:
        if edge["from"] not in nodes or edge["to"] not in nodes:
            raise InputError(
                f"edge {edge['from']!r} -> {edge['to']!r} names an unknown node"
            )
        outgoing[edge["from"]].append(edge)

    heuristic = _heuristic_ms(nodes, graph["edges"], destination) if use_heuristic else None
    start: State = (origin, initial, 0)
    start_label: Label = (0, 0, 0.0)
    best: dict[State, Label] = {start: start_label}
    parent: dict[State, tuple[State, dict] | None] = {start: None}
    queue: list[tuple[Label, int, Label, State]] = []
    heapq.heappush(
        queue, (_priority(start_label, origin, heuristic), 0, start_label, start)
    )
    expanded = 0
    goal: State | None = None

    while queue:
        _priority_key, _tie, label, state = heapq.heappop(queue)
        if best.get(state) != label:
            continue
        expanded += 1
        location, battery, flag = state
        if location == destination:
            goal = state
            break

        for edge in outgoing.get(location, ()):
            energy = edge_energy_steps(edge["distance_miles"], config)
            arrival = battery - energy
            if arrival < reserve:
                continue
            drive_ms = minutes_to_ms(edge["drive_time_minutes"])
            successor_label = (
                label[0] + drive_ms,
                label[1],
                label[2] + edge["distance_miles"],
            )
            _relax(
                best,
                parent,
                queue,
                (edge["to"], arrival, 0),
                successor_label,
                _priority(successor_label, edge["to"], heuristic),
                state,
                {
                    "type": "drive",
                    "from": location,
                    "to": edge["to"],
                    "distance_miles": edge["distance_miles"],
                    "duration_ms": drive_ms,
                    "energy_steps": energy,
                    "soc_before_steps": battery,
                    "soc_after_steps": arrival,
                },
            )

        rate = nodes[location]["charge_rate_kw"]
        if rate is not None and battery + 1 <= capacity:
            charge_ms = minutes_to_ms(charge_step_minutes(delta_e, rate))
            successor_label = (
                label[0] + charge_ms,
                label[1] + (1 if flag == 0 else 0),
                label[2],
            )
            _relax(
                best,
                parent,
                queue,
                (location, battery + 1, 1),
                successor_label,
                _priority(successor_label, location, heuristic),
                state,
                {
                    "type": "charge",
                    "station": location,
                    "duration_ms": charge_ms,
                    "soc_before_steps": battery,
                    "soc_after_steps": battery + 1,
                },
            )

    if goal is None:
        return _infeasible(config, started, algorithm, expanded=expanded, stored=len(best))

    built = reconstruct(parent, goal, graph, config)
    time_ms, stops, distance = best[goal]
    return {
        "status": "FEASIBLE",
        "algorithm": algorithm,
        "total_time_minutes": ms_to_minutes(time_ms),
        "driving_time_minutes": built["driving_time_minutes"],
        "charging_time_minutes": built["charging_time_minutes"],
        "total_distance_miles": distance,
        "number_of_charging_stops": stops,
        "soc_step_kwh": config["soc_step_kwh"],
        "route_node_ids": built["route_node_ids"],
        "events": built["events"],
        "metrics": _metrics(started, expanded, len(best)),
    }


def _relax(
    best: dict[State, Label],
    parent: dict[State, tuple[State, dict] | None],
    queue: list[tuple[Label, int, Label, State]],
    successor: State,
    successor_label: Label,
    priority: Label,
    predecessor: State,
    action: dict,
) -> None:
    current = best.get(successor)
    if current is not None and successor_label >= current:
        return
    best[successor] = successor_label
    parent[successor] = (predecessor, action)
    tie = len(queue)
    heapq.heappush(queue, (priority, tie, successor_label, successor))


def _priority(label: Label, location: str, heuristic: dict[str, int] | None) -> Label:
    if heuristic is None:
        return label
    return (label[0] + heuristic[location], label[1], label[2])


def _heuristic_ms(nodes: dict[str, dict], edges: list[dict], destination: str) -> dict[str, int]:
    """Floored milliseconds of straight-line time at the fastest edge speed."""
    fastest_distance: float | None = None
    fastest_ms = 0
    for edge in edges:
        distance = float(edge["distance_miles"])
        duration_ms = minutes_to_ms(edge["drive_time_minutes"])
        if duration_ms == 0:
            if distance > 0:
                return {node_id: 0 for node_id in nodes}
            continue
        if distance <= 0:
            continue
        if fastest_distance is None or _is_faster(
            distance, duration_ms, fastest_distance, fastest_ms
        ):
            fastest_distance = distance
            fastest_ms = duration_ms
    dest = nodes[destination]
    if fastest_distance is None:
        return {node_id: 0 for node_id in nodes}
    return {
        node_id: _lower_bound_ms(
            haversine_miles(node["lat"], node["lon"], dest["lat"], dest["lon"]),
            fastest_distance,
            fastest_ms,
        )
        for node_id, node in nodes.items()
    }


def _is_faster(distance: float, duration_ms: int, best_distance: float, best_ms: int) -> bool:
    return Decimal(str(distance)) * best_ms > Decimal(str(best_distance)) * duration_ms


def _lower_bound_ms(straight_miles: float, fastest_distance: float, fastest_ms: int) -> int:
    if straight_miles <= 0:
        return 0
    millis = Decimal(str(straight_miles)) * fastest_ms / Decimal(str(fastest_distance))
    return int(millis.to_integral_value(ROUND_FLOOR))


def _infeasible(
    config: dict,
    started: float,
    algorithm: str,
    *,
    expanded: int,
    stored: int,
) -> dict:
    return {
        "status": "INFEASIBLE",
        "algorithm": algorithm,
        "total_time_minutes": 0,
        "driving_time_minutes": 0,
        "charging_time_minutes": 0,
        "total_distance_miles": 0,
        "number_of_charging_stops": 0,
        "soc_step_kwh": config["soc_step_kwh"],
        "route_node_ids": [],
        "events": [],
        "metrics": _metrics(started, expanded, stored),
    }


def _metrics(started: float, expanded: int, stored: int) -> dict:
    return {
        "runtime_ms": (time.perf_counter() - started) * 1000.0,
        "states_expanded": expanded,
        "peak_states_stored": stored,
    }
