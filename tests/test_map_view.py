"""H6 route map: node roles, rendered markers, file loading, and the app page."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from visualizer.data_io import VisualizerInputError, load_graph, load_result
from visualizer.map_view import build_map_model, main, render_map

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = ROOT / "data" / "synthetic"
MOCK_RESULT = ROOT / "visualizer" / "mock_results" / "algorithm_result.json"


def _node(node_id: str, kind: str, lat: float, lon: float, rate: float | None) -> dict:
    return {"id": node_id, "type": kind, "lat": lat, "lon": lon, "charge_rate_kw": rate}


def _graph() -> dict:
    """Origin, destination, and chargers A (used), B (passed, not used), C and D (unused)."""
    return {
        "nodes": [
            _node("origin", "origin", 42.0, -83.0, None),
            _node("A", "charger", 42.5, -84.0, 150.0),
            _node("B", "charger", 42.7, -85.0, 50.0),
            _node("C", "charger", 43.0, -84.0, 50.0),
            _node("D", "charger", 41.5, -84.5, 24.0),
            _node("destination", "destination", 42.9, -86.0, None),
        ],
        "edges": [],
    }


def _drive(src: str, dst: str, before: int, after: int) -> dict:
    return {
        "type": "drive",
        "from": src,
        "to": dst,
        "distance_miles": 50.0,
        "duration_minutes": 45.0,
        "energy_kwh_discretized": before - after,
        "soc_before_kwh": before,
        "soc_after_kwh": after,
    }


def _result(status: str = "FEASIBLE") -> dict:
    if status == "INFEASIBLE":
        route, events = [], []
    else:
        route = ["origin", "A", "B", "destination"]
        events = [
            _drive("origin", "A", 60, 40),
            {
                "type": "charge",
                "station": "A",
                "soc_before_kwh": 40,
                "soc_after_kwh": 70,
                "duration_minutes": 12.0,
            },
            _drive("A", "B", 70, 50),
            _drive("B", "destination", 50, 30),
        ]
    return {
        "status": status,
        "algorithm": "label_setting",
        "total_time_minutes": 150.0,
        "driving_time_minutes": 138.0,
        "charging_time_minutes": 12.0,
        "total_distance_miles": 150.0,
        "number_of_charging_stops": 1 if route else 0,
        "soc_step_kwh": 1.0,
        "route_node_ids": route,
        "events": events,
        "metrics": {"runtime_ms": 1.0, "states_expanded": 1, "peak_states_stored": 1},
    }


def _ids(nodes) -> list[str]:
    return [n["id"] for n in nodes]


def test_roles_separate_endpoints_stops_pass_through_and_unused():
    model = build_map_model(_graph(), _result())

    assert model.feasible
    assert _ids(model.endpoints) == ["origin", "destination"]
    assert [(s.order, s.node["id"]) for s in model.stops] == [(1, "A")]
    assert _ids(model.pass_through) == ["B"]
    assert _ids(model.unused) == ["C", "D"]
    assert _ids(model.route) == ["origin", "A", "B", "destination"]
    assert [leg["to"] for leg in model.legs] == ["A", "B", "destination"]


def test_stops_are_numbered_in_visit_order_and_merge_repeat_visits():
    result = _result()
    result["route_node_ids"] = ["origin", "B", "A", "destination"]
    result["events"] = [
        _drive("origin", "B", 60, 30),
        {
            "type": "charge",
            "station": "B",
            "soc_before_kwh": 30,
            "soc_after_kwh": 40,
            "duration_minutes": 5.0,
        },
        _drive("B", "A", 40, 20),
        {
            "type": "charge",
            "station": "A",
            "soc_before_kwh": 20,
            "soc_after_kwh": 50,
            "duration_minutes": 9.0,
        },
        {
            "type": "charge",
            "station": "A",
            "soc_before_kwh": 50,
            "soc_after_kwh": 60,
            "duration_minutes": 3.0,
        },
        _drive("A", "destination", 60, 30),
    ]

    model = build_map_model(_graph(), result)

    assert [(s.order, s.node["id"], len(s.charges)) for s in model.stops] == [
        (1, "B", 1),
        (2, "A", 2),
    ]
    assert model.pass_through == ()


def test_origin_charge_is_not_a_selected_stop():
    graph = _graph()
    graph["nodes"][0]["charge_rate_kw"] = 7.0
    result = _result()
    result["events"].insert(
        0,
        {
            "type": "charge",
            "station": "origin",
            "soc_before_kwh": 10,
            "soc_after_kwh": 60,
            "duration_minutes": 30.0,
        },
    )

    model = build_map_model(graph, result)

    assert _ids(s.node for s in model.stops) == ["A"]
    assert len(model.charges_at_endpoints["origin"]) == 1
    assert "Charge 10 &rarr; 60 kWh" in render_map(graph, result).get_root().render()


def test_infeasible_result_has_no_route_but_keeps_chargers():
    model = build_map_model(_graph(), _result("INFEASIBLE"))

    assert not model.feasible
    assert model.route == () and model.legs == () and model.stops == ()
    assert _ids(model.unused) == ["A", "B", "C", "D"]

    page = render_map(_graph(), _result("INFEASIBLE")).get_root().render()
    assert "INFEASIBLE" in page
    assert "Optimized route" in page


def test_unknown_node_ids_are_rejected():
    result = _result()
    result["route_node_ids"][1] = "missing"
    with pytest.raises(VisualizerInputError, match="missing"):
        build_map_model(_graph(), result)

    result = _result()
    result["events"][1]["station"] = "ghost"
    with pytest.raises(VisualizerInputError, match="ghost"):
        build_map_model(_graph(), result)


def test_rendered_map_has_distinct_markers_and_legend():
    page = render_map(_graph(), _result()).get_root().render()

    assert page.count('"icon": "play"') == 1
    assert page.count('"icon": "flag"') == 1
    assert "Charging stop 1" in page
    assert "Charge 40 &rarr; 70 kWh (12.0 min)" in page
    assert "Unused charger: C (50 kW)" in page
    assert "On route, no charge: B (50 kW)" in page
    assert "Drive 50.0 mi, 45.0 min" in page
    for label in (
        "Origin",
        "Destination",
        "Selected charging stop",
        "Optimized route",
        "Unused charger",
    ):
        assert label in page
    assert "INFEASIBLE" not in page


def test_node_ids_are_escaped():
    graph = _graph()
    graph["nodes"][2]["id"] = "<script>alert(1)</script>"
    result = _result()
    result["route_node_ids"][2] = "<script>alert(1)</script>"
    result["events"][2]["to"] = "<script>alert(1)</script>"
    result["events"][3]["from"] = "<script>alert(1)</script>"

    page = render_map(graph, result).get_root().render()

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_mock_result_renders_on_the_synthetic_graph():
    graph = load_graph(SYNTHETIC / "canonical_graph.json")
    result = load_result(MOCK_RESULT)

    model = build_map_model(graph, result)

    # The frozen sample lists only one charge event although it reports two stops.
    assert [s.node["id"] for s in model.stops] == ["station_1"]
    assert _ids(model.pass_through) == ["station_2"]
    assert model.unused == ()
    assert "Charging stop 1" in render_map(graph, result).get_root().render()


def test_loaders_validate_against_the_schemas(tmp_path):
    bad = copy.deepcopy(_result())
    del bad["metrics"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(VisualizerInputError, match="algorithm_result.schema.json"):
        load_result(path)

    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(VisualizerInputError, match="not valid JSON"):
        load_graph(path)

    with pytest.raises(VisualizerInputError, match="cannot read"):
        load_graph(tmp_path / "absent.json")


def test_cli_writes_standalone_html(tmp_path, capsys):
    out = tmp_path / "nested" / "map.html"
    code = main(
        [
            "--graph",
            str(SYNTHETIC / "canonical_graph.json"),
            "--result",
            str(MOCK_RESULT),
            "--out",
            str(out),
        ]
    )

    assert code == 0
    assert "leaflet" in out.read_text(encoding="utf-8").lower()
    assert "wrote" in capsys.readouterr().out


def test_cli_reports_bad_input(tmp_path, capsys):
    code = main(
        [
            "--graph",
            str(tmp_path / "absent.json"),
            "--result",
            str(MOCK_RESULT),
            "--out",
            str(tmp_path / "map.html"),
        ]
    )

    assert code == 1
    assert "error:" in capsys.readouterr().err


def test_app_renders_the_default_files():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(ROOT / "visualizer" / "app.py"), default_timeout=30).run()

    assert not app.exception
    assert not app.error
    assert any("2 charging stop" in c.value for c in app.caption)


def test_app_shows_an_error_for_a_missing_file():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(ROOT / "visualizer" / "app.py"), default_timeout=30).run()
    app.text_input[1].set_value("/nonexistent/result.json").run()

    assert not app.exception
    assert any("cannot read" in e.value for e in app.error)
