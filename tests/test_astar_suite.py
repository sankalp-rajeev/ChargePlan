"""Experiment J: the dedicated A* benchmark, its metrics, and its aggregates.

The first Experiment J copied Experiment D and C rows. These tests check that
the new one builds and measures its own instances, that both algorithms see the
same input, that a label disagreement fails the run, and that a result where A*
expands the same or more states is recorded rather than rejected.
"""

from __future__ import annotations

import json
import statistics
from functools import lru_cache
from pathlib import Path

import pytest

from experiments.astar_suite import (
    GEOMETRIES,
    HETEROGENEOUS_MPH,
    HOMOGENEOUS_MPH,
    POOL_SIZE,
    SIZES,
    SOC_STEPS,
    SPEEDS,
    SUITE_REPLICATES,
    VEHICLE,
    build_astar_suite,
    build_pool,
    heuristic_origin_minutes,
    usable_steps,
)
from experiments.astar_summary import (
    FAILURES_FILE,
    SUITE_NOTE,
    instance_pairs,
    reduction_pct,
    summarize_astar,
)
from experiments.instances import Instance
from experiments.metrics import metric_row
from experiments.run_all import run
from experiments.scaling_suite import build_scaling_suite
from experiments.sweeps import experiment_j, experiment_j_report
from optimizer.battery_state_optimizer import _heuristic_ms
from optimizer.battery_state_optimizer import solve as real_solve
from optimizer.model import (
    InputError,
    discretize,
    edge_energy_steps,
    haversine_miles,
    load_config,
    load_graph,
    minutes_to_ms,
)

SEED = 505


@lru_cache(maxsize=1)
def _grid() -> tuple[Instance, ...]:
    """Two replicates of the whole 36-cell grid."""
    return tuple(build_astar_suite(SEED, 2))


def _pick(**factors) -> list[Instance]:
    keep = {
        "geometry": "geometry",
        "size": "size",
        "speed": "speed",
        "soc": "soc_step",
        "replicate": "replicate",
    }
    return [
        item
        for item in _grid()
        if all(item.meta[keep[name]] == value for name, value in factors.items())
    ]


def _nodes(instance: Instance) -> dict[str, dict]:
    return {node["id"]: node for node in instance.graph["nodes"]}


# --- design and determinism ---------------------------------------------------------------


def test_default_design_is_the_documented_grid() -> None:
    assert SIZES == (10, 25, 50)
    assert set(GEOMETRIES) == {"straight", "curved", "detour"}
    assert SPEEDS == ("homogeneous", "heterogeneous")
    assert SOC_STEPS == (1.0, 4.0)
    assert SUITE_REPLICATES == 6
    assert len(build_astar_suite(SEED, 1)) == 3 * 3 * 2 * 2
    assert len(_grid()) == 3 * 3 * 2 * 2 * 2


def test_every_instance_names_its_factors_and_has_a_unique_id() -> None:
    ids = [item.instance_id for item in _grid()]
    assert len(set(ids)) == len(ids)
    for item in _grid():
        meta = item.meta
        assert item.instance_id.startswith("astar-")
        assert meta["geometry"] in GEOMETRIES
        assert meta["size"] in SIZES
        assert meta["speed"] in SPEEDS
        assert meta["soc_step"] in SOC_STEPS
        assert meta["replicate"] in (0, 1)
        assert item.charger_count == meta["size"]
        assert item.soc_step_kwh == meta["soc_step"]
        assert item.seed == SEED
    cells = {}
    for item in _grid():
        key = (item.meta["geometry"], item.meta["size"], item.meta["speed"], item.meta["soc_step"])
        cells[key] = cells.get(key, 0) + 1
    assert len(cells) == 36
    assert set(cells.values()) == {2}


def test_same_seed_gives_identical_inputs_and_a_different_seed_does_not() -> None:
    again = build_astar_suite(SEED, 2)
    assert [(i.instance_id, i.graph, i.config) for i in again] == [
        (i.instance_id, i.graph, i.config) for i in _grid()
    ]
    other = build_astar_suite(SEED + 1, 2)
    assert [i.instance_id for i in other] == [i.instance_id for i in _grid()]
    differing = sum(1 for a, b in zip(other, _grid(), strict=True) if a.graph != b.graph)
    assert differing == len(other)
    assert other[0].graph["nodes"] != _grid()[0].graph["nodes"]


