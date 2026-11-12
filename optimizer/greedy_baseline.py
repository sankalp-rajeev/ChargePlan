"""Farthest-reachable-charger baseline.

Owned by Shannon. This is the only file under ``optimizer/`` that Shannon
edits. Import ``optimizer.model`` for loading and discretization, and do not
change the other optimizer modules.

The frozen rule is PRD Section 12: from the current node, drive to the
destination when it is reachable after an optional charge; otherwise charge
just enough to reach the charger that is closest to the destination among
chargers strictly closer to it than the current node. Ties break by higher
charging rate, then smaller node id. The result uses ``algorithm`` =
``greedy_frc``.
"""

from __future__ import annotations

import time
from collections import defaultdict
from decimal import Decimal

from optimizer.model import (
    InputError,
    charge_step_minutes,
    discretize,
    edge_energy_steps,
    haversine_miles,
    minutes_to_ms,
    ms_to_minutes,
)


def solve(graph: dict, config: dict) -> dict:
    """Return an ``algorithm_result`` dict for the FRC heuristic."""
    started = time.perf_counter()
    capacity, initial, reserve, delta_e = discretize(config)
    decisions = 0

    if initial < reserve:
        return _infeasible(config, started, decisions)

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

    location = origin
    battery = initial
    route = [origin]
    events: list[dict] = []
    drive_ms = 0
    charge_ms = 0
    stops = 0
    distance_total = 0.0

    while location != destination:
        decisions += 1
        target_edge = _choose_target_edge(
            location,
            battery,
            capacity,
            reserve,
            destination,
            nodes,
            outgoing,
            config,
        )
        if target_edge is None:
            return _infeasible(config, started, decisions)

        energy = edge_energy_steps(target_edge["distance_miles"], config)
        needed = reserve + energy
        if battery < needed:
            rate = nodes[location]["charge_rate_kw"]
            if rate is None:
                return _infeasible(config, started, decisions)
            charged = _charge_to(
                location,
                battery,
                needed,
                capacity,
                float(rate),
                delta_e,
            )
            if charged is None:
                return _infeasible(config, started, decisions)
            events.append(charged["event"])
            charge_ms += charged["duration_ms"]
            battery = charged["battery"]
            stops += 1

        arrival = battery - energy
        if arrival < reserve:
            return _infeasible(config, started, decisions)

        duration_ms = minutes_to_ms(target_edge["drive_time_minutes"])
        events.append(
            {
                "type": "drive",
                "from": location,
                "to": target_edge["to"],
                "distance_miles": float(target_edge["distance_miles"]),
                "duration_minutes": ms_to_minutes(duration_ms),
                "energy_kwh_discretized": _kwh(energy, delta_e),
                "soc_before_kwh": _kwh(battery, delta_e),
                "soc_after_kwh": _kwh(arrival, delta_e),
            }
        )
        drive_ms += duration_ms
        distance_total += float(target_edge["distance_miles"])
        battery = arrival
        location = target_edge["to"]
        route.append(location)

    return {
        "status": "FEASIBLE",
        "algorithm": "greedy_frc",
        "total_time_minutes": ms_to_minutes(drive_ms + charge_ms),
        "driving_time_minutes": ms_to_minutes(drive_ms),
        "charging_time_minutes": ms_to_minutes(charge_ms),
        "total_distance_miles": distance_total,
        "number_of_charging_stops": stops,
        "soc_step_kwh": config["soc_step_kwh"],
        "route_node_ids": route,
        "events": events,
        "metrics": {
            "runtime_ms": (time.perf_counter() - started) * 1000.0,
            "states_expanded": decisions,
            "peak_states_stored": decisions,
        },
    }


def _choose_target_edge(
    location: str,
    battery: int,
    capacity: int,
    reserve: int,
    destination: str,
    nodes: dict[str, dict],
    outgoing: dict[str, list[dict]],
    config: dict,
) -> dict | None:
    """Return the Section 12 target edge from ``location``, or ``None``."""
    can_charge = nodes[location]["charge_rate_kw"] is not None
    cap = capacity if can_charge else battery
    reachable: list[dict] = []
    for edge in outgoing.get(location, ()):
        energy = edge_energy_steps(edge["distance_miles"], config)
        if energy <= cap - reserve:
            reachable.append(edge)

    if not reachable:
        return None

    for edge in reachable:
        if edge["to"] == destination:
            return edge

    here = nodes[location]
    dest = nodes[destination]
    here_to_dest = haversine_miles(here["lat"], here["lon"], dest["lat"], dest["lon"])

    candidates: list[dict] = []
    for edge in reachable:
        node = nodes[edge["to"]]
        if node["charge_rate_kw"] is None:
            continue
        dist = haversine_miles(node["lat"], node["lon"], dest["lat"], dest["lon"])
        if dist < here_to_dest:
            candidates.append(edge)

    if not candidates:
        return None

    def sort_key(edge: dict) -> tuple[float, float, str]:
        node = nodes[edge["to"]]
        dist = haversine_miles(node["lat"], node["lon"], dest["lat"], dest["lon"])
        rate = float(node["charge_rate_kw"])
        return (dist, -rate, edge["to"])

    return min(candidates, key=sort_key)


def _charge_to(
    station: str,
    battery: int,
    needed: int,
    capacity: int,
    rate_kw: float,
    delta_e: float,
) -> dict | None:
    """Charge in ``ΔE`` steps until ``battery >= needed`` (capped at capacity)."""
    if needed > capacity:
        return None
    before = battery
    duration_ms = 0
    while battery < needed:
        if battery + 1 > capacity:
            return None
        duration_ms += minutes_to_ms(charge_step_minutes(delta_e, rate_kw))
        battery += 1
    return {
        "battery": battery,
        "duration_ms": duration_ms,
        "event": {
            "type": "charge",
            "station": station,
            "soc_before_kwh": _kwh(before, delta_e),
            "soc_after_kwh": _kwh(battery, delta_e),
            "duration_minutes": ms_to_minutes(duration_ms),
        },
    }


def _kwh(steps: int, delta_e: float) -> float:
    return float(Decimal(steps) * Decimal(str(delta_e)))


def _infeasible(config: dict, started: float, decisions: int) -> dict:
    return {
        "status": "INFEASIBLE",
        "algorithm": "greedy_frc",
        "total_time_minutes": 0,
        "driving_time_minutes": 0,
        "charging_time_minutes": 0,
        "total_distance_miles": 0,
        "number_of_charging_stops": 0,
        "soc_step_kwh": config["soc_step_kwh"],
        "route_node_ids": [],
        "events": [],
        "metrics": {
            "runtime_ms": (time.perf_counter() - started) * 1000.0,
            "states_expanded": decisions,
            "peak_states_stored": decisions,
        },
    }
