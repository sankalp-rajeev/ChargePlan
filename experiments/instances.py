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
from dataclasses import dataclass, field
from pathlib import Path

from optimizer.model import InputError, discretize, haversine_miles, load_config, load_graph

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = ROOT / "data" / "synthetic"

DEFAULT_SEED = 505
DEFAULT_TINY = 20
CORRECTNESS_COUNT = 200
EXACT_MAX_NODES = 8
EXACT_MAX_CHARGERS = 5
EXACT_MAX_CAPACITY_STEPS = 8
SAMPLE_ID = "detroit-chicago"
# Section 22 lists 200 stations. The default run stops at 100 because 200 costs
# about three times as much as the other four counts together (see
# experiments/README.md). 200 is the size of the
# station pool, so it runs only when asked for with ``--charger-counts``, and
# nothing larger can be built.
CHARGER_COUNTS = (10, 25, 50, 100)
CHARGER_SCALE_MAX = 200
SCALING_REPLICATES = 10
# Percentages of the 70 kWh sample pack, plus the 0.1 kWh reference.
SOC_SAMPLE_STEPS = (14.0, 7.0, 3.5, 1.4, 0.7, 0.1)
# 60%, 20%, 10%, and 1% of the 10 kWh rounding probe. 6 kWh is kept because
# the 20% step still fits that edge; 6 kWh is the coarse bucket that does not.
SOC_ROUNDUP_STEPS = (6.0, 2.0, 1.0, 0.1)
CAPACITIES = (40, 55, 70, 85, 100)
CAPACITY_INITIAL_KWH = 30
TIMING_REPEATS = 3
_LENGTH_TOLERANCE_MILES = 1e-6

@dataclass(frozen=True)
class Instance:
    """One graph and trip config, plus the counts the CSV records.

    ``meta`` is empty except for Experiments A and D. For A it names the
    construction category and the properties the generator guarantees. For D it
    holds the replicate, edge count, SOC capacity steps, and the certificate
    minimum stops. It never reaches a solver and is not a CSV column.
    """

    instance_id: str
    graph: dict
    config: dict
    seed: int | None
    meta: dict = field(default_factory=dict)

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


def correctness_instances(seed: int, count: int) -> list[Instance]:
    """Build the Experiment A suite: 4–8 nodes, 1–5 chargers, exact-legal capacity.

    ``experiments.exact_suite`` owns the design. Each instance has its own
    random stream, so the first ``n`` instances do not depend on ``count`` and
    the S6 tiny suite is untouched.
    """
    from experiments.exact_suite import build_suite

    return build_suite(seed, count)


def scaling_instances(
    counts: tuple[int, ...] = CHARGER_COUNTS,
    *,
    seed: int = DEFAULT_SEED,
    replicates: int = SCALING_REPLICATES,
) -> list[Instance]:
    """Build the Experiment D instances: matched subsets of seeded station pools.

    Each replicate has one pool, and a count is a prefix of it, so a station
    keeps its coordinates and rate at every count. The trip and the vehicle are
    the same for every instance. Counts above ``CHARGER_SCALE_MAX``, the pool
    size, are rejected. ``experiments.scaling_suite`` owns the design.
    """
    from experiments.scaling_suite import build_scaling_suite

    for count in counts:
        if count < 1 or count > CHARGER_SCALE_MAX:
            raise InputError(f"charger count {count} is outside 1..{CHARGER_SCALE_MAX}")
    if len(set(counts)) != len(counts):
        raise InputError("charger counts must be distinct")
    return build_scaling_suite(seed, tuple(counts), replicates)


def soc_instances(steps: tuple[float, ...], *, family: str, base: Instance) -> list[Instance]:
    """Copy one trip and change only ``soc_step_kwh``."""
    if not steps:
        raise InputError("SOC sweep needs at least one step")
    instances = []
    for step in steps:
        if step <= 0:
            raise InputError("soc_step_kwh must be positive")
        config = dict(base.config)
        config["soc_step_kwh"] = step
        instances.append(
            Instance(f"soc-{family}-{_step_label(step)}", base.graph, config, base.seed)
        )
    return instances


def roundup_probe() -> Instance:
    """A 10 kWh direct trip used to watch coarse energy rounding.

    The road is the straight-line distance, about 9.2 miles. Finer steps can
    still represent that energy inside 10 kWh. A 6 kWh step cannot.
    """
    origin = _node("origin", "origin", None, 42.0, -83.0)
    destination = _node("destination", "destination", None, 42.0, -83.18)
    miles = haversine_miles(origin["lat"], origin["lon"], destination["lat"], destination["lon"])
    edges = [
        {
            "from": "origin",
            "to": "destination",
            "distance_miles": miles,
            "drive_time_minutes": miles * 1.2,
        }
    ]
    _require_straight_line({"origin": origin, "destination": destination}, edges)
    config = {
        "origin": "origin",
        "destination": "destination",
        "battery_capacity_kwh": 10,
        "initial_soc_kwh": 10,
        "reserve_kwh": 0,
        "consumption_kwh_per_mile": 1,
        "soc_step_kwh": 0.1,
    }
    return Instance("soc-roundup", {"nodes": [origin, destination], "edges": edges}, config, None)


def capacity_instances(
    sample: Instance,
    capacities: tuple[int, ...] = CAPACITIES,
) -> list[Instance]:
    """Keep the sample roads and change only battery capacity.

    Initial energy is fixed at 30 kWh so every capacity in the sweep is legal.
    That is lower than the sample's published 54 kWh start, so these times are
    not the 322.5 minute demonstration plan.
    """
    if sample.config["reserve_kwh"] > CAPACITY_INITIAL_KWH:
        raise InputError("capacity sweep initial SOC is below the reserve")
    instances = []
    for capacity in capacities:
        if capacity < CAPACITY_INITIAL_KWH:
            raise InputError("capacity sweep cannot start above the battery")
        config = dict(sample.config)
        config["battery_capacity_kwh"] = capacity
        config["initial_soc_kwh"] = CAPACITY_INITIAL_KWH
        config["soc_step_kwh"] = 1
        instances.append(Instance(f"capacity-{capacity:03d}", sample.graph, config, None))
    return instances


def _step_label(step: float) -> str:
    return format(step, ".10g")


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
