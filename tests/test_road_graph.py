"""Cached road metrics (H2), using the haversine backend offline."""

from __future__ import annotations

from pathlib import Path

import pytest

from data_pipeline.road_graph import (
    DEFAULT_SPEED_MPH,
    HAVERSINE_DISTANCE_FACTOR,
    clear_route_cache,
    road_metrics,
)
from optimizer.model import haversine_miles

DETROIT = {"lat": 42.3314, "lon": -83.0458}
ANN_ARBOR = {"lat": 42.2808, "lon": -83.7430}


def test_road_metrics_haversine_respects_straight_line_bound(tmp_path: Path) -> None:
    metrics = road_metrics(DETROIT, ANN_ARBOR, tmp_path, backend="haversine")
    straight = haversine_miles(
        DETROIT["lat"], DETROIT["lon"], ANN_ARBOR["lat"], ANN_ARBOR["lon"]
    )
    assert metrics["backend"] == "haversine"
    assert metrics["distance_miles"] == pytest.approx(straight * HAVERSINE_DISTANCE_FACTOR)
    assert metrics["distance_miles"] >= straight
    expected_minutes = (metrics["distance_miles"] / DEFAULT_SPEED_MPH) * 60.0
    assert metrics["drive_time_minutes"] == pytest.approx(expected_minutes)


def test_road_metrics_uses_disk_cache(tmp_path: Path) -> None:
    first = road_metrics(DETROIT, ANN_ARBOR, tmp_path, backend="haversine")
    second = road_metrics(DETROIT, ANN_ARBOR, tmp_path, backend="haversine")
    assert first["distance_miles"] == second["distance_miles"]
    assert second["backend"] == "cache"
    assert clear_route_cache(tmp_path) >= 1


def test_road_metrics_rejects_incomplete_points(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="destination"):
        road_metrics(DETROIT, {"lat": 42.0}, tmp_path, backend="haversine")
