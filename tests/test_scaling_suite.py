"""Experiment D: the charger-scaling generator, its rows, and their aggregate.

These tests check the properties the redesign promises. The old generator put
every station on one line, tied the rate to the list index, linked each station
to the next three, and produced one instance per count.
"""

from __future__ import annotations

import json
import math
import statistics
from functools import lru_cache
from pathlib import Path

import pytest

from experiments.instances import (
    CHARGER_COUNTS,
    CHARGER_SCALE_MAX,
    SCALING_REPLICATES,
    Instance,
    scaling_instances,
)
from experiments.scaling import quartiles, summarize_scaling
from experiments.scaling_suite import (
    POOL_SIZE,
    RATE_MIX,
    ROAD_SPEED_MPH,
    TRIP,
    _Roads,
    build_pool,
    build_scaling_suite,
    max_usable_steps,
    stations_of,
)
from experiments.sweeps import experiment_d
from optimizer.battery_state_optimizer import solve
from optimizer.model import (
    InputError,
    edge_energy_steps,
    haversine_miles,
    load_config,
    load_graph,
)

SEED = 505
COUNTS = (10, 25, 50, 100)


@lru_cache(maxsize=1)
def _suite() -> tuple[Instance, ...]:
    return tuple(scaling_instances(COUNTS, seed=SEED, replicates=4))


def _by(replicate: int, count: int) -> Instance:
    return next(
        item
        for item in _suite()
        if item.meta["replicate"] == replicate and item.meta["count"] == count
    )


def _nodes(instance: Instance) -> dict[str, dict]:
    return {node["id"]: node for node in instance.graph["nodes"]}


def test_defaults_are_ten_replicates_over_four_counts() -> None:
    assert CHARGER_COUNTS == (10, 25, 50, 100)
    assert SCALING_REPLICATES == 10
    assert CHARGER_SCALE_MAX == POOL_SIZE == 200


def test_one_instance_per_replicate_and_count() -> None:
    suite = _suite()
    assert len(suite) == 4 * len(COUNTS)
    assert len({item.instance_id for item in suite}) == len(suite)
    assert [item.charger_count for item in suite[: len(COUNTS)]] == list(COUNTS)
    assert suite[0].instance_id == "scale-010-r00"


def test_same_seed_gives_identical_graphs_and_a_different_seed_does_not() -> None:
    again = scaling_instances(COUNTS, seed=SEED, replicates=4)
    assert [item.graph for item in again] == [item.graph for item in _suite()]
    assert [item.config for item in again] == [item.config for item in _suite()]
    other = scaling_instances((10, 25), seed=SEED + 1, replicates=1)
    assert other[0].graph != _by(0, 10).graph
    assert other[1].graph != _by(0, 25).graph


def test_replicates_have_different_station_pools() -> None:
    first = stations_of(_by(0, 50))
    second = stations_of(_by(1, 50))
    assert [(s["lat"], s["lon"]) for s in first] != [(s["lat"], s["lon"]) for s in second]


def test_trip_and_vehicle_are_fixed_across_counts_and_replicates() -> None:
    reference = _by(0, 10)
    origin = _nodes(reference)["origin"]
    destination = _nodes(reference)["destination"]
    for item in _suite():
        assert item.config == TRIP
        assert (_nodes(item)["origin"]["lat"], _nodes(item)["origin"]["lon"]) == (
            origin["lat"],
            origin["lon"],
        )
        assert _nodes(item)["destination"]["lat"] == destination["lat"]
        assert _nodes(item)["destination"]["lon"] == destination["lon"]
        assert item.capacity_kwh == 40
        assert item.soc_step_kwh == 1


def test_smaller_counts_are_prefixes_of_larger_ones() -> None:
    for replicate in range(4):
        for small, large in zip(COUNTS, COUNTS[1:], strict=False):
            first = stations_of(_by(replicate, small))
            second = stations_of(_by(replicate, large))
            assert first == second[:small]


