"""S9 real-data runner: offline checks on two tiny scenario folders."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import jsonschema
import pytest

from experiments import real_data
from experiments.metrics import COLUMNS, read_csv
from experiments.plan_check import check_plan
from optimizer import exact_solver
from optimizer.model import load_config, load_graph

ROOT = Path(__file__).resolve().parents[1]
RESULT_SCHEMA = json.loads(
    (ROOT / "contracts" / "algorithm_result.schema.json").read_text(encoding="utf-8")
)
ALGORITHM_FILES = ("label_setting", "astar", "greedy", "exact")


def _node(node_id: str, kind: str, lon: float, rate: float | None) -> dict:
    return {"id": node_id, "type": kind, "lat": 42.0, "lon": lon, "charge_rate_kw": rate}


def _graph() -> dict:
    """A -20mi-> C(60 kW) -30mi-> B. One charge is needed from a 25 kWh start."""
    return {
        "nodes": [
            _node("A", "origin", -83.0, None),
            _node("C", "charger", -83.1, 60),
            _node("B", "destination", -83.2, None),
        ],
        "edges": [
            {"from": "A", "to": "C", "distance_miles": 20, "drive_time_minutes": 20},
            {"from": "C", "to": "B", "distance_miles": 30, "drive_time_minutes": 30},
        ],
    }


def _config(**updates: float) -> dict:
    # 40 kWh in 5 kWh steps is 8 steps, the largest the exact solver accepts.
    config = {
        "origin": "A",
        "destination": "B",
        "battery_capacity_kwh": 40,
        "initial_soc_kwh": 25,
        "reserve_kwh": 5,
        "consumption_kwh_per_mile": 1,
        "soc_step_kwh": 5,
    }
    config.update(updates)
    return config


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _scenario(
    root: Path,
    name: str,
    config: dict,
    *,
    meta: dict | None = None,
    with_hashes: bool = True,
) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    _write(folder / "canonical_graph.json", _graph())
    _write(folder / "trip_config.json", config)
    if meta is not None:
        meta = dict(meta)
        if with_hashes:
            meta["graph_sha256"] = _sha(folder / "canonical_graph.json")
            meta["config_sha256"] = _sha(folder / "trip_config.json")
        _write(folder / "scenario_meta.json", meta)
    return folder


@pytest.fixture
def scenarios(tmp_path: Path) -> Path:
    root = tmp_path / "processed"
    root.mkdir()
    meta = {"road_backend": "haversine", "category": "one_stop"}
    _scenario(root, "feasible_one_stop", _config(), meta=meta)
    _scenario(
        root,
        "infeasible_low_soc",
        _config(initial_soc_kwh=15),
        meta={"road_backend": "haversine", "category": "no_route"},
    )
    return root


def _patch_results(
    monkeypatch: pytest.MonkeyPatch, algorithm: str, change: Callable[[dict], dict]
) -> None:
    real = real_data.run_algorithm

    def patched(name: str, graph: dict, config: dict) -> dict:
        result = real(name, graph, config)
        if name == algorithm:
            return change(copy.deepcopy(result))
        return result

    monkeypatch.setattr(real_data, "run_algorithm", patched)


def test_runner_writes_verified_outputs_for_a_feasible_and_an_infeasible_scenario(
    scenarios: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    reports = real_data.run_scenarios(scenarios, out, repeats=2)
    assert [report.scenario_id for report in reports] == ["feasible_one_stop", "infeasible_low_soc"]
    assert all(report.ok for report in reports), [r.errors for r in reports]

    validator = jsonschema.Draft202012Validator(RESULT_SCHEMA)
    for scenario_id in ("feasible_one_stop", "infeasible_low_soc"):
        folder = scenarios / scenario_id
        graph = load_graph(folder / "canonical_graph.json")
        config = load_config(folder / "trip_config.json")
        results = {}
        for name in ALGORITHM_FILES:
            payload = json.loads((out / scenario_id / f"{name}.json").read_text(encoding="utf-8"))
            assert list(validator.iter_errors(payload)) == [], (scenario_id, name)
            assert check_plan(graph, config, payload) == [], (scenario_id, name)
            results[name] = payload
        for key in ("status", "total_time_minutes", "number_of_charging_stops",
                    "total_distance_miles"):
            assert results["astar"][key] == results["label_setting"][key]
            assert results["exact"][key] == results["label_setting"][key]
        manifest = json.loads(
            (out / scenario_id / "run_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["status"] == "OK"
        assert manifest["scenario_id"] == scenario_id
        assert manifest["graph_sha256"] == _sha(folder / "canonical_graph.json")
        assert manifest["config_sha256"] == _sha(folder / "trip_config.json")
        assert manifest["scenario_meta"] == json.loads(
            (folder / "scenario_meta.json").read_text(encoding="utf-8")
        )
        assert manifest["python_version"].startswith("3.")
        assert manifest["generated_utc"].endswith("Z")
        assert manifest["git_commit"]
        assert manifest["platform"]
        assert set(manifest["algorithms"]) == set(ALGORITHM_FILES)
        for name, entry in manifest["algorithms"].items():
            assert entry["status"] == results[name]["status"]
            assert entry["plan_check"] == {"passed": True, "problems": []}
            assert entry["runtime_ms"] == results[name]["metrics"]["runtime_ms"]
            assert entry["states_expanded"] == results[name]["metrics"]["states_expanded"]
        assert manifest["algorithms"]["astar"]["cross_checks"][0]["passed"] is True
        assert manifest["algorithms"]["exact"]["cross_checks"][0]["passed"] is True
        assert manifest["algorithms"]["greedy"]["cross_checks"][0]["passed"] is True

    feasible = json.loads((out / "feasible_one_stop" / "label_setting.json").read_text("utf-8"))
    assert feasible["status"] == "FEASIBLE"
    assert feasible["number_of_charging_stops"] == 1
    assert feasible["route_node_ids"] == ["A", "C", "B"]
    assert feasible["total_time_minutes"] == 80
    infeasible = json.loads((out / "infeasible_low_soc" / "astar.json").read_text("utf-8"))
    assert infeasible["status"] == "INFEASIBLE"
    assert infeasible["events"] == []


def test_real_routes_csv_has_one_row_per_algorithm_per_scenario(
    scenarios: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    real_data.run_scenarios(scenarios, out, repeats=1)
    rows = read_csv(out / "real_routes.csv")
    assert tuple(rows[0]) == COLUMNS
    assert len(rows) == 8
    assert {row["experiment"] for row in rows} == {"I"}
    by_key = {(row["instance_id"], row["algorithm"]): row for row in rows}
    assert set(by_key) == {
        (scenario, algorithm)
        for scenario in ("feasible_one_stop", "infeasible_low_soc")
        for algorithm in ("label_setting", "astar", "greedy_frc", "exact")
    }
    feasible = by_key[("feasible_one_stop", "label_setting")]
    assert feasible["status"] == "FEASIBLE"
    assert feasible["feasible"] == "true"
    assert feasible["total_time_minutes"] == "80"
    assert feasible["number_of_charging_stops"] == "1"
    assert feasible["total_distance_miles"] == "50"
    assert feasible["nodes"] == "3"
    assert feasible["chargers"] == "1"
    assert feasible["capacity_kwh"] == "40"
    assert feasible["soc_step_kwh"] == "5"
    assert feasible["agreement"] == "true"
    assert feasible["note"] == "road_backend=haversine; category=one_stop"
    assert by_key[("infeasible_low_soc", "exact")]["status"] == "INFEASIBLE"
    assert by_key[("infeasible_low_soc", "exact")]["feasible"] == "false"
    assert by_key[("infeasible_low_soc", "astar")]["note"].endswith("category=no_route")


def test_missing_meta_file_is_allowed(tmp_path: Path) -> None:
    root = tmp_path / "processed"
    root.mkdir()
    _scenario(root, "bare", _config())
    out = tmp_path / "out"
    (report,) = real_data.run_scenarios(root, out, repeats=1)
    assert report.ok
    manifest = json.loads((out / "bare" / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["scenario_meta"] is None
    assert {row["note"] for row in read_csv(out / "real_routes.csv")} == {""}


def test_meta_without_optional_fields_is_allowed(tmp_path: Path) -> None:
    root = tmp_path / "processed"
    root.mkdir()
    _scenario(root, "thin_meta", _config(), meta={"scenario_id": "thin_meta"}, with_hashes=False)
    (report,) = real_data.run_scenarios(root, tmp_path / "out", repeats=1)
    assert report.ok


def test_matching_hashes_in_meta_are_accepted_with_a_sha256_prefix(tmp_path: Path) -> None:
    root = tmp_path / "processed"
    root.mkdir()
    folder = _scenario(root, "prefixed", _config(), meta={}, with_hashes=False)
    meta = {
        "graph_sha256": "sha256:" + _sha(folder / "canonical_graph.json").upper(),
        "config_sha256": _sha(folder / "trip_config.json"),
    }
    _write(folder / "scenario_meta.json", meta)
    (report,) = real_data.run_scenarios(root, tmp_path / "out", repeats=1)
    assert report.ok, report.errors


@pytest.mark.parametrize("key", ["config_sha256", "graph_sha256"])
def test_mismatched_hash_in_meta_fails_that_scenario_only(
    scenarios: Path, tmp_path: Path, key: str, capsys: pytest.CaptureFixture[str]
) -> None:
    meta_path = scenarios / "feasible_one_stop" / "scenario_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta[key] = "0" * 64
    _write(meta_path, meta)
    out = tmp_path / "out"

    code = real_data.main(["--scenarios", str(scenarios), "--out", str(out), "--repeats", "1"])

    assert code == 1
    captured = capsys.readouterr()
    assert "FAILED feasible_one_stop" in captured.err
    assert key in captured.err
    assert "does not match" in captured.err
    assert "infeasible_low_soc" not in captured.err
    failed = out / "feasible_one_stop"
    assert not any((failed / f"{name}.json").exists() for name in ALGORITHM_FILES)
    manifest = json.loads((failed / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "FAILED"
    assert key in manifest["errors"][0]
    assert (out / "infeasible_low_soc" / "label_setting.json").exists()
    assert {row["instance_id"] for row in read_csv(out / "real_routes.csv")} == {
        "infeasible_low_soc"
    }


def test_editing_an_input_after_the_meta_was_written_is_caught(
    scenarios: Path, tmp_path: Path
) -> None:
    config_path = scenarios / "feasible_one_stop" / "trip_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["initial_soc_kwh"] = 30
    _write(config_path, config)
    reports = real_data.run_scenarios(scenarios, tmp_path / "out", repeats=1)
    failed = [report for report in reports if not report.ok]
    assert [report.scenario_id for report in failed] == ["feasible_one_stop"]
    assert "config_sha256" in failed[0].errors[0]


def test_scenario_outside_the_exact_bound_records_skipped_without_calling_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(graph: dict, config: dict) -> dict:
        raise AssertionError("exact solver must not run outside its bound")

    monkeypatch.setattr(exact_solver, "solve", forbidden)
    root = tmp_path / "processed"
    root.mkdir()
    # 1 kWh steps make 40 capacity steps, above the limit of 8.
    _scenario(root, "big_battery", _config(soc_step_kwh=1), meta={"category": "one_stop"})
    out = tmp_path / "out"
    (report,) = real_data.run_scenarios(root, out, repeats=1)
    assert report.ok, report.errors
    assert not (out / "big_battery" / "exact.json").exists()
    manifest = json.loads((out / "big_battery" / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["algorithms"]["exact"]["status"] == "SKIPPED"
    assert "capacity steps 40 exceed 8" in manifest["algorithms"]["exact"]["reason"]
    rows = {row["algorithm"]: row for row in read_csv(out / "real_routes.csv")}
    assert len(rows) == 4
    assert rows["exact"]["status"] == "SKIPPED"
    assert rows["exact"]["runtime_ms"] == ""
    assert rows["exact"]["note"].startswith("capacity steps 40 exceed 8")
    assert rows["exact"]["note"].endswith("category=one_stop")
    assert rows["label_setting"]["status"] == "FEASIBLE"


def test_greedy_better_than_label_setting_fails_the_run(
    scenarios: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def better(result: dict) -> dict:
        if result["status"] == "FEASIBLE":
            result["total_time_minutes"] -= 1
        return result

    _patch_results(monkeypatch, "greedy", better)
    out = tmp_path / "out"
    code = real_data.main(["--scenarios", str(scenarios), "--out", str(out), "--repeats", "1"])
    assert code == 1
    err = capsys.readouterr().err
    assert "FAILED feasible_one_stop" in err
    assert "strictly better than label_setting" in err
    assert "infeasible_low_soc" not in err
    assert not (out / "feasible_one_stop" / "greedy.json").exists()
    assert not (out / "feasible_one_stop" / "label_setting.json").exists()


def test_a_result_that_breaks_the_schema_fails_its_scenario_and_leaves_no_files(
    scenarios: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def negative(result: dict) -> dict:
        result["total_time_minutes"] = -1
        return result

    _patch_results(monkeypatch, "greedy", negative)
    out = tmp_path / "out"
    code = real_data.main(["--scenarios", str(scenarios), "--out", str(out), "--repeats", "1"])
    assert code == 1
    err = capsys.readouterr().err
    assert "FAILED infeasible_low_soc: result file failed schema validation" in err
    folder = out / "infeasible_low_soc"
    assert sorted(path.name for path in folder.iterdir()) == ["run_manifest.json"]


def test_greedy_feasible_where_the_optimizer_is_infeasible_fails(
    scenarios: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = real_data.run_algorithm

    def patched(name: str, graph: dict, config: dict) -> dict:
        if name == "greedy" and config["initial_soc_kwh"] == 15:
            return real("greedy", graph, _config())  # a feasible plan for the same graph
        return real(name, graph, config)

    monkeypatch.setattr(real_data, "run_algorithm", patched)
    reports = real_data.run_scenarios(scenarios, tmp_path / "out", repeats=1)
    bad = next(report for report in reports if report.scenario_id == "infeasible_low_soc")
    assert any("greedy is FEASIBLE but label_setting is INFEASIBLE" in e for e in bad.errors)


def test_greedy_infeasible_while_label_setting_feasible_is_allowed_and_recorded(
    scenarios: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def give_up(result: dict) -> dict:
        result.update(
            status="INFEASIBLE",
            total_time_minutes=0,
            driving_time_minutes=0,
            charging_time_minutes=0,
            total_distance_miles=0,
            number_of_charging_stops=0,
            route_node_ids=[],
            events=[],
        )
        return result

    _patch_results(monkeypatch, "greedy", give_up)
    out = tmp_path / "out"
    reports = real_data.run_scenarios(scenarios, out, repeats=1)
    assert all(report.ok for report in reports), [r.errors for r in reports]
    manifest = json.loads(
        (out / "feasible_one_stop" / "run_manifest.json").read_text(encoding="utf-8")
    )
    check = manifest["algorithms"]["greedy"]["cross_checks"][0]
    assert check["passed"] is True
    assert check["outcome"] == "infeasible_while_optimal_feasible"
    rows = {
        (row["instance_id"], row["algorithm"]): row for row in read_csv(out / "real_routes.csv")
    }
    note = rows[("feasible_one_stop", "greedy_frc")]["note"]
    assert "greedy INFEASIBLE while label_setting FEASIBLE" in note


@pytest.mark.parametrize("algorithm", ["astar", "exact"])
def test_label_disagreement_with_astar_or_exact_fails_the_run(
    scenarios: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, algorithm: str
) -> None:
    def longer(result: dict) -> dict:
        result["total_distance_miles"] += 0.5
        return result

    _patch_results(monkeypatch, algorithm, longer)
    reports = real_data.run_scenarios(scenarios, tmp_path / "out", repeats=1)
    feasible = next(report for report in reports if report.scenario_id == "feasible_one_stop")
    assert not feasible.ok
    assert any(f"{algorithm} disagrees with label_setting" in e for e in feasible.errors)


def test_a_result_that_fails_the_plan_checker_fails_the_run(
    scenarios: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def wrong_stops(result: dict) -> dict:
        result["number_of_charging_stops"] += 1
        return result

    # Apply to label_setting and astar alike so only the plan check can notice.
    real = real_data.run_algorithm

    def patched(name: str, graph: dict, config: dict) -> dict:
        result = real(name, graph, config)
        if name in ("label_setting", "astar") and result["status"] == "FEASIBLE":
            return wrong_stops(copy.deepcopy(result))
        return result

    monkeypatch.setattr(real_data, "run_algorithm", patched)
    reports = real_data.run_scenarios(scenarios, tmp_path / "out", repeats=1)
    feasible = next(report for report in reports if report.scenario_id == "feasible_one_stop")
    assert any("plan check" in e and "number_of_charging_stops" in e for e in feasible.errors)


def test_repeats_must_give_identical_labels_and_states(
    scenarios: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = real_data.run_algorithm
    calls = {"count": 0}

    def flaky(name: str, graph: dict, config: dict) -> dict:
        result = real(name, graph, config)
        if name == "astar":
            calls["count"] += 1
            if calls["count"] == 2:
                result["metrics"]["states_expanded"] += 1
        return result

    monkeypatch.setattr(real_data, "run_algorithm", flaky)
    reports = real_data.run_scenarios(scenarios, tmp_path / "out", repeats=3)
    assert any("astar is not deterministic" in e for r in reports for e in r.errors)


def test_runtime_is_the_median_of_the_repeats(
    scenarios: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = real_data.run_algorithm
    runtimes = iter([9.0, 1.0, 5.0] * 8)

    def timed(name: str, graph: dict, config: dict) -> dict:
        result = real(name, graph, config)
        result["metrics"]["runtime_ms"] = next(runtimes)
        return result

    monkeypatch.setattr(real_data, "run_algorithm", timed)
    out = tmp_path / "out"
    real_data.run_scenarios(scenarios, out, repeats=3)
    payload = json.loads((out / "feasible_one_stop" / "astar.json").read_text(encoding="utf-8"))
    assert payload["metrics"]["runtime_ms"] == 5.0


def test_rerunning_replaces_rows_and_only_keeps_other_scenarios(
    scenarios: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    real_data.run_scenarios(scenarios, out, repeats=1)
    real_data.run_scenarios(scenarios, out, repeats=1)
    assert len(read_csv(out / "real_routes.csv")) == 8
    reports = real_data.run_scenarios(scenarios, out, only=["infeasible_low_soc"], repeats=1)
    assert [report.scenario_id for report in reports] == ["infeasible_low_soc"]
    rows = read_csv(out / "real_routes.csv")
    assert len(rows) == 8
    assert [row["instance_id"] for row in rows][:4] == ["feasible_one_stop"] * 4


def test_unknown_only_id_is_an_error(scenarios: Path, tmp_path: Path) -> None:
    code = real_data.main(
        ["--scenarios", str(scenarios), "--out", str(tmp_path / "o"), "--only", "nope"]
    )
    assert code == 1


def test_empty_or_missing_scenario_folder_is_an_error(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert real_data.main(["--scenarios", str(empty), "--out", str(tmp_path / "o")]) == 1
    gone = tmp_path / "gone"
    assert real_data.main(["--scenarios", str(gone), "--out", str(tmp_path / "o")]) == 1


def test_scenario_missing_its_config_is_reported_by_name(tmp_path: Path) -> None:
    root = tmp_path / "processed"
    root.mkdir()
    folder = _scenario(root, "half", _config())
    (folder / "trip_config.json").unlink()
    (report,) = real_data.run_scenarios(root, tmp_path / "out", repeats=1)
    assert not report.ok
    assert "trip_config.json is missing" in report.errors[0]


def test_command_line_prints_a_table_and_exits_zero(
    scenarios: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = real_data.main(
        ["--scenarios", str(scenarios), "--out", str(tmp_path / "out"), "--repeats", "1",
         "--only", "feasible_one_stop"]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "feasible_one_stop  [OK]  nodes=3 edges=2 chargers=1" in out
    assert "label_setting" in out
    assert "greedy_frc" in out
    assert "1 scenario(s), 1 passed, 0 failed" in out


def test_scenario_layout_matches_what_the_astar_real_data_test_globs(scenarios: Path) -> None:
    """``test_t15_real_data_graph_when_present`` globs ``**/canonical_graph.json``."""
    graphs = sorted(scenarios.glob("**/canonical_graph.json"))
    assert len(graphs) == 2
    for path in graphs:
        assert path.with_name("trip_config.json").is_file()