def test_replicates_have_different_pools_and_sizes_are_nested() -> None:
    by_replicate = {}
    for item in _pick(geometry="curved", size=50, speed="homogeneous", soc=1.0):
        by_replicate[item.meta["replicate"]] = [
            (n["lat"], n["lon"], n["charge_rate_kw"]) for n in item.graph["nodes"]
        ]
    assert by_replicate[0] != by_replicate[1]
    for replicate in (0, 1):
        small = _station_rows(_one("curved", 10, "homogeneous", 1.0, replicate))
        large = _station_rows(_one("curved", 50, "homogeneous", 1.0, replicate))
        assert large[:10] == small
    assert build_pool(SEED, "detour", 1, 25) == build_pool(SEED, "detour", 1, 50)[:25]
    with pytest.raises(InputError, match="pool size"):
        build_pool(SEED, "detour", 0, POOL_SIZE + 1)


def _one(geometry: str, size: int, speed: str, step: float, replicate: int) -> Instance:
    found = [
        item
        for item in _grid()
        if item.meta["geometry"] == geometry
        and item.meta["size"] == size
        and item.meta["speed"] == speed
        and item.meta["soc_step"] == step
        and item.meta["replicate"] == replicate
    ]
    assert len(found) == 1
    return found[0]


def _station_rows(instance: Instance) -> list[tuple]:
    return [
        (n["id"], n["lat"], n["lon"], n["charge_rate_kw"])
        for n in instance.graph["nodes"]
        if n["type"] == "charger"
    ]


def test_factors_are_matched_where_only_one_factor_changes() -> None:
    base = _one("curved", 25, "homogeneous", 1.0, 1)
    faster = _one("curved", 25, "heterogeneous", 1.0, 1)
    coarse = _one("curved", 25, "homogeneous", 4.0, 1)
    # Speed changes drive times only.
    assert base.graph["nodes"] == faster.graph["nodes"]
    assert [(e["from"], e["to"], e["distance_miles"]) for e in base.graph["edges"]] == [
        (e["from"], e["to"], e["distance_miles"]) for e in faster.graph["edges"]
    ]
    assert [e["drive_time_minutes"] for e in base.graph["edges"]] != [
        e["drive_time_minutes"] for e in faster.graph["edges"]
    ]
    # The SOC step changes the config only. The physical graph is identical.
    assert base.config["soc_step_kwh"] != coarse.config["soc_step_kwh"]
    assert {k: v for k, v in base.config.items() if k != "soc_step_kwh"} == {
        k: v for k, v in coarse.config.items() if k != "soc_step_kwh"
    }
    assert base.graph == coarse.graph


def test_the_physical_graph_is_identical_across_soc_resolutions_for_every_match() -> None:
    by_match: dict[tuple, list[Instance]] = {}
    for item in _grid():
        key = (
            item.meta["geometry"],
            item.meta["size"],
            item.meta["speed"],
            item.meta["replicate"],
        )
        by_match.setdefault(key, []).append(item)
    assert len(by_match) == len(_grid()) // len(SOC_STEPS)
    for key, group in by_match.items():
        assert sorted(item.meta["soc_step"] for item in group) == sorted(SOC_STEPS), key
        first = group[0]
        for other in group[1:]:
            assert other.graph == first.graph, key  # nodes, roads, distances, drive times
            assert other.meta["edges"] == first.meta["edges"], key
            assert other.meta["h_origin_minutes"] == first.meta["h_origin_minutes"], key


def test_initial_energy_and_reserve_are_unchanged_by_discretization() -> None:
    assert VEHICLE["initial_soc_kwh"] == 28
    assert VEHICLE["reserve_kwh"] == 4
    for item in _grid():
        step = item.config["soc_step_kwh"]
        capacity, initial, reserve, _ = discretize(item.config)
        assert capacity * step == item.config["battery_capacity_kwh"], item.instance_id
        assert initial * step == item.config["initial_soc_kwh"], item.instance_id
        assert reserve * step == item.config["reserve_kwh"], item.instance_id
        assert (capacity, initial, reserve) in {(40, 28, 4), (10, 7, 1)}


def test_vehicle_trip_and_rate_mix_are_fixed_across_the_grid() -> None:
    reference = _nodes(_grid()[0])
    for item in _grid():
        config = {k: v for k, v in item.config.items() if k != "soc_step_kwh"}
        assert config == VEHICLE
        nodes = _nodes(item)
        for end in ("origin", "destination"):
            assert (nodes[end]["lat"], nodes[end]["lon"]) == (
                reference[end]["lat"],
                reference[end]["lon"],
            )
        rates = [n["charge_rate_kw"] for n in item.graph["nodes"] if n["type"] == "charger"]
        fast = sum(1 for rate in rates if rate >= 150) / len(rates)
        assert 0.6 <= fast <= 0.8 + 1e-9
        assert set(rates) <= {50.0, 150.0, 250.0}
    mixed = [
        n["charge_rate_kw"] for n in _one("detour", 50, "heterogeneous", 1.0, 0).graph["nodes"]
    ]
    assert len({rate for rate in mixed if rate is not None}) == 3


