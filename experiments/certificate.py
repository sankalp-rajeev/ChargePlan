"""An energy-only feasibility certificate for Experiment A.

This module answers one question without looking at time or distance: what is
the smallest number of charging stops any plan needs to reach the destination,
or is the trip impossible? It is not a third solver for the lexicographic
objective. It shares nothing with ``battery_state_optimizer`` or
``exact_solver`` except the Section 7.1 rounding helpers in ``optimizer.model``.

Why a charge-to-full argument is enough for this question. Between two stops the
vehicle only drives, so the energy of that stretch must fit inside the battery
above the reserve: at most ``b0' - r'`` before the first stop and at most
``B' - r'`` after any stop, because charging to ``B'`` dominates every other
amount for reachability. Stops therefore form layers: layer ``k`` is every node
reachable with ``k`` stops, found by running a bounded energy search from every
charger already reached.

The generator uses this to build instances whose category is known by
construction. The tests use it as an independent check on the solvers: it must
agree on feasibility, and no solver may use fewer stops than it reports.
"""

from __future__ import annotations

import heapq

from optimizer.model import InputError, discretize, edge_energy_steps


def min_charging_stops(graph: dict, config: dict) -> int | None:
    """Return the fewest stops a feasible plan needs, or ``None`` if none exists.

    A trip that starts below the discretized reserve is infeasible, which is how
    both solvers treat it.
    """
    capacity, initial, reserve, _step = discretize(config)
    origin = config["origin"]
    destination = config["destination"]
    nodes = {node["id"]: node for node in graph["nodes"]}
    if origin not in nodes or destination not in nodes:
        raise InputError("origin and destination must be nodes in the graph")
    if initial < reserve:
        return None

    adjacency: dict[str, list[tuple[str, int]]] = {}
    for edge in graph["edges"]:
        adjacency.setdefault(edge["from"], []).append(
            (edge["to"], edge_energy_steps(edge["distance_miles"], config))
        )

    reached = _within_budget(adjacency, origin, initial - reserve)
    used: set[str] = set()
    stops = 0
    while destination not in reached:
        chargers = {
            node_id
            for node_id in reached
            if nodes[node_id]["charge_rate_kw"] is not None and node_id not in used
        }
        if not chargers:
            return None
        for charger in sorted(chargers):
            reached |= _within_budget(adjacency, charger, capacity - reserve)
        used |= chargers
        stops += 1
    return stops


def min_energy_steps(graph: dict, config: dict, source: str, target: str) -> int | None:
    """Return the cheapest energy, in steps, to drive ``source`` to ``target``.

    Charging is ignored. ``None`` means no road path exists.
    """
    adjacency: dict[str, list[tuple[str, int]]] = {}
    for edge in graph["edges"]:
        adjacency.setdefault(edge["from"], []).append(
            (edge["to"], edge_energy_steps(edge["distance_miles"], config))
        )
    best = _search(adjacency, source, None)
    return best.get(target)


def _within_budget(
    adjacency: dict[str, list[tuple[str, int]]],
    source: str,
    budget: int,
) -> set[str]:
    return set(_search(adjacency, source, budget))


def _search(
    adjacency: dict[str, list[tuple[str, int]]],
    source: str,
    budget: int | None,
) -> dict[str, int]:
    best = {source: 0}
    heap = [(0, source)]
    while heap:
        spent, node = heapq.heappop(heap)
        if spent > best[node]:
            continue
        for neighbor, energy in adjacency.get(node, ()):
            total = spent + energy
            if budget is not None and total > budget:
                continue
            if total < best.get(neighbor, total + 1):
                best[neighbor] = total
                heapq.heappush(heap, (total, neighbor))
    return best
