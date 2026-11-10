"""Experiment A instances: a seeded mixture that the exact solver can still check.

Why this module exists. The first generator drew random roads of 1–4 miles at
1 kWh per mile against 3–6 kWh batteries, so 93 of 100 graphs were infeasible
and the other 7 needed no charging. Agreement on those graphs only showed that
three solvers can say "infeasible" together. This generator builds each graph
from a named category instead, so the suite contains the cases the PRD's test
plan cares about: no charging, one stop, several stops, partial charging,
charger speed trade-offs, objective ties, reserve boundaries, and several
different ways to be infeasible.

Design rules:

* Every instance has its own random stream, ``Random("…:seed:index")``. The first
  ``n`` instances are the same for any ``count``, and the S6 tiny suite does not
  share a stream with this one.
* The category is fixed by the index (a 20-slot schedule), not drawn from the
  stream. Any multiple of 20 therefore has exact category counts. The seed
  changes batteries, step sizes, consumption, charger rates, road lengths,
  speeds, coordinates and decoys, not the category mix.
* A feasible instance is built around a backbone path whose leg energies are
  chosen in discretized steps, so feasibility holds by construction. Random
  decoy chargers and roads are then added one edge at a time, and an edge is
  kept only if the instance still has its category. The test is the
  energy-only certificate in ``experiments.certificate``. The main optimizer and
  the exact solver are never consulted, and no optimal plan is written down.
* Every instance stays inside the exact solver's bounds: at most 8 nodes, 5
  chargers, and 8 capacity steps.

Roads obey the contract (``distance_miles`` is at least the straight line) and
distances are rounded down to 0.001 mile, so ``c * d / ΔE`` is exact in the
decimal arithmetic of ``optimizer.model``.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal

from experiments.certificate import min_charging_stops
from experiments.instances import (
    EXACT_MAX_CAPACITY_STEPS,
    EXACT_MAX_CHARGERS,
    EXACT_MAX_NODES,
    Instance,
    exact_block_reason,
)
from optimizer.model import InputError, discretize, edge_energy_steps, haversine_miles

MIN_NODES = 4
STEPS = (0.5, 1.0, 2.0, 2.5, 5.0, 10.0)
CONSUMPTION = (0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.75, 1.0)
RATES = (20.0, 30.0, 50.0, 60.0, 100.0, 150.0)
SLOW_RATES = (20.0, 30.0, 50.0)
FAST_RATES = (100.0, 150.0)
INFEASIBLE_VARIANTS = (
    "range_gap",
    "no_path",
    "stranded_origin",
    "reserve_near_miss",
    "below_reserve",
)

_ATTEMPTS = 400
_RESERVES = (0, 1, 1, 2)
_FRACTIONS = (0.0, 0.0, 0.25, 0.5, 0.75)
_STREAM = "chargeplan-experiment-a"
_BASE_LAT = 42.0
_BASE_LON = -83.0
_MILES_PER_DEGREE_LAT = 69.0
_MILES_PER_DEGREE_LON = 51.4
_DETOUR = 1.15
_LENGTH_TOLERANCE_MILES = 1e-6

# One 20-slot cycle. Counts per cycle: one_stop 3, multi_stop 4, speed_tradeoff 2,
# partial_chain 2, origin_charge 1, tie_stops 1 (feasible with charging: 13);
# no_charge 2, tie_distance 1 (feasible, no charging: 3); infeasible 4.
SCHEDULE = (
    "one_stop",
    "multi_stop",
    "infeasible",
    "no_charge",
    "speed_tradeoff",
    "multi_stop",
    "partial_chain",
    "infeasible",
    "one_stop",
    "tie_stops",
    "multi_stop",
    "origin_charge",
    "infeasible",
    "partial_chain",
    "no_charge",
    "one_stop",
    "tie_distance",
    "multi_stop",
    "speed_tradeoff",
    "infeasible",
)
CATEGORIES = tuple(dict.fromkeys(SCHEDULE))


class _Retry(Exception):
    """A draw did not meet its category. The builder draws again."""


@dataclass(frozen=True)
class _Battery:
    step: float
    consumption: float
    capacity: int
    reserve: int
    capacity_fraction: float
    reserve_fraction: float

    @property
    def usable(self) -> int:
        """Steps between the reserve and a full battery."""
        return self.capacity - self.reserve

    def kwh(self, steps: float) -> float:
        return round(steps * self.step, 6)

    def config(self, initial_steps: int, initial_fraction: float) -> dict:
        """kWh values whose Section 7.1 rounding is exactly the step counts.

        Capacity and initial energy keep a fraction of a step that rounds down.
        The reserve sits a fraction below its step count and rounds up.
        """
        fraction = initial_fraction
        if initial_steps >= self.capacity:
            fraction = min(fraction, self.capacity_fraction)
        reserve = 0.0 if self.reserve == 0 else self.kwh(self.reserve - self.reserve_fraction)
        return {
            "origin": "origin",
            "destination": "destination",
            "battery_capacity_kwh": self.kwh(self.capacity + self.capacity_fraction),
            "initial_soc_kwh": self.kwh(initial_steps + fraction),
            "reserve_kwh": reserve,
            "consumption_kwh_per_mile": self.consumption,
            "soc_step_kwh": self.step,
        }


@dataclass
class _Draft:
    rng: random.Random
    battery: _Battery
    initial: int
    config: dict
    nodes: dict[str, dict] = field(default_factory=dict)
    edges: dict[tuple[str, str], dict] = field(default_factory=dict)
    backbone: list[tuple[str, str]] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    scale: float = 1.0

    @property
    def available(self) -> int:
        """Steps the origin can spend before it must charge."""
        return self.initial - self.battery.reserve

    def node(self, node_id: str, kind: str, rate: float | None) -> None:
        self.nodes[node_id] = {"type": kind, "rate": rate, "xy": None, "lat": 0.0, "lon": 0.0}

    def leg(self, start: str, end: str, steps: int, minutes: float | None = None) -> None:
        """Add a backbone road whose rounded energy is exactly ``steps``."""
        miles = _leg_miles(self.rng, steps, self.battery)
        self.edge(start, end, miles, minutes)
        self.backbone.append((start, end))

    def edge(self, start: str, end: str, miles: float, minutes: float | None = None) -> None:
        if minutes is None:
            minutes = _minutes(self.rng, miles)
        self.edges[(start, end)] = {"miles": miles, "minutes": minutes}

    def chain(self, ids: list[str], legs: list[int]) -> None:
        if len(legs) != len(ids) - 1:
            raise InputError("a chain needs one leg between each pair of nodes")
        for position, steps in enumerate(legs):
            self.leg(ids[position], ids[position + 1], steps)

    def charger_count(self) -> int:
        return sum(1 for node in self.nodes.values() if node["rate"] is not None)

    def graph(self) -> dict:
        nodes = [
            {
                "id": node_id,
                "type": node["type"],
                "lat": node["lat"],
                "lon": node["lon"],
                "charge_rate_kw": node["rate"],
            }
            for node_id, node in self.nodes.items()
        ]
        edges = [
            {
                "from": start,
                "to": end,
                "distance_miles": edge["miles"],
                "drive_time_minutes": edge["minutes"],
            }
            for (start, end), edge in self.edges.items()
        ]
        return {"nodes": nodes, "edges": edges}


Accepts = Callable[[int | None], bool]


def build_suite(seed: int, count: int) -> list[Instance]:
    """Return ``count`` Experiment A instances for ``seed``."""
    if count < 1:
        raise InputError("correctness instance count must be positive")
    return [build_instance(seed, index) for index in range(count)]


def build_instance(seed: int, index: int) -> Instance:
    """Build instance ``index``. It does not depend on how many others exist."""
    category, occurrence = _slot(index)
    rng = random.Random(f"{_STREAM}:{seed}:{index}")
    for _ in range(_ATTEMPTS):
        try:
            draft, accepts = _BUILDERS[category](rng, occurrence)
            return _finish(draft, accepts, seed, index, category)
        except _Retry:
            continue
    raise InputError(f"exact-{index:03d}: no {category} draw met its category")


def schedule_category(index: int) -> str:
    """Return the category that ``index`` is built as."""
    return _slot(index)[0]


def _slot(index: int) -> tuple[str, int]:
    cycle, position = divmod(index, len(SCHEDULE))
    category = SCHEDULE[position]
    rank = SCHEDULE[:position].count(category)
    return category, cycle * SCHEDULE.count(category) + rank


# --- category builders ------------------------------------------------------------


def _no_charge(rng: random.Random, occurrence: int) -> tuple[_Draft, Accepts]:
    """Feasible without charging. Even occurrences end exactly at the reserve."""
    boundary = occurrence % 2 == 0
    reserve = rng.choice(_RESERVES)
    capacity = rng.randint(max(5, reserve + 3), EXACT_MAX_CAPACITY_STEPS)
    draft = _draft(rng, capacity, reserve, rng.randint(reserve + 3, capacity))
    total = draft.available if boundary else rng.randint(1, draft.available - 1)
    parts = rng.randint(1, min(3, total))
    ids = ["origin"] + [f"w{i}" for i in range(parts - 1)] + ["destination"]
    draft.node("origin", "origin", rng.choice((None, None, *RATES)))
    for waypoint in ids[1:-1]:
        draft.node(waypoint, "charger", rng.choice((None, *RATES)))
    draft.node("destination", "destination", None)
    draft.chain(ids, _split(rng, total, parts))
    draft.meta["variant"] = "reserve_boundary" if boundary else "slack"
    return draft, lambda stops: stops == 0


def _one_stop(rng: random.Random, occurrence: int) -> tuple[_Draft, Accepts]:
    """One charger sits between a first leg and a second leg that together need a stop."""
    reserve = rng.choice(_RESERVES)
    capacity = rng.randint(max(4, reserve + 3), EXACT_MAX_CAPACITY_STEPS)
    draft = _draft(rng, capacity, reserve, rng.randint(reserve + 1, capacity))
    usable = draft.battery.usable
    first = rng.randint(1, draft.available)
    second = rng.randint(max(1, draft.available - first + 1), usable)
    origin_charges = occurrence % 3 == 2
    draft.node("origin", "origin", rng.choice(RATES) if origin_charges else None)
    draft.node("c0", "charger", rng.choice(RATES))
    draft.node("destination", "destination", None)
    before = _bridge(rng, draft, "j0", first)
    after = _bridge(rng, draft, "j1", second)
    ids = ["origin", *before.nodes, "c0", *after.nodes, "destination"]
    draft.chain(ids, [*before.legs, *after.legs])
    draft.meta["variant"] = "origin_charger_available" if origin_charges else "plain"
    return draft, lambda stops: stops == 1


def _multi_stop(rng: random.Random, occurrence: int) -> tuple[_Draft, Accepts]:
    """A chain whose total energy exceeds what one fewer stop can cover."""
    stops = 3 if occurrence % 4 == 3 else 2
    reserve = rng.choice(_RESERVES)
    capacity = rng.randint(max(4, reserve + 3), 7)
    draft = _draft(rng, capacity, reserve, rng.randint(reserve + 1, capacity))
    usable = draft.battery.usable
    first = rng.randint(max(1, draft.available - 1), draft.available)
    later = [rng.randint(max(1, usable - 1), usable) for _ in range(stops)]
    ids = ["origin"] + [f"c{i}" for i in range(stops)] + ["destination"]
    draft.node("origin", "origin", None)
    for charger in ids[1:-1]:
        draft.node(charger, "charger", rng.choice(RATES))
    draft.node("destination", "destination", None)
    draft.chain(ids, [first, *later])
    draft.meta["variant"] = f"{stops}_stops"
    return draft, lambda found: found == stops


def _speed_tradeoff(rng: random.Random, occurrence: int) -> tuple[_Draft, Accepts]:
    """A slow charger on the shorter route and a fast one on a longer route."""
    del occurrence
    reserve = rng.choice(_RESERVES)
    capacity = rng.randint(max(5, reserve + 3), EXACT_MAX_CAPACITY_STEPS)
    draft = _draft(rng, capacity, reserve, rng.randint(reserve + 2, capacity))
    usable = draft.battery.usable
    slow_in = rng.randint(1, draft.available)
    slow_out_low = max(1, draft.available - slow_in + 1)
    if slow_out_low > usable - 1:
        raise _Retry
    slow_out = rng.randint(slow_out_low, usable - 1)
    fast_in = rng.randint(slow_in, draft.available)
    fast_out = rng.randint(slow_out, usable)
    if (fast_in, fast_out) == (slow_in, slow_out):
        raise _Retry
    draft.node("origin", "origin", None)
    draft.node("c0", "charger", rng.choice(SLOW_RATES))
    draft.node("c1", "charger", rng.choice(FAST_RATES))
    draft.node("destination", "destination", None)
    draft.leg("origin", "c0", slow_in)
    draft.leg("c0", "destination", slow_out)
    draft.leg("origin", "c1", fast_in)
    draft.leg("c1", "destination", fast_out)
    draft.meta["variant"] = "slow_short_vs_fast_long"
    return draft, lambda stops: stops == 1


def _partial_chain(rng: random.Random, occurrence: int) -> tuple[_Draft, Accepts]:
    """A slow charger, a fast charger a short hop later, and a long bypass past the fast one.

    Charging only enough at the slow charger to reach the fast one competes with
    charging deep at the slow one to take the bypass.
    """
    del occurrence
    reserve = rng.choice(_RESERVES)
    capacity = rng.randint(max(5, reserve + 4), EXACT_MAX_CAPACITY_STEPS)
    draft = _draft(rng, capacity, reserve, rng.randint(reserve + 1, capacity))
    usable = draft.battery.usable
    draft.node("origin", "origin", None)
    draft.node("c0", "charger", rng.choice(SLOW_RATES))
    draft.node("c1", "charger", rng.choice(FAST_RATES))
    draft.node("destination", "destination", None)
    draft.leg("origin", "c0", rng.randint(1, draft.available))
    draft.leg("c0", "c1", rng.randint(1, max(1, usable // 2)))
    draft.leg("c1", "destination", rng.randint(1, usable))
    draft.leg("c0", "destination", rng.randint(max(1, usable - 1), usable))
    draft.meta["variant"] = "slow_fast_with_bypass"
    return draft, lambda stops: stops is not None and stops >= 1


def _origin_charge(rng: random.Random, occurrence: int) -> tuple[_Draft, Accepts]:
    """The first road is out of range, so the plan must charge at a charging origin."""
    del occurrence
    reserve = rng.choice(_RESERVES)
    capacity = rng.randint(max(4, reserve + 3), EXACT_MAX_CAPACITY_STEPS)
    spare = rng.choice((0, 0, 1))
    draft = _draft(rng, capacity, reserve, reserve + spare)
    usable = draft.battery.usable
    first = rng.randint(spare + 1, usable)
    second = rng.randint(1, usable)
    draft.node("origin", "origin", rng.choice(RATES))
    draft.node("c0", "charger", rng.choice((None, *RATES)))
    draft.node("destination", "destination", None)
    draft.chain(["origin", "c0", "destination"], [first, second])
    draft.meta["variant"] = "initial_at_reserve" if spare == 0 else "initial_one_step_above"
    return draft, lambda stops: stops is not None and stops >= 1


def _tie_distance(rng: random.Random, occurrence: int) -> tuple[_Draft, Accepts]:
    """Two no-charge routes with equal drive time, equal stops, and different miles."""
    del occurrence
    reserve = rng.choice(_RESERVES)
    capacity = rng.randint(max(6, reserve + 4), EXACT_MAX_CAPACITY_STEPS)
    draft = _draft(rng, capacity, reserve, rng.randint(reserve + 4, capacity))
    total_minutes = Decimal(str(round(rng.uniform(20, 90), 2)))
    draft.node("origin", "origin", None)
    draft.node("c0", "charger", rng.choice(RATES))
    draft.node("c1", "charger", rng.choice(RATES))
    draft.node("destination", "destination", None)
    for junction in ("c0", "c1"):
        first = rng.randint(1, draft.available - 1)
        second = rng.randint(1, draft.available - first)
        head = _split_minutes(rng, total_minutes)
        draft.leg("origin", junction, first, float(head[0]))
        draft.leg(junction, "destination", second, float(head[1]))
    lengths = [
        draft.edges[("origin", "c0")]["miles"] + draft.edges[("c0", "destination")]["miles"],
        draft.edges[("origin", "c1")]["miles"] + draft.edges[("c1", "destination")]["miles"],
    ]
    if abs(lengths[0] - lengths[1]) < 0.05:
        raise _Retry
    draft.meta["variant"] = "equal_time_different_distance"
    draft.meta["decoys"] = False
    return draft, lambda stops: stops == 0


def _tie_stops(rng: random.Random, occurrence: int) -> tuple[_Draft, Accepts]:
    """A no-charge route and a faster-driving route that must charge, equal in total time.

    The charging route drives exactly as much faster as its minimum charge costs.
    The second tie-break, fewer stops, must then prefer the first route.
    """
    del occurrence
    reserve = rng.choice(_RESERVES)
    capacity = rng.randint(max(5, reserve + 3), EXACT_MAX_CAPACITY_STEPS)
    draft = _draft(rng, capacity, reserve, rng.randint(reserve + 2, capacity - 1))
    usable = draft.battery.usable
    free_first = rng.randint(1, draft.available - 1)
    free_second = rng.randint(1, draft.available - free_first)
    charge_first = rng.randint(1, draft.available)
    charge_second = rng.randint(max(1, draft.available - charge_first + 1), usable)
    needed = charge_first + charge_second - draft.available
    rate = rng.choice(RATES)
    per_step = Decimal(str(draft.battery.step)) * 60 / Decimal(str(rate))
    if per_step != per_step.quantize(Decimal("0.001")):
        raise _Retry
    charge_minutes = per_step * needed
    total_minutes = charge_minutes + Decimal(str(round(rng.uniform(10, 60), 2)))
    free = _split_minutes(rng, total_minutes)
    driven = _split_minutes(rng, total_minutes - charge_minutes)
    draft.node("origin", "origin", None)
    draft.node("c0", "charger", rng.choice((None, *RATES)))
    draft.node("c1", "charger", rate)
    draft.node("destination", "destination", None)
    draft.leg("origin", "c0", free_first, float(free[0]))
    draft.leg("c0", "destination", free_second, float(free[1]))
    draft.leg("origin", "c1", charge_first, float(driven[0]))
    draft.leg("c1", "destination", charge_second, float(driven[1]))
    draft.meta["variant"] = "equal_time_fewer_stops"
    draft.meta["decoys"] = False
    return draft, lambda stops: stops == 0


def _infeasible(rng: random.Random, occurrence: int) -> tuple[_Draft, Accepts]:
    variant = INFEASIBLE_VARIANTS[occurrence % len(INFEASIBLE_VARIANTS)]
    return _INFEASIBLE_BUILDERS[variant](rng, variant)


def _range_gap(rng: random.Random, variant: str) -> tuple[_Draft, Accepts]:
    """One road needs more energy than a full battery can spend above the reserve."""
    reserve = rng.choice(_RESERVES)
    capacity = rng.randint(max(4, reserve + 3), EXACT_MAX_CAPACITY_STEPS)
    draft = _draft(rng, capacity, reserve, rng.randint(reserve + 1, capacity))
    usable = draft.battery.usable
    chargers = rng.randint(1, 2)
    ids = ["origin"] + [f"c{i}" for i in range(chargers)] + ["destination"]
    gap = rng.randrange(len(ids) - 1)
    legs = []
    for position in range(len(ids) - 1):
        if position == gap:
            legs.append(usable + rng.randint(1, 2))
        else:
            legs.append(rng.randint(1, draft.available if position == 0 else usable))
    draft.node("origin", "origin", None)
    for charger in ids[1:-1]:
        draft.node(charger, "charger", rng.choice(RATES))
    draft.node("destination", "destination", None)
    draft.chain(ids, legs)
    draft.meta["variant"] = variant
    return draft, lambda stops: stops is None


def _no_path(rng: random.Random, variant: str) -> tuple[_Draft, Accepts]:
    """No road leads into the destination from the part of the graph the trip can reach."""
    reserve = rng.choice(_RESERVES)
    capacity = rng.randint(max(4, reserve + 3), EXACT_MAX_CAPACITY_STEPS)
    draft = _draft(rng, capacity, reserve, rng.randint(reserve + 1, capacity))
    usable = draft.battery.usable
    chargers = rng.randint(1, 3)
    ids = ["origin"] + [f"c{i}" for i in range(chargers)]
    draft.node("origin", "origin", None)
    for charger in ids[1:]:
        draft.node(charger, "charger", rng.choice(RATES))
    draft.node("destination", "destination", None)
    legs = [rng.randint(1, draft.available)] + [rng.randint(1, usable) for _ in ids[2:]]
    draft.chain(ids, legs)
    draft.leg("destination", "c0", rng.randint(1, usable))
    if rng.random() < 0.5 and draft.charger_count() < EXACT_MAX_CHARGERS:
        draft.node("island", "charger", rng.choice(RATES))
        draft.leg("island", "destination", rng.randint(1, usable))
    draft.meta["variant"] = variant
    return draft, lambda stops: stops is None


def _stranded_origin(rng: random.Random, variant: str) -> tuple[_Draft, Accepts]:
    """The origin cannot charge and every road out of it is longer than the starting energy."""
    reserve = rng.choice(_RESERVES)
    capacity = rng.randint(max(4, reserve + 3), EXACT_MAX_CAPACITY_STEPS)
    draft = _draft(rng, capacity, reserve, rng.randint(reserve, capacity - 1))
    usable = draft.battery.usable
    if draft.available + 1 > usable:
        raise _Retry
    draft.node("origin", "origin", None)
    draft.node("c0", "charger", rng.choice(RATES))
    draft.node("c1", "charger", rng.choice(RATES))
    draft.node("destination", "destination", None)
    for charger in ("c0", "c1"):
        draft.leg("origin", charger, rng.randint(draft.available + 1, usable))
        draft.leg(charger, "destination", rng.randint(1, usable))
    draft.meta["variant"] = variant
    return draft, lambda stops: stops is None


def _reserve_near_miss(rng: random.Random, variant: str) -> tuple[_Draft, Accepts]:
    """The only route needs one step more than the energy above the reserve."""
    reserve = rng.choice(_RESERVES)
    capacity = rng.randint(max(4, reserve + 3), EXACT_MAX_CAPACITY_STEPS)
    draft = _draft(rng, capacity, reserve, rng.randint(reserve + 1, capacity))
    total = draft.available + 1
    parts = rng.randint(1, min(3, total))
    ids = ["origin"] + [f"w{i}" for i in range(parts - 1)] + ["destination"]
    draft.node("origin", "origin", None)
    for waypoint in ids[1:-1]:
        draft.node(waypoint, "charger", None)
    draft.node("destination", "destination", None)
    draft.node("c0", "charger", rng.choice(RATES))
    draft.chain(ids, _split(rng, total, parts))
    usable = draft.battery.usable
    draft.leg("origin", "c0", rng.randint(draft.available + 1, max(draft.available + 1, usable)))
    draft.leg("c0", "destination", rng.randint(1, usable))
    draft.meta["variant"] = variant
    return draft, lambda stops: stops is None


def _below_reserve(rng: random.Random, variant: str) -> tuple[_Draft, Accepts]:
    """The trip starts below the discretized reserve, which both solvers reject."""
    reserve = rng.choice((1, 2))
    capacity = rng.randint(max(4, reserve + 3), EXACT_MAX_CAPACITY_STEPS)
    draft = _draft(rng, capacity, reserve, rng.randint(0, reserve - 1))
    usable = draft.battery.usable
    draft.node("origin", "origin", None)
    draft.node("c0", "charger", rng.choice(RATES))
    draft.node("destination", "destination", None)
    draft.chain(["origin", "c0", "destination"], [rng.randint(1, usable), rng.randint(1, usable)])
    draft.meta["variant"] = variant
    return draft, lambda stops: stops is None


_BUILDERS: dict[str, Callable[[random.Random, int], tuple[_Draft, Accepts]]] = {
    "no_charge": _no_charge,
    "one_stop": _one_stop,
    "multi_stop": _multi_stop,
    "speed_tradeoff": _speed_tradeoff,
    "partial_chain": _partial_chain,
    "origin_charge": _origin_charge,
    "tie_distance": _tie_distance,
    "tie_stops": _tie_stops,
    "infeasible": _infeasible,
}
_INFEASIBLE_BUILDERS = {
    "range_gap": _range_gap,
    "no_path": _no_path,
    "stranded_origin": _stranded_origin,
    "reserve_near_miss": _reserve_near_miss,
    "below_reserve": _below_reserve,
}


# --- assembly -------------------------------------------------------------------


def _finish(draft: _Draft, accepts: Accepts, seed: int, index: int, category: str) -> Instance:
    """Place the nodes, add decoys that keep the category, and validate the result."""
    if not accepts(_stops(draft)):
        raise _Retry
    _place(draft)
    if draft.meta.pop("decoys", True):
        _add_decoys(draft, accepts)
    _check_bounds(draft)
    stops = _stops(draft)
    if not accepts(stops):
        raise _Retry

    rng = draft.rng
    graph = draft.graph()
    rng.shuffle(graph["nodes"])
    rng.shuffle(graph["edges"])
    meta = {
        "category": category,
        "variant": draft.meta.get("variant", ""),
        "min_stops": stops,
        "capacity_steps": draft.battery.capacity,
        "reserve_steps": draft.battery.reserve,
        "initial_steps": draft.initial,
        "consumption_kwh_per_mile": draft.battery.consumption,
        "soc_step_kwh": draft.battery.step,
    }
    instance = Instance(f"exact-{index:03d}", graph, draft.config, seed, meta)
    reason = exact_block_reason(instance.graph, instance.config)
    if reason is not None:
        raise InputError(f"{instance.instance_id} violated the exact bound: {reason}")
    return instance


def _draft(rng: random.Random, capacity: int, reserve: int, initial: int) -> _Draft:
    if not 0 <= reserve <= capacity <= EXACT_MAX_CAPACITY_STEPS:
        raise _Retry
    if not 0 <= initial <= capacity:
        raise _Retry
    battery = _Battery(
        step=rng.choice(STEPS),
        consumption=rng.choice(CONSUMPTION),
        capacity=capacity,
        reserve=reserve,
        capacity_fraction=rng.choice(_FRACTIONS),
        reserve_fraction=rng.choice(_FRACTIONS) if reserve else 0.0,
    )
    config = battery.config(initial, rng.choice((0.0, 0.0, 0.3, 0.6, 0.9)))
    if discretize(config)[:3] != (capacity, initial, reserve):
        raise _Retry
    return _Draft(rng=rng, battery=battery, initial=initial, config=config)


def _stops(draft: _Draft) -> int | None:
    return min_charging_stops(draft.graph(), draft.config)


@dataclass(frozen=True)
class _Bridge:
    nodes: list[str]
    legs: list[int]


def _bridge(rng: random.Random, draft: _Draft, junction: str, steps: int) -> _Bridge:
    """Sometimes split a stretch of ``steps`` into two roads through a plain junction."""
    if steps >= 2 and rng.random() < 0.4:
        first = rng.randint(1, steps - 1)
        draft.node(junction, "charger", None)
        return _Bridge([junction], [first, steps - first])
    return _Bridge([], [steps])


def _split(rng: random.Random, total: int, parts: int) -> list[int]:
    """Split ``total`` into ``parts`` positive integers."""
    cuts = sorted(rng.sample(range(1, total), parts - 1)) if parts > 1 else []
    edges = [0, *cuts, total]
    return [high - low for low, high in zip(edges, edges[1:], strict=False)]


def _split_minutes(rng: random.Random, total: Decimal) -> tuple[Decimal, Decimal]:
    """Split ``total`` minutes into two hundredths-of-a-minute parts that sum exactly."""
    first = Decimal(str(round(rng.uniform(0.2, 0.8), 3))) * total
    first = first.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
    second = total - first
    if first < Decimal("0.05") or second < Decimal("0.05"):
        raise _Retry
    return first, second


def _leg_miles(rng: random.Random, steps: int, battery: _Battery) -> float:
    """Return a road length whose rounded energy is exactly ``steps`` steps.

    The road uses a random fraction of the last step. A zero fraction puts the
    energy exactly on a step boundary, which exercises the ceiling.
    """
    probe = {
        "consumption_kwh_per_mile": battery.consumption,
        "soc_step_kwh": battery.step,
    }
    consumption = Decimal(str(battery.consumption))
    for fraction in (rng.choice(_FRACTIONS), 0.5, 0.0):
        energy = Decimal(str(round((steps - fraction) * battery.step, 6)))
        miles = (energy / consumption).quantize(Decimal("0.001"), rounding=ROUND_FLOOR)
        if miles > 0 and edge_energy_steps(float(miles), probe) == steps:
            return float(miles)
    raise _Retry


def _minutes(rng: random.Random, miles: float) -> float:
    speed = rng.uniform(28.0, 68.0)
    return max(0.05, round(miles / speed * 60.0, 2))


def _place(draft: _Draft) -> None:
    """Give every node coordinates so each backbone road is at least the straight line."""
    rng = draft.rng
    neighbors: dict[str, list[tuple[str, float]]] = {}
    for start, end in draft.backbone:
        miles = draft.edges[(start, end)]["miles"]
        neighbors.setdefault(start, []).append((end, miles))
        neighbors.setdefault(end, []).append((start, miles))
    heading = rng.uniform(0.0, 2.0 * math.pi)
    positions: dict[str, tuple[float, float]] = {"origin": (0.0, 0.0)}
    queue = ["origin"]
    while queue:
        current = queue.pop(0)
        for other, miles in neighbors.get(current, ()):
            if other in positions:
                continue
            angle = heading + rng.uniform(-1.0, 1.0)
            reach = miles / _DETOUR
            x, y = positions[current]
            positions[other] = (x + reach * math.cos(angle), y + reach * math.sin(angle))
            queue.append(other)
    for node_id in draft.nodes:
        positions.setdefault(node_id, (rng.uniform(-1.0, 1.0), rng.uniform(-1.0, 1.0)))

    shrink = 1.0
    for start, end in draft.backbone:
        run = math.dist(positions[start], positions[end])
        if run > 0:
            shrink = min(shrink, 0.97 * draft.edges[(start, end)]["miles"] / run)
    for _ in range(8):
        for node_id, (x, y) in positions.items():
            draft.nodes[node_id]["xy"] = (x * shrink, y * shrink)
            draft.nodes[node_id]["lat"], draft.nodes[node_id]["lon"] = _latlon(
                x * shrink, y * shrink
            )
        if all(
            _straight(draft, start, end)
            <= draft.edges[(start, end)]["miles"] - _LENGTH_TOLERANCE_MILES
            for start, end in draft.backbone
        ):
            break
        shrink *= 0.9
    else:
        raise _Retry
    spans = [draft.edges[pair]["miles"] for pair in draft.backbone]
    draft.scale = sum(spans) / len(spans) / _DETOUR if spans else 1.0


def _add_decoys(draft: _Draft, accepts: Accepts) -> None:
    """Add chargers and roads one at a time, keeping only what leaves the category intact."""
    rng = draft.rng
    room = EXACT_MAX_NODES - len(draft.nodes)
    wanted = max(rng.randint(0, min(3, room)), MIN_NODES - len(draft.nodes))
    wanted = min(wanted, room)
    for added in range(wanted):
        anchor = draft.nodes[rng.choice(list(draft.nodes))]
        reach = draft.scale * rng.uniform(0.3, 1.0)
        angle = rng.uniform(0.0, 2.0 * math.pi)
        x = anchor["xy"][0] + reach * math.cos(angle)
        y = anchor["xy"][1] + reach * math.sin(angle)
        rate = rng.choice((None, *RATES))
        if draft.charger_count() >= EXACT_MAX_CHARGERS:
            rate = None
        if draft.charger_count() == 0 and added == wanted - 1:
            rate = rng.choice(RATES)
        node_id = f"d{added}"
        draft.node(node_id, "charger", rate)
        draft.nodes[node_id]["xy"] = (x, y)
        draft.nodes[node_id]["lat"], draft.nodes[node_id]["lon"] = _latlon(x, y)
    if draft.charger_count() == 0:
        raise _Retry

    pairs = [
        (start, end)
        for start in draft.nodes
        for end in draft.nodes
        if start != end and (start, end) not in draft.edges
    ]
    rng.shuffle(pairs)
    density = rng.choice((0.15, 0.3, 0.5))
    for start, end in pairs:
        if rng.random() >= density:
            continue
        straight = max(_straight(draft, start, end), 0.01)
        miles = math.ceil(straight * rng.uniform(1.05, 1.6) * 1000.0) / 1000.0
        draft.edge(start, end, miles)
        if not accepts(_stops(draft)):
            del draft.edges[(start, end)]


def _check_bounds(draft: _Draft) -> None:
    count = len(draft.nodes)
    if not MIN_NODES <= count <= EXACT_MAX_NODES:
        raise _Retry
    if not 1 <= draft.charger_count() <= EXACT_MAX_CHARGERS:
        raise _Retry
    for (start, end), edge in draft.edges.items():
        if edge["miles"] + _LENGTH_TOLERANCE_MILES < _straight(draft, start, end):
            raise InputError(f"edge {start!r} -> {end!r} is shorter than the straight line")


def _straight(draft: _Draft, start: str, end: str) -> float:
    a = draft.nodes[start]
    b = draft.nodes[end]
    return haversine_miles(a["lat"], a["lon"], b["lat"], b["lon"])


def _latlon(x: float, y: float) -> tuple[float, float]:
    return (
        round(_BASE_LAT + y / _MILES_PER_DEGREE_LAT, 6),
        round(_BASE_LON + x / _MILES_PER_DEGREE_LON, 6),
    )
