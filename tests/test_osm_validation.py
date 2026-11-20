"""H12: haversine against OSMnx on one scenario, with a synthetic OSM network (no network)."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_pipeline.road_graph import (
    annotate_osm_graph,
    cache_name,
    haversine_metrics,
    route_on_osm_graph,
)
from experiments import osm_validation
from experiments.osm_validation import (
    ValidationError,
    check_snap_distances,
    compare_edges,
    download_drive_graph,
    filter_osm_xml_for_driving,
    format_report,
    is_drivable_way,
    plan_comparison,
    plan_summary,
    ratio_stats,
    run_validation,
)

ox = pytest.importorskip("osmnx")
nx = pytest.importorskip("networkx")

SCENARIO = "no_charge_detroit_ann_arbor"
LON_STEP = 0.05
LAT_STEP = 0.05
LONS = [round(-84.0 + i * LON_STEP, 4) for i in range(21)]
LATS = [round(42.1 + j * LAT_STEP, 4) for j in range(8)]


def grid_graph(detour: float = 1.0):
    """A bidirectional street grid over Detroit and Ann Arbor, as OSMnx would return it."""
    graph = nx.MultiDiGraph(crs="EPSG:4326")
    for i, lon in enumerate(LONS):
        for j, lat in enumerate(LATS):
            graph.add_node(i * 100 + j, x=lon, y=lat)

    def link(a: int, b: int) -> None:
        a_data, b_data = graph.nodes[a], graph.nodes[b]
        meters = osm_miles(a_data["y"], a_data["x"], b_data["y"], b_data["x"]) * 1609.344 * detour
        for start, end in ((a, b), (b, a)):
            graph.add_edge(start, end, length=meters, highway="motorway", maxspeed="65 mph")

    for i in range(len(LONS)):
        for j in range(len(LATS)):
            if i + 1 < len(LONS):
                link(i * 100 + j, (i + 1) * 100 + j)
            if j + 1 < len(LATS):
                link(i * 100 + j, i * 100 + j + 1)
    return annotate_osm_graph(ox, graph)


def osm_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from optimizer.model import haversine_miles

    return haversine_miles(lat1, lon1, lat2, lon2)


def station(station_id: str, lon: float, lat: float = 42.3) -> dict:
    return {
        "id": station_id,
        "name": station_id,
        "lat": lat,
        "lon": lon,
        "charger_class": "dc_fast",
        "charge_rate_kw": 150.0,
        "used_fallback_rate": False,
        "network": None,
        "city": None,
        "state": "MI",
        "street_address": None,
    }


@pytest.fixture
def snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "afdc_test_2026-11-09.json"
    path.write_text("[]", encoding="utf-8")
    stations = [station("s1", -83.20), station("s2", -83.40), station("s3", -83.60, lat=42.31)]
    monkeypatch.setattr(osm_validation, "load_stations", lambda _path: stations)
    return path


def test_route_on_osm_graph_matches_a_known_grid_path() -> None:
    graph = grid_graph()
    one_leg = route_on_osm_graph(ox, graph, 42.3, -83.5, 42.3, -83.4)
    assert one_leg["backend"] == "osmnx"
    straight = osm_miles(42.3, -83.5, 42.3, -83.4)
    assert one_leg["distance_miles"] == pytest.approx(straight, rel=0.01)
    assert one_leg["drive_time_minutes"] == pytest.approx(straight / 65 * 60, rel=0.02)
    assert route_on_osm_graph(ox, graph, 42.3, -83.5, 42.3, -83.5)["distance_miles"] == 0.0


def test_run_validation_compares_and_writes_everything(snapshot: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    summary = run_validation(SCENARIO, snapshot, out, osm_graph=grid_graph(detour=1.2))

    assert summary["scenario_id"] == SCENARIO
    assert summary["afdc_snapshot"]["date"] == "2026-11-09"
    edges = summary["edges"]
    assert edges["compared_edges"] > 0
    distance = edges["distance_ratio"]
    assert distance["n"] == edges["compared_edges"]
    assert distance["min"] <= distance["median"] <= distance["max"]
    # A 1.2 detour on a grid is about the haversine 1.25 factor, so ratios stay near 1.
    assert 0.8 < distance["median"] < 1.3
    assert edges["time_ratio"]["median"] > 0

    for name in ("osm_validation.json", "osm_validation.md", "edge_comparison.csv"):
        assert (out / name).is_file()
    rows = list(csv.DictReader((out / "edge_comparison.csv").open(encoding="utf-8")))
    assert len(rows) == edges["compared_edges"]
    assert list(rows[0]) == osm_validation.EDGE_COLUMNS
    assert json.loads((out / "osm_validation.json").read_text(encoding="utf-8")) == summary

    for backend in ("haversine", "osmnx"):
        meta = json.loads(
            (out / f"scenarios_{backend}" / SCENARIO / "scenario_meta.json").read_text(
                encoding="utf-8"
            )
        )
        assert meta["road_backend"] == backend
        assert (out / f"results_{backend}" / SCENARIO / "label_setting.json").is_file()
        assert (out / f"results_{backend}" / SCENARIO / "run_manifest.json").is_file()

    for algorithm, entry in summary["plans"].items():
        assert entry["haversine"]["status"] == entry["osmnx"]["status"] == "FEASIBLE", algorithm
        assert set(entry) >= {"route_changed", "stops_changed", "stop_count_changed"}


def test_osm_build_reads_only_osmnx_cache_entries(snapshot: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    run_validation(SCENARIO, snapshot, out, osm_graph=grid_graph())
    cache = out / "route_cache"
    osm_entries = list(cache.glob("route_*_osmnx.json"))
    assert osm_entries
    assert list(cache.glob("route_*_haversine.json"))
    payload = json.loads(osm_entries[0].read_text(encoding="utf-8"))
    assert payload["backend"] == "osmnx"
    origin, destination = payload["origin"], payload["destination"]
    straight = haversine_metrics(
        origin["lat"], origin["lon"], destination["lat"], destination["lon"]
    )["distance_miles"]
    assert payload["distance_miles"] >= straight / 1.25 - 1e-9
    assert (
        cache
        / cache_name(origin["lat"], origin["lon"], destination["lat"], destination["lon"], "osmnx")
    ).is_file()


def test_missing_snapshot_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="snapshot not found"):
        run_validation(SCENARIO, tmp_path / "absent.json", tmp_path / "out")
    assert osm_validation.main(["--snapshot", str(tmp_path / "absent.json")]) == 1


def test_unknown_scenario_and_missing_graphml_are_reported(snapshot: Path, tmp_path: Path) -> None:
    assert osm_validation.main(["--scenario", "nope", "--snapshot", str(snapshot)]) == 1
    code = osm_validation.main(
        [
            "--snapshot",
            str(snapshot),
            "--out",
            str(tmp_path / "out"),
            "--graphml",
            str(tmp_path / "missing.graphml"),
        ]
    )
    assert code == 1


def test_graphml_round_trip_gives_the_same_ratios(snapshot: Path, tmp_path: Path) -> None:
    graphml = tmp_path / "grid.graphml"
    ox.save_graphml(grid_graph(detour=1.1), graphml)
    first = run_validation(SCENARIO, snapshot, tmp_path / "a", osm_graph=grid_graph(detour=1.1))
    second = run_validation(SCENARIO, snapshot, tmp_path / "b", graphml=graphml)
    assert second["osm_graph"]["source"].startswith("file ")
    assert second["edges"]["distance_ratio"]["median"] == pytest.approx(
        first["edges"]["distance_ratio"]["median"], rel=1e-6
    )


def test_download_retries_with_backoff_then_succeeds() -> None:
    calls: list[int] = []

    def graph_from_bbox(**kwargs):
        calls.append(1)
        assert set(kwargs) == {"bbox", "network_type"}
        if len(calls) < 3:
            raise RuntimeError("502 Bad Gateway\nserver at capacity")
        return "graph"

    fake = SimpleNamespace(graph=SimpleNamespace(graph_from_bbox=graph_from_bbox))
    waits: list[float] = []
    nodes = [{"lat": 42.0, "lon": -83.0}, {"lat": 42.5, "lon": -84.0}]
    graph = download_drive_graph(
        fake, nodes, pad_deg=0.1, retries=4, wait_seconds=10, sleep=waits.append
    )
    assert graph == "graph"
    assert waits == [10, 20]


def test_download_failure_names_the_graphml_fallback() -> None:
    def graph_from_bbox(**_kwargs):
        raise RuntimeError("502 Bad Gateway")

    fake = SimpleNamespace(graph=SimpleNamespace(graph_from_bbox=graph_from_bbox))
    waits: list[float] = []
    with pytest.raises(ValidationError, match="--graphml") as info:
        download_drive_graph(
            fake,
            [{"lat": 42.0, "lon": -83.0}],
            pad_deg=0.1,
            retries=2,
            wait_seconds=1,
            sleep=waits.append,
        )
    assert "3 attempt" in str(info.value)
    assert waits == [1, 2]


def test_download_bbox_covers_every_node_with_padding() -> None:
    seen: dict = {}

    def graph_from_bbox(**kwargs):
        seen.update(kwargs)
        return "graph"

    fake = SimpleNamespace(graph=SimpleNamespace(graph_from_bbox=graph_from_bbox))
    nodes = [{"lat": 42.0, "lon": -83.0}, {"lat": 42.5, "lon": -84.0}]
    download_drive_graph(fake, nodes, pad_deg=0.1, retries=0, wait_seconds=0, sleep=lambda _s: None)
    west, south, east, north = seen["bbox"]
    assert (west, south, east, north) == pytest.approx((-84.1, 41.9, -82.9, 42.6))


def test_ratio_stats_reports_median_and_range() -> None:
    stats = ratio_stats([1.0, 1.1, 1.2, 1.3, 2.0])
    assert stats["n"] == 5
    assert stats["median"] == pytest.approx(1.2)
    assert (stats["min"], stats["max"]) == (1.0, 2.0)
    assert stats["min"] <= stats["p10"] <= stats["median"] <= stats["p90"] <= stats["max"]
    assert ratio_stats([1.5]) == {
        "n": 1,
        "median": 1.5,
        "min": 1.5,
        "max": 1.5,
        "p10": 1.5,
        "p90": 1.5,
    }
    assert ratio_stats([])["median"] is None


def edge(start: str, end: str, miles: float, minutes: float) -> dict:
    return {"from": start, "to": end, "distance_miles": miles, "drive_time_minutes": minutes}


def test_compare_edges_uses_common_edges_and_counts_the_rest() -> None:
    haversine = [edge("a", "b", 10, 10), edge("a", "c", 20, 20), edge("b", "c", 5, 5)]
    osm = [edge("a", "b", 12, 9), edge("b", "c", 5, 6), edge("c", "d", 1, 1)]
    result = compare_edges(haversine, osm)
    assert result["compared_edges"] == 2
    assert result["only_in_haversine"] == 1
    assert result["only_in_osmnx"] == 1
    by_pair = {(row["from"], row["to"]): row for row in result["rows"]}
    assert by_pair[("a", "b")]["distance_ratio"] == pytest.approx(1.2)
    assert by_pair[("a", "b")]["time_ratio"] == pytest.approx(0.9)
    assert result["distance_ratio"]["median"] == pytest.approx(1.1)
    assert math.isclose(result["time_ratio"]["max"], 1.2)


def feasible(stops: list[str], minutes: float, route: list[str]) -> dict:
    events = []
    for stop in stops:
        events.append({"type": "charge", "station": stop})
    return {
        "algorithm": "label_setting",
        "status": "FEASIBLE",
        "total_time_minutes": minutes,
        "total_distance_miles": 10.0,
        "number_of_charging_stops": len(stops),
        "route_node_ids": route,
        "events": events,
    }


def test_plan_summary_collapses_consecutive_charges_into_one_stop() -> None:
    result = feasible(["x", "x", "y"], 50.0, ["o", "x", "y", "d"])
    summary = plan_summary(result)
    assert summary["stops"] == ["x", "y"]
    infeasible = {
        "algorithm": "greedy_frc",
        "status": "INFEASIBLE",
        "route_node_ids": [],
        "events": [],
        "total_time_minutes": 0.0,
        "total_distance_miles": 0.0,
        "number_of_charging_stops": 0,
    }
    summary = plan_summary(infeasible)
    assert summary["total_time_minutes"] is None
    assert summary["number_of_charging_stops"] is None


def test_plan_comparison_flags_what_changed() -> None:
    same = plan_summary(feasible([], 40.0, ["o", "d"]))
    moved = plan_summary(feasible(["x"], 90.0, ["o", "x", "d"]))
    comparison = plan_comparison(
        {
            "haversine": {"label_setting": same, "greedy": same},
            "osmnx": {"label_setting": same, "greedy": moved},
        }
    )
    assert not comparison["label_setting"]["route_changed"]
    greedy = comparison["greedy"]
    assert greedy["route_changed"] and greedy["stops_changed"] and greedy["stop_count_changed"]
    assert not greedy["status_changed"]


def test_report_text_states_ratios_and_plan_changes(snapshot: Path, tmp_path: Path) -> None:
    summary = run_validation(SCENARIO, snapshot, tmp_path / "out", osm_graph=grid_graph())
    text = format_report(summary)
    assert "OSMnx / haversine" in text
    assert "| distance |" in text and "| drive time |" in text
    assert "label_setting: " in text and "greedy: " in text
    assert (tmp_path / "out" / "osm_validation.md").read_text(encoding="utf-8").strip() == text


def test_main_writes_each_scenario_to_its_own_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict = {}

    def fake_run_validation(scenario_id, snapshot, out, **kwargs):
        seen.update(scenario_id=scenario_id, out=out)
        raise ValidationError("stop here")

    monkeypatch.setattr(osm_validation, "run_validation", fake_run_validation)
    code = osm_validation.main(["--scenario", "one_stop_detroit_cleveland", "--out", str(tmp_path)])
    assert code == 1
    assert seen["out"] == tmp_path / "one_stop_detroit_cleveland"
    assert "stop here" in capsys.readouterr().err


OSM_XML = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6">
  <bounds minlat="42.0" minlon="-84.0" maxlat="42.5" maxlon="-83.0"/>
  <node id="1" lat="42.30" lon="-83.50"><tag k="highway" v="traffic_signals"/></node>
  <node id="2" lat="42.30" lon="-83.45"/>
  <node id="3" lat="42.30" lon="-83.40"/>
  <node id="4" lat="42.35" lon="-83.45"/>
  <node id="5" lat="42.40" lon="-83.45"/>
  <node id="6" lat="42.45" lon="-83.45"/>
  <way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/>
    <tag k="highway" v="motorway"/><tag k="maxspeed" v="65 mph"/></way>
  <way id="11"><nd ref="2"/><nd ref="4"/><tag k="highway" v="residential"/></way>
  <way id="12"><nd ref="4"/><nd ref="5"/><tag k="highway" v="footway"/></way>
  <way id="13"><nd ref="5"/><nd ref="6"/><tag k="highway" v="service"/></way>
  <way id="14"><nd ref="3"/><nd ref="5"/>
    <tag k="highway" v="primary"/><tag k="access" v="private"/></way>
  <relation id="20"><member type="way" ref="10" role="outer"/></relation>
</osm>
"""


