"""Independent plan checker for ``algorithm_result.json`` files.

This module re-derives every number in a result from its events, the graph,
and the trip config. It does not import ``battery_state_optimizer``,
``exact_solver``, ``greedy_baseline``, ``reconstruction``, or ``optimizer.model``,
so a rounding or accounting bug in the solvers cannot also hide in the check.
All arithmetic uses ``fractions.Fraction`` built from the shortest decimal
spelling of each JSON number, which is exact for decimal inputs.

Rules (PRD Section 7.1 and Section 16.3):

- capacity ``B'`` and initial energy ``b0'`` floor to multiples of ``ΔE``, the
  reserve ``r'`` ceils, and each edge costs ``ceil(c * distance / ΔE)`` steps,
- events form one walk from the origin to the destination, every drive uses an
  edge of the graph with that distance, and ``route_node_ids`` is that walk,
- SOC chains from the initial energy, no drive arrives below the reserve, no
  charge passes capacity or happens where the graph has no charger,
- a charge event is a new stop unless the event before it was a charge at the
  same node,
- total, driving and charging minutes agree with the recomputed integer
  milliseconds (1 ms tolerance) and distance with the summed edge lengths
  (1e-6 miles),
- an ``INFEASIBLE`` result has no route and no events.

``check_plan`` returns a list of problem strings. An empty list means valid.
"""

from __future__ import annotations

import math
from fractions import Fraction

_MS_PER_MINUTE = 60_000
_TIME_TOLERANCE_MS = 1
_DISTANCE_TOLERANCE_MILES = 1e-6
_EDGE_DISTANCE_TOLERANCE_MILES = 1e-9


class _Malformed(Exception):
    """The result, graph, or config cannot be read well enough to continue."""


def check_plan(graph: dict, config: dict, result: dict) -> list[str]:
    """Return every problem found in ``result``. An empty list means it is valid."""
    problems: list[str] = []
    try:
        _check(graph, config, result, problems)
    except _Malformed as exc:
        problems.append(f"malformed input: {exc}")
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        problems.append(f"malformed input: {type(exc).__name__}: {exc}")
    return problems


