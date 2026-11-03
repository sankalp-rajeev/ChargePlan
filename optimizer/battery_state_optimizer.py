"""Label-setting search and the A* variant over battery-expanded states.

Owned by Sankalp. Both modes live here. ``use_heuristic=False`` is plain
label-setting (S2). ``use_heuristic=True`` adds the admissible
straight-line-time heuristic from PRD Section 11.3 (S10). Labels are
``(time_ms, stops, distance)`` and compare lexicographically.

The search must be implemented in this module. Do not call NetworkX or
another library for the shortest path.

TODO: priority queue, relaxation, stale-state handling, parent pointers, and
the heuristic flag (S2, S10).
"""


def solve(graph: dict, config: dict, *, use_heuristic: bool = False) -> dict:
    """Return an ``algorithm_result`` dict.

    ``use_heuristic=False`` writes ``algorithm`` = ``label_setting``.
    ``use_heuristic=True`` writes ``algorithm`` = ``astar`` and must settle
    the same label as label-setting.

    TODO: implement the search (S2, S10).
    """
    raise NotImplementedError
