"""Turn parent pointers into a route and an event list.

Owned by Sankalp. Drive and charge events must follow the result schema:
ordered node ids, SOC before and after each event, and durations.

TODO: walk parent links from the settled goal back to the origin and emit
``route_node_ids`` plus ``events`` (S2).
"""


def reconstruct(parent: dict, goal: tuple, graph: dict, config: dict) -> dict:
    """Return ``route_node_ids`` and ``events`` for one settled goal state.

    TODO: implement reconstruction (S2).
    """
    raise NotImplementedError