# --- contracts and heuristic assumptions --------------------------------------------------


def test_every_graph_satisfies_the_input_contracts(tmp_path: Path) -> None:
    for item in _grid():
        graph_path = tmp_path / f"{item.instance_id}.graph.json"
        config_path = tmp_path / f"{item.instance_id}.config.json"
        graph_path.write_text(json.dumps(item.graph), encoding="utf-8")
        config_path.write_text(json.dumps(item.config), encoding="utf-8")
        graph = load_graph(graph_path)  # schema, unique ids, road >= straight line
        load_config(config_path)
        assert graph["edges"], item.instance_id
        nodes = _nodes(item)
        assert {"origin", "destination"} <= set(nodes)
        assert not any(e["from"] == "destination" or e["to"] == "origin" for e in graph["edges"])


def test_roads_are_not_shorter_than_the_straight_line_and_speeds_are_plausible() -> None:
    low, high = HETEROGENEOUS_MPH
    for item in _grid():
        nodes = _nodes(item)
        for edge in item.graph["edges"]:
            start, end = nodes[edge["from"]], nodes[edge["to"]]
            straight = haversine_miles(start["lat"], start["lon"], end["lat"], end["lon"])
            assert edge["distance_miles"] >= straight
            assert edge["drive_time_minutes"] > 0
            speed = edge["distance_miles"] / edge["drive_time_minutes"] * 60.0
            assert low - 0.5 <= speed <= high + 0.5
            assert edge_energy_steps(edge["distance_miles"], item.config) <= usable_steps(
                item.config
            )


def test_heuristic_assumptions_hold_on_every_instance() -> None:
    for item in _grid():
        nodes = _nodes(item)
        h = _heuristic_ms(nodes, item.graph["edges"], "destination")
        assert h["destination"] == 0
        assert all(value >= 0 for value in h.values())
        for edge in item.graph["edges"]:
            step_ms = minutes_to_ms(edge["drive_time_minutes"])
            assert h[edge["from"]] <= h[edge["to"]] + step_ms, item.instance_id  # consistency
        assert abs(
            h["origin"] / 60000 - item.meta["h_origin_minutes"]
        ) < 0.01  # the recorded value is the one the solver uses


def test_the_recorded_heuristic_never_exceeds_the_optimal_trip_time() -> None:
    for item in _pick(replicate=0, size=10):
        result = real_solve(item.graph, item.config, use_heuristic=False)
        assert result["status"] == "FEASIBLE"
        assert item.meta["h_origin_minutes"] <= result["total_time_minutes"]
        assert heuristic_origin_minutes(item.graph) == item.meta["h_origin_minutes"] or abs(
            heuristic_origin_minutes(item.graph) - item.meta["h_origin_minutes"]
        ) < 1e-3


# --- the factors really differ ---------------------------------------------------------------


def _off_line_miles(instance: Instance) -> list[float]:
    nodes = _nodes(instance)
    origin, destination = nodes["origin"], nodes["destination"]
    straight = haversine_miles(origin["lat"], origin["lon"], destination["lat"], destination["lon"])
    extra = []
    for node in nodes.values():
        if node["type"] != "charger":
            continue
        there = haversine_miles(origin["lat"], origin["lon"], node["lat"], node["lon"])
        back = haversine_miles(node["lat"], node["lon"], destination["lat"], destination["lon"])
        extra.append(there + back - straight)
    return extra


def _road_ratio(instance: Instance) -> float:
    nodes = _nodes(instance)
    ratios = []
    for edge in instance.graph["edges"]:
        start, end = nodes[edge["from"]], nodes[edge["to"]]
        straight = haversine_miles(start["lat"], start["lon"], end["lat"], end["lon"])
        ratios.append(edge["distance_miles"] / straight)
    return statistics.median(ratios)


