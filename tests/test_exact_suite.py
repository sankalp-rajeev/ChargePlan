"""Experiment A: the generator, the certificate, and agreement with the exact solver.

Every check is a property of the suite, not a recorded answer. Nothing here
pins a runtime or an optimal plan that was copied from a solver run.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import pytest

from experiments.certificate import min_charging_stops, min_energy_steps
from experiments.correctness import deterministic_part
from experiments.exact_suite import (
    CATEGORIES,
    INFEASIBLE_VARIANTS,
    SCHEDULE,
    build_instance,
    build_suite,
    schedule_category,
)
from experiments.instances import (
    CORRECTNESS_COUNT,
    EXACT_MAX_CAPACITY_STEPS,
    EXACT_MAX_CHARGERS,
    EXACT_MAX_NODES,
    Instance,
    correctness_instances,
    exact_block_reason,
    tiny_instances,
)
from experiments.metrics import COLUMNS, OUTPUT_FILES, read_csv
from experiments.run_all import run
from experiments.sweeps import experiment_a_report
from optimizer.battery_state_optimizer import solve as label_solve
from optimizer.exact_solver import solve as exact_solve
from optimizer.model import (
    InputError,
    discretize,
    edge_energy_steps,
    haversine_miles,
    load_config,
    load_graph,
)

SEED = 505


@lru_cache(maxsize=1)
def _suite() -> list[Instance]:
    return build_suite(SEED, CORRECTNESS_COUNT)


@lru_cache(maxsize=1)
def _report():
    return experiment_a_report(SEED, CORRECTNESS_COUNT)


def _signature(instances: list[Instance]) -> list[tuple]:
    return [
        (
            instance.instance_id,
            json.dumps(instance.graph, sort_keys=True),
            json.dumps(instance.config, sort_keys=True),
            json.dumps(instance.meta, sort_keys=True),
        )
        for instance in instances
    ]


def _without(instance: Instance, node_id: str) -> Instance:
    graph = {
        "nodes": [node for node in instance.graph["nodes"] if node["id"] != node_id],
        "edges": [
            edge
            for edge in instance.graph["edges"]
            if node_id not in (edge["from"], edge["to"])
        ],
    }
    return Instance(instance.instance_id, graph, instance.config, instance.seed, instance.meta)


def _of(category: str) -> list[Instance]:
    return [item for item in _suite() if item.meta["category"] == category]


# --- reproducibility ---------------------------------------------------------------


def test_same_seed_repeats_instances_and_results_except_runtime() -> None:
    assert _signature(build_suite(SEED, 60)) == _signature(build_suite(SEED, 60))
    first = _report()
    second = experiment_a_report(SEED, CORRECTNESS_COUNT)
    assert first.rows[0].keys() == second.rows[0].keys()
    assert _without_runtime(first.rows) == _without_runtime(second.rows)
    assert deterministic_part({"summary": first.summary, "instances": first.records}) == (
        deterministic_part({"summary": second.summary, "instances": second.records})
    )


def test_different_seeds_draw_different_instances() -> None:
    base = _signature(build_suite(SEED, 40))
    other = _signature(build_suite(SEED + 1, 40))
    assert base != other
    differing = sum(1 for left, right in zip(base, other, strict=True) if left[1:3] != right[1:3])
    assert differing >= 38
    # The category schedule is a function of the index, not of the seed.
    assert [item[3] for item in base] != [item[3] for item in other]
    assert [json.loads(item[3])["category"] for item in base] == [
        json.loads(item[3])["category"] for item in other
    ]


def test_an_instance_does_not_depend_on_the_suite_size() -> None:
    small = build_suite(SEED, 25)
    large = build_suite(SEED, 45)
    assert _signature(small) == _signature(large[:25])
    assert _signature([build_instance(SEED, 33)]) == _signature([large[33]])


def test_the_s6_tiny_suite_is_untouched_by_the_new_stream() -> None:
    before = _signature(tiny_instances(SEED, 20))
    build_suite(SEED, 40)
    assert before == _signature(tiny_instances(SEED, 20))


def test_bad_counts_are_rejected() -> None:
    with pytest.raises(InputError, match="positive"):
        build_suite(SEED, 0)


# --- bounds and the input contract ---------------------------------------------------


def test_every_instance_stays_inside_the_exact_bound() -> None:
    assert len(_suite()) == CORRECTNESS_COUNT >= 200
    for instance in _suite():
        assert exact_block_reason(instance.graph, instance.config) is None
        assert 4 <= instance.node_count <= EXACT_MAX_NODES
        assert 1 <= instance.charger_count <= EXACT_MAX_CHARGERS
        assert discretize(instance.config)[0] <= EXACT_MAX_CAPACITY_STEPS
    assert len({item.instance_id for item in _suite()}) == CORRECTNESS_COUNT


def test_every_instance_passes_the_frozen_contracts(tmp_path: Path) -> None:
    for instance in _suite():
        graph_path = tmp_path / f"{instance.instance_id}.graph.json"
        config_path = tmp_path / f"{instance.instance_id}.config.json"
        graph_path.write_text(json.dumps(instance.graph), encoding="utf-8")
        config_path.write_text(json.dumps(instance.config), encoding="utf-8")
        graph = load_graph(graph_path)
        load_config(config_path)
        nodes = {node["id"]: node for node in graph["nodes"]}
        assert instance.config["origin"] in nodes
        assert instance.config["destination"] in nodes
        for edge in graph["edges"]:
            start, end = nodes[edge["from"]], nodes[edge["to"]]
            straight = haversine_miles(start["lat"], start["lon"], end["lat"], end["lon"])
            assert edge["distance_miles"] + 1e-6 >= straight


# --- the mixture -------------------------------------------------------------------


def test_the_schedule_fixes_category_counts() -> None:
    assert len(SCHEDULE) == 20
    assert set(SCHEDULE) == set(CATEGORIES)
    counts = {category: len(_of(category)) for category in CATEGORIES}
    assert counts == {
        category: SCHEDULE.count(category) * (CORRECTNESS_COUNT // 20)
        for category in CATEGORIES
    }
    for index, instance in enumerate(_suite()):
        assert instance.meta["category"] == schedule_category(index)


def test_a_useful_share_of_instances_require_charging() -> None:
    report = _report().summary
    total = report["instances"]
    assert report["feasible"] + report["infeasible"] == total
    assert 0.15 <= report["infeasible"] / total <= 0.30
    assert report["feasible_requiring_charging"] / total >= 0.5
    assert report["feasible_requiring_charging"] / report["feasible"] >= 0.7
    assert report["charging_stops_in_optimal_plan"].get("0", 0) >= 20


def test_multiple_stop_instances_are_present() -> None:
    report = _report().summary
    assert report["feasible_requiring_multiple_stops"] >= 0.2 * report["instances"]
    stops = report["certificate_minimum_stops"]
    assert stops.get("2", 0) >= 20
    assert stops.get("3", 0) >= 5
    assert report["feasible_optimal_plan_multi_stop"] >= report["feasible_requiring_multiple_stops"]


def test_every_infeasible_variant_is_present() -> None:
    variants = {item.meta["variant"] for item in _of("infeasible")}
    assert variants == set(INFEASIBLE_VARIANTS)
    for item in _of("infeasible"):
        assert min_charging_stops(item.graph, item.config) is None


def test_partial_charging_and_full_charging_both_occur() -> None:
    profile = _report().summary["partial_charging"]
    assert profile["instances_with_partial_charge"] >= 40
    assert profile["partial_charge_events"] > 0
    assert profile["full_charge_events"] > 0
    assert profile["instances_with_any_charge"] >= profile["instances_with_partial_charge"]


def test_steps_consumption_rates_and_batteries_vary() -> None:
    suite = _suite()
    steps = {item.config["soc_step_kwh"] for item in suite}
    consumption = {item.config["consumption_kwh_per_mile"] for item in suite}
    capacities = {discretize(item.config)[0] for item in suite}
    reserves = {discretize(item.config)[2] for item in suite}
    rates = {
        node["charge_rate_kw"]
        for item in suite
        for node in item.graph["nodes"]
        if node["charge_rate_kw"] is not None
    }
    assert len(steps) >= 5 and len(consumption) >= 6
    assert any(value != math.floor(value) for value in consumption)
    assert capacities >= {4, 5, 6, 7, 8}
    assert {0, 1, 2} <= reserves
    assert len(rates) >= 5
    # Some capacities and initial energies carry a fraction of a step that rounds down.
    remainders = [
        Decimal(str(item.config["battery_capacity_kwh"]))
        % Decimal(str(item.config["soc_step_kwh"]))
        for item in suite
    ]
    assert any(value != 0 for value in remainders)


def _waypoint_path_steps(item: Instance) -> int:
    """Energy of the constructed origin -> w* -> destination path, in steps."""
    waypoints = sorted(
        node["id"] for node in item.graph["nodes"] if node["id"].startswith("w")
    )
    path = ["origin", *waypoints, "destination"]
    miles = {(edge["from"], edge["to"]): edge["distance_miles"] for edge in item.graph["edges"]}
    return sum(
        edge_energy_steps(miles[(start, end)], item.config)
        for start, end in zip(path, path[1:], strict=False)
    )


def test_reserve_boundary_instances_end_exactly_at_the_reserve() -> None:
    boundary = [item for item in _of("no_charge") if item.meta["variant"] == "reserve_boundary"]
    slack = [item for item in _of("no_charge") if item.meta["variant"] == "slack"]
    assert len(boundary) >= 10 and len(slack) >= 10
    for item in boundary:
        _capacity, initial, reserve, _step = discretize(item.config)
        assert _waypoint_path_steps(item) == initial - reserve
        result = exact_solve(item.graph, item.config)
        assert result["status"] == "FEASIBLE" and result["number_of_charging_stops"] == 0
    for item in slack:
        _capacity, initial, reserve, _step = discretize(item.config)
        assert _waypoint_path_steps(item) < initial - reserve


def test_near_miss_instances_fall_one_step_short() -> None:
    near = [item for item in _of("infeasible") if item.meta["variant"] == "reserve_near_miss"]
    assert len(near) >= 8
    for item in near:
        _capacity, initial, reserve, _step = discretize(item.config)
        cheapest = min_energy_steps(item.graph, item.config, "origin", "destination")
        assert cheapest == initial - reserve + 1
        assert _waypoint_path_steps(item) == initial - reserve + 1


def test_construction_categories_hold_for_every_instance() -> None:
    for item in _suite():
        stops = min_charging_stops(item.graph, item.config)
        assert stops == item.meta["min_stops"]
        category = item.meta["category"]
        if category in {"no_charge", "tie_distance", "tie_stops"}:
            assert stops == 0
        elif category in {"one_stop", "speed_tradeoff"}:
            assert stops == 1
        elif category == "multi_stop":
            assert stops == int(item.meta["variant"][0])
        elif category in {"partial_chain", "origin_charge"}:
            assert stops is not None and stops >= 1
        else:
            assert category == "infeasible" and stops is None


def test_equal_time_ties_are_real_and_broken_by_the_prd_order() -> None:
    for item in _of("tie_stops"):
        free = exact_solve(_without(item, "c1").graph, item.config)
        charged = exact_solve(_without(item, "c0").graph, item.config)
        both = exact_solve(item.graph, item.config)
        assert free["number_of_charging_stops"] == 0
        assert charged["number_of_charging_stops"] >= 1
        assert free["total_time_minutes"] == charged["total_time_minutes"]
        assert both["total_time_minutes"] == free["total_time_minutes"]
        assert both["number_of_charging_stops"] == 0
    for item in _of("tie_distance"):
        left = exact_solve(_without(item, "c1").graph, item.config)
        right = exact_solve(_without(item, "c0").graph, item.config)
        both = exact_solve(item.graph, item.config)
        assert left["total_time_minutes"] == right["total_time_minutes"]
        assert left["number_of_charging_stops"] == right["number_of_charging_stops"] == 0
        assert left["total_distance_miles"] != right["total_distance_miles"]
        assert both["total_distance_miles"] == min(
            left["total_distance_miles"], right["total_distance_miles"]
        )


def test_speed_tradeoff_winners_are_not_always_the_nearer_charger() -> None:
    winners = set()
    for item in _of("speed_tradeoff"):
        result = label_solve(item.graph, item.config, use_heuristic=False)
        winners.add(result["route_node_ids"][1])
    assert {"c0", "c1"} <= winners


# --- agreement ---------------------------------------------------------------------


def test_all_three_solvers_and_the_certificate_agree_on_every_instance() -> None:
    report = _report()
    assert report.failed == ()
    assert report.summary["agreement"]["objective_mismatches"] == 0
    assert report.summary["agreement"]["feasibility_mismatches"] == 0
    assert report.summary["certificate"]["conflicts"] == 0
    for record in report.records:
        assert record["mismatches"] == []
    rows = [row for row in report.rows if row["experiment"] == "A"]
    assert len(rows) == 3 * CORRECTNESS_COUNT
    assert {row["agreement"] for row in rows} == {"true"}
    assert {row["status"] for row in rows} == {"FEASIBLE", "INFEASIBLE"}
    for item in _suite():
        plain = label_solve(item.graph, item.config, use_heuristic=False)
        guided = label_solve(item.graph, item.config, use_heuristic=True)
        exact = exact_solve(item.graph, item.config)
        for field in (
            "status",
            "total_time_minutes",
            "number_of_charging_stops",
            "total_distance_miles",
        ):
            assert plain[field] == guided[field] == exact[field], (item.instance_id, field)
        assert guided["metrics"]["states_expanded"] <= plain["metrics"]["states_expanded"]


def test_the_certificate_agrees_with_the_solvers_on_the_s6_graphs() -> None:
    for item in tiny_instances(SEED, 60):
        stops = min_charging_stops(item.graph, item.config)
        result = exact_solve(item.graph, item.config)
        assert (stops is None) == (result["status"] != "FEASIBLE"), item.instance_id
        if stops is not None:
            assert result["number_of_charging_stops"] >= stops


@pytest.mark.parametrize("mutant", ["truncated_energy", "no_reserve"])
def test_the_suite_detects_a_seeded_optimizer_bug(
    mutant: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agreement is only meaningful if a wrong optimizer would not agree."""
    import optimizer.battery_state_optimizer as module

    if mutant == "truncated_energy":
        real_edge = module.edge_energy_steps

        def truncated(distance: float, config: dict) -> int:
            return max(0, real_edge(distance, config) - 1)

        monkeypatch.setattr(module, "edge_energy_steps", truncated)
    else:
        real_discretize = module.discretize

        def without_reserve(config: dict) -> tuple[int, int, int, float]:
            capacity, initial, _reserve, step = real_discretize(config)
            return capacity, initial, 0, step

        monkeypatch.setattr(module, "discretize", without_reserve)
    report = experiment_a_report(SEED, 100)
    assert len(report.failed) >= 10
    assert report.summary["agreement"]["objective_mismatches"] == len(report.failed)


