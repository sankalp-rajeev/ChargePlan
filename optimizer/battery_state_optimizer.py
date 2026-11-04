"""Label-setting search over battery-expanded states.

Owned by Sankalp. ``use_heuristic=False`` is plain label-setting (S2).
``use_heuristic=True`` is the A* variant and is intentionally not implemented
here (S10). Labels are ``(time_ms, stops, distance)`` and compare
lexicographically. One minute is 60,000 ms.

The search is implemented in this module with ``heapq``. It does not call
NetworkX or another shortest-path library.
"""

from __future__ import annotations

import heapq
import time
from collections import defaultdict

from optimizer.model import (
    InputError,
    charge_step_minutes,
    discretize,
    edge_energy_steps,
    minutes_to_ms,
    ms_to_minutes,
)
from optimizer.reconstruction import reconstruct

State = tuple[str, int, int]
Label = tuple[int, int, float]


def solve(graph: dict, config: dict, *, use_heuristic: bool = False) -> dict:
    """Return an ``algorithm_result`` dict for plain label-setting.

    ``use_heuristic=True`` is the A* variant and raises ``NotImplementedError``
    until S10. The feasible result uses ``algorithm`` = ``label_setting``.
    """
    if use_heuristic:
        raise NotImplementedError("A* is not implemented yet (S10)")

    started = time.perf_counter()
    capacity, initial, reserve, delta_e = discretize(config)
    if initial < reserve:
        return _infeasible(config, started, expanded=0, stored=0)

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

    start: State = (origin, initial, 0)
    start_label: Label = (0, 0, 0.0)
    best: dict[State, Label] = {start: start_label}
    parent: dict[State, tuple[State, dict] | None] = {start: None}
    queue: list[tuple[Label, int, State]] = []
    counter = 0
    heapq.heappush(queue, (start_label, counter, start))
    expanded = 0
    goal: State | None = None

    while queue:
        label, _tie, state = heapq.heappop(queue)
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
            _relax(
                best,
                parent,
                queue,
                (edge["to"], arrival, 0),
                (label[0] + drive_ms, label[1], label[2] + edge["distance_miles"]),
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
            _relax(
                best,
                parent,
                queue,
                (location, battery + 1, 1),
                (label[0] + charge_ms, label[1] + (1 if flag == 0 else 0), label[2]),
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
        return _infeasible(config, started, expanded=expanded, stored=len(best))

    built = reconstruct(parent, goal, graph, config)
    time_ms, stops, distance = best[goal]
    return {
        "status": "FEASIBLE",
        "algorithm": "label_setting",
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
    queue: list[tuple[Label, int, State]],
    successor: State,
    successor_label: Label,
    predecessor: State,
    action: dict,
) -> None:
    current = best.get(successor)
    if current is not None and successor_label >= current:
        return
    best[successor] = successor_label
    parent[successor] = (predecessor, action)
    tie = len(queue)
    heapq.heappush(queue, (successor_label, tie, successor))


def _infeasible(config: dict, started: float, *, expanded: int, stored: int) -> dict:
    return {
        "status": "INFEASIBLE",
        "algorithm": "label_setting",
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