def test_geometry_conditions_genuinely_differ() -> None:
    medians = {}
    tails = {}
    ratios = {}
    for name in GEOMETRIES:
        group = [_one(name, 50, "homogeneous", 1.0, r) for r in (0, 1)]
        extra = sorted(v for item in group for v in _off_line_miles(item))
        medians[name] = statistics.median(extra)
        tails[name] = extra[int(0.9 * len(extra))]
        ratios[name] = statistics.median(_road_ratio(item) for item in group)
    # Road winding rises from straight to curved to detour. A coherent arc (curved) puts the
    # typical station far from the line, and scattered detours (detour) have the longest tail.
    assert ratios["straight"] < ratios["curved"] < ratios["detour"]
    assert tails["straight"] < tails["curved"] < tails["detour"]
    assert medians["straight"] < medians["curved"]
    assert medians["straight"] < medians["detour"]
    assert medians["detour"] > 10 * medians["straight"]
    assert ratios["straight"] < 1.12
    assert ratios["detour"] > 1.2
    # A curved corridor is not on one line, and is not the first benchmark's single latitude.
    curved = _one("curved", 50, "homogeneous", 1.0, 0)
    assert len({n["lat"] for n in curved.graph["nodes"]}) > 40
    assert max(_off_line_miles(curved)) > 1.0


def test_speed_conditions_genuinely_differ() -> None:
    for item in _pick(speed="homogeneous"):
        speeds = {
            round(e["distance_miles"] / e["drive_time_minutes"] * 60.0, 1)
            for e in item.graph["edges"]
        }
        assert speeds == {HOMOGENEOUS_MPH}
    mixed = _one("curved", 50, "heterogeneous", 1.0, 0)
    values = [e["distance_miles"] / e["drive_time_minutes"] * 60.0 for e in mixed.graph["edges"]]
    assert max(values) - min(values) > 25
    assert statistics.pstdev(values) > 5
    assert 50 < statistics.mean(values) < 60  # same mean as the homogeneous condition


def test_graph_size_and_soc_resolution_differ() -> None:
    sizes = [_one("curved", n, "homogeneous", 1.0, 0) for n in SIZES]
    assert [item.charger_count for item in sizes] == [10, 25, 50]
    assert sizes[0].meta["edges"] < sizes[1].meta["edges"] < sizes[2].meta["edges"]
    fine = _one("curved", 25, "homogeneous", 1.0, 0)
    coarse = _one("curved", 25, "homogeneous", 4.0, 0)
    assert fine.meta["capacity_steps"] == 40
    assert coarse.meta["capacity_steps"] == 10


def test_the_benchmark_needs_charging_and_is_solvable() -> None:
    for item in _grid():
        assert item.meta["min_stops"] is not None
        assert item.meta["min_stops"] >= 1  # no instance is a direct drive
        assert item.meta["straight_miles"] > 200
    assert any(item.meta["min_stops"] >= 2 for item in _grid())
    result = real_solve(
        _one("detour", 25, "heterogeneous", 4.0, 0).graph,
        _one("detour", 25, "heterogeneous", 4.0, 0).config,
        use_heuristic=False,
    )
    assert result["status"] == "FEASIBLE"
    assert result["number_of_charging_stops"] >= 1


def test_bad_arguments_are_rejected() -> None:
    with pytest.raises(InputError, match="replicates"):
        build_astar_suite(SEED, 0)
    with pytest.raises(InputError, match="size"):
        build_astar_suite(SEED, 1, sizes=(POOL_SIZE + 1,))
    with pytest.raises(InputError, match="geometry"):
        build_astar_suite(SEED, 1, geometries=("zigzag",))
    with pytest.raises(InputError, match="speed"):
        build_astar_suite(SEED, 1, speeds=("warp",))
    with pytest.raises(InputError, match="SOC step"):
        build_astar_suite(SEED, 1, soc_steps=(0.0,))
    with pytest.raises(InputError, match="factor"):
        build_astar_suite(SEED, 1, sizes=())


def test_the_benchmark_is_independent_of_experiment_d() -> None:
    d_ids = {item.instance_id for item in build_scaling_suite(SEED, (10, 25), 2)}
    assert d_ids.isdisjoint(item.instance_id for item in _grid())
    d_graph = build_scaling_suite(SEED, (10,), 1)[0].graph
    j_graph = _one("straight", 10, "homogeneous", 1.0, 0).graph
    assert d_graph["nodes"] != j_graph["nodes"]
    assert d_graph["edges"] != j_graph["edges"]


# --- fair comparison -------------------------------------------------------------------------


def _small() -> list[Instance]:
    return [
        _one("straight", 10, "homogeneous", 1.0, 0),
        _one("detour", 10, "heterogeneous", 4.0, 0),
    ]