def test_drive_filter_matches_the_osmnx_drive_rules() -> None:
    assert is_drivable_way({"highway": "motorway"})
    assert is_drivable_way({"highway": "residential", "oneway": "yes"})
    assert not is_drivable_way({"highway": "footway"})
    assert not is_drivable_way({"highway": "service"})
    assert not is_drivable_way({"highway": "primary", "access": "private"})
    assert not is_drivable_way({"highway": "primary", "motor_vehicle": "no"})
    assert not is_drivable_way({"highway": "primary", "area": "yes"})
    assert not is_drivable_way({"building": "yes"})


def test_filter_keeps_only_drivable_ways_and_their_nodes(tmp_path: Path) -> None:
    source = tmp_path / "area.osm"
    source.write_text(OSM_XML, encoding="utf-8")
    destination = tmp_path / "drive.osm"
    counts = filter_osm_xml_for_driving(source, destination)
    assert counts == {"ways": 2, "nodes": 4}
    text = destination.read_text(encoding="utf-8")
    assert 'way id="10"' in text and 'way id="11"' in text
    for dropped in ('way id="12"', 'way id="13"', 'way id="14"', "relation"):
        assert dropped not in text
    assert 'node id="6"' not in text and 'node id="5"' not in text
    graph = ox.graph_from_xml(destination, simplify=False)
    assert graph.number_of_nodes() == 4


