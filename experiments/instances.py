"""Benchmark instances for the experiment runner.

The Detroit–Chicago sample is the frozen file in ``data/synthetic``. The tiny
suite is drawn from ``random.Random(seed)`` with seed 505 by default. Nodes are
spread by a few miles and each road is at least the straight-line distance, so
the A* heuristic is nonzero. Every tiny instance stays inside the exact-solver
bounds below. The sample does not: its capacity is 70 steps.

Exact search is limited to:

- at most 8 nodes,
- at most 5 chargers (nodes whose ``charge_rate_kw`` is not null),
- at most 8 discrete capacity steps after Section 7.1 rounding.

Those bounds match the tiny-instance regime in Sections 13 and 21. A query
outside them is recorded as ``SKIPPED`` and the exact solver is not called.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from optimizer.model import InputError, discretize, haversine_miles, load_config, load_graph

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = ROOT / "data" / "synthetic"

DEFAULT_SEED = 505
DEFAULT_TINY = 20
EXACT_MAX_NODES = 8
EXACT_MAX_CHARGERS = 5
EXACT_MAX_CAPACITY_STEPS = 8
SAMPLE_ID = "detroit-chicago"
_LENGTH_TOLERANCE_MILES = 1e-6


@dataclass(frozen=True)
class Instance:
    """One graph and trip config, plus the counts the CSV records."""

    instance_id: str
    graph: dict
    config: dict
    seed: int | None

    @property
    def node_count(self) -> int:
        return len(self.graph["nodes"])

    @property
    def charger_count(self) -> int:
        return sum(1 for node in self.graph["nodes"] if node["charge_rate_kw"] is not None)

    @property
    def capacity_kwh(self) -> float:
        return float(self.config["battery_capacity_kwh"])

    @property
    def soc_step_kwh(self) -> float:
        return float(self.config["soc_step_kwh"])


def exact_block_reason(graph: dict, config: dict) -> str | None:
    """Return why exact search must not run, or ``None`` when the query is tiny."""
    node_count = len(graph["nodes"])
    if node_count > EXACT_MAX_NODES:
        return f"nodes {node_count} exceed {EXACT_MAX_NODES}"
    charger_count = sum(1 for node in graph["nodes"] if node["charge_rate_kw"] is not None)
    if charger_count > EXACT_MAX_CHARGERS:
        return f"chargers {charger_count} exceed {EXACT_MAX_CHARGERS}"
    capacity_steps, _initial, _reserve, _step = discretize(config)
    if capacity_steps > EXACT_MAX_CAPACITY_STEPS:
        return f"capacity steps {capacity_steps} exceed {EXACT_MAX_CAPACITY_STEPS}"
    return None


def load_sample(directory: Path | None = None) -> Instance:
    """Load the frozen Detroit–Chicago sample. The files must validate."""
    folder = SYNTHETIC if directory is None else directory
    graph_path = folder / "canonical_graph.json"
    config_path = folder / "trip_config.json"
    if not graph_path.is_file() or not config_path.is_file():
        raise InputError(f"benchmark sample is missing under {folder}")
    graph = load_graph(graph_path)
    config = load_config(config_path)
    return Instance(SAMPLE_ID, graph, config, None)


def tiny_instances(seed: int, count: int) -> list[Instance]:
    """Build ``count`` exact-legal graphs from ``random.Random(seed)``."""
    if count < 1:
        raise InputError("tiny instance count must be positive")
    rng = random.Random(seed)
    instances = [_tiny_query(rng, seed, index) for index in range(count)]
    for instance in instances:
        reason = exact_block_reason(instance.graph, instance.config)
        if reason is not None:
            raise InputError(f"{instance.instance_id} violated the exact bound: {reason}")
    return instances


def _tiny_query(rng: random.Random, seed: int, index: int) -> Instance:
    charger_count = rng.randint(1, 3)
    capacity = rng.randint(4, EXACT_MAX_CAPACITY_STEPS)
    origin_rate = 60 if rng.random() < 0.25 else None
    nodes = [
        _node("origin", "origin", origin_rate, *_coordinate(index, 0)),
        _node("destination", "destination", None, *_coordinate(index, 1)),
    ]
    for charger_index in range(charger_count):
        rate = 60 if rng.random() < 0.5 else 30
        lat, lon = _coordinate(index, 2 + charger_index)
        nodes.append(_node(f"c{charger_index}", "charger", rate, lat, lon))
    located = {node["id"]: node for node in nodes}
    edges: list[dict] = []
    for start in located:
        for end in located:
            if start != end and rng.random() < 0.5:
                edges.append(_road(rng, located, start, end))
    if not any(edge["from"] == "origin" for edge in edges):
        edges.append(_road(rng, located, "origin", "destination"))
    _require_straight_line(located, edges)
    initial = rng.randint(0, capacity)
    reserve = rng.randint(0, min(2, capacity))
    config = {
        "origin": "origin",
        "destination": "destination",
        "battery_capacity_kwh": capacity,
        "initial_soc_kwh": initial,
        "reserve_kwh": reserve,
        "consumption_kwh_per_mile": 1,
        "soc_step_kwh": 1,
    }
    return Instance(f"tiny-{index:03d}", {"nodes": nodes, "edges": edges}, config, seed)


def _coordinate(index: int, slot: int) -> tuple[float, float]:
    """Spread nodes by about 0.7–5 miles so the straight-line heuristic is positive."""
    lat = 42.0 + ((index * 3 + slot * 5) % 7) * 0.015
    lon = -83.0 + ((index * 5 + slot * 3) % 9) * 0.015
    return lat, lon


def _node(
    node_id: str,
    node_type: str,
    rate: float | None,
    lat: float,
    lon: float,
) -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "lat": lat,
        "lon": lon,
        "charge_rate_kw": rate,
    }


def _road(rng: random.Random, nodes: dict[str, dict], start: str, end: str) -> dict:
    straight = haversine_miles(
        nodes[start]["lat"],
        nodes[start]["lon"],
        nodes[end]["lat"],
        nodes[end]["lon"],
    )
    miles = float(rng.randint(1, 4))
    if miles + _LENGTH_TOLERANCE_MILES < straight:
        miles = straight
    return {
        "from": start,
        "to": end,
        "distance_miles": miles,
        "drive_time_minutes": rng.randint(1, 6),
    }


def _require_straight_line(nodes: dict[str, dict], edges: list[dict]) -> None:
    for edge in edges:
        start = nodes[edge["from"]]
        end = nodes[edge["to"]]
        straight = haversine_miles(start["lat"], start["lon"], end["lat"], end["lon"])
        if edge["distance_miles"] + _LENGTH_TOLERANCE_MILES < straight:
            raise InputError(
                f"edge {edge['from']!r} -> {edge['to']!r} is shorter than "
                "the straight-line distance"
            )