def test_roads_between_shared_nodes_do_not_change_with_the_count() -> None:
    for replicate in range(4):
        small = {(e["from"], e["to"]): e for e in _by(replicate, 25).graph["edges"]}
        large = {(e["from"], e["to"]): e for e in _by(replicate, 50).graph["edges"]}
        shared_nodes = set(_nodes(_by(replicate, 25)))
        for (start, end), edge in large.items():
            if start in shared_nodes and end in shared_nodes:
                assert small[(start, end)] == edge
        assert set(small) <= set(large)


def test_pool_is_independent_of_the_requested_size() -> None:
    assert build_pool(SEED, 3, 25) == build_pool(SEED, 3, 100)[:25]
    assert build_pool(SEED, 3, 10) == build_pool(SEED, 3, 200)[:10]
    with pytest.raises(InputError, match="pool size"):
        build_pool(SEED, 0, POOL_SIZE + 1)


def test_stations_are_not_collinear() -> None:
    instance = _by(0, 100)
    nodes = _nodes(instance)
    origin = nodes["origin"]
    destination = nodes["destination"]
    stations = stations_of(instance)
    assert len({station["lat"] for station in stations}) > 90
    straight = haversine_miles(origin["lat"], origin["lon"], destination["lat"], destination["lon"])
    off_line = []
    for station in stations:
        there = haversine_miles(origin["lat"], origin["lon"], station["lat"], station["lon"])
        back = haversine_miles(
            station["lat"], station["lon"], destination["lat"], destination["lon"]
        )
        off_line.append(there + back - straight)  # zero for a station on the segment
    assert max(off_line) > 10
    assert statistics.median(off_line) > 0.2
    # Some stations are real detours and most are near the corridor.
    assert sum(1 for extra in off_line if extra > 5) >= 3
    assert sum(1 for extra in off_line if extra < 5) >= 50


def test_rates_do_not_follow_the_station_index() -> None:
    pools = [build_pool(SEED, replicate, 100) for replicate in range(10)]
    allowed = {rate for rate, _ in RATE_MIX}
    assert {station.rate_kw for pool in pools for station in pool} == allowed
    # The same index gets different rates in different replicates.
    assert len({pool[0].rate_kw for pool in pools}) > 1
    assert len({pool[1].rate_kw for pool in pools}) > 1
    # Even and odd indices see the same mixture, unlike the old parity rule.
    for parity in (0, 1):
        rates = [s.rate_kw for pool in pools for s in pool if s.index % 2 == parity]
        assert len(set(rates)) == len(allowed)
        fast = sum(1 for rate in rates if rate >= 150) / len(rates)
        assert 0.6 < fast < 0.8


def test_every_count_has_nearly_the_same_rate_mix() -> None:
    total = sum(weight for _, weight in RATE_MIX)
    for item in _suite():
        stations = stations_of(item)
        for rate, weight in RATE_MIX:
            expected = len(stations) * weight / total
            actual = sum(1 for station in stations if station["charge_rate_kw"] == rate)
            assert abs(actual - expected) <= 1.5, (item.instance_id, rate)
        assert 0.6 <= item.meta["fast_share"] <= 0.8 + 1e-9


def test_edges_follow_the_range_rule_exactly() -> None:
    usable = max_usable_steps(TRIP)
    for replicate in range(2):
        for count in (10, 50):
            instance = _by(replicate, count)
            roads = _Roads(SEED, replicate)
            located = [(n["id"], n["lat"], n["lon"]) for n in instance.graph["nodes"]]
            present = {(e["from"], e["to"]) for e in instance.graph["edges"]}
            for start in located:
                for end in located:
                    if start[0] == end[0]:
                        continue
                    miles, _ = roads.get(start, end)
                    should = (
                        start[0] != "destination"
                        and end[0] != "origin"
                        and edge_energy_steps(miles, TRIP) <= usable
                    )
                    assert ((start[0], end[0]) in present) is should, (start[0], end[0])


