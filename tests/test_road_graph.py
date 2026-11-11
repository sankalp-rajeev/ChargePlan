"""Cached road metrics (H2), using the haversine backend offline."""

from __future__ import annotations

from pathlib import Path

import pytest

from data_pipeline import road_graph
from data_pipeline.road_graph import (
    DEFAULT_SPEED_MPH,
    HAVERSINE_DISTANCE_FACTOR,
    clear_route_cache,
    road_metrics,
)
from optimizer.model import haversine_miles

DETROIT = {"lat": 42.3314, "lon": -83.0458}
ANN_ARBOR = {"lat": 42.2808, "lon": -83.7430}
DEARBORN = {"lat": 42.3223, "lon": -83.1763}


def test_road_metrics_haversine_respects_straight_line_bound(tmp_path: Path) -> None:
    metrics = road_metrics(DETROIT, ANN_ARBOR, tmp_path, backend="haversine")
    straight = haversine_miles(
        DETROIT["lat"], DETROIT["lon"], ANN_ARBOR["lat"], ANN_ARBOR["lon"]
    )
    assert metrics["backend"] == "haversine"
    assert metrics["cached"] is False
    assert metrics["distance_miles"] == pytest.approx(straight * HAVERSINE_DISTANCE_FACTOR)
    assert metrics["distance_miles"] >= straight
    expected_minutes = (metrics["distance_miles"] / DEFAULT_SPEED_MPH) * 60.0
    assert metrics["drive_time_minutes"] == pytest.approx(expected_minutes)


def test_road_metrics_uses_disk_cache(tmp_path: Path) -> None:
    first = road_metrics(DETROIT, ANN_ARBOR, tmp_path, backend="haversine")
    second = road_metrics(DETROIT, ANN_ARBOR, tmp_path, backend="haversine")
    assert first["distance_miles"] == second["distance_miles"]
    assert second["backend"] == "haversine"
    assert second["cached"] is True
    assert clear_route_cache(tmp_path) >= 1


def test_road_metrics_rejects_incomplete_points(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="destination"):
        road_metrics(DETROIT, {"lat": 42.0}, tmp_path, backend="haversine")


def test_osmnx_does_not_reuse_haversine_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A haversine estimate must not satisfy a later backend='osmnx' request."""
    haversine = road_metrics(DETROIT, DEARBORN, tmp_path, backend="haversine")
    assert haversine["backend"] == "haversine"
    assert list(tmp_path.glob("route_*_haversine.json"))

    def fake_osmnx(
        origin_lat: float,
        origin_lon: float,
        dest_lat: float,
        dest_lon: float,
    ) -> dict:
        return {
            "distance_miles": 12.5,
            "drive_time_minutes": 18.0,
            "backend": "osmnx",
        }

    monkeypatch.setattr(road_graph, "osmnx_metrics", fake_osmnx)

    osm = road_metrics(DETROIT, DEARBORN, tmp_path, backend="osmnx")
    assert osm["backend"] == "osmnx"
    assert osm["cached"] is False
    assert osm["distance_miles"] == pytest.approx(12.5)
    assert osm["drive_time_minutes"] == pytest.approx(18.0)
    assert osm["distance_miles"] != pytest.approx(haversine["distance_miles"])
    assert list(tmp_path.glob("route_*_osmnx.json"))

    cached_osm = road_metrics(DETROIT, DEARBORN, tmp_path, backend="osmnx")
    assert cached_osm["backend"] == "osmnx"
    assert cached_osm["cached"] is True
    assert cached_osm["distance_miles"] == pytest.approx(12.5)


def test_osmnx_raises_when_only_haversine_is_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    road_metrics(DETROIT, DEARBORN, tmp_path, backend="haversine")

    def boom(
        origin_lat: float,
        origin_lon: float,
        dest_lat: float,
        dest_lon: float,
    ) -> dict:
        raise RuntimeError("overpass unavailable")

    monkeypatch.setattr(road_graph, "osmnx_metrics", boom)

    with pytest.raises(RuntimeError, match="overpass unavailable"):
        road_metrics(DETROIT, DEARBORN, tmp_path, backend="osmnx")