def test_both_algorithms_receive_the_same_graph_and_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def spy(graph: dict, config: dict, *, use_heuristic: bool = False) -> dict:
        snapshot = json.dumps([graph, config], sort_keys=True)
        calls.append((id(graph), id(config), snapshot, use_heuristic))
        return real_solve(graph, config, use_heuristic=use_heuristic)

    monkeypatch.setattr("experiments.sweeps.label_solve", spy)
    instances = _small()
    report = experiment_j_report(2, instances=instances)
    assert report.failed == ()
    assert len(calls) == len(instances) * 2 * 2  # instances x algorithms x repeats
    for index, instance in enumerate(instances):
        mine = calls[index * 4 : index * 4 + 4]
        assert {call[3] for call in mine} == {False, True}
        assert {call[0] for call in mine} == {id(instance.graph)}
        assert {call[1] for call in mine} == {id(instance.config)}
        assert len({call[2] for call in mine}) == 1


def test_a_solver_that_changes_its_input_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    def mutating(graph: dict, config: dict, *, use_heuristic: bool = False) -> dict:
        config["reserve_kwh"] = config["reserve_kwh"] + 0.0001
        return real_solve(graph, config, use_heuristic=use_heuristic)

    monkeypatch.setattr("experiments.sweeps.label_solve", mutating)
    with pytest.raises(InputError, match="changed its input"):
        experiment_j_report(1, instances=[_small()[0]])


def test_interleaved_repeats_alternate_who_goes_first(monkeypatch: pytest.MonkeyPatch) -> None:
    order = []

    def spy(graph: dict, config: dict, *, use_heuristic: bool = False) -> dict:
        order.append(use_heuristic)
        return real_solve(graph, config, use_heuristic=use_heuristic)

    monkeypatch.setattr("experiments.sweeps.label_solve", spy)
    experiment_j_report(3, instances=[_small()[0]])
    assert order == [False, True, True, False, False, True]


@lru_cache(maxsize=2)
def _benchmark_rows(seed: int) -> tuple[tuple[dict[str, str], ...], tuple[str, ...]]:
    rows, failed = experiment_j(1, seed=seed, replicates=1)
    return tuple(rows), failed


def test_the_rows_carry_one_pair_per_instance_with_their_factors() -> None:
    cached, failed = _benchmark_rows(SEED)
    rows = list(cached)
    assert failed == ()
    assert len(rows) == 36 * 2
    assert {row["experiment"] for row in rows} == {"J"}
    assert {row["agreement"] for row in rows} == {"true"}
    for row in rows:
        assert row["note"].startswith(SUITE_NOTE)
        for key in ("geometry=", "size=", "speed=", "soc_step=", "replicate=", "edges="):
            assert key in row["note"]
        assert row["instance_id"].startswith("astar-")
    guided = [row for row in rows if row["algorithm"] == "astar"]
    assert all("states_reduction_pct=" in row["note"] for row in guided)
    assert not any("states_reduction_pct=" in row["note"] for row in rows if row not in guided)


def test_same_seed_repeats_every_column_except_runtime() -> None:
    def stable(rows: list[dict[str, str]]) -> list[dict[str, str]]:
        return [{k: v for k, v in row.items() if k != "runtime_ms"} for row in rows]

    first = list(_benchmark_rows(SEED)[0])
    again, _ = experiment_j(1, replicates=1)
    other = list(_benchmark_rows(SEED + 1)[0])
    assert stable(first) == stable(again)
    assert stable(first) != stable(other)


# --- objective equality is enforced ----------------------------------------------------------


@pytest.mark.parametrize(
    "forged",
    [
        {"total_time_minutes": 1},
        {"number_of_charging_stops": 99},
        {"total_distance_miles": 1.0},
        {"status": "INFEASIBLE"},
    ],
)
def test_a_label_disagreement_fails_and_keeps_the_case(
    monkeypatch: pytest.MonkeyPatch,
    forged: dict,
) -> None:
    def broken(graph: dict, config: dict, *, use_heuristic: bool = False) -> dict:
        result = real_solve(graph, config, use_heuristic=use_heuristic)
        return {**result, **forged} if use_heuristic else result

    monkeypatch.setattr("experiments.sweeps.label_solve", broken)
    instances = _small()
    report = experiment_j_report(1, seed=SEED, instances=instances)
    assert report.failed == tuple(item.instance_id for item in instances)
    assert {row["agreement"] for row in report.rows} == {"false"}
    assert report.summary["objective_mismatches"] == len(instances)
    case = report.failures[0]
    assert case["instance_id"] == instances[0].instance_id
    assert case["seed"] == SEED
    assert case["factors"]["geometry"] == "straight"
    assert case["graph"] == instances[0].graph
    assert case["config"] == instances[0].config
    assert case["label_setting"] != case["astar"] or case["label_setting"] == case["astar"]
    key = next(iter(forged))
    assert case["astar"][key] == forged[key]
    assert case["label_setting"][key] != forged[key]


