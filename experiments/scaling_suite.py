"""Experiment D instances: matched subsets of a seeded two-dimensional station pool.

Why this module exists. The first Experiment D put every station on one line,
gave each station the rate of its list slot's parity, and linked each station
only to the next three. Fewer stations then meant longer hops, the fast-charger
share was 100%, 100%, 100%, then 50% across the four counts, and the edge count
was exactly ``3 * M`` by construction. Each count had one instance, so no
spread could be reported.

Design:

* One replicate is one seeded station pool. The trip (origin, destination) and
  the vehicle (capacity, initial energy, reserve, consumption, SOC step) are
  the same for every replicate and every count, so a count changes only which
  stations are present.
* The pool is built in blocks of 10, 15, 25, 50, and 100 stations. A count is a
  prefix of the pool, so 10 is contained in 25, 25 in 50, 50 in 100, and 100 in
  200. A station keeps its id, coordinates, and rate at every count.
* Each block places one station per equal slice of the corridor, so a prefix is
  spread along the trip instead of clumping. Each slice gets a random
  along-track jitter and a random cross-track offset, with a minority of
  stations placed far off the line as detour candidates. The stations are not
  collinear.
* Rates come from a fixed mix (30% 50 kW, 40% 150 kW, 30% 250 kW) applied per
  block to a shuffled list. A rate depends on neither the pool index nor the
  coordinates, and every prefix has nearly the same composition.
* Roads link stations by range, not by neighbor order. A directed road exists
  from the origin or a station to a station or the destination when its
  discretized energy fits in a full battery above the reserve. Road length is
  the straight line times a factor drawn once per pair, and speed is drawn once
  per pair. A pair therefore has the same road at every count in which both
  ends appear.

What this cannot isolate. Adding stations also adds roads, because more pairs
are within range, and shortens the typical gap to the next station. Node count,
edge count, and the available choices all grow together. The measurements
describe the algorithm on that growing family. They are not the pure effect of
the node count with the graph density held constant.

This module does not use ``data_pipeline`` and is independent of the real-data
exporter.
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
POOL_BLOCKS = (10, 15, 25, 50, 100)
POOL_SIZE = sum(POOL_BLOCKS)
RATE_MIX = ((50.0, 3), (150.0, 4), (250.0, 3))
FAST_KW = 150.0  # the share of stations at or above this rate is recorded per instance
ROAD_FACTOR = (1.05, 1.35)
ROAD_SPEED_MPH = (45.0, 70.0)

# Fixed for every replicate and every count.
TRIP = {
    "origin": "origin",
    "destination": "destination",
    "battery_capacity_kwh": 40,
    "initial_soc_kwh": 30,
    "reserve_kwh": 2,
    "consumption_kwh_per_mile": 0.30,
    "soc_step_kwh": 1,
}

_STREAM = "chargeplan-experiment-d"
_MILES_PER_DEGREE_LAT = 69.0
_MILES_PER_DEGREE_LON_AT_EQUATOR = 69.17
_NEAR_SIGMA_MILES = 6.0
_DETOUR_SIGMA_MILES = 22.0
_DETOUR_SHARE = 0.2
_CROSS_LIMIT_MILES = 45.0


@dataclass(frozen=True)
class Station:
    """One pool station. ``index`` is its position in the pool, not a property."""

    index: int
    block: int
    lat: float
    lon: float
    rate_kw: float

    @property
    def node_id(self) -> str:
        return f"s{self.index:03d}"


def build_scaling_suite(
    seed: int,
    counts: tuple[int, ...],
    replicates: int,
    trip: dict | None = None,
) -> list[Instance]:
    """Return one instance per replicate and count, replicate-major.

    ``trip`` replaces the fixed vehicle, for tests that profile other batteries.
    """
    if replicates < 1:
        raise InputError("scaling replicates must be positive")
    if not counts:
        raise InputError("scaling needs at least one charger count")
    config = dict(TRIP if trip is None else trip)
    instances = []
    for replicate in range(replicates):
        pool = build_pool(seed, replicate, max(counts))
        roads = _Roads(seed, replicate)
        for count in counts:
            instances.append(_instance(seed, replicate, count, pool[:count], roads, config))
    return instances


def build_pool(seed: int, replicate: int, size: int) -> list[Station]:
    """Return the first ``size`` stations of the pool for one replicate.

    Whole blocks are generated from one stream in order, so a larger ``size``
    never changes a station that a smaller ``size`` already contains.
    """
    if not 1 <= size <= POOL_SIZE:
        raise InputError(f"pool size {size} is outside 1..{POOL_SIZE}")
    rng = random.Random(f"{_STREAM}:{seed}:{replicate}:pool")
    origin_x, origin_y = 0.0, 0.0
    dest_x, dest_y = _project(*DESTINATION_LATLON)
    length = math.hypot(dest_x - origin_x, dest_y - origin_y)
    along = ((dest_x - origin_x) / length, (dest_y - origin_y) / length)
    across = (-along[1], along[0])
    stations: list[Station] = []
    for block, block_size in enumerate(POOL_BLOCKS):
        if len(stations) >= size:
            break
        rates = _rates(rng, block_size)
        slots = list(range(block_size))
        rng.shuffle(slots)
        for position, slot in enumerate(slots):
            fraction = (slot + rng.random()) / block_size
            sigma = _DETOUR_SIGMA_MILES if rng.random() < _DETOUR_SHARE else _NEAR_SIGMA_MILES
            offset = max(-_CROSS_LIMIT_MILES, min(_CROSS_LIMIT_MILES, rng.gauss(0.0, sigma)))
            x = fraction * (dest_x - origin_x) + offset * across[0]
            y = fraction * (dest_y - origin_y) + offset * across[1]
            lat, lon = _unproject(x, y)
            stations.append(Station(len(stations), block, lat, lon, rates[position]))
    return stations[:size]


def stations_of(instance: Instance) -> list[dict]:
    """Return the station nodes of an instance in pool order."""
    return [node for node in instance.graph["nodes"] if node["type"] == "charger"]


def max_usable_steps(config: dict) -> int:
    """Steps a full battery can spend above the reserve."""
    capacity, _initial, reserve, _step = discretize(config)
    return capacity - reserve


class _Roads:
    """Roads between pool nodes. A pair's road is drawn once and reused at every count."""

    def __init__(self, seed: int, replicate: int) -> None:
        self._prefix = f"{_STREAM}:{seed}:{replicate}:road"
        self._cache: dict[tuple[str, str], tuple[float, float]] = {}

    def get(
        self,
        first: tuple[str, float, float],
        second: tuple[str, float, float],
    ) -> tuple[float, float]:
        key = (first[0], second[0]) if first[0] < second[0] else (second[0], first[0])
        found = self._cache.get(key)
        if found is not None:
            return found
        rng = random.Random(f"{self._prefix}:{key[0]}:{key[1]}")
        factor = rng.uniform(*ROAD_FACTOR)
        speed = rng.uniform(*ROAD_SPEED_MPH)
        straight = haversine_miles(first[1], first[2], second[1], second[2])
        miles = math.ceil(straight * factor * 1000.0) / 1000.0
        minutes = max(0.01, round(miles / speed * 60.0, 4))
        self._cache[key] = (miles, minutes)
        return miles, minutes


