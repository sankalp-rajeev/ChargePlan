"""Turn parent pointers into a route and an event list.

Owned by Sankalp. Drive and charge events follow the result schema. Consecutive
charge steps at one visit become one charge event, so the stop counter matches
the number of charge events.
"""

from __future__ import annotations

from decimal import Decimal

from optimizer.model import InputError, ms_to_minutes


def reconstruct(parent: dict, goal: tuple, graph: dict, config: dict) -> dict:
    """Return the route, events, and drive/charge minutes for one settled goal.

    ``parent[state]`` is ``None`` at the origin and otherwise
    ``(previous_state, action)``. ``graph`` is part of the shared signature.
    """
    del graph
    delta_e = float(config["soc_step_kwh"])
    actions = _actions(parent, goal)
    route = [_origin(parent, goal)]
    events: list[dict] = []
    drive_ms = 0
    charge_ms = 0
    index = 0
    while index < len(actions):
        action = actions[index]
        if action["type"] == "drive":
            route.append(action["to"])
            drive_ms += action["duration_ms"]
            events.append(_drive_event(action, delta_e))
            index += 1
            continue
        station = action["station"]
        total_ms = 0
        last = action
        while (
            index < len(actions)
            and actions[index]["type"] == "charge"
            and actions[index]["station"] == station
        ):
            total_ms += actions[index]["duration_ms"]
            last = actions[index]
            index += 1
        charge_ms += total_ms
        events.append(_charge_event(action, last, total_ms, delta_e))
    return {
        "route_node_ids": route,
        "events": events,
        "driving_time_minutes": ms_to_minutes(drive_ms),
        "charging_time_minutes": ms_to_minutes(charge_ms),
    }


def _actions(parent: dict, goal: tuple) -> list[dict]:
    actions: list[dict] = []
    state = goal
    seen: set[tuple] = set()
    while True:
        link = parent[state]
        if link is None:
            break
        if state in seen:
            raise InputError("reconstruction found a cycle")
        seen.add(state)
        previous, action = link
        actions.append(action)
        state = previous
    actions.reverse()
    return actions


def _origin(parent: dict, goal: tuple) -> str:
    state = goal
    while parent[state] is not None:
        state = parent[state][0]
    return state[0]


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


def _charge_event(first: dict, last: dict, total_ms: int, delta_e: float) -> dict:
    return {
        "type": "charge",
        "station": first["station"],
        "soc_before_kwh": _kwh(first["soc_before_steps"], delta_e),
        "soc_after_kwh": _kwh(last["soc_after_steps"], delta_e),
        "duration_minutes": ms_to_minutes(total_ms),
    }


def _kwh(steps: int, delta_e: float) -> float:
    return float(Decimal(steps) * Decimal(str(delta_e)))
