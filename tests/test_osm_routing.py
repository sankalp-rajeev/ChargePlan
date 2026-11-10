"""Live OSMnx routing check for a short Detroit hop (optional; needs network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from data_pipeline.road_graph import haversine_metrics, road_metrics
from optimizer.model import haversine_miles

pytest.importorskip("osmnx")

DETROIT = {"lat": 42.3314, "lon": -83.0458}
DEARBORN = {"lat": 42.3223, "lon": -83.1763}

@pytest.mark.network
def test_road_metrics_osmnx_detroit_dearborn_fresh_cache(tmp_path: Path) -> None:
    """Fresh cache + backend='osmnx' must return real road metrics, not haversine."""
    cache = tmp_path / "osm_cache"
    cache.mkdir()

    try:
        osm = road_metrics(DETROIT, DEARBORN, cache, backend="osmnx")
    except Exception as exc:  # Overpass / SSL / timeout — skip rather than fail CI
        pytest.skip(f"OSMnx routing unavailable: {exc}")

    hav = haversine_metrics(
        DETROIT["lat"], DETROIT["lon"], DEARBORN["lat"], DEARBORN["lon"]
    )
    straight = haversine_miles(
        DETROIT["lat"], DETROIT["lon"], DEARBORN["lat"], DEARBORN["lon"]
    )

    assert osm["backend"] == "osmnx"
    assert osm["cached"] is False
    assert osm["distance_miles"] >= straight
    assert osm["drive_time_minutes"] > 0
    # Not the documented haversine stand-in (×1.25 @ 55 mph).
    assert osm["distance_miles"] != pytest.approx(hav["distance_miles"])
    assert osm["drive_time_minutes"] != pytest.approx(hav["drive_time_minutes"])

    cached = road_metrics(DETROIT, DEARBORN, cache, backend="osmnx")
    assert cached["backend"] == "osmnx"
    assert cached["cached"] is True
    assert cached["distance_miles"] == osm["distance_miles"]
    assert cached["drive_time_minutes"] == osm["drive_time_minutes"]