def test_connectivity_is_not_the_next_three_stations() -> None:
    for count in COUNTS:
        instance = _by(0, count)
        out_degree: dict[str, int] = {}
        for edge in instance.graph["edges"]:
            out_degree[edge["from"]] = out_degree.get(edge["from"], 0) + 1
        assert instance.meta["edges"] == len(instance.graph["edges"])
        if count >= 25:
            assert instance.meta["edges"] != 3 * count
            assert max(out_degree.values()) > 3
    # Density grows with the count, so the edge count rises faster than the nodes.
    edges = [_by(0, count).meta["edges"] for count in COUNTS]
    assert edges == sorted(edges)
    assert edges[-1] / edges[0] > (COUNTS[-1] + 2) / (COUNTS[0] + 2)


def test_roads_are_valid_positive_and_not_shorter_than_the_straight_line(
    tmp_path: Path,
) -> None:
    for item in (_by(0, 10), _by(1, 50), _by(2, 100)):
        graph_path = tmp_path / f"{item.instance_id}.graph.json"
        config_path = tmp_path / f"{item.instance_id}.config.json"
        graph_path.write_text(json.dumps(item.graph), encoding="utf-8")
        config_path.write_text(json.dumps(item.config), encoding="utf-8")
        graph = load_graph(graph_path)  # schema, unique ids, road >= straight line
        load_config(config_path)
        nodes = _nodes(item)
        assert graph["edges"]
        for edge in graph["edges"]:
            start, end = nodes[edge["from"]], nodes[edge["to"]]
            straight = haversine_miles(start["lat"], start["lon"], end["lat"], end["lon"])
            assert edge["distance_miles"] >= straight
            assert edge["distance_miles"] <= straight * 1.36 + 0.001
            assert edge["drive_time_minutes"] > 0
            speed = edge["distance_miles"] / edge["drive_time_minutes"] * 60
            assert ROAD_SPEED_MPH[0] - 1 <= speed <= ROAD_SPEED_MPH[1] + 1
        assert not any(edge["from"] == "destination" for edge in graph["edges"])
        assert not any(edge["to"] == "origin" for edge in graph["edges"])


def test_the_trip_needs_charging_and_is_mostly_feasible() -> None:
    suite = _suite()
    direct = [item for item in suite if item.meta["min_stops"] == 0]
    assert direct == []
    infeasible = [item for item in suite if item.meta["min_stops"] is None]
    assert len(infeasible) <= len(suite) // 4
    assert all(item.meta["min_stops"] >= 1 for item in suite if item.meta["min_stops"] is not None)
    assert any(item.meta["min_stops"] >= 2 for item in suite)
    for item in (_by(0, 10), _by(0, 50)):
        result = solve(item.graph, item.config, use_heuristic=False)
        assert result["status"] == "FEASIBLE"
        assert result["number_of_charging_stops"] >= item.meta["min_stops"]
        assert result["number_of_charging_stops"] >= 1


def test_label_setting_and_astar_agree_on_the_objective() -> None:
    # Only the label must match. How many states A* expands is Experiment J's question.
    for item in _suite():
        if item.meta["count"] > 50:
            continue
        plain = solve(item.graph, item.config, use_heuristic=False)
        guided = solve(item.graph, item.config, use_heuristic=True)
        assert plain["status"] == guided["status"]
        for key in ("total_time_minutes", "number_of_charging_stops", "total_distance_miles"):
            assert plain[key] == guided[key], item.instance_id


def test_counts_outside_the_pool_and_bad_arguments_are_rejected() -> None:
    with pytest.raises(InputError, match="outside 1"):
        scaling_instances((CHARGER_SCALE_MAX + 1,), replicates=1)
    with pytest.raises(InputError, match="outside 1"):
        scaling_instances((0,), replicates=1)
    with pytest.raises(InputError, match="distinct"):
        scaling_instances((10, 10), replicates=1)
    with pytest.raises(InputError, match="replicates"):
        build_scaling_suite(SEED, (10,), 0)
    with pytest.raises(InputError, match="charger count"):
        build_scaling_suite(SEED, (), 1)