def _check(graph: dict, config: dict, result: dict, problems: list[str]) -> None:
    status = result.get("status")
    route = result.get("route_node_ids")
    events = result.get("events")
    if not isinstance(route, list) or not isinstance(events, list):
        raise _Malformed("route_node_ids and events must be lists")
    if status == "INFEASIBLE":
        if route:
            problems.append("INFEASIBLE result has a route")
        if events:
            problems.append("INFEASIBLE result has events")
        return
    if status != "FEASIBLE":
        problems.append(f"status must be FEASIBLE or INFEASIBLE, got {status!r}")
        return

    step = _number(config["soc_step_kwh"], "soc_step_kwh")
    if step <= 0:
        raise _Malformed("soc_step_kwh must be positive")
    capacity_steps = math.floor(_number(config["battery_capacity_kwh"], "capacity") / step)
    initial_steps = math.floor(_number(config["initial_soc_kwh"], "initial_soc_kwh") / step)
    reserve_steps = math.ceil(_number(config["reserve_kwh"], "reserve_kwh") / step)
    consumption = _number(config["consumption_kwh_per_mile"], "consumption")
    origin = config["origin"]
    destination = config["destination"]

    if _number(result["soc_step_kwh"], "result soc_step_kwh") != step:
        problems.append("result soc_step_kwh differs from the config")

    rates = {node["id"]: node["charge_rate_kw"] for node in graph["nodes"]}
    if origin not in rates:
        raise _Malformed(f"origin {origin!r} is not a node")
    if destination not in rates:
        raise _Malformed(f"destination {destination!r} is not a node")
    roads: dict[tuple[str, str], list[dict]] = {}
    for edge in graph["edges"]:
        roads.setdefault((edge["from"], edge["to"]), []).append(edge)

    position = origin
    walk = [origin]
    expected_before: Fraction = Fraction(initial_steps)
    stops = 0
    previous: dict | None = None
    drive_ms = 0
    charge_ms = 0
    distance = Fraction(0)

    for index, event in enumerate(events):
        tag = f"event {index}"
        if not isinstance(event, dict):
            raise _Malformed(f"{tag} is not an object")
        kind = event.get("type")
        before = _steps(event, "soc_before_kwh", step, tag, problems)
        after = _steps(event, "soc_after_kwh", step, tag, problems)
        if before != expected_before:
            problems.append(
                f"{tag}: soc_before is {_fmt(before)} steps but the previous state "
                f"left {_fmt(expected_before)}"
            )
        if kind == "drive":
            start = event["from"]
            end = event["to"]
            if start != position:
                problems.append(f"{tag}: drive starts at {start!r} but the walk is at {position!r}")
            edge = _find_edge(roads, event, tag, problems)
            if edge is not None:
                energy = _ceil(
                    _number(edge["distance_miles"], "edge distance") * consumption / step
                )
                if _steps(event, "energy_kwh_discretized", step, tag, problems) != energy:
                    problems.append(f"{tag}: drive energy is not ceil(c*d/dE) = {energy} steps")
                if after != before - energy:
                    problems.append(
                        f"{tag}: soc_after is {_fmt(after)} steps, expected "
                        f"{_fmt(before - energy)}"
                    )
                expected_ms = _half_up(
                    _number(edge["drive_time_minutes"], "edge drive time") * _MS_PER_MINUTE
                )
                _near(event["duration_minutes"], expected_ms, f"{tag}: drive duration", problems)
                drive_ms += expected_ms
                distance += _number(edge["distance_miles"], "edge distance")
            if after < reserve_steps:
                problems.append(
                    f"{tag}: arrives with {_fmt(after)} steps, below the reserve of "
                    f"{reserve_steps}"
                )
            position = end
            walk.append(end)
        elif kind == "charge":
            station = event["station"]
            if station != position:
                problems.append(
                    f"{tag}: charge at {station!r} but the walk is at {position!r}"
                )
            rate = rates.get(station)
            if rate is None:
                problems.append(f"{tag}: {station!r} has no charger")
            added = after - before
            if added <= 0:
                problems.append(f"{tag}: charge adds no energy")
            if after > capacity_steps:
                problems.append(
                    f"{tag}: charges to {_fmt(after)} steps, above capacity {capacity_steps}"
                )
            if rate is not None and added > 0 and added.denominator == 1:
                step_ms = _half_up(step * 3_600_000 / _number(rate, "charge rate"))
                _near(
                    event["duration_minutes"],
                    int(added) * step_ms,
                    f"{tag}: charge duration",
                    problems,
                )
                charge_ms += int(added) * step_ms
            continued = (
                previous is not None
                and previous.get("type") == "charge"
                and previous.get("station") == station
            )
            if not continued:
                stops += 1
        else:
            problems.append(f"{tag}: unknown event type {kind!r}")
        expected_before = after
        previous = event

    if position != destination:
        problems.append(f"walk ends at {position!r}, not the destination {destination!r}")
    if route != walk:
        problems.append("route_node_ids does not equal the walk of the events")
    if result["number_of_charging_stops"] != stops:
        problems.append(
            f"number_of_charging_stops is {result['number_of_charging_stops']}, "
            f"recomputed {stops}"
        )
    _near(result["driving_time_minutes"], drive_ms, "driving_time_minutes", problems)
    _near(result["charging_time_minutes"], charge_ms, "charging_time_minutes", problems)
    _near(result["total_time_minutes"], drive_ms + charge_ms, "total_time_minutes", problems)
    reported = _number(result["total_distance_miles"], "total_distance_miles")
    if abs(float(reported - distance)) > _DISTANCE_TOLERANCE_MILES:
        problems.append(
            f"total_distance_miles is {float(reported)}, recomputed {float(distance)}"
        )


def _find_edge(
    roads: dict[tuple[str, str], list[dict]], event: dict, tag: str, problems: list[str]
) -> dict | None:
    candidates = roads.get((event["from"], event["to"]), [])
    if not candidates:
        problems.append(f"{tag}: no edge {event['from']!r} -> {event['to']!r} in the graph")
        return None
    claimed = _number(event["distance_miles"], "drive distance")
    for edge in candidates:
        gap = abs(float(_number(edge["distance_miles"], "edge distance") - claimed))
        if gap <= _EDGE_DISTANCE_TOLERANCE_MILES:
            return edge
    problems.append(
        f"{tag}: drive distance {float(claimed)} matches no edge "
        f"{event['from']!r} -> {event['to']!r}"
    )
    return None


def _near(minutes: object, expected_ms: int, label: str, problems: list[str]) -> None:
    actual_ms = _number(minutes, label) * _MS_PER_MINUTE
    if abs(actual_ms - expected_ms) > _TIME_TOLERANCE_MS:
        problems.append(
            f"{label} is {float(actual_ms) / _MS_PER_MINUTE} min, recomputed "
            f"{expected_ms / _MS_PER_MINUTE} min"
        )


def _steps(event: dict, key: str, step: Fraction, tag: str, problems: list[str]) -> Fraction:
    value = _number(event[key], f"{tag} {key}") / step
    if value.denominator != 1:
        problems.append(f"{tag}: {key} is not a multiple of the SOC step")
    return value


def _number(value: object, name: str) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _Malformed(f"{name} is not a number: {value!r}")
    if isinstance(value, float) and not math.isfinite(value):
        raise _Malformed(f"{name} is not finite: {value!r}")
    return Fraction(str(value))


def _half_up(value: Fraction) -> int:
    return math.floor(value + Fraction(1, 2))


def _ceil(value: Fraction) -> int:
    return math.ceil(value)


def _fmt(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else str(float(value))