# --- the certificate on hand-built graphs --------------------------------------------


def _hand(edges: list[tuple[str, str, float]], rates: dict[str, float | None]) -> dict:
    nodes = [
        {"id": node_id, "type": "charger", "lat": 42.0, "lon": -83.0, "charge_rate_kw": rate}
        for node_id, rate in rates.items()
    ]
    return {
        "nodes": nodes,
        "edges": [
            {"from": a, "to": b, "distance_miles": miles, "drive_time_minutes": 1.0}
            for a, b, miles in edges
        ],
    }


def _config(initial: float, *, capacity: float = 10.0, reserve: float = 1.0) -> dict:
    return {
        "origin": "o",
        "destination": "d",
        "battery_capacity_kwh": capacity,
        "initial_soc_kwh": initial,
        "reserve_kwh": reserve,
        "consumption_kwh_per_mile": 1.0,
        "soc_step_kwh": 1.0,
    }


def test_certificate_on_hand_built_graphs() -> None:
    rates = {"o": None, "a": 50.0, "b": 50.0, "d": None}
    chain = _hand([("o", "a", 4), ("a", "b", 8), ("b", "d", 8)], rates)
    assert min_charging_stops(chain, _config(5)) == 2
    assert min_charging_stops(chain, _config(9)) == 2
    assert min_charging_stops(_hand([("o", "d", 4)], rates), _config(5)) == 0
    assert min_charging_stops(_hand([("o", "d", 5)], rates), _config(5)) is None
    gap = _hand([("o", "a", 3), ("a", "d", 10)], rates)
    assert min_charging_stops(gap, _config(5)) is None
    assert min_charging_stops(chain, _config(0.5)) is None
    origin_charger = {"o": 50.0, "a": None, "b": None, "d": None}
    assert min_charging_stops(_hand([("o", "d", 8)], origin_charger), _config(2)) == 1
    assert min_energy_steps(chain, _config(5), "o", "d") == 20
    assert min_energy_steps(chain, _config(5), "d", "o") is None


