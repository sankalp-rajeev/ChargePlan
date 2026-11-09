"""Reduce a corridor to the optimizer's node set.

Owned by Shannon (H2). Keep the origin, the destination, and candidate
chargers near the origin–destination line. Omit edges whose energy exceeds
the largest battery capacity in the experiment plan.

The corridor test projects WGS84 points into a local equirectangular frame
(miles) and keeps stations whose distance to the origin–destination segment
is at most ``corridor_width_miles``. Stations may sit a little past either
endpoint (same padding) so near-OD chargers are not dropped.

Experiment E's largest capacity is 100 kWh at 0.30 kWh/mi, so edges longer
than that energy budget are omitted when assembling the reduced graph.
"""

from __future__ import annotations

import math
from typing import Any

from optimizer.model import haversine_miles

# Half-width of the candidate band around the OD great-circle segment.
DEFAULT_CORRIDOR_WIDTH_MILES = 40.0

# Largest battery in the experiment plan (Experiment E) and default consumption.
DEFAULT_MAX_BATTERY_KWH = 100.0
DEFAULT_CONSUMPTION_KWH_PER_MILE = 0.30

EARTH_RADIUS_MILES = 3958.7613


def select_candidates(
    stations: list[dict],
    origin: dict,
    destination: dict,
    corridor_width_miles: float = DEFAULT_CORRIDOR_WIDTH_MILES,
) -> list[dict]:
    """Return the charger subset that will become graph nodes.

    ``origin`` and ``destination`` must provide ``lat`` / ``lon``. Stations
    need the same keys. Results keep input order and drop duplicate ``id``
    values after the first occurrence.
    """
    if corridor_width_miles < 0:
        raise ValueError("corridor_width_miles must be non-negative")

    origin_lat, origin_lon = require_lat_lon(origin, "origin")
    dest_lat, dest_lon = require_lat_lon(destination, "destination")

    selected: list[dict] = []
    seen_ids: set[str] = set()
    for station in stations:
        station_lat, station_lon = require_lat_lon(station, "station")
        if not inside_corridor(
            station_lat,
            station_lon,
            origin_lat,
            origin_lon,
            dest_lat,
            dest_lon,
            corridor_width_miles,
        ):
            continue
        station_id = str(station.get("id", "")).strip()
        if station_id:
            if station_id in seen_ids:
                continue
            seen_ids.add(station_id)
        selected.append(station)
    return selected


def max_drivable_miles(
    max_battery_kwh: float = DEFAULT_MAX_BATTERY_KWH,
    consumption_kwh_per_mile: float = DEFAULT_CONSUMPTION_KWH_PER_MILE,
) -> float:
    """Longest road the largest experiment battery can cover at the given rate."""
    if max_battery_kwh < 0:
        raise ValueError("max_battery_kwh must be non-negative")
    if consumption_kwh_per_mile <= 0:
        raise ValueError("consumption_kwh_per_mile must be positive")
    return max_battery_kwh / consumption_kwh_per_mile


def is_edge_within_battery(
    distance_miles: float,
    max_battery_kwh: float = DEFAULT_MAX_BATTERY_KWH,
    consumption_kwh_per_mile: float = DEFAULT_CONSUMPTION_KWH_PER_MILE,
) -> bool:
    """True when ``distance * consumption`` does not exceed ``max_battery_kwh``."""
    if distance_miles < 0:
        raise ValueError("distance_miles must be non-negative")
    energy = distance_miles * consumption_kwh_per_mile
    return energy <= max_battery_kwh + 1e-9


def filter_edges_by_battery(
    edges: list[dict],
    max_battery_kwh: float = DEFAULT_MAX_BATTERY_KWH,
    consumption_kwh_per_mile: float = DEFAULT_CONSUMPTION_KWH_PER_MILE,
) -> list[dict]:
    """Drop edges whose energy would exceed the largest experiment battery."""
    kept: list[dict] = []
    for edge in edges:
        distance = float(edge["distance_miles"])
        if is_edge_within_battery(distance, max_battery_kwh, consumption_kwh_per_mile):
            kept.append(edge)
    return kept


def inside_corridor(
    lat: float,
    lon: float,
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    corridor_width_miles: float,
) -> bool:
    od_miles = haversine_miles(origin_lat, origin_lon, dest_lat, dest_lon)
    if od_miles <= 1e-9:
        return haversine_miles(origin_lat, origin_lon, lat, lon) <= corridor_width_miles

    # Local tangent plane centered on the origin. Adequate for Midwest corridors.
    origin_xy = (0.0, 0.0)
    dest_xy = to_local_miles(dest_lat, dest_lon, origin_lat, origin_lon)
    point_xy = to_local_miles(lat, lon, origin_lat, origin_lon)
    distance, along = point_to_segment(point_xy, origin_xy, dest_xy)
    pad = corridor_width_miles
    if along < -pad or along > od_miles + pad:
        return False
    return distance <= corridor_width_miles + 1e-9


def to_local_miles(
    lat: float,
    lon: float,
    lat0: float,
    lon0: float,
) -> tuple[float, float]:
    """Equirectangular east/north offsets in miles from ``(lat0, lon0)``."""
    cos_lat = math.cos(math.radians(lat0))
    east = math.radians(lon - lon0) * cos_lat * EARTH_RADIUS_MILES
    north = math.radians(lat - lat0) * EARTH_RADIUS_MILES
    return east, north


def point_to_segment(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> tuple[float, float]:
    """Return (perpendicular miles, along-track miles from start)."""
    px, py = point
    sx, sy = start
    ex, ey = end
    dx = ex - sx
    dy = ey - sy
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-18:
        return math.hypot(px - sx, py - sy), 0.0

    t = ((px - sx) * dx + (py - sy) * dy) / length_sq
    along = t * math.sqrt(length_sq)
    if t <= 0.0:
        return math.hypot(px - sx, py - sy), along
    if t >= 1.0:
        return math.hypot(px - ex, py - ey), along
    proj_x = sx + t * dx
    proj_y = sy + t * dy
    return math.hypot(px - proj_x, py - proj_y), along


def require_lat_lon(point: dict[str, Any], label: str) -> tuple[float, float]:
    try:
        lat = float(point["lat"])
        lon = float(point["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must provide numeric lat and lon") from exc
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValueError(f"{label} lat/lon out of range")
    return lat, lon
