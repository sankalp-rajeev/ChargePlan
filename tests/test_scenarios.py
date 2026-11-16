"""Real-data scenario builders (H5), tested function by function."""

from __future__ import annotations

import hashlib
import json
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
    select_dc_fast_stations,
    select_mixed_speed_stations,
    select_sparse_stations,
    snapshot_file_meta,
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
        _station("l2a", -84.0, charger_class="level2", rate=7.2, used_fallback_rate=True),
        _station("dc2", -84.5, charger_class="dc_fast", rate=150, used_fallback_rate=True),
        _station("l2b", -85.0, charger_class="level2", rate=7.2),
        _station("dc3", -85.5, charger_class="dc_fast", rate=150),
        _station("l2c", -86.0, charger_class="level2", rate=7.2),
        _station("dc4", -86.5, charger_class="dc_fast", rate=150),
        _station("l2d", -87.0, charger_class="level2", rate=7.2),
        _station("off", -85.0, charger_class="dc_fast", rate=150, lat=46.0),
    ]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def test_snapshot_file_meta_parses_dated_filename() -> None:
    meta = snapshot_file_meta("data/raw/afdc_ev_midwest_2026-11-09.json")
    assert meta["afdc_snapshot_file"] == "afdc_ev_midwest_2026-11-09.json"
    assert meta["afdc_snapshot_date"] == "2026-11-09"
    assert snapshot_file_meta(None) == {
        "afdc_snapshot_file": None,
        "afdc_snapshot_date": None,
    }


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


def test_select_dc_fast_stations_drops_level2() -> None:
    origin = place("detroit")
    destination = place("chicago")
    selected = select_dc_fast_stations(
        _corridor_stations(),
        origin,
        destination,
        max_chargers=4,
    )
    assert len(selected) == 4
    assert {station["charger_class"] for station in selected} == {"dc_fast"}
    assert all(station["id"].startswith("dc") for station in selected)


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
    sparse = get_scenario_spec("sparse_detroit_chicago")
    selected = filter_stations_for_scenario(
        _corridor_stations(),
        sparse,
        place("detroit"),
        place("chicago"),
    )
    assert len(selected) == 6

    multi = get_scenario_spec("multi_stop_detroit_chicago")
    assert multi.station_filter == "dc_fast"
    dc_only = filter_stations_for_scenario(
        _corridor_stations(),
        multi,
        place("detroit"),
        place("chicago"),
    )
    assert {station["charger_class"] for station in dc_only} == {"dc_fast"}


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
    multi = next(spec for spec in specs if spec.scenario_id == "multi_stop_detroit_chicago")
    assert multi.station_filter == "dc_fast"


def test_build_and_write_scenario_meta_fields(tmp_path: Path) -> None:
    spec = ScenarioSpec(
        scenario_id="unit_multi_stop",
        category="multi_stop",
        description="unit test corridor",
        origin_place="detroit",
        destination_place="chicago",
        initial_soc_kwh=40.0,
        max_chargers=4,
        station_filter="dc_fast",
    )
    snapshot = tmp_path / "afdc_ev_midwest_2026-11-09.json"
    snapshot.write_text("{}", encoding="utf-8")
    scenario = build_scenario(
        spec,
        _corridor_stations(),
        tmp_path / "cache",
        road_backend="haversine",
        snapshot_path=snapshot,
    )
    assert scenario["charger_count"] == 4
    assert scenario["config"]["initial_soc_kwh"] == 40.0
    assert scenario["road_backend"] == "haversine"
    assert scenario["edge_backend_counts"]["haversine"] > 0
    assert scenario["edge_backend_counts"]["osmnx"] == 0
    assert scenario["afdc_snapshot_file"] == "afdc_ev_midwest_2026-11-09.json"
    assert scenario["afdc_snapshot_date"] == "2026-11-09"
    assert scenario["fallback_rate_charger_count"] == 1  # dc2 only among selected DC-fast

    root = write_scenario(scenario, tmp_path / "processed")
    graph = load_graph(root / "canonical_graph.json")
    config = load_config(root / "trip_config.json")
    assert len(graph["nodes"]) == 6  # origin + 4 chargers + destination
    assert config["origin"] == "origin"
    assert {node["id"] for node in graph["nodes"] if node["type"] == "charger"} <= {
        "dc1",
        "dc2",
        "dc3",
        "dc4",
    }

    meta = json.loads((root / "scenario_meta.json").read_text(encoding="utf-8"))
    assert meta["road_backend"] == "haversine"
    assert meta["edge_backend_counts"] == scenario["edge_backend_counts"]
    assert meta["afdc_snapshot_file"] == "afdc_ev_midwest_2026-11-09.json"
    assert meta["afdc_snapshot_date"] == "2026-11-09"
    assert meta["graph_sha256"] == _sha(root / "canonical_graph.json")
    assert meta["config_sha256"] == _sha(root / "trip_config.json")
    assert meta["fallback_rate_charger_count"] == 1
    assert sum(meta["edge_backend_counts"].values()) == len(graph["edges"])


def test_build_all_scenarios_writes_requested_ids(tmp_path: Path) -> None:
    snapshot = tmp_path / "afdc_ev_midwest_2026-11-09.json"
    snapshot.write_text("{}", encoding="utf-8")
    written = build_all_scenarios(
        _corridor_stations(),
        tmp_path / "cache",
        tmp_path / "processed",
        road_backend="haversine",
        scenario_ids=["sparse_detroit_chicago", "no_charge_detroit_ann_arbor"],
        snapshot_path=snapshot,
    )
    assert {path.name for path in written} == {
        "sparse_detroit_chicago",
        "no_charge_detroit_ann_arbor",
    }
    for path in written:
        load_graph(path / "canonical_graph.json")
        load_config(path / "trip_config.json")
        meta = json.loads((path / "scenario_meta.json").read_text(encoding="utf-8"))
        assert meta["graph_sha256"] == _sha(path / "canonical_graph.json")
        assert meta["config_sha256"] == _sha(path / "trip_config.json")
        assert meta["afdc_snapshot_date"] == "2026-11-09"
