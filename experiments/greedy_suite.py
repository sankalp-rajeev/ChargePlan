"""Instances for Experiments B, F, G, and H (optimizer against the FRC baseline).

One trip (Detroit to Chicago, about 237 straight-line miles) and one family of
seeded station layouts serve all four experiments, so their results can be read
against each other. Each experiment changes the factors named in PRD Section 22
and holds the rest fixed.

* A replicate is one seeded station pool. Station positions come from the
  Experiment D pool generator (``scaling_suite.build_pool``), so a layout of
  ``n`` stations is a prefix of the pool and is spread along the corridor with
  a minority of detour candidates. Rates, roads, and the vehicle are drawn here,
  from streams keyed by ``chargeplan-experiment-h9``.
* A rate profile fixes the charging rate of every station. The uniform profiles
  give every station one rate. ``mixed_50_150`` gives half of each block 50 kW
  and half 150 kW, shuffled, so its mean (100 kW) equals ``uniform_100``.
* Roads are range-based, as in Experiment D: a directed road runs from the
  origin or a station to any station or the destination when its discretized
  energy fits a full battery above the reserve. A pair's road (length factor
  1.05-1.35, speed 45-70 mph) is drawn once per replicate and reused by every
  variant that contains both ends, so variants differ only in the factor under
  test.

These roads are synthetic, not OpenStreetMap, and they say nothing about a real
network.
"""

from __future__ import annotations

import math
import random

from experiments.instances import Instance
from experiments.scaling_suite import (
    DESTINATION_LATLON,
    ORIGIN_LATLON,
    POOL_BLOCKS,
    POOL_SIZE,
    ROAD_FACTOR,
    ROAD_SPEED_MPH,
    Station,
    build_pool,
)
from optimizer.model import InputError, discretize, edge_energy_steps, haversine_miles

STREAM = "chargeplan-experiment-h9"
FAST_KW = 150.0
SLOW_KW = 50.0

PROFILES = ("uniform_50", "uniform_100", "uniform_150", "mixed_50_150")
_UNIFORM = {"uniform_50": 50.0, "uniform_100": 100.0, "uniform_150": 150.0}

RESERVE_KWH = 4.0
CONSUMPTION_KWH_PER_MILE = 0.30
SOC_STEP_KWH = 1.0

# Experiment B: charger layout x charger speed x initial SOC x capacity.
B_LAYOUTS = (("sparse", 8), ("medium", 16), ("dense", 32))
B_SPEEDS = ("uniform_50", "mixed_50_150", "uniform_150")
B_SOC_PCT = (40, 80)
B_CAPACITIES = (40, 60, 80)
GREEDY_REPLICATES = 5

# Experiment F: the same route at five initial SOCs.
F_SOC_PCT = (20, 40, 60, 80, 100)
F_CAPACITY_KWH = 60
F_STATIONS = 25
F_PROFILE = "mixed_50_150"
F_REPLICATES = 5

# Experiment G: charger density. A 40 kWh battery and 80% start cannot drive the
# trip, and three stops or fewer is feasible only for well placed stations.
G_COUNTS = (2, 3, 4, 5, 6, 8, 10, 15, 25, 50)
G_TIERS = (("sparse", 4), ("medium", 10), ("dense", 50))
G_CAPACITY_KWH = 40
G_SOC_PCT = 80
G_PROFILE = "uniform_100"
DENSITY_REPLICATES = 20

# Experiment H: identical speed against a mixed network of the same mean rate.
H_PROFILES = ("uniform_50", "uniform_100", "mixed_50_150", "uniform_150")
H_STATIONS = 25
H_CAPACITY_KWH = 60
H_SOC_PCT = 60
H_REPLICATES = 10

MILES_STRAIGHT = haversine_miles(*ORIGIN_LATLON, *DESTINATION_LATLON)


def straight_miles() -> float:
    """Straight-line miles between the origin and the destination."""
    return MILES_STRAIGHT


def tier_of_count(count: int) -> str:
    """The Experiment G density tier of a station count."""
    for name, upper in G_TIERS:
        if count <= upper:
            return name
    raise InputError(f"station count {count} is above the largest tier")


