"""Shared graph, trip-config, discretization, and result I/O.

This is the only shared module. Sankalp owns it. It is frozen with the
Phase 1 contracts: later changes need both teammates and a README version bump.

Discretization (PRD Section 7.1):

- capacity ``B`` and initial energy ``b0`` round down to multiples of ``ΔE``,
- reserve ``r`` rounds up,
- edge energy ``c * distance_miles`` rounds up,
- a plan accepted after that rounding stays at or above the reserve in real kWh.

Label time is an integer number of milliseconds, with 1 minute = 60,000 ms.
``minutes_to_ms`` rounds half up at the boundary between the search and
``algorithm_result.json``.
"""

from __future__ import annotations

import json
import math
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path

import jsonschema

_ROOT = Path(__file__).resolve().parents[1]
_CONTRACTS = _ROOT / "contracts"
_EARTH_RADIUS_MILES = 3958.7613
_LENGTH_TOLERANCE_MILES = 1e-6
_MS_PER_MINUTE = 60_000

_GRAPH_SCHEMA = "canonical_graph.schema.json"
_CONFIG_SCHEMA = "trip_config.schema.json"
_RESULT_SCHEMA = "algorithm_result.schema.json"

_validators: dict[str, jsonschema.Validator] = {}


class InputError(ValueError):
    """A graph, trip config, or result failed to load or validate."""


def load_graph(path: str | Path) -> dict:
    """Load a ``canonical_graph.json`` file and validate the frozen contract.

    Beyond the schema, node ids are unique, every edge names two of those
    ids, and each road distance is at least the straight-line distance.
    """
    graph = _read_json(path)
    _validate(graph, _GRAPH_SCHEMA)
    _check_graph_structure(graph)
    return graph


def load_config(path: str | Path) -> dict:
    """Load a ``trip_config.json`` file and validate it against the schema."""
    config = _read_json(path)
    _validate(config, _CONFIG_SCHEMA)
    discretize(config)
    return config


def validate_query(graph: dict, config: dict) -> None:
    """Require the configured origin and destination to be nodes in the graph."""
    node_ids = {node["id"] for node in graph["nodes"]}
    origin = config["origin"]
    destination = config["destination"]
    if origin not in node_ids:
        raise InputError(f"origin {origin!r} is not a node in the graph")
    if destination not in node_ids:
        raise InputError(f"destination {destination!r} is not a node in the graph")


def discretize(config: dict) -> tuple[int, int, int, float]:
    """Return ``(B', b0', r', ΔE)``.

    ``B'`` and ``b0'`` are floored counts of ``ΔE``. ``r'`` is the ceiled
    count. The fourth value is ``soc_step_kwh`` itself, in kWh.

    Initial SOC and reserve must be at most the battery capacity, both in
    kWh and after rounding.
    """
    step = _required_number(config, "soc_step_kwh")
    capacity = _required_number(config, "battery_capacity_kwh")
    initial = _required_number(config, "initial_soc_kwh")
    reserve = _required_number(config, "reserve_kwh")
    if step <= 0 or capacity <= 0:
        raise InputError("battery capacity and soc_step_kwh must be positive")
    if initial < 0 or reserve < 0:
        raise InputError("initial energy and reserve must be non-negative")
    if initial > capacity:
        raise InputError("initial_soc_kwh exceeds battery_capacity_kwh")
    if reserve > capacity:
        raise InputError("reserve_kwh exceeds battery_capacity_kwh")
    step_dec = _decimal(step)
    capacity_steps = _count(capacity, step_dec, ROUND_FLOOR)
    initial_steps = _count(initial, step_dec, ROUND_FLOOR)
    reserve_steps = _count(reserve, step_dec, ROUND_CEILING)
    if initial_steps > capacity_steps:
        raise InputError("discretized initial SOC exceeds battery capacity")
    if reserve_steps > capacity_steps:
        raise InputError("discretized reserve exceeds battery capacity")
    return capacity_steps, initial_steps, reserve_steps, float(step)


def edge_energy_steps(distance_miles: float, config: dict) -> int:
    """Return ``ceil(c * distance_miles / ΔE)``."""
    if distance_miles < 0:
        raise InputError("distance_miles must be non-negative")
    rate = _required_number(config, "consumption_kwh_per_mile")
    step = _required_number(config, "soc_step_kwh")
    if rate <= 0 or step <= 0:
        raise InputError("consumption and soc_step_kwh must be positive")
    energy = _decimal(distance_miles) * _decimal(rate)
    return _count_decimal(energy, _decimal(step), ROUND_CEILING)