def test_a_disagreement_fails_the_run_and_writes_the_reproduction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(graph: dict, config: dict, *, use_heuristic: bool = False) -> dict:
        result = real_solve(graph, config, use_heuristic=use_heuristic)
        if use_heuristic and result["status"] == "FEASIBLE":
            return {**result, "number_of_charging_stops": result["number_of_charging_stops"] + 1}
        return result

    monkeypatch.setattr("experiments.sweeps.label_solve", broken)
    summary = run(
        tmp_path,
        seed=SEED,
        tiny_count=1,
        correctness_count=0,
        sweeps=True,
        timing_repeats=1,
        plots_dir=tmp_path / "plots",
        charger_counts=(10,),
        capacities=(70,),
        scaling_replicates=1,
        astar_replicates=1,
    )
    failed_ids = [d for d in summary.disagreements if d.startswith("astar-")]
    assert len(failed_ids) == 36
    stored = json.loads((tmp_path / FAILURES_FILE).read_text(encoding="utf-8"))
    assert [case["instance_id"] for case in stored] == failed_ids
    assert {"graph", "config", "seed", "factors", "label_setting", "astar"} <= set(stored[0])
    # A later clean run removes the stale reproduction file.
    monkeypatch.undo()
    clean = run(
        tmp_path,
        seed=SEED,
        tiny_count=1,
        correctness_count=0,
        sweeps=True,
        timing_repeats=1,
        plots_dir=tmp_path / "plots",
        charger_counts=(10,),
        capacities=(70,),
        scaling_replicates=1,
        astar_replicates=1,
    )
    assert clean.disagreements == ()
    assert not (tmp_path / FAILURES_FILE).exists()


# --- metric calculations ---------------------------------------------------------------------


def test_reduction_pct_formula_and_edge_cases() -> None:
    assert reduction_pct(100, 75) == 25.0
    assert reduction_pct(200, 150) == 25.0
    assert reduction_pct(100, 100) == 0.0
    assert reduction_pct(100, 0) == 100.0
    assert reduction_pct(100, 120) == -20.0  # A* expanded more: negative, kept
    assert reduction_pct(0, 0) is None  # zero denominator
    assert reduction_pct(0, 5) is None


def _row(
    instance_id: str,
    algorithm: str,
    states: int,
    *,
    status: str = "FEASIBLE",
    time: float = 100.0,
    peak: int | None = None,
    runtime: float = 1.0,
    geometry: str = "straight",
    size: int = 10,
    speed: str = "homogeneous",
    soc: str = "1",
    distance: float = 80.0,
    stops: int = 1,
) -> dict[str, str]:
    note = (
        f"{SUITE_NOTE}; geometry={geometry}; size={size}; speed={speed}; soc_step={soc}; "
        "replicate=0; edges=10; capacity_steps=40; straight_miles=100; "
        "h_origin_minutes=60; min_stops=1"
    )
    return metric_row(
        experiment="J",
        instance_id=instance_id,
        seed=1,
        nodes=12,
        chargers=size,
        capacity_kwh=40,
        soc_step_kwh=float(soc),
        algorithm=algorithm,
        status=status,
        result={
            "algorithm": algorithm,
            "status": status,
            "total_time_minutes": time if status == "FEASIBLE" else 0,
            "number_of_charging_stops": stops if status == "FEASIBLE" else 0,
            "total_distance_miles": distance if status == "FEASIBLE" else 0,
            "metrics": {
                "runtime_ms": runtime,
                "states_expanded": states,
                "peak_states_stored": states if peak is None else peak,
            },
        },
        agreement="true",
        note=note,
    )


def _pair_rows(name: str, plain: int, guided: int, **kwargs) -> list[dict[str, str]]:
    runtimes = kwargs.pop("runtimes", (1.0, 1.0))
    peaks = kwargs.pop("peaks", (None, None))
    return [
        _row(name, "label_setting", plain, runtime=runtimes[0], peak=peaks[0], **kwargs),
        _row(name, "astar", guided, runtime=runtimes[1], peak=peaks[1], **kwargs),
    ]