def build_b_suite(seed: int, replicates: int = GREEDY_REPLICATES) -> list[Instance]:
    """Experiment B: layout x speed x initial SOC x capacity, matched by replicate."""
    require_replicates(replicates)
    instances = []
    for replicate in range(replicates):
        corridor = Corridor(seed, replicate, max(count for _, count in B_LAYOUTS))
        for layout, count in B_LAYOUTS:
            for profile in B_SPEEDS:
                for capacity in B_CAPACITIES:
                    for soc_pct in B_SOC_PCT:
                        instance_id = (
                            f"B-{layout}-{profile}-cap{capacity:03d}-soc{soc_pct:03d}"
                            f"-r{replicate:02d}"
                        )
                        meta = {
                            "layout": layout,
                            "stations": count,
                            "speed": profile,
                            "soc_pct": soc_pct,
                            "capacity_kwh": capacity,
                        }
                        instances.append(
                            corridor.instance(instance_id, count, profile, capacity, soc_pct, meta)
                        )
    return instances


def build_f_suite(seed: int, replicates: int = F_REPLICATES) -> list[Instance]:
    """Experiment F: the same layout, roads, and battery at 20-100% initial SOC."""
    require_replicates(replicates)
    instances = []
    for replicate in range(replicates):
        corridor = Corridor(seed, replicate, F_STATIONS)
        for soc_pct in F_SOC_PCT:
            meta = {"soc_pct": soc_pct, "stations": F_STATIONS, "speed": F_PROFILE}
            instances.append(
                corridor.instance(
                    f"F-soc{soc_pct:03d}-r{replicate:02d}",
                    F_STATIONS,
                    F_PROFILE,
                    F_CAPACITY_KWH,
                    soc_pct,
                    meta,
                )
            )
    return instances


def build_g_suite(
    seed: int,
    replicates: int = DENSITY_REPLICATES,
    counts: tuple[int, ...] = G_COUNTS,
) -> list[Instance]:
    """Experiment G: station count over matched prefixes of seeded pools."""
    require_replicates(replicates)
    if not counts or len(set(counts)) != len(counts) or min(counts) < 1:
        raise InputError("density counts must be distinct and positive")
    instances = []
    for replicate in range(replicates):
        corridor = Corridor(seed, replicate, max(counts))
        for count in counts:
            meta = {
                "stations": count,
                "tier": tier_of_count(count),
                "stations_per_100_miles": round(count * 100.0 / MILES_STRAIGHT, 3),
                "speed": G_PROFILE,
            }
            instances.append(
                corridor.instance(
                    f"G-n{count:03d}-r{replicate:02d}",
                    count,
                    G_PROFILE,
                    G_CAPACITY_KWH,
                    G_SOC_PCT,
                    meta,
                )
            )
    return instances


def build_h_suite(seed: int, replicates: int = H_REPLICATES) -> list[Instance]:
    """Experiment H: one layout and one set of roads under four rate profiles."""
    require_replicates(replicates)
    instances = []
    for replicate in range(replicates):
        corridor = Corridor(seed, replicate, H_STATIONS)
        for profile in H_PROFILES:
            instances.append(
                corridor.instance(
                    f"H-{profile}-r{replicate:02d}",
                    H_STATIONS,
                    profile,
                    H_CAPACITY_KWH,
                    H_SOC_PCT,
                    {"speed": profile, "stations": H_STATIONS},
                )
            )
    return instances


