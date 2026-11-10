"""Corridor reduction and battery-range edge filter (H2)."""

from __future__ import annotations

import pytest

from data_pipeline.reduce_graph import (
    DEFAULT_CORRIDOR_WIDTH_MILES,
    DEFAULT_MAX_BATTERY_KWH,
    filter_edges_by_battery,
    is_edge_within_battery,
    max_drivable_miles,
    select_candidates,
)

DETROIT = {"id": "origin", "lat": 42.3314, "lon": -83.0458}
CHICAGO = {"id": "destination", "lat": 41.8781, "lon": -87.6298}


def test_select_candidates_keeps_corridor_stations() -> None:
    stations = [
        {"id": "ann_arbor", "lat": 42.2808, "lon": -83.7430, "charge_rate_kw": 150},
        {"id": "kalamazoo", "lat": 42.2917, "lon": -85.5872, "charge_rate_kw": 50},
        {"id": "marquette", "lat": 46.5436, "lon": -87.3954, "charge_rate_kw": 50},
        {"id": "indianapolis", "lat": 39.7684, "lon": -86.1581, "charge_rate_kw": 150},
    ]
    selected = select_candidates(stations, DETROIT, CHICAGO)
    assert [station["id"] for station in selected] == ["ann_arbor", "kalamazoo"]


def test_select_candidates_dedupes_ids() -> None:
    near = {"id": "dtw", "lat": 42.34, "lon": -83.05, "charge_rate_kw": 150}
    duplicate = {"id": "dtw", "lat": 42.341, "lon": -83.051, "charge_rate_kw": 50}
    selected = select_candidates(
        [near, duplicate],
        DETROIT,
        CHICAGO,
        corridor_width_miles=DEFAULT_CORRIDOR_WIDTH_MILES,
    )
    assert len(selected) == 1
    assert selected[0]["charge_rate_kw"] == 150


def test_battery_edge_filter_matches_experiment_budget() -> None:
    limit = max_drivable_miles()
    assert limit == pytest.approx(DEFAULT_MAX_BATTERY_KWH / 0.30)
    assert is_edge_within_battery(limit)
    assert not is_edge_within_battery(limit + 1.0)

    edges = [
        {"from": "a", "to": "b", "distance_miles": 100.0, "drive_time_minutes": 110.0},
        {
            "from": "a",
            "to": "c",
            "distance_miles": limit + 5.0,
            "drive_time_minutes": 400.0,
        },
    ]
    assert filter_edges_by_battery(edges) == [edges[0]]
