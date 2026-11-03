"""Exhaustive backtracking solver for tiny instances.

Owned by Sankalp. This is a depth-first enumeration, not a priority-queue
search. At each charger visit it chooses the charge amount in one decision.
It may import loaders and discretization from ``optimizer.model`` and must
not share search code with ``battery_state_optimizer``.

TODO: enumerate feasible plans, prune dominated branches, and return the
lexicographically best label (S3).
"""


def solve(graph: dict, config: dict) -> dict:
    """Return an ``algorithm_result`` dict with ``algorithm`` = ``exact``.

    TODO: implement bounded exhaustive search (S3).
    """
    raise NotImplementedError