# --- the runner output ---------------------------------------------------------------


def test_runner_keeps_the_csv_schema_and_writes_the_supplementary_summary(
    tmp_path: Path,
) -> None:
    summary = run(tmp_path, seed=SEED, tiny_count=2, correctness_count=40, sweeps=False)
    assert summary.disagreements == ()
    assert summary.experiment_a is not None
    assert {path.name for path in tmp_path.iterdir()} == set(OUTPUT_FILES) | {
        "correctness_summary.json"
    }
    rows = read_csv(tmp_path / "correctness.csv")
    assert tuple(rows[0]) == COLUMNS
    suite = [row for row in rows if row["instance_id"].startswith("exact-")]
    assert len(suite) == 120
    assert all(row["note"].startswith("suite=A; timing=single run; category=") for row in suite)
    payload = json.loads((tmp_path / "correctness_summary.json").read_text(encoding="utf-8"))
    assert payload["summary"]["instances"] == 40
    assert len(payload["instances"]) == 40
    assert payload["summary"]["agreement"]["objective_mismatches"] == 0
    assert payload["summary"]["exact_bounds"]["instances_outside_bound"] == 0


def test_runner_without_experiment_a_writes_no_summary(tmp_path: Path) -> None:
    run(tmp_path, seed=SEED, tiny_count=1, correctness_count=0, sweeps=False)
    assert not (tmp_path / "correctness_summary.json").exists()


def test_the_default_suite_is_the_instance_helper_with_the_same_seed() -> None:
    assert _signature(correctness_instances(SEED, 30)) == _signature(build_suite(SEED, 30))


def _without_runtime(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{key: value for key, value in row.items() if key != "runtime_ms"} for row in rows]