def test_experiment_d_rows_are_complete_deterministic_and_agree() -> None:
    first, failed = experiment_d(1, (10, 25), seed=SEED, replicates=3)
    again, _ = experiment_d(1, (10, 25), seed=SEED, replicates=3)
    assert failed == ()
    assert len(first) == 3 * 2 * 2  # replicates x counts x algorithms
    assert {row["experiment"] for row in first} == {"D"}
    assert {row["agreement"] for row in first} == {"true"}
    assert {row["algorithm"] for row in first} == {"label_setting", "astar"}
    assert {float(row["capacity_kwh"]) for row in first} == {40.0}
    assert {float(row["soc_step_kwh"]) for row in first} == {1.0}
    assert {row["seed"] for row in first} == {str(SEED)}
    assert len({row["instance_id"] for row in first}) == 6
    for row in first:
        assert "replicate=" in row["note"]
        assert "edges=" in row["note"]
        assert "min_stops=" in row["note"]

    def stable(rows: list[dict[str, str]]) -> list[dict[str, str]]:
        return [{k: v for k, v in row.items() if k != "runtime_ms"} for row in rows]

    assert stable(first) == stable(again)


def test_summary_matches_a_direct_recomputation() -> None:
    rows, _ = experiment_d(1, (10, 25), seed=SEED, replicates=4)
    summary = summarize_scaling(rows)
    assert [record["chargers"] for record in summary["counts"]] == [10, 25]
    assert summary["instances"] == 8
    for record in summary["counts"]:
        wanted = [
            row
            for row in rows
            if row["algorithm"] == "label_setting" and int(row["chargers"]) == record["chargers"]
        ]
        assert record["instances"] == len(wanted) == 4
        assert record["feasible"] + record["infeasible"] == record["instances"]
        states = [float(row["states_expanded"]) for row in wanted]
        assert record["label_setting"]["states_expanded"]["median"] == statistics.median(states)
        assert record["label_setting"]["states_expanded"]["min"] == min(states)
        assert record["label_setting"]["states_expanded"]["max"] == max(states)
        peak = [float(row["peak_states_stored"]) for row in wanted]
        assert record["label_setting"]["peak_states_stored"]["median"] == statistics.median(peak)
        edges = [float(row["note"].split("edges=")[1].split(";")[0]) for row in wanted]
        assert record["edges"]["median"] == statistics.median(edges)
        assert record["nodes"] == record["chargers"] + 2
        assert record["state_ceiling"] == 2 * record["nodes"] * (record["capacity_steps"] + 1)
        assert 0 < record["label_setting"]["states_over_ceiling"]["median"] <= 1
        ratios = [
            float(pair_a["states_expanded"]) / float(pair_l["states_expanded"])
            for pair_l, pair_a in zip(wanted, _astar_rows(rows, record["chargers"]), strict=True)
        ]
        assert record["astar_over_label_states"]["median"] == statistics.median(ratios)
    slope = summary["growth"][0]
    assert slope["from"] == 10 and slope["to"] == 25
    expected = math.log(
        summary["counts"][1]["label_setting"]["states_expanded"]["median"]
        / summary["counts"][0]["label_setting"]["states_expanded"]["median"]
    ) / math.log(2.5)
    assert slope["states_expanded"] == pytest.approx(expected)


def _astar_rows(rows: list[dict[str, str]], chargers: int) -> list[dict[str, str]]:
    return [
        row for row in rows if row["algorithm"] == "astar" and int(row["chargers"]) == chargers
    ]


def test_summary_rejects_a_count_with_an_unpaired_row() -> None:
    rows, _ = experiment_d(1, (10,), seed=SEED, replicates=2)
    unpaired = [row for row in rows if row["algorithm"] == "label_setting"]
    with pytest.raises(ValueError, match="missing"):
        summarize_scaling(unpaired)


def test_quartiles_use_the_inclusive_method() -> None:
    assert quartiles([5.0]) == (5.0, 5.0, 5.0)
    assert quartiles([1.0, 2.0, 3.0, 4.0, 5.0]) == (2.0, 3.0, 4.0)
    assert quartiles([1.0, 2.0]) == (1.25, 1.5, 1.75)
    with pytest.raises(ValueError):
        quartiles([])
