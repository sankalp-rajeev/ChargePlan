"""Write a ``canonical_graph.json`` file that matches the frozen schema.

Owned by Shannon (H3). Nodes use ids, types, coordinates, and
``charge_rate_kw``. Edges are directed and store distance and driving time only.

TODO: assemble nodes and edges and validate before writing (H3).
"""


def export_graph(nodes: list[dict], edges: list[dict], path: str) -> None:
    """Write one canonical graph.

    TODO: validate against ``contracts/canonical_graph.schema.json`` and write (H3).
    """
    raise NotImplementedError
