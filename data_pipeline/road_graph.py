"""Road-network preprocessing with OSM.

Owned by Shannon (H2). OSMnx may be used to precompute shortest-time paths.
The battery-state search itself stays in ``optimizer`` and is not called here.

The default corridor is Michigan and the adjacent Midwest, including
Detroit–Chicago and Detroit–Cleveland.

``road_metrics`` caches each OD pair under ``cache_dir`` so experiment runs do
not need live Overpass access. Cache entries are backend-aware: an OSMnx
request never reuses a haversine estimate, and each hit preserves the original
routing ``backend`` (with ``cached=True``).

Backend selection:

1. return a matching cache hit when present (OSMnx and haversine are separate),
2. otherwise try OSMnx shortest-time routing when the package is installed,
3. otherwise (``backend="auto"`` only) fall back to a documented haversine
   estimate (distance factor 1.25, speed 55 mph) that still respects the
   contract lower bound ``distance_miles >= great-circle``.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Literal

from optimizer.model import haversine_miles

# Roads are longer than the great-circle chord; 1.25 is a stable offline stand-in.
HAVERSINE_DISTANCE_FACTOR = 1.25
DEFAULT_SPEED_MPH = 55.0

# Degrees of padding around an OD bbox when downloading an OSM drive graph.
OSM_BBOX_PAD_DEG = 0.2

CacheSource = Literal["osmnx", "haversine"]
CACHE_SOURCES: tuple[CacheSource, ...] = ("osmnx", "haversine")


def road_metrics(
    origin: dict,
    destination: dict,
    cache_dir: str | Path,
    *,
    backend: Literal["auto", "osmnx", "haversine"] = "auto",
) -> dict:
    """Return road ``distance_miles`` and ``drive_time_minutes`` between two points.

    ``origin`` / ``destination`` need ``lat`` and ``lon``. The returned dict
    always includes those two metric keys plus ``backend`` naming the routing
    source (``osmnx`` or ``haversine``) and ``cached`` for cache hits.
    """
    origin_lat, origin_lon = require_lat_lon(origin, "origin")
    dest_lat, dest_lon = require_lat_lon(destination, "destination")
    cache_root = Path(cache_dir)
    cache_root.mkdir(parents=True, exist_ok=True)

    if backend == "haversine":
        cached = load_cached_metrics(
            cache_root, origin_lat, origin_lon, dest_lat, dest_lon, source="haversine"
        )
        if cached is not None:
            return cached
        metrics = haversine_metrics(origin_lat, origin_lon, dest_lat, dest_lon)
    elif backend == "osmnx":
        cached = load_cached_metrics(
            cache_root, origin_lat, origin_lon, dest_lat, dest_lon, source="osmnx"
        )
        if cached is not None:
            return cached
        # Never fall back to a haversine cache or estimate for an explicit OSM request.
        metrics = osmnx_metrics(origin_lat, origin_lon, dest_lat, dest_lon)
    else:
        cached = load_cached_metrics(
            cache_root, origin_lat, origin_lon, dest_lat, dest_lon, source="osmnx"
        )
        if cached is not None:
            return cached
        cached = load_cached_metrics(
            cache_root, origin_lat, origin_lon, dest_lat, dest_lon, source="haversine"
        )
        if cached is not None:
            return cached
        try:
            metrics = osmnx_metrics(origin_lat, origin_lon, dest_lat, dest_lon)
        except Exception:
            # Offline / missing osmnx / Overpass failure: keep preprocessing usable.
            metrics = haversine_metrics(origin_lat, origin_lon, dest_lat, dest_lon)

    straight = haversine_miles(origin_lat, origin_lon, dest_lat, dest_lon)
    distance = max(float(metrics["distance_miles"]), straight)
    drive_time = max(float(metrics["drive_time_minutes"]), 0.0)
    raw_source = metrics["backend"]
    if raw_source not in CACHE_SOURCES:
        raise RuntimeError(f"unexpected routing backend {raw_source!r}")
    source: CacheSource = raw_source
    result = {
        "distance_miles": distance,
        "drive_time_minutes": drive_time,
        "backend": source,
        "cached": False,
    }
    write_cache(
        cache_root / cache_name(origin_lat, origin_lon, dest_lat, dest_lon, source),
        {
            **result,
            "origin": {"lat": origin_lat, "lon": origin_lon},
            "destination": {"lat": dest_lat, "lon": dest_lon},
        },
    )
    return result


def clear_route_cache(cache_dir: str | Path) -> int:
    """Delete cached ``road_metrics`` JSON files. Returns the number removed."""
    root = Path(cache_dir)
    if not root.is_dir():
        return 0
    removed = 0
    for path in root.glob("route_*.json"):
        path.unlink()
        removed += 1
    return removed


def haversine_metrics(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
) -> dict[str, Any]:
    straight = haversine_miles(origin_lat, origin_lon, dest_lat, dest_lon)
    distance = straight * HAVERSINE_DISTANCE_FACTOR
    if distance <= 0:
        drive_time = 0.0
    else:
        drive_time = (distance / DEFAULT_SPEED_MPH) * 60.0
    return {
        "distance_miles": distance,
        "drive_time_minutes": drive_time,
        "backend": "haversine",
    }


def osmnx_metrics(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
) -> dict[str, Any]:
    """Shortest-time drive path via OSMnx. Raises if routing is unavailable.

    Short hops download a point-centered drive extract (more reliable against
    public Overpass). Longer corridors use a padded bbox.
    """
    try:
        import osmnx as ox
    except ImportError as exc:
        raise RuntimeError(
            "osmnx is not installed; pip install 'chargeplan[roads]' "
            "or use backend='haversine'"
        ) from exc

    if math.isclose(origin_lat, dest_lat) and math.isclose(origin_lon, dest_lon):
        return {
            "distance_miles": 0.0,
            "drive_time_minutes": 0.0,
            "backend": "osmnx",
        }

    graph = _osm_drive_graph(ox, origin_lat, origin_lon, dest_lat, dest_lon)
    graph = ox.add_edge_speeds(graph)
    graph = ox.add_edge_travel_times(graph)

    origin_node = ox.nearest_nodes(graph, origin_lon, origin_lat)
    dest_node = ox.nearest_nodes(graph, dest_lon, dest_lat)
    route = shortest_path(ox, graph, origin_node, dest_node)
    if not route or len(route) < 2:
        raise RuntimeError("OSMnx found no drive path between the two points")

    length_m = 0.0
    travel_s = 0.0
    for u, v in zip(route[:-1], route[1:], strict=True):
        edge_data = graph.get_edge_data(u, v)
        if not edge_data:
            raise RuntimeError("OSMnx route referenced a missing edge")
        # Multigraph: pick the fastest parallel edge used by the weight.
        best = min(edge_data.values(), key=lambda data: data.get("travel_time", math.inf))
        length_m += float(best.get("length", 0.0))
        travel_s += float(best.get("travel_time", 0.0))

    meters_per_mile = 1609.344
    distance_miles = length_m / meters_per_mile
    drive_time_minutes = travel_s / 60.0
    return {
        "distance_miles": distance_miles,
        "drive_time_minutes": drive_time_minutes,
        "backend": "osmnx",
    }


def _osm_drive_graph(
    ox: Any,
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
):
    """Download the OSM drive network covering both endpoints."""
    straight_miles = haversine_miles(origin_lat, origin_lon, dest_lat, dest_lon)
    # Point extracts stay smaller and succeed more often on public Overpass.
    if straight_miles <= 30.0:
        mid_lat = (origin_lat + dest_lat) / 2.0
        mid_lon = (origin_lon + dest_lon) / 2.0
        half_m = (straight_miles * 1609.344) / 2.0
        dist_m = max(half_m + 1500.0, 2000.0)
        graph_mod = getattr(ox, "graph", None)
        if graph_mod is not None and hasattr(graph_mod, "graph_from_point"):
            return graph_mod.graph_from_point(
                (mid_lat, mid_lon),
                dist=dist_m,
                network_type="drive",
            )
        return ox.graph_from_point((mid_lat, mid_lon), dist=dist_m, network_type="drive")

    north = max(origin_lat, dest_lat) + OSM_BBOX_PAD_DEG
    south = min(origin_lat, dest_lat) - OSM_BBOX_PAD_DEG
    east = max(origin_lon, dest_lon) + OSM_BBOX_PAD_DEG
    west = min(origin_lon, dest_lon) - OSM_BBOX_PAD_DEG
    return graph_from_bbox(ox, north=north, south=south, east=east, west=west)


def graph_from_bbox(ox: Any, *, north: float, south: float, east: float, west: float):
    """Support both OSMnx 1.x positional bbox and 2.x ``bbox=(left, bottom, right, top)``."""
    graph_mod = getattr(ox, "graph", None)
    if graph_mod is not None and hasattr(graph_mod, "graph_from_bbox"):
        return graph_mod.graph_from_bbox(
            bbox=(west, south, east, north),
            network_type="drive",
        )
    return ox.graph_from_bbox(north, south, east, west, network_type="drive")


def shortest_path(ox: Any, graph: Any, origin_node: Any, dest_node: Any):
    routing = getattr(ox, "routing", None)
    if routing is not None and hasattr(routing, "shortest_path"):
        return routing.shortest_path(graph, origin_node, dest_node, weight="travel_time")
    return ox.shortest_path(graph, origin_node, dest_node, weight="travel_time")


def pair_digest(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
) -> str:
    key = (
        f"{origin_lat:.5f},{origin_lon:.5f}->"
        f"{dest_lat:.5f},{dest_lon:.5f}"
    )
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def cache_name(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    source: CacheSource,
) -> str:
    if source not in CACHE_SOURCES:
        raise ValueError(f"unsupported cache source {source!r}")
    digest = pair_digest(origin_lat, origin_lon, dest_lat, dest_lon)
    return f"route_{digest}_{source}.json"


def legacy_cache_name(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
) -> str:
    """Pre-backend-aware filename used only for read compatibility."""
    return f"route_{pair_digest(origin_lat, origin_lon, dest_lat, dest_lon)}.json"


def load_cached_metrics(
    cache_root: Path,
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    *,
    source: CacheSource,
) -> dict | None:
    """Load a cache entry only when it was produced by ``source``."""
    primary = cache_root / cache_name(
        origin_lat, origin_lon, dest_lat, dest_lon, source
    )
    cached = read_cache(primary, expected_source=source)
    if cached is not None:
        return cached

    # Older caches used a single OD filename and erased the source on read.
    legacy = cache_root / legacy_cache_name(
        origin_lat, origin_lon, dest_lat, dest_lon
    )
    return read_cache(legacy, expected_source=source)


def read_cache(path: Path, *, expected_source: CacheSource) -> dict | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        distance = float(payload["distance_miles"])
        drive_time = float(payload["drive_time_minutes"])
    except (KeyError, TypeError, ValueError):
        return None

    stored = payload.get("backend")
    # Legacy files may omit backend; only haversine may claim those, since the
    # historical shared cache was dominated by offline estimates.
    if stored is None:
        if expected_source != "haversine":
            return None
    elif stored in CACHE_SOURCES:
        if stored != expected_source:
            return None
    else:
        # Old readers rewrote hits as backend="cache"; treat as unknown source.
        return None

    return {
        "distance_miles": distance,
        "drive_time_minutes": drive_time,
        "backend": expected_source,
        "cached": True,
    }


def write_cache(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def require_lat_lon(point: dict[str, Any], label: str) -> tuple[float, float]:
    try:
        lat = float(point["lat"])
        lon = float(point["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must provide numeric lat and lon") from exc
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValueError(f"{label} lat/lon out of range")
    return lat, lon
