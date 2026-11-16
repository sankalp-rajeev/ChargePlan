"""Real-data scenario builders (H5), tested function by function."""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

import pytest

from data_pipeline.scenarios import (
    PLACES,
    ScenarioSpec,
    build_all_scenarios,
    build_scenario,
    default_trip_config,
    effective_road_backend,
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
    used_fallback_rate: bool = False,
) -> dict:
    return {
        "id": station_id,
        "name": station_id,
        "lat": lat,
        "lon": lon,
        "charger_class": charger_class,
        "charge_rate_kw": rate,
        "used_fallback_rate": used_fallback_rate,
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


def test_dc_fast_filter_excludes_level2() -> None:
    spec = get_scenario_spec("multi_stop_detroit_chicago")
    assert spec.station_filter == "dc_fast"
    selected = filter_stations_for_scenario(
        _corridor_stations(),
        spec,
        place("detroit"),
        place("chicago"),
    )
    assert {station["id"] for station in selected} == {"dc1", "dc2", "dc3", "dc4"}
    assert all(station["charger_class"] == "dc_fast" for station in selected)


def test_dc_fast_filter_requires_dc_fast_stations() -> None:
    spec = get_scenario_spec("multi_stop_detroit_chicago")
    only_l2 = [s for s in _corridor_stations() if s["charger_class"] == "level2"]
    with pytest.raises(ValueError, match="no DC-fast"):
        filter_stations_for_scenario(only_l2, spec, place("detroit"), place("chicago"))


def test_scenario_meta_records_provenance(tmp_path: Path) -> None:
    snapshot = tmp_path / "afdc_ev_midwest_2026-11-09.json"
    snapshot.write_text('{"fuel_stations": []}', encoding="utf-8")
    stations = _corridor_stations()
    stations[0]["used_fallback_rate"] = True
    stations[1]["used_fallback_rate"] = True  # l2a: inside the corridor
    spec = ScenarioSpec(
        scenario_id="unit_meta",
        category="multi_stop",
        description="unit test corridor",
        origin_place="detroit",
        destination_place="chicago",
        initial_soc_kwh=40.0,
        max_chargers=None,
        station_filter="all",
    )
    scenario = build_scenario(
        spec,
        stations,
        tmp_path / "cache",
        road_backend="haversine",
        snapshot_path=snapshot,
    )
    root = write_scenario(scenario, tmp_path / "processed")
    meta = json.loads((root / "scenario_meta.json").read_text(encoding="utf-8"))

    graph_path = root / "canonical_graph.json"
    config_path = root / "trip_config.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))

    assert meta["road_backend"] == "haversine"
    assert meta["edge_count"] == len(graph["edges"]) > 0
    assert meta["edge_count_by_backend"] == {"osmnx": 0, "haversine": len(graph["edges"])}
    assert meta["afdc_snapshot_file"] == snapshot.name
    assert meta["afdc_snapshot_date"] == "2026-11-09"
    assert meta["afdc_snapshot_sha256"] == hashlib.sha256(snapshot.read_bytes()).hexdigest()
    assert meta["canonical_graph_sha256"] == hashlib.sha256(graph_path.read_bytes()).hexdigest()
    assert meta["trip_config_sha256"] == hashlib.sha256(config_path.read_bytes()).hexdigest()
    assert meta["fallback_rate_charger_count"] == 2


def test_scenario_meta_without_snapshot(tmp_path: Path) -> None:
    scenario = build_scenario(
        get_scenario_spec("sparse_detroit_chicago"),
        _corridor_stations(),
        tmp_path / "cache",
    )
    root = write_scenario(scenario, tmp_path / "processed")
    meta = json.loads((root / "scenario_meta.json").read_text(encoding="utf-8"))
    assert meta["afdc_snapshot_file"] is None
    assert meta["afdc_snapshot_date"] is None
    assert meta["fallback_rate_charger_count"] == 0


def test_effective_road_backend() -> None:
    assert effective_road_backend({"osmnx": 3, "haversine": 0}) == "osmnx"
    assert effective_road_backend({"osmnx": 0, "haversine": 3}) == "haversine"
    assert effective_road_backend({"osmnx": 2, "haversine": 1}) == "mixed"
    assert effective_road_backend({}) == "haversine"


def test_pyproject_declares_roads_extra() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    extras = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"][
        "optional-dependencies"
    ]
    assert any(req.startswith("osmnx") for req in extras["roads"])