def test_check_snap_distances_flags_nodes_outside_the_extract() -> None:
    graph = grid_graph()
    inside = [{"id": "a", "lat": 42.3, "lon": -83.5}]
    assert check_snap_distances(ox, graph, inside, 3.0)["max_snap_miles"] < 3.0
    outside = inside + [{"id": "far", "lat": 42.3, "lon": -81.5}]
    with pytest.raises(ValidationError, match="far") as info:
        check_snap_distances(ox, graph, outside, 3.0)
    assert "does not cover" in str(info.value)


def test_run_validation_fails_when_a_node_is_outside_the_osm_data(
    snapshot: Path, tmp_path: Path
) -> None:
    with pytest.raises(ValidationError, match="does not cover"):
        run_validation(
            SCENARIO, snapshot, tmp_path / "out", osm_graph=grid_graph(), max_snap_miles=0.01
        )


def test_run_validation_from_an_osm_xml_extract(
    snapshot: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "area.osm"
    source.write_text(OSM_XML, encoding="utf-8")
    seen: dict = {}

    def fake_check(ox_module, graph, nodes, max_miles):
        seen["graph_nodes"] = graph.number_of_nodes()
        raise ValidationError("stop after loading")

    monkeypatch.setattr(osm_validation, "check_snap_distances", fake_check)
    out = tmp_path / "out"
    with pytest.raises(ValidationError, match="stop after loading"):
        run_validation(SCENARIO, snapshot, out, osm_xml=source)
    assert seen["graph_nodes"] > 0
    assert (out / "osm_drive_graph.graphml").is_file()
    assert (out / "osm_drive_filtered.osm").is_file()
    with pytest.raises(ValidationError, match="--osm-xml file not found"):
        run_validation(SCENARIO, snapshot, tmp_path / "other", osm_xml=tmp_path / "none.osm")