def test_zero_negative_and_undefined_reductions_are_counted_not_dropped() -> None:
    rows = (
        _pair_rows("astar-a", 100, 75)  # 25%
        + _pair_rows("astar-b", 100, 100)  # 0: no improvement
        + _pair_rows("astar-c", 100, 120)  # -20%: A* expanded more
        + _pair_rows("astar-d", 200, 100)  # 50%
        + _pair_rows("astar-e", 0, 0, status="INFEASIBLE")  # zero denominator
        + _pair_rows("astar-f", 50, 50, status="INFEASIBLE")  # infeasible, no saving
    )
    summary = summarize_astar(rows)
    assert (summary["instances"], summary["feasible"], summary["infeasible"]) == (6, 4, 2)
    assert summary["negative_reduction_instances"] == ["astar-c"]
    assert summary["objective_mismatches"] == 0
    feasible = summary["overall"]["feasible_only"]
    assert feasible["undefined_reduction"] == 0
    assert feasible["states_reduction_pct"]["min"] == -20.0
    assert feasible["states_reduction_pct"]["max"] == 50.0
    assert feasible["states_reduction_pct"]["median"] == statistics.median([25, 0, -20, 50])
    assert feasible["fraction_zero"] == 0.25
    assert feasible["fraction_negative"] == 0.25
    assert feasible["fraction_no_improvement"] == 0.5
    # Pooled, not the mean of percentages: (500 - 395) / 500.
    assert feasible["pooled_states_reduction_pct"] == pytest.approx(100 * (500 - 395) / 500)
    assert feasible["pooled_states_reduction_pct"] != pytest.approx(
        statistics.mean([25, 0, -20, 50])
    )
    infeasible = summary["overall"]["infeasible_only"]
    assert infeasible["undefined_reduction"] == 1  # the zero-denominator instance
    assert infeasible["states_reduction_pct"]["median"] == 0.0
    assert infeasible["fraction_no_improvement"] == 1.0
    everything = summary["overall"]["all"]
    assert everything["instances"] == 6
    assert everything["undefined_reduction"] == 1


def test_peak_and_runtime_comparisons_use_their_own_denominators() -> None:
    rows = _pair_rows("astar-a", 100, 80, peaks=(120, 90), runtimes=(10.0, 12.0)) + _pair_rows(
        "astar-b", 100, 80, peaks=(100, 110), runtimes=(10.0, 8.0)
    )
    scope = summarize_astar(rows)["overall"]["feasible_only"]
    assert scope["peak_reduction_pct"]["min"] == pytest.approx(-10.0)
    assert scope["peak_reduction_pct"]["max"] == pytest.approx(25.0)
    assert scope["pooled_peak_reduction_pct"] == pytest.approx(100 * (220 - 200) / 220)
    assert scope["fraction_peak_not_lower"] == 0.5
    assert scope["runtime_ratio_astar_over_ls"]["median"] == pytest.approx(1.0)
    assert scope["pooled_runtime_ratio_astar_over_ls"] == pytest.approx(20.0 / 20.0)
    assert scope["fraction_astar_slower"] == 0.5


def test_a_label_difference_in_the_rows_is_counted_as_a_mismatch() -> None:
    rows = _pair_rows("astar-a", 100, 90)
    rows[1] = _row("astar-a", "astar", 90, time=101.0)
    summary = summarize_astar(rows)
    assert summary["objective_mismatches"] == 1
    assert summary["mismatched_instances"] == ["astar-a"]


def test_the_summary_ignores_s6_rows_and_rejects_half_measured_instances() -> None:
    s6 = _row("detroit-chicago", "label_setting", 127)
    s6["note"] = "suite=S6; set=sample"
    s6_pair = [s6, {**s6, "algorithm": "astar"}]
    rows = s6_pair + _pair_rows("astar-a", 100, 90)
    assert [p["instance_id"] for p in instance_pairs(rows)] == ["astar-a"]
    with pytest.raises(ValueError, match="missing"):
        summarize_astar(_pair_rows("astar-a", 100, 90)[:1])
    with pytest.raises(ValueError, match="no dedicated"):
        summarize_astar(s6_pair)


