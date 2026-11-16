"""H8 greedy versus optimized comparison: metrics, infeasible cases, and the app panel."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from optimizer.battery_state_optimizer import solve as label_setting_solve
from optimizer.greedy_baseline import solve as greedy_solve
from visualizer.app import find_greedy_result
from visualizer.comparison import comparison_table, render_comparison
from visualizer.data_io import VisualizerInputError, load_result, validate

ROOT = Path(__file__).resolve().parents[1]
MOCK_DIR = ROOT / "visualizer" / "mock_results"
MOCK_OPTIMIZED = MOCK_DIR / "algorithm_result.json"
MOCK_GREEDY = MOCK_DIR / "algorithm_result_greedy.json"
SCENARIO_DIRS = sorted((ROOT / "results").glob("*/"))


def result(algorithm: str, status: str = "FEASIBLE", **overrides) -> dict:
    result = {
        "status": status,
        "algorithm": algorithm,
        "total_time_minutes": 100.0,
        "driving_time_minutes": 80.0,
        "charging_time_minutes": 20.0,
        "total_distance_miles": 120.0,
        "number_of_charging_stops": 1,
        "soc_step_kwh": 1.0,
        "route_node_ids": ["origin", "a", "destination"],
        "events": [],
        "metrics": {"runtime_ms": 1.0, "states_expanded": 3, "peak_states_stored": 3},
    }
    result.update(overrides)
    if status == "INFEASIBLE":
        result.update(route_node_ids=[], events=[])
    return result


def greedy(status: str = "FEASIBLE", **overrides) -> dict:
    return result("greedy_frc", status, **overrides)


def optimized(status: str = "FEASIBLE", **overrides) -> dict:
    return result("label_setting", status, **overrides)


def rows(comparison: dict) -> dict[str, dict]:
    return {row["metric"]: row for row in comparison["rows"]}


def test_rows_follow_the_prd_table_order() -> None:
    comparison = render_comparison(greedy(), optimized())
    assert [row["metric"] for row in comparison["rows"]] == [
        "Total time",
        "Driving time",
        "Charging time",
        "Stops",
        "Distance",
    ]


def test_feasible_pair_reports_each_metric_and_difference() -> None:
    greedy = greedy(
        total_time_minutes=200.0,
        driving_time_minutes=150.0,
        charging_time_minutes=50.0,
        number_of_charging_stops=1,
        total_distance_miles=100.0,
    )
    optimized = optimized(
        total_time_minutes=150.0,
        driving_time_minutes=130.0,
        charging_time_minutes=20.0,
        number_of_charging_stops=3,
        total_distance_miles=110.5,
    )
    comparison = render_comparison(greedy, optimized)
    rows = rows(comparison)

    assert rows["Total time"]["greedy_value"] == 200.0
    assert rows["Total time"]["optimized_value"] == 150.0
    assert rows["Total time"]["delta"] == pytest.approx(-50.0)
    assert rows["Total time"]["difference"] == "-50.0 min (-25.0%)"
    assert rows["Charging time"]["difference"] == "-30.0 min (-60.0%)"
    assert rows["Stops"]["greedy"] == "1"
    assert rows["Stops"]["optimized"] == "3"
    assert rows["Stops"]["difference"] == "+2"
    assert rows["Distance"]["greedy"] == "100.0 mi"
    assert rows["Distance"]["difference"] == "+10.5 mi"
    assert comparison["greedy_status"] == comparison["optimized_status"] == "FEASIBLE"
    assert comparison["severity"] == "success"
    assert "saves" in comparison["verdict"]
    assert "25.0%" in comparison["verdict"]


def test_equal_plans_report_no_difference() -> None:
    comparison = render_comparison(greedy(), optimized())
    assert comparison["severity"] == "info"
    assert "same total time" in comparison["verdict"]
    assert all(row["delta"] == 0 for row in comparison["rows"])
    assert rows(comparison)["Total time"]["difference"] == "+0.0 min (+0.0%)"


def test_optimized_slower_than_greedy_is_flagged() -> None:
    comparison = render_comparison(greedy(total_time_minutes=90.0), optimized())
    assert comparison["severity"] == "warning"
    assert "slower" in comparison["verdict"]


def test_greedy_infeasible_optimized_feasible_shows_no_greedy_plan() -> None:
    # An infeasible result still reports metrics; they must not be shown as a 0-minute plan.
    greedy = greedy("INFEASIBLE", total_time_minutes=0.0, total_distance_miles=0.0)
    optimized = optimized(total_time_minutes=321.5, number_of_charging_stops=2)
    comparison = render_comparison(greedy, optimized)

    assert comparison["greedy_status"] == "INFEASIBLE"
    assert comparison["optimized_status"] == "FEASIBLE"
    assert comparison["severity"] == "success"
    assert "no feasible plan" in comparison["verdict"]
    assert "321.5 min" in comparison["verdict"]
    assert "2 charging stop" in comparison["verdict"]
    for row in comparison["rows"]:
        assert row["greedy"] == "n/a"
        assert row["greedy_value"] is None
        assert row["delta"] is None
        assert row["difference"] == "n/a"
        assert row["optimized"] != "n/a"


def test_optimized_infeasible_greedy_feasible_is_flagged() -> None:
    comparison = render_comparison(greedy(), optimized("INFEASIBLE"))
    assert comparison["severity"] == "warning"
    assert all(row["optimized"] == "n/a" and row["delta"] is None for row in comparison["rows"])
    assert all(row["greedy"] != "n/a" for row in comparison["rows"])


def test_both_infeasible() -> None:
    comparison = render_comparison(greedy("INFEASIBLE"), optimized("INFEASIBLE"))
    assert comparison["severity"] == "info"
    assert "Neither" in comparison["verdict"]
    assert all(row["greedy"] == row["optimized"] == "n/a" for row in comparison["rows"])


def test_long_durations_use_the_h7_hours_format() -> None:
    comparison = render_comparison(greedy(total_time_minutes=501.5), optimized())
    assert rows(comparison)["Total time"]["greedy"] == "501.5 min (8 h 22 min)"


@pytest.mark.parametrize("algorithm", ["label_setting", "astar", "exact"])
def test_any_optimizer_algorithm_is_accepted(algorithm: str) -> None:
    comparison = render_comparison(greedy(), optimized(algorithm))
    assert comparison["optimized_algorithm"] == algorithm


def test_wrong_algorithm_roles_are_rejected() -> None:
    with pytest.raises(VisualizerInputError, match="greedy_frc"):
        render_comparison(optimized(), optimized())
    with pytest.raises(VisualizerInputError, match="must not be"):
        render_comparison(greedy(), greedy())


def test_inputs_are_not_modified() -> None:
    greedy, optimized = greedy("INFEASIBLE"), optimized()
    before = copy.deepcopy((greedy, optimized))
    render_comparison(greedy, optimized)
    assert (greedy, optimized) == before


def test_comparison_table_has_the_prd_columns() -> None:
    table = comparison_table(render_comparison(greedy(), optimized()))
    assert list(table[0]) == ["Metric", "Greedy (FRC)", "Optimized (label_setting)", "Difference"]
    assert [row["Metric"] for row in table][:2] == ["Total time", "Driving time"]


def test_t16_greedy_infeasible_instance_through_the_real_solvers() -> None:
    def node(node_id: str, kind: str, rate: float | None, lon: float) -> dict:
        return {"id": node_id, "type": kind, "lat": 42.0, "lon": lon, "charge_rate_kw": rate}

    def edge(start: str, end: str, miles: float, minutes: float) -> dict:
        return {
            "from": start,
            "to": end,
            "distance_miles": miles,
            "drive_time_minutes": minutes,
        }

    graph = {
        "nodes": [
            node("origin", "origin", None, -83.0),
            node("away", "charger", 60, -82.0),
            node("destination", "destination", None, -85.0),
        ],
        "edges": [
            edge("origin", "destination", 110, 110),
            edge("origin", "away", 52, 50),
            edge("away", "destination", 160, 160),
        ],
    }
    config = {
        "origin": "origin",
        "destination": "destination",
        "battery_capacity_kwh": 200,
        "initial_soc_kwh": 60,
        "reserve_kwh": 0,
        "consumption_kwh_per_mile": 1,
        "soc_step_kwh": 1,
    }
    greedy = greedy_solve(graph, config)
    optimized = label_setting_solve(graph, config)
    validate(greedy, "algorithm_result.schema.json")
    validate(optimized, "algorithm_result.schema.json")
    assert greedy["status"] == "INFEASIBLE"
    assert optimized["status"] == "FEASIBLE"

    comparison = render_comparison(greedy, optimized)
    assert comparison["severity"] == "success"
    assert all(row["greedy"] == "n/a" for row in comparison["rows"])


def test_mock_results_compare_and_validate() -> None:
    greedy = load_result(MOCK_GREEDY)
    optimized = load_result(MOCK_OPTIMIZED)
    comparison = render_comparison(greedy, optimized)
    assert greedy["algorithm"] == "greedy_frc"
    assert comparison["severity"] == "success"
    assert rows(comparison)["Total time"]["delta"] < 0


@pytest.mark.parametrize("scenario", SCENARIO_DIRS, ids=lambda p: p.name)
def test_precomputed_scenario_results_compare(scenario: Path) -> None:
    greedy = load_result(scenario / "algorithm_result_greedy.json")
    optimized = load_result(scenario / "algorithm_result_label_setting.json")
    comparison = render_comparison(greedy, optimized)
    assert len(comparison["rows"]) == 5
    assert comparison["severity"] in {"success", "info"}
    assert find_greedy_result(str(scenario / "algorithm_result_label_setting.json"), "") == (
        scenario / "algorithm_result_greedy.json"
    )


def test_find_greedy_result_prefers_explicit_path_and_tolerates_missing_sibling(
    tmp_path: Path,
) -> None:
    result = tmp_path / "algorithm_result.json"
    assert find_greedy_result(str(result), "") is None
    assert find_greedy_result(str(result), "  other.json ") == Path("other.json")
    sibling = tmp_path / "algorithm_result_greedy.json"
    sibling.write_text("{}", encoding="utf-8")
    assert find_greedy_result(str(result), "") == sibling


def _run_app(monkeypatch: pytest.MonkeyPatch, *args: str):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(sys, "argv", ["app.py", *args])
    return AppTest.from_file(str(ROOT / "visualizer" / "app.py"), default_timeout=30).run()


def _table_text(app) -> str:
    return json.dumps([t.value.to_dict() for t in app.table])


def test_app_shows_comparison_for_the_default_mock_results(monkeypatch) -> None:
    app = _run_app(monkeypatch)
    assert not app.exception
    assert not app.error
    assert any(s.value == "Greedy vs optimized" for s in app.subheader)
    assert len(app.table) == 1
    table = app.table[0].value
    assert list(table.columns) == [
        "Metric",
        "Greedy (FRC)",
        "Optimized (label_setting)",
        "Difference",
    ]
    assert list(table["Metric"]) == [
        "Total time",
        "Driving time",
        "Charging time",
        "Stops",
        "Distance",
    ]
    assert any("saves" in s.value for s in app.success)


def test_app_without_a_greedy_result_shows_a_hint(monkeypatch, tmp_path: Path) -> None:
    alone = tmp_path / "algorithm_result.json"
    alone.write_text(MOCK_OPTIMIZED.read_text(encoding="utf-8"), encoding="utf-8")
    app = _run_app(monkeypatch, "--result", str(alone))
    assert not app.exception
    assert len(app.table) == 0
    assert any("No greedy baseline" in i.value for i in app.info)
    assert len(app.get("vega_lite_chart")) == 1


def test_app_handles_greedy_infeasible(monkeypatch, tmp_path: Path) -> None:
    infeasible = greedy("INFEASIBLE", total_time_minutes=0.0, total_distance_miles=0.0)
    path = tmp_path / "greedy.json"
    path.write_text(json.dumps(infeasible), encoding="utf-8")
    app = _run_app(monkeypatch, "--greedy", str(path))
    assert not app.exception
    assert not app.error
    assert any("no feasible plan" in s.value for s in app.success)
    assert "n/a" in _table_text(app)
    assert len(app.get("vega_lite_chart")) == 1


def test_app_reports_a_bad_greedy_file_without_hiding_the_rest(monkeypatch, tmp_path: Path) -> None:
    bad = tmp_path / "greedy.json"
    bad.write_text("not json", encoding="utf-8")
    app = _run_app(monkeypatch, "--greedy", str(bad))
    assert not app.exception
    assert any("not valid JSON" in e.value for e in app.error)
    assert len(app.get("vega_lite_chart")) == 1


def test_app_with_a_greedy_main_result_asks_for_an_optimized_one(monkeypatch) -> None:
    app = _run_app(monkeypatch, "--result", str(MOCK_GREEDY))
    assert not app.exception
    assert not app.error
    assert any("main result is the greedy baseline" in i.value for i in app.info)
    assert len(app.table) == 0
