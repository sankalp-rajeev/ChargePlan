"""H7 SOC chart, timeline, and trip summary: series, text, chart spec, and the app panels."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from visualizer.data_io import VisualizerInputError, load_result, load_trip_config
from visualizer.soc_chart import (
    build_segments,
    build_trip_summary,
    render_soc_chart,
    render_timeline,
    render_timeline_diagram,
    soc_series,
)

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = ROOT / "data" / "synthetic"
MOCK_RESULT = ROOT / "visualizer" / "mock_results" / "algorithm_result.json"
TRIP = {
    "origin": "origin",
    "destination": "destination",
    "battery_capacity_kwh": 80,
    "initial_soc_kwh": 60,
    "reserve_kwh": 8,
    "consumption_kwh_per_mile": 0.4,
    "soc_step_kwh": 1.0,
}


def _drive(src: str, dst: str, miles: float, minutes: float, before: int, after: int) -> dict:
    return {
        "type": "drive",
        "from": src,
        "to": dst,
        "distance_miles": miles,
        "duration_minutes": minutes,
        "energy_kwh_discretized": before - after,
        "soc_before_kwh": before,
        "soc_after_kwh": after,
    }


def _charge(station: str, before: int, after: int, minutes: float) -> dict:
    return {
        "type": "charge",
        "station": station,
        "soc_before_kwh": before,
        "soc_after_kwh": after,
        "duration_minutes": minutes,
    }


def _result(status: str = "FEASIBLE") -> dict:
    """Origin -> A (charge) -> B (charge) -> destination, events cover the whole route."""
    if status == "INFEASIBLE":
        route, events = [], []
    else:
        route = ["origin", "A", "B", "destination"]
        events = [
            _drive("origin", "A", 100.0, 90.0, 60, 20),
            _charge("A", 20, 50, 20.0),
            _drive("A", "B", 100.0, 90.0, 50, 10),
            _charge("B", 10, 40, 25.0),
            _drive("B", "destination", 60.0, 50.0, 40, 16),
        ]
    return {
        "status": status,
        "algorithm": "label_setting",
        "total_time_minutes": 275.0,
        "driving_time_minutes": 230.0,
        "charging_time_minutes": 45.0,
        "total_distance_miles": 260.0,
        "number_of_charging_stops": 2 if route else 0,
        "soc_step_kwh": 1.0,
        "route_node_ids": route,
        "events": events,
        "metrics": {"runtime_ms": 12.34, "states_expanded": 12345, "peak_states_stored": 99},
    }


def _spec(result: dict, **kwargs) -> dict:
    return render_soc_chart(result, **kwargs).to_dict()


def test_segments_accumulate_distance_and_time():
    segments = build_segments(_result())

    assert [s.kind for s in segments] == ["drive", "charge", "drive", "charge", "drive"]
    assert [s.distance_end for s in segments] == [100.0, 100.0, 200.0, 200.0, 260.0]
    assert [s.time_end for s in segments] == [90.0, 110.0, 200.0, 225.0, 275.0]
    charge = segments[1]
    assert (charge.distance_start, charge.distance_end) == (100.0, 100.0)
    assert (charge.node_from, charge.node_to) == ("A", "A")


def test_series_has_two_points_per_event_and_soc_moves_the_right_way():
    rows = soc_series(_result())

    assert len(rows) == 10
    for event in range(5):
        start, end = (r for r in rows if r["event"] == event)
        assert (start["point"], end["point"]) == (0, 1)
        if start["kind"] == "Drive":
            assert end["soc_kwh"] < start["soc_kwh"]
            assert end["distance_mi"] > start["distance_mi"]
        else:
            assert end["soc_kwh"] > start["soc_kwh"]
            assert end["distance_mi"] == start["distance_mi"]
            assert end["time_min"] > start["time_min"]
    assert all(r["soc_pct"] is None and r["soc"] == r["soc_kwh"] for r in rows)


def test_series_converts_to_percent_with_a_trip_config():
    rows = soc_series(_result(), TRIP)

    assert rows[0]["soc_kwh"] == 60
    assert rows[0]["soc_pct"] == pytest.approx(75.0)
    assert rows[0]["soc"] == pytest.approx(75.0)


def _value_lists(node) -> list[list[dict]]:
    """Every inline data table in an Altair spec, wherever the library placed it."""
    found = []
    if isinstance(node, dict):
        if isinstance(node.get("values"), list):
            found.append(node["values"])
        for child in node.values():
            found += _value_lists(child)
    elif isinstance(node, list):
        for child in node:
            found += _value_lists(child)
    return found


def test_chart_plots_distance_or_time_on_x():
    by_distance = _spec(_result(), x_axis="distance")
    by_time = _spec(_result(), x_axis="time")

    def x_field(spec: dict) -> str:
        return spec["layer"][0]["encoding"]["x"]["field"]

    assert x_field(by_distance) == "distance_mi"
    assert x_field(by_time) == "time_min"
    assert by_distance["layer"][0]["encoding"]["y"]["title"] == "State of charge (kWh)"
    assert len(by_distance["layer"]) == 2


def test_chart_with_trip_adds_capacity_and_reserve_lines():
    spec = _spec(_result(), trip=TRIP)

    assert len(spec["layer"]) == 4
    assert spec["layer"][0]["encoding"]["y"]["title"] == "State of charge (% of battery)"
    rules = [rows[0] for rows in _value_lists(spec) if len(rows) == 1 and "label" in rows[0]]
    assert {r["label"]: r["soc"] for r in rules} == {"Battery capacity": 100.0, "Reserve": 10.0}


def test_chart_rejects_an_unknown_axis():
    with pytest.raises(ValueError, match="x_axis"):
        render_soc_chart(_result(), x_axis="speed")


def test_chart_for_an_infeasible_result_is_empty_but_valid():
    spec = _spec(_result("INFEASIBLE"))

    assert "No plan to chart" in spec["title"]
    assert all(len(rows) == 0 for rows in _value_lists(spec))


def test_timeline_has_one_line_per_event():
    lines = render_timeline(_result())

    assert len(lines) == 5
    assert lines[0] == "1. Drive origin -> A: 100.0 mi, 90.0 min, SOC 60 -> 20 kWh"
    assert lines[1] == "2. Charge at A: 20.0 min, SOC 20 -> 50 kWh (+30 kWh)"
    assert lines[4].startswith("5. Drive B -> destination: 60.0 mi, 50.0 min")


def test_timeline_shows_percent_with_a_trip_config():
    assert render_timeline(_result(), TRIP)[0].endswith("SOC 60 -> 20 kWh (75% -> 25%)")


def test_timeline_for_an_infeasible_result_is_empty():
    assert render_timeline(_result("INFEASIBLE")) == []
    assert render_timeline_diagram(_result("INFEASIBLE")) == ""


def test_diagram_follows_the_prd_layout():
    diagram = render_timeline_diagram(_result()).splitlines()

    assert diagram[:9] == [
        "origin (origin)",
        "SOC 60 kWh",
        "   |",
        "   | Drive 100.0 mi, 90.0 min",
        "   v",
        "A",
        "SOC 20 kWh",
        "   |",
        "   | Charge 20 kWh -> 50 kWh, 20.0 min",
    ]
    assert diagram[-2:] == ["destination (destination)", "SOC 16 kWh"]
    assert diagram.count("A") == 2


def test_diagram_starts_a_new_block_at_a_gap():
    result = _result()
    del result["events"][1]

    diagram = render_timeline_diagram(result)

    assert "\n\nA\nSOC 50 kWh" in diagram


def test_summary_lists_the_prd_fields_in_order():
    summary = build_trip_summary(_result())

    assert [label for label, _ in summary] == [
        "Total time",
        "Driving time",
        "Charging time",
        "Distance",
        "Charging stops",
        "Arrival SOC",
        "Algorithm runtime",
        "States expanded",
    ]
    values = dict(summary)
    assert values["Total time"] == "275.0 min (4 h 35 min)"
    assert values["Charging time"] == "45.0 min"
    assert values["Distance"] == "260.0 mi"
    assert values["Charging stops"] == "2"
    assert values["Arrival SOC"] == "16 kWh"
    assert values["Algorithm runtime"] == "12.3 ms"
    assert values["States expanded"] == "12,345"


def test_summary_arrival_soc_in_percent():
    assert dict(build_trip_summary(_result(), TRIP))["Arrival SOC"] == "16 kWh (20%)"


def test_arrival_soc_is_not_available_when_events_stop_short():
    result = _result()
    result["events"] = result["events"][:2]

    assert dict(build_trip_summary(result))["Arrival SOC"] == "n/a"


def test_arrival_soc_after_a_final_charge_at_the_destination():
    result = _result()
    result["events"].append(_charge("destination", 16, 20, 3.0))

    assert dict(build_trip_summary(result))["Arrival SOC"] == "20 kWh"


def test_summary_for_an_infeasible_result_still_reports_metrics():
    values = dict(build_trip_summary(_result("INFEASIBLE")))

    assert values["Arrival SOC"] == "n/a"
    assert values["States expanded"] == "12,345"


@pytest.mark.parametrize("path", [MOCK_RESULT, SYNTHETIC / "algorithm_result.json"])
def test_panels_handle_the_shipped_mock_results(path):
    result = load_result(path)

    assert _spec(result, x_axis="time")["layer"]
    assert len(render_timeline(result)) == len(result["events"])
    assert render_timeline_diagram(result).startswith("origin")
    assert dict(build_trip_summary(result))["Charging stops"] == "2"
    # the mock events stop at station_1, so the destination SOC is not known
    assert dict(build_trip_summary(result))["Arrival SOC"] == "n/a"


def test_panels_do_not_mutate_the_result():
    result = _result()
    before = copy.deepcopy(result)

    render_soc_chart(result, trip=TRIP)
    render_timeline(result, TRIP)
    render_timeline_diagram(result, TRIP)
    build_trip_summary(result, TRIP)

    assert result == before


def test_load_trip_config_validates(tmp_path):
    assert load_trip_config(SYNTHETIC / "trip_config.json")["battery_capacity_kwh"] == 70
    bad = tmp_path / "trip.json"
    bad.write_text(json.dumps({"origin": "o"}), encoding="utf-8")
    with pytest.raises(VisualizerInputError, match="trip_config.schema.json"):
        load_trip_config(bad)


def _app():
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(str(ROOT / "visualizer" / "app.py"), default_timeout=30).run()


def test_app_shows_summary_chart_and_timeline():
    app = _app()

    assert not app.exception
    assert not app.error
    assert {m.label for m in app.metric} >= {"Total time", "Arrival SOC", "States expanded"}
    assert len(app.get("vega_lite_chart")) == 1
    assert any("Drive origin -> station_1" in c.value for c in app.code)


def test_app_switches_the_chart_to_time():
    app = _app()
    app.radio[0].set_value("time").run()

    assert not app.exception
    assert len(app.get("vega_lite_chart")) == 1


def test_app_accepts_a_trip_config_and_reports_a_bad_one():
    app = _app()
    app.text_input[2].set_value(str(SYNTHETIC / "trip_config.json")).run()
    assert not app.exception
    assert not app.error
    assert any("%" in c.value for c in app.code)

    app.text_input[2].set_value("/nonexistent/trip.json").run()
    assert any("cannot read" in e.value for e in app.error)


def test_app_without_events_skips_the_chart(tmp_path):
    from streamlit.testing.v1 import AppTest

    path = tmp_path / "infeasible.json"
    path.write_text(json.dumps(_result("INFEASIBLE")), encoding="utf-8")
    app = AppTest.from_file(str(ROOT / "visualizer" / "app.py"), default_timeout=30).run()
    app.text_input[1].set_value(str(path)).run()

    assert not app.exception
    assert len(app.get("vega_lite_chart")) == 0
    assert any("no drive or charge events" in i.value for i in app.info)
    assert any(m.label == "Total time" for m in app.metric)