class Corridor:
    """One replicate: a station pool, its rates, and its roads."""

    def __init__(self, seed: int, replicate: int, size: int) -> None:
        if not 1 <= size <= POOL_SIZE:
            raise InputError(f"station count {size} is outside 1..{POOL_SIZE}")
        self.seed = seed
        self.replicate = replicate
        self.pool = build_pool(seed, replicate, size)
        self._mixed = self.mixed_rates(size)
        self._roads: dict[tuple[str, str], tuple[float, float]] = {}

    def instance(
        self,
        instance_id: str,
        count: int,
        profile: str,
        capacity_kwh: float,
        soc_pct: float,
        meta: dict,
    ) -> Instance:
        """Build the first ``count`` stations, with ``profile`` rates, as one query."""
        if count > len(self.pool):
            raise InputError(f"corridor has {len(self.pool)} stations, asked for {count}")
        if profile not in PROFILES:
            raise InputError(f"unknown rate profile {profile!r}")
        config = {
            "origin": "origin",
            "destination": "destination",
            "battery_capacity_kwh": float(capacity_kwh),
            "initial_soc_kwh": float(capacity_kwh) * soc_pct / 100.0,
            "reserve_kwh": RESERVE_KWH,
            "consumption_kwh_per_mile": CONSUMPTION_KWH_PER_MILE,
            "soc_step_kwh": SOC_STEP_KWH,
        }
        stations = self.pool[:count]
        rates = [self.rate(profile, station) for station in stations]
        nodes = [node("origin", "origin", None, *ORIGIN_LATLON)]
        nodes.extend(
            node(station.node_id, "charger", rate, station.lat, station.lon)
            for station, rate in zip(stations, rates, strict=True)
        )
        nodes.append(node("destination", "destination", None, *DESTINATION_LATLON))
        edges = self.edges(nodes, config)
        capacity_steps, _initial, _reserve, _step = discretize(config)
        full_meta = dict(meta)
        full_meta.update(
            {
                "replicate": self.replicate,
                "count": count,
                "edges": len(edges),
                "capacity_steps": capacity_steps,
                "fast_share": round(sum(1 for rate in rates if rate >= FAST_KW) / count, 4),
            }
        )
        return Instance(
            instance_id,
            {"nodes": nodes, "edges": edges},
            config,
            self.seed,
            full_meta,
        )

    def rate(self, profile: str, station: Station) -> float:
        """The rate of ``station`` under ``profile``. Mixed rates do not depend on the count."""
        if profile in _UNIFORM:
            return _UNIFORM[profile]
        return self._mixed[station.index]

    def mixed_rates(self, size: int) -> list[float]:
        rates: list[float] = []
        for block, block_size in enumerate(POOL_BLOCKS):
            if len(rates) >= size:
                break
            rng = random.Random(f"{STREAM}:{self.seed}:{self.replicate}:rates:{block}")
            fast = block_size // 2 + (1 if block_size % 2 and rng.random() < 0.5 else 0)
            block_rates = [FAST_KW] * fast + [SLOW_KW] * (block_size - fast)
            rng.shuffle(block_rates)
            rates.extend(block_rates)
        return rates

    def edges(self, nodes: list[dict], config: dict) -> list[dict]:
        capacity, _initial, reserve, _step = discretize(config)
        usable = capacity - reserve
        edges = []
        for start in nodes:
            if start["id"] == "destination":
                continue
            for end in nodes:
                if end["id"] in {start["id"], "origin"}:
                    continue
                miles, minutes = self.road(start, end)
                if edge_energy_steps(miles, config) <= usable:
                    edges.append(
                        {
                            "from": start["id"],
                            "to": end["id"],
                            "distance_miles": miles,
                            "drive_time_minutes": minutes,
                        }
                    )
        return edges

    def road(self, first: dict, second: dict) -> tuple[float, float]:
        low, high = sorted((first["id"], second["id"]))
        found = self._roads.get((low, high))
        if found is not None:
            return found
        rng = random.Random(f"{STREAM}:{self.seed}:{self.replicate}:road:{low}:{high}")
        factor = rng.uniform(*ROAD_FACTOR)
        speed = rng.uniform(*ROAD_SPEED_MPH)
        straight = haversine_miles(first["lat"], first["lon"], second["lat"], second["lon"])
        miles = math.ceil(straight * factor * 1000.0) / 1000.0
        minutes = max(0.01, round(miles / speed * 60.0, 4))
        self._roads[(low, high)] = (miles, minutes)
        return miles, minutes


def node(node_id: str, kind: str, rate: float | None, lat: float, lon: float) -> dict:
    return {"id": node_id, "type": kind, "lat": lat, "lon": lon, "charge_rate_kw": rate}


def require_replicates(replicates: int) -> None:
    if replicates < 1:
        raise InputError("replicates must be positive")
