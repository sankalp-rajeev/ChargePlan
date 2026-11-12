"""Exhaustive backtracking solver for tiny instances.

Owned by Sankalp. This is a depth-first enumeration, not a priority-queue
search. At each charger visit it chooses the charge amount in one decision
(0 through ``B' - b`` steps), then tries each legal drive. It imports
discretization helpers from ``optimizer.model`` and does not share search
code with ``battery_state_optimizer``.

Branches are pruned when they break the reserve or the capacity, when their
label is already no better than a finished plan, or when the same node and
charge flag was already reached with at least as much battery and a
lexicographically no-worse label.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import NamedTuple

from optimizer.model import (
    InputError,
    charge_step_minutes,
    discretize,
    edge_energy_steps,
    minutes_to_ms,
    ms_to_minutes,
)

Label = tuple[int, int, float]
RecordKey = tuple[str, int]


class _Road(NamedTuple):
    """One outgoing edge with its discretized energy and integer drive time."""

    to: str
    distance_miles: float
    energy_steps: int
    drive_ms: int


def solve(graph: dict, config: dict) -> dict:
    """Return an ``algorithm_result`` dict with ``algorithm`` = ``exact``.

    The search is exact on the discretized model for small graphs. It is not
    a production router.
    """
    started = time.perf_counter()
    capacity, initial, reserve, delta_e = discretize(config)
    if initial < reserve:
        return _infeasible(config, started, expanded=0, stored=0)

    nodes = {node["id"]: node for node in graph["nodes"]}
    origin = config["origin"]
    destination = config["destination"]
    if origin not in nodes or destination not in nodes:
        raise InputError("origin and destination must be nodes in the graph")

    # Energy steps and drive milliseconds depend on the edge alone, so they are
    # computed once here (Decimal arithmetic) and reused by every visit.
    outgoing: dict[str, list[_Road]] = {}
    for edge in graph["edges"]:
        if edge["from"] not in nodes or edge["to"] not in nodes:
            raise InputError(
                f"edge {edge['from']!r} -> {edge['to']!r} names an unknown node"
            )
        outgoing.setdefault(edge["from"], []).append(
            _Road(
                edge["to"],
                edge["distance_miles"],
                edge_energy_steps(edge["distance_miles"], config),
                minutes_to_ms(edge["drive_time_minutes"]),
            )
        )

    step_ms = {
        node_id: _charge_step_ms(node, delta_e) for node_id, node in nodes.items()
    }
    records: dict[RecordKey, list[tuple[int, Label]]] = {}
    best: dict[str, Label | list | None] = {"label": None, "actions": None}
    stats = {"expanded": 0, "peak": 0}
    actions: list[dict] = []

    def visit(node: str, battery: int, flag: int, label: Label) -> None:
        incumbent = best["label"]
        if isinstance(incumbent, tuple) and label >= incumbent:
            return
        key = (node, flag)
        if _dominated(records, key, battery, label):
            return
        _remember(records, key, battery, label)
        _note_stored(records, stats)
        stats["expanded"] += 1
        if node == destination:
            best["label"] = label
            best["actions"] = list(actions)
            return

        rate = nodes[node]["charge_rate_kw"]
        if rate is not None and flag == 0:
            limit = capacity - battery
            for added in range(0, limit + 1):
                _try_charge(node, battery, label, added, step_ms[node])
            return
        _try_drives(node, battery, label)

    def _try_charge(node: str, battery: int, label: Label, added: int, one_step_ms: int) -> None:
        if added == 0:
            _try_drives(node, battery, label)
            return
        charged = battery + added
        charged_label = (label[0] + added * one_step_ms, label[1] + 1, label[2])
        incumbent = best["label"]
        if isinstance(incumbent, tuple) and charged_label >= incumbent:
            return
        key = (node, 1)
        if _dominated(records, key, charged, charged_label):
            return
        _remember(records, key, charged, charged_label)
        _note_stored(records, stats)
        stats["expanded"] += 1
        actions.append(
            {
                "type": "charge",
                "station": node,
                "duration_ms": added * one_step_ms,
                "soc_before_steps": battery,
                "soc_after_steps": charged,
            }
        )
        _try_drives(node, charged, charged_label)
        actions.pop()

    def _try_drives(node: str, battery: int, label: Label) -> None:
        for road in outgoing.get(node, ()):
            energy = road.energy_steps
            arrival = battery - energy
            if arrival < reserve:
                continue
            drive_ms = road.drive_ms
            actions.append(
                {
                    "type": "drive",
                    "from": node,
                    "to": road.to,
                    "distance_miles": road.distance_miles,
                    "duration_ms": drive_ms,
                    "energy_steps": energy,
                    "soc_before_steps": battery,
                    "soc_after_steps": arrival,
                }
            )
            visit(
                road.to,
                arrival,
                0,
                (label[0] + drive_ms, label[1], label[2] + road.distance_miles),
            )
            actions.pop()

    visit(origin, initial, 0, (0, 0, 0.0))
    found = best["label"]
    if not isinstance(found, tuple):
        return _infeasible(config, started, expanded=stats["expanded"], stored=stats["peak"])
    built = _plan(best["actions"], origin, config)
    return {
        "status": "FEASIBLE",
        "algorithm": "exact",
        "total_time_minutes": ms_to_minutes(found[0]),
        "driving_time_minutes": built["driving_time_minutes"],
        "charging_time_minutes": built["charging_time_minutes"],
        "total_distance_miles": found[2],
        "number_of_charging_stops": found[1],
        "soc_step_kwh": config["soc_step_kwh"],
        "route_node_ids": built["route_node_ids"],
        "events": built["events"],
        "metrics": _metrics(started, stats["expanded"], stats["peak"]),
    }


def _charge_step_ms(node: dict, delta_e: float) -> int:
    rate = node["charge_rate_kw"]
    if rate is None:
        return 0
    return minutes_to_ms(charge_step_minutes(delta_e, rate))


def _dominated(
    records: dict[RecordKey, list[tuple[int, Label]]],
    key: RecordKey,
    battery: int,
    label: Label,
) -> bool:
    for seen_battery, seen_label in records.get(key, ()):
        if seen_battery >= battery and seen_label <= label:
            return True
    return False


def _remember(
    records: dict[RecordKey, list[tuple[int, Label]]],
    key: RecordKey,
    battery: int,
    label: Label,
) -> None:
    kept = [
        seen
        for seen in records.get(key, ())
        if not (battery >= seen[0] and label <= seen[1])
    ]
    kept.append((battery, label))
    records[key] = kept


def _stored(records: dict[RecordKey, list[tuple[int, Label]]]) -> int:
    return sum(len(seen) for seen in records.values())


def _note_stored(records: dict[RecordKey, list[tuple[int, Label]]], stats: dict[str, int]) -> None:
    stats["peak"] = max(stats["peak"], _stored(records))


def _plan(actions: list | None, origin: str, config: dict) -> dict:
    delta_e = float(config["soc_step_kwh"])
    route = [origin]
    events: list[dict] = []
    drive_ms = 0
    charge_ms = 0
    for action in actions or ():
        if action["type"] == "drive":
            route.append(action["to"])
            drive_ms += action["duration_ms"]
            events.append(_drive_event(action, delta_e))
            continue
        charge_ms += action["duration_ms"]
        events.append(_charge_event(action, delta_e))
    return {
        "route_node_ids": route,
        "events": events,
        "driving_time_minutes": ms_to_minutes(drive_ms),
        "charging_time_minutes": ms_to_minutes(charge_ms),
    }


def _drive_event(action: dict, delta_e: float) -> dict:
    return {
        "type": "drive",
        "from": action["from"],
        "to": action["to"],
        "distance_miles": action["distance_miles"],
        "duration_minutes": ms_to_minutes(action["duration_ms"]),
        "energy_kwh_discretized": _kwh(action["energy_steps"], delta_e),
        "soc_before_kwh": _kwh(action["soc_before_steps"], delta_e),
        "soc_after_kwh": _kwh(action["soc_after_steps"], delta_e),
    }


def _charge_event(action: dict, delta_e: float) -> dict:
    return {
        "type": "charge",
        "station": action["station"],
        "soc_before_kwh": _kwh(action["soc_before_steps"], delta_e),
        "soc_after_kwh": _kwh(action["soc_after_steps"], delta_e),
        "duration_minutes": ms_to_minutes(action["duration_ms"]),
    }


def _kwh(steps: int, delta_e: float) -> float:
    return float(Decimal(steps) * Decimal(str(delta_e)))


def _infeasible(config: dict, started: float, *, expanded: int, stored: int) -> dict:
    return {
        "status": "INFEASIBLE",
        "algorithm": "exact",
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
