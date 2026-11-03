"""Farthest-reachable-charger baseline.

Owned by Shannon. This is the only file under ``optimizer/`` that Shannon
edits. Import ``optimizer.model`` for loading and discretization, and do not
change the other optimizer modules.

The frozen rule is PRD Section 12: from the current node, drive to the
destination when it is reachable after an optional charge; otherwise charge
just enough to reach the charger that is closest to the destination among
chargers strictly closer to it than the current node. Ties break by higher
charging rate, then smaller node id. The result uses ``algorithm`` =
``greedy_frc``.

TODO: implement the heuristic and its result writer (H4).
"""


def solve(graph: dict, config: dict) -> dict:
    """Return an ``algorithm_result`` dict for the FRC heuristic.

    TODO: implement Section 12 (H4).
    """
    raise NotImplementedError
