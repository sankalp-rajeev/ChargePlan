"""Experiment J instances: seeded synthetic road networks for A* versus label-setting.

Why this module exists. The first Experiment J did not run its own benchmark.
It copied the measured rows of Experiments D and C, runtime included, and
relabeled them ``J``. The graphs behind the charger rows were then one
collinear corridor, which is the case where a straight-line heuristic is most
accurate. This module builds instances for J alone.

Design. Four factors are crossed. Everything else is fixed.

* ``size``: 10, 25, or 50 candidate chargers (charger density on one trip).
* ``geometry``: ``straight`` (near-straight corridor, roads close to the
  straight line), ``curved`` (the stations follow an arc and the roads wind a
  little), or ``detour`` (a wide scatter of stations, many far off the line,
  and winding roads). Each geometry bundles corridor shape, scatter, and the
  road-to-straight-line ratio, because those move together on a real network.
* ``speed``: ``homogeneous`` (every road 55 mph) or ``heterogeneous`` (each
  road 35 to 75 mph, mean 55).
* ``soc_step``: 1 kWh or 4 kWh on a 40 kWh battery (41 versus 11 SOC levels).

A replicate is one pool of 50 stations per geometry. Size is a prefix of the
pool, so a station keeps its position and rate at every size. The road between
two nodes (its length and its speed draw) is the same in every condition that
contains both nodes. The speed condition changes only drive times, and the SOC
step changes only the battery discretization and therefore which roads are in
range. A factor change is therefore matched on the rest.

Fixed: trip (Detroit to Chicago, about 237 straight-line miles), battery
40 kWh, initial energy 30 kWh, reserve 2 kWh, 0.30 kWh per mile, and a rate
mix of 30% 50 kW, 40% 150 kW, and 30% 250 kW.

Roads link every pair within battery range (the energy is at most a full
battery above the reserve). Road length is the straight line times a factor
and is never shorter than it. All distances and drive times are synthetic.
They are not OSM or routing-service measurements.

The heuristic assumptions in ``optimizer/battery_state_optimizer.py`` hold by
construction: every road is at least as long as the straight line, and every
drive time is positive. ``h`` uses the fastest edge speed, so no edge beats it.

This module does not import ``data_pipeline`` and does not use Experiment D's
generator. It shares only the contracts in ``optimizer.model``.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from experiments.certificate import min_charging_stops
from experiments.instances import Instance
from optimizer.model import InputError, discretize, edge_energy_steps, haversine_miles

ORIGIN_LATLON = (42.3314, -83.0458)
DESTINATION_LATLON = (41.8781, -87.6298)
POOL_BLOCKS = (10, 15, 25)
POOL_SIZE = sum(POOL_BLOCKS)
SIZES = (10, 25, 50)
SPEEDS = ("homogeneous", "heterogeneous")
SOC_STEPS = (1.0, 4.0)
RATE_MIX = ((50.0, 3), (150.0, 4), (250.0, 3))
HOMOGENEOUS_MPH = 55.0
HETEROGENEOUS_MPH = (35.0, 75.0)
SUITE_REPLICATES = 6

VEHICLE = {
    "origin": "origin",
    "destination": "destination",
    "battery_capacity_kwh": 40,
    "initial_soc_kwh": 30,
    "reserve_kwh": 2,
    "consumption_kwh_per_mile": 0.30,
}

_STREAM = "chargeplan-experiment-j"
_MILES_PER_DEGREE_LAT = 69.0
_MILES_PER_DEGREE_LON_AT_EQUATOR = 69.17


@dataclass(frozen=True)
class Geometry:
    """Corridor shape and road winding for one geometry level."""

    name: str
    bulge_miles: float  # peak sideways displacement of the corridor's arc
    scatter_miles: float  # normal sigma of a station's sideways offset
    detour_share: float  # share of stations drawn with the wide sigma
    detour_miles: float  # sigma of those stations
    cross_limit_miles: float  # largest allowed sideways offset
    road_factor: tuple[float, float]  # road length over straight line


GEOMETRIES = {
    "straight": Geometry("straight", 0.0, 3.0, 0.0, 0.0, 12.0, (1.02, 1.10)),
    "curved": Geometry("curved", 40.0, 6.0, 0.10, 15.0, 70.0, (1.08, 1.25)),
    "detour": Geometry("detour", 25.0, 15.0, 0.35, 45.0, 110.0, (1.20, 1.55)),
}


@dataclass(frozen=True)
class Station:
    """One pool station. ``index`` is its position in the pool, not a property."""

    index: int
    lat: float
    lon: float
    rate_kw: float

    @property
    def node_id(self) -> str:
        return f"s{self.index:03d}"


def build_astar_suite(
    seed: int,
    replicates: int = SUITE_REPLICATES,
    *,
    sizes: tuple[int, ...] = SIZES,
    geometries: tuple[str, ...] = tuple(GEOMETRIES),
    speeds: tuple[str, ...] = SPEEDS,
    soc_steps: tuple[float, ...] = SOC_STEPS,
) -> list[Instance]:
    """Return one instance per replicate and factor combination.

    Order is geometry, replicate, size, speed, SOC step. The arguments narrow
    the grid for profiling and tests. The default arguments are the benchmark.
    """
    if replicates < 1:
        raise InputError("A* replicates must be positive")
    for size in sizes:
        if not 1 <= size <= POOL_SIZE:
            raise InputError(f"A* size {size} is outside 1..{POOL_SIZE}")
    for name in geometries:
        if name not in GEOMETRIES:
            raise InputError(f"unknown geometry {name!r}")
    for name in speeds:
        if name not in SPEEDS:
            raise InputError(f"unknown speed condition {name!r}")
    for step in soc_steps:
        if step <= 0:
            raise InputError("SOC step must be positive")
    if not (sizes and geometries and speeds and soc_steps):
        raise InputError("every A* factor needs at least one level")
    instances = []
    for name in geometries:
        shape = GEOMETRIES[name]
        for replicate in range(replicates):
            pool = build_pool(seed, name, replicate, max(sizes))
            roads = _Roads(seed, name, replicate)
            for size in sizes:
                for speed in speeds:
                    for step in soc_steps:
                        instances.append(
                            _instance(seed, shape, replicate, size, speed, step, pool[:size], roads)
                        )
    return instances


def build_pool(seed: int, geometry: str, replicate: int, size: int) -> list[Station]:
    """Return the first ``size`` stations of one geometry's pool for one replicate.

    Whole blocks come from one stream in order, so a larger ``size`` never
    changes a station that a smaller one already contains.
    """
    if geometry not in GEOMETRIES:
        raise InputError(f"unknown geometry {geometry!r}")
    if not 1 <= size <= POOL_SIZE:
        raise InputError(f"pool size {size} is outside 1..{POOL_SIZE}")
    shape = GEOMETRIES[geometry]
    rng = random.Random(f"{_STREAM}:{seed}:{geometry}:{replicate}:pool")
    side = 1.0 if rng.random() < 0.5 else -1.0  # which way the arc bulges
    dest_x, dest_y = _project(*DESTINATION_LATLON)
    length = math.hypot(dest_x, dest_y)
    across = (-dest_y / length, dest_x / length)
    stations: list[Station] = []
    for block_size in POOL_BLOCKS:
        if len(stations) >= size:
            break
        rates = _rates(rng, block_size)
        slots = list(range(block_size))
        rng.shuffle(slots)
        for position, slot in enumerate(slots):
            fraction = (slot + rng.random()) / block_size
            sigma = shape.detour_miles if rng.random() < shape.detour_share else shape.scatter_miles
            offset = side * shape.bulge_miles * math.sin(math.pi * fraction) + rng.gauss(0.0, sigma)
            offset = max(-shape.cross_limit_miles, min(shape.cross_limit_miles, offset))
            x = fraction * dest_x + offset * across[0]
            y = fraction * dest_y + offset * across[1]
            lat, lon = _unproject(x, y)
            stations.append(Station(len(stations), lat, lon, rates[position]))
    return stations[:size]


def usable_steps(config: dict) -> int:
    """Steps a full battery can spend above the reserve."""
    capacity, _initial, reserve, _step = discretize(config)
    return capacity - reserve


def heuristic_origin_minutes(graph: dict) -> float:
    """The A* heuristic at the origin in minutes: straight line over the fastest road."""
    nodes = {node["id"]: node for node in graph["nodes"]}
    fastest = max(
        (edge["distance_miles"] / edge["drive_time_minutes"] for edge in graph["edges"]),
        default=0.0,
    )
    if fastest <= 0:
        return 0.0
    origin, destination = nodes["origin"], nodes["destination"]
    straight = haversine_miles(origin["lat"], origin["lon"], destination["lat"], destination["lon"])
    return straight / fastest  # miles over miles-per-minute is minutes


class _Roads:
    """Road length and speed draw per node pair, drawn once and reused by every condition."""

    def __init__(self, seed: int, geometry: str, replicate: int) -> None:
        self._prefix = f"{_STREAM}:{seed}:{geometry}:{replicate}:road"
        self._factor = GEOMETRIES[geometry].road_factor
        self._cache: dict[tuple[str, str], tuple[float, float]] = {}

    def get(
        self,
        first: tuple[str, float, float],
        second: tuple[str, float, float],
    ) -> tuple[float, float]:
        """Return ``(miles, heterogeneous_mph)`` for an unordered pair."""
        key = (first[0], second[0]) if first[0] < second[0] else (second[0], first[0])
        found = self._cache.get(key)
        if found is not None:
            return found
        rng = random.Random(f"{self._prefix}:{key[0]}:{key[1]}")
        factor = rng.uniform(*self._factor)
        speed = rng.uniform(*HETEROGENEOUS_MPH)
        straight = haversine_miles(first[1], first[2], second[1], second[2])
        found = (math.ceil(straight * factor * 1000.0) / 1000.0, speed)
        self._cache[key] = found
        return found


def _instance(
    seed: int,
    shape: Geometry,
    replicate: int,
    size: int,
    speed: str,
    step: float,
    stations: list[Station],
    roads: _Roads,
) -> Instance:
    if len(stations) != size:
        raise InputError(f"pool cannot supply {size} stations")
    config = {**VEHICLE, "soc_step_kwh": step}
    usable = usable_steps(config)
    nodes = [_node("origin", "origin", None, *ORIGIN_LATLON)]
    nodes.extend(_node(s.node_id, "charger", s.rate_kw, s.lat, s.lon) for s in stations)
    nodes.append(_node("destination", "destination", None, *DESTINATION_LATLON))
    located = [(node["id"], node["lat"], node["lon"]) for node in nodes]
    edges = []
    for start in located:
        if start[0] == "destination":
            continue
        for end in located:
            if end[0] in {start[0], "origin"}:
                continue
            miles, mph = roads.get(start, end)
            if edge_energy_steps(miles, config) > usable:
                continue
            mph = HOMOGENEOUS_MPH if speed == "homogeneous" else mph
            edges.append(
                {
                    "from": start[0],
                    "to": end[0],
                    "distance_miles": miles,
                    "drive_time_minutes": max(0.01, round(miles / mph * 60.0, 4)),
                }
            )
    graph = {"nodes": nodes, "edges": edges}
    origin, destination = nodes[0], nodes[-1]
    capacity_steps, _initial, _reserve, _step = discretize(config)
    meta = {
        "geometry": shape.name,
        "size": size,
        "speed": speed,
        "soc_step": step,
        "replicate": replicate,
        "edges": len(edges),
        "capacity_steps": capacity_steps,
        "straight_miles": round(
            haversine_miles(origin["lat"], origin["lon"], destination["lat"], destination["lon"]),
            3,
        ),
        "h_origin_minutes": round(heuristic_origin_minutes(graph), 4),
        "min_stops": min_charging_stops(graph, config),
    }
    instance_id = (
        f"astar-{shape.name}-{size:03d}-{speed[:3]}-s{format(step, 'g')}-r{replicate:02d}"
    )
    return Instance(instance_id, graph, config, seed, meta)


def _rates(rng: random.Random, size: int) -> list[float]:
    """A shuffled list of ``size`` rates with the fixed mix, to within one station."""
    total = sum(weight for _, weight in RATE_MIX)
    counts = [size * weight // total for _, weight in RATE_MIX]
    leftovers = sorted(
        range(len(RATE_MIX)),
        key=lambda level: (-(size * RATE_MIX[level][1] % total), rng.random()),
    )
    for level in leftovers[: size - sum(counts)]:
        counts[level] += 1
    rates = [rate for (rate, _), n in zip(RATE_MIX, counts, strict=True) for _ in range(n)]
    rng.shuffle(rates)
    return rates


def _node(node_id: str, kind: str, rate: float | None, lat: float, lon: float) -> dict:
    return {"id": node_id, "type": kind, "lat": lat, "lon": lon, "charge_rate_kw": rate}


def _project(lat: float, lon: float) -> tuple[float, float]:
    """Miles east and north of the origin."""
    mid = math.radians((lat + ORIGIN_LATLON[0]) / 2.0)
    x = (lon - ORIGIN_LATLON[1]) * _MILES_PER_DEGREE_LON_AT_EQUATOR * math.cos(mid)
    y = (lat - ORIGIN_LATLON[0]) * _MILES_PER_DEGREE_LAT
    return x, y


def _unproject(x: float, y: float) -> tuple[float, float]:
    lat = ORIGIN_LATLON[0] + y / _MILES_PER_DEGREE_LAT
    mid = math.radians((lat + ORIGIN_LATLON[0]) / 2.0)
    lon = ORIGIN_LATLON[1] + x / (_MILES_PER_DEGREE_LON_AT_EQUATOR * math.cos(mid))
    return round(lat, 6), round(lon, 6)