def _instance(
    seed: int,
    replicate: int,
    count: int,
    stations: list[Station],
    roads: _Roads,
    config: dict,
) -> Instance:
    if len(stations) != count:
        raise InputError(f"pool cannot supply {count} stations")
    usable = max_usable_steps(config)
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
            miles, minutes = roads.get(start, end)
            if edge_energy_steps(miles, config) <= usable:
                edges.append(
                    {
                        "from": start[0],
                        "to": end[0],
                        "distance_miles": miles,
                        "drive_time_minutes": minutes,
                    }
                )
    graph = {"nodes": nodes, "edges": edges}
    capacity_steps, _initial, _reserve, _step = discretize(config)
    meta = {
        "replicate": replicate,
        "count": count,
        "edges": len(edges),
        "capacity_steps": capacity_steps,
        "fast_share": round(sum(1 for s in stations if s.rate_kw >= FAST_KW) / count, 4),
        "min_stops": min_charging_stops(graph, config),
    }
    return Instance(f"scale-{count:03d}-r{replicate:02d}", graph, dict(config), seed, meta)


def _rates(rng: random.Random, size: int) -> list[float]:
    """A shuffled list of ``size`` rates with the fixed mix, to within one station."""
    total = sum(weight for _, weight in RATE_MIX)
    counts = [size * weight // total for _, weight in RATE_MIX]
    # Largest remainder, ties broken by the stream, so each rate stays within one
    # station of its share. A rate that divides evenly never gets an extra.
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
    return {
        "id": node_id,
        "type": kind,
        "lat": lat,
        "lon": lon,
        "charge_rate_kw": rate,
    }


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