def test_breakdowns_group_the_rows_by_factor_level() -> None:
    rows = (
        _pair_rows("astar-a", 100, 50, geometry="straight", size=10, speed="homogeneous", soc="1")
        + _pair_rows("astar-b", 100, 90, geometry="detour", size=25, speed="heterogeneous", soc="4")
        + _pair_rows("astar-c", 100, 80, geometry="detour", size=25, speed="homogeneous", soc="1")
    )
    summary = summarize_astar(rows)
    assert list(summary["by_size"]) == ["10", "25"]
    assert summary["by_size"]["25"]["instances"] == 2
    assert summary["by_geometry"]["detour"]["feasible_only"]["states_reduction_pct"][
        "median"
    ] == pytest.approx(15.0)
    assert summary["by_geometry"]["straight"]["feasible_only"]["states_reduction_pct"][
        "median"
    ] == pytest.approx(50.0)
    assert list(summary["by_soc_step"]) == ["1", "4"]
    assert summary["by_speed"]["heterogeneous"]["instances"] == 1
    assert set(summary["by_geometry_speed"]) == {
        "straight/homogeneous",
        "detour/heterogeneous",
        "detour/homogeneous",
    }


def test_aggregates_match_a_direct_recomputation_from_measured_rows() -> None:
    report = experiment_j_report(1, instances=list(_grid()[:12]) + list(_grid()[40:52]))
    rows = report.rows
    summary = report.summary
    by_instance: dict[str, dict[str, dict[str, str]]] = {}
    for row in rows:
        by_instance.setdefault(row["instance_id"], {})[row["algorithm"]] = row
    assert summary["instances"] == len(by_instance) == 24
    reductions: dict[str, list[float]] = {}
    for pair in by_instance.values():
        plain, guided = pair["label_setting"], pair["astar"]
        if plain["status"] != "FEASIBLE":
            continue
        a, b = int(plain["states_expanded"]), int(guided["states_expanded"])
        value = 100.0 * (a - b) / a
        assert float(guided["note"].split("states_reduction_pct=")[1]) == pytest.approx(value)
        geometry = plain["note"].split("geometry=")[1].split(";")[0]
        reductions.setdefault(geometry, []).append(value)
        reductions.setdefault("all", []).append(value)
    overall = summary["overall"]["feasible_only"]["states_reduction_pct"]
    assert overall["median"] == pytest.approx(statistics.median(reductions["all"]))
    assert overall["min"] == pytest.approx(min(reductions["all"]))
    assert overall["max"] == pytest.approx(max(reductions["all"]))
    for geometry, values in reductions.items():
        if geometry == "all":
            continue
        got = summary["by_geometry"][geometry]["feasible_only"]["states_reduction_pct"]
        assert got["median"] == pytest.approx(statistics.median(values))
    total_ls = sum(
        int(p["label_setting"]["states_expanded"])
        for p in by_instance.values()
        if p["label_setting"]["status"] == "FEASIBLE"
    )
    total_a = sum(
        int(p["astar"]["states_expanded"])
        for p in by_instance.values()
        if p["label_setting"]["status"] == "FEASIBLE"
    )
    assert summary["overall"]["feasible_only"]["pooled_states_reduction_pct"] == pytest.approx(
        100.0 * (total_ls - total_a) / total_ls
    )
    group_sizes = sum(group["instances"] for group in summary["by_size"].values())
    assert group_sizes == summary["instances"]
    assert summary["objective_mismatches"] == 0
    assert summary["design"]["cells"] > 1
    for key in ("reduction_vs_heuristic_ratio", "reduction_vs_route_ratio"):
        assert summary["rank_correlation"][key]["n"] == summary["feasible"]
        rho = summary["rank_correlation"][key]["rho"]
        assert rho is None or -1.0 <= rho <= 1.0


def test_rank_correlation_is_correct_on_a_known_ordering() -> None:
    rows = []
    for index, (reduction_states, h_minutes) in enumerate([(90, 10), (80, 20), (70, 30), (60, 40)]):
        name = f"astar-{index}"
        for algorithm, states in (("label_setting", 100), ("astar", reduction_states)):
            row = _row(name, algorithm, states)
            row["note"] = row["note"].replace(
                "h_origin_minutes=60", f"h_origin_minutes={h_minutes}"
            )
            rows.append(row)
    rho = summarize_astar(rows)["rank_correlation"]["reduction_vs_heuristic_ratio"]["rho"]
    # A larger heuristic ratio goes with a larger reduction here, so the correlation is +1.
    assert rho == pytest.approx(1.0)
    for row in rows:
        row["note"] = row["note"].replace("h_origin_minutes=10", "h_origin_minutes=99")
    changed = summarize_astar(rows)["rank_correlation"]["reduction_vs_heuristic_ratio"]["rho"]
    assert changed < 1.0
