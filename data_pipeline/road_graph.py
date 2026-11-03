"""Road-network preprocessing with OSM.

Owned by Shannon (H2). OSMnx may be used to precompute shortest-time paths.
The battery-state search itself stays in ``optimizer`` and is not called here.

The default corridor is Michigan and the adjacent Midwest, including
Detroit–Chicago and Detroit–Cleveland.

TODO: map selected nodes onto the road network and cache distance and time (H2).
"""


def road_metrics(origin: dict, destination: dict, cache_dir: str) -> dict:
    """Return road ``distance_miles`` and ``drive_time_minutes`` between two points.

    TODO: implement cached OSM routing (H2).
    """
    raise NotImplementedError
