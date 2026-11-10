"""Real-data scenario builders (H5), tested function by function."""

from __future__ import annotations

from pathlib import Path

import pytest

from data_pipeline.scenarios import (
    PLACES,
    ScenarioSpec,
    build_all_scenarios,
    build_scenario,
    default_trip_config,
    export_trip_config,
    filter_stations_for_scenario,
    get_scenario_spec,
    place,
    scenario_specs,
    select_mixed_speed_stations,
    select_sparse_stations,
    stations_between_endpoints,
    validate_trip_config,
    write_scenario,
)
from optimizer.model import load_config, load_graph


def _station(
    station_id: str,
    lon: float,
    *,
    charger_class: str = "dc_fast",
    rate: float = 150.0,
    lat: float = 42.2,
) -> dict:
    return {
        "id": station_id,
        "name": station_id,
        "lat": lat,
        "lon": lon,
        "charger_class": charger_class,
        "charge_rate_kw": rate,
        "used_fallback_rate": False,
        "network": None,
        "city": None,
        "state": "MI",
        "street_address": None,
    }


def _corridor_stations() -> list[dict]:
    # Detroit (-83.05) → Chicago (-87.63)-ish longitudes.
    return [
        _station("dc1", -83.5, charger_class="dc_fast", rate=150),
        _station("l2a", -84.0, charger_class="level2", rate=7.2),
        _station("dc2", -84.5, charger_class="dc_fast", rate=150),
        _station("l2b", -85.0, charger_class="level2", rate=7.2),
        _station("dc3", -85.5, charger_class="dc_fast", rate=150),
        _station("l2c", -86.0, charger_class="level2", rate=7.2),
        _station("dc4", -86.5, charger_class="dc_fast", rate=150),
        _station("l2d", -87.0, charger_class="level2", rate=7.2),
        _station("off", -85.0, charger_class="dc_fast", rate=150, lat=46.0),
    ]


def test_place_returns_frozen_coordinates() -> None:
    detroit = place("detroit")
    assert detroit == PLACES["detroit"]
    detroit["lat"] = 0.0
    assert place("detroit")["lat"] == pytest.approx(42.3314)
    with pytest.raises(KeyError, match="unknown place"):
        place("toledo")


def test_default_and_validate_trip_config(tmp_path: Path) -> None:
    config = default_trip_config(initial_soc_kwh=40, battery_capacity_kwh=70)
    validate_trip_config(config)
    export_trip_config(config, tmp_path / "trip_config.json")
    loaded = load_config(tmp_path / "trip_config.json")
    assert loaded["initial_soc_kwh"] == 40

    bad = default_trip_config(initial_soc_kwh=80, battery_capacity_kwh=70)
    with pytest.raises(ValueError, match="initial_soc_kwh"):
        validate_trip_config(bad)


def test_stations_between_endpoints_drops_past_and_behind() -> None:
    origin = place("detroit")
    destination = place("chicago")
    stations = _corridor_stations() + [
        _station("behind", -82.0, charger_class="dc_fast", rate=150),
        _station("past", -90.0, charger_class="dc_fast", rate=150),
    ]
    kept = stations_between_endpoints(stations, origin, destination)
    ids = {station["id"] for station in kept}
    assert "behind" not in ids
    assert "past" not in ids
    assert "dc1" in ids


def test_select_mixed_speed_stations_keeps_both_classes() -> None:
    origin = place("detroit")
    destination = place("chicago")
    selected = select_mixed_speed_stations(
        _corridor_stations(),
        origin,
        destination,
        max_chargers=6,
    )
    assert len(selected) == 6
    classes = {station["charger_class"] for station in selected}
    assert classes == {"dc_fast", "level2"}


def test_select_sparse_stations_keeps_even_spacing() -> None:
    origin = place("detroit")
    destination = place("chicago")
    selected = select_sparse_stations(
        _corridor_stations(),
        origin,
        destination,
        max_chargers=3,
    )
    assert len(selected) == 3
    assert all(station["lat"] < 45 for station in selected)


def test_filter_stations_for_scenario_dispatches() -> None:
    spec = get_scenario_spec("sparse_detroit_chicago")
    selected = filter_stations_for_scenario(
        _corridor_stations(),
        spec,
        place("detroit"),
        place("chicago"),
    )
    assert len(selected) == 6


def test_scenario_specs_cover_required_categories() -> None:
    specs = scenario_specs()
    categories = {spec.category for spec in specs}
    assert categories == {
        "no_charge",
        "one_stop",
        "multi_stop",
        "mixed_speeds",
        "sparse",
    }
    assert len(specs) == 5


def test_build_and_write_scenario(tmp_path: Path) -> None:
    spec = ScenarioSpec(
        scenario_id="unit_multi_stop",
        category="multi_stop",
        description="unit test corridor",
        origin_place="detroit",
        destination_place="chicago",
        initial_soc_kwh=40.0,
        max_chargers=4,
        station_filter="all",
    )
    scenario = build_scenario(
        spec,
        _corridor_stations(),
        tmp_path / "cache",
        road_backend="haversine",
    )
    assert scenario["charger_count"] == 4
    assert scenario["config"]["initial_soc_kwh"] == 40.0

    root = write_scenario(scenario, tmp_path / "processed")
    graph = load_graph(root / "canonical_graph.json")
    config = load_config(root / "trip_config.json")
    assert len(graph["nodes"]) == 6  # origin + 4 chargers + destination
    assert config["origin"] == "origin"
    assert (root / "scenario_meta.json").is_file()


def test_build_all_scenarios_writes_requested_ids(tmp_path: Path) -> None:
    written = build_all_scenarios(
        _corridor_stations(),
        tmp_path / "cache",
        tmp_path / "processed",
        road_backend="haversine",
        scenario_ids=["sparse_detroit_chicago", "no_charge_detroit_ann_arbor"],
    )
    assert {path.name for path in written} == {
        "sparse_detroit_chicago",
        "no_charge_detroit_ann_arbor",
    }
    for path in written:
        load_graph(path / "canonical_graph.json")
        load_config(path / "trip_config.json")