def edge_energy_kwh(distance_miles: float, config: dict) -> float:
    """Return the ceiled edge energy in kWh, a multiple of ``ΔE``."""
    steps = edge_energy_steps(distance_miles, config)
    step = _required_number(config, "soc_step_kwh")
    return float(Decimal(steps) * _decimal(step))


def charge_step_minutes(delta_e_kwh: float, rate_kw: float) -> float:
    """Minutes to add ``delta_e_kwh`` at ``rate_kw``.

    One discretization step takes ``(ΔE / p) * 60`` minutes.
    """
    if delta_e_kwh < 0 or rate_kw <= 0:
        raise InputError("charging needs non-negative energy and a positive rate")
    minutes = _decimal(delta_e_kwh) * Decimal(60) / _decimal(rate_kw)
    return float(minutes)


def minutes_to_ms(minutes: float) -> int:
    """Convert minutes to integer milliseconds, rounding half up.

    One minute is 60,000 milliseconds.
    """
    if minutes < 0:
        raise InputError("minutes must be non-negative")
    millis = _decimal(minutes) * Decimal(_MS_PER_MINUTE)
    return int(millis.to_integral_value(ROUND_HALF_UP))


def ms_to_minutes(time_ms: int) -> float:
    """Convert an integer millisecond label back to minutes.

    One minute is 60,000 milliseconds.
    """
    if isinstance(time_ms, bool) or not isinstance(time_ms, int) or time_ms < 0:
        raise InputError("time_ms must be a non-negative integer")
    return float(Decimal(time_ms) / Decimal(_MS_PER_MINUTE))


def write_result(result: dict, path: str | Path) -> None:
    """Validate ``result`` and write ``algorithm_result.json``."""
    _validate(result, _RESULT_SCHEMA)
    destination = Path(path)
    try:
        destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise InputError(f"cannot write {destination}: {exc.strerror}") from exc


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles between two WGS84 points."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    haversine = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_MILES * math.asin(math.sqrt(haversine))


def _read_json(path: str | Path) -> dict:
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError(f"cannot read {file_path}: {exc.strerror}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(f"invalid JSON in {file_path}: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise InputError(f"{file_path} must contain a JSON object")
    return payload


def _validator(schema_name: str) -> jsonschema.Validator:
    cached = _validators.get(schema_name)
    if cached is not None:
        return cached
    schema_path = _CONTRACTS / schema_name
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    cached = validator_cls(schema)
    _validators[schema_name] = cached
    return cached


def _validate(instance: dict, schema_name: str) -> None:
    errors = sorted(_validator(schema_name).iter_errors(instance), key=_error_sort_key)
    if not errors:
        return
    first = errors[0]
    location = ".".join(str(part) for part in first.path) or "<root>"
    raise InputError(f"{schema_name}: {location}: {first.message}")


def _error_sort_key(error: jsonschema.ValidationError) -> list[str]:
    return [str(part) for part in error.path]


def _check_graph_structure(graph: dict) -> None:
    nodes = {node["id"]: node for node in graph["nodes"]}
    if len(nodes) != len(graph["nodes"]):
        raise InputError("canonical graph has duplicate node ids")
    for edge in graph["edges"]:
        start_id = edge["from"]
        end_id = edge["to"]
        if start_id not in nodes or end_id not in nodes:
            raise InputError(f"edge {start_id!r} -> {end_id!r} names an unknown node")
        start = nodes[start_id]
        end = nodes[end_id]
        straight = haversine_miles(start["lat"], start["lon"], end["lat"], end["lon"])
        if edge["distance_miles"] + _LENGTH_TOLERANCE_MILES < straight:
            raise InputError(
                f"edge {start_id!r} -> {end_id!r} is shorter than the straight-line distance"
            )


def _required_number(payload: dict, key: str) -> float:
    if key not in payload:
        raise InputError(f"missing {key}")
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InputError(f"{key} must be a finite number")
    return float(value)


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _count(amount: float, step: Decimal, rounding: str) -> int:
    return _count_decimal(_decimal(amount), step, rounding)


def _count_decimal(amount: Decimal, step: Decimal, rounding: str) -> int:
    if step <= 0:
        raise InputError("soc_step_kwh must be positive")
    return int((amount / step).to_integral_value(rounding))
