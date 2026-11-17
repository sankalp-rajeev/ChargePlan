"""The per-edge precompute in the solvers must not change any answer.

``tests/fixtures/solver_golden.json`` was written by the solvers as they were
at commit 20835f9, before edge energy and drive time were computed once per
edge. It holds every result field except ``runtime_ms`` for the Detroit-Chicago
sample and the first 50 instances of ``experiments.exact_suite.build_suite(505)``,
for label-setting, A*, and (inside the exact bound) the exact solver. The tests
re-run today's solvers and require the same status, label, route, events,
``states_expanded``, and ``peak_states_stored``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pytest

from experiments.exact_suite import build_suite
from experiments.instances import Instance, exact_block_reason, load_sample
from optimizer import battery_state_optimizer, exact_solver

GOLDEN = Path(__file__).parent / "fixtures" / "solver_golden.json"
SEED = 505
COUNT = 50


@lru_cache(maxsize=1)
def _golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _instances() -> tuple[Instance, ...]:
    return (load_sample(), *build_suite(SEED, COUNT))


def _without_runtime(result: dict) -> dict:
    copy = json.loads(json.dumps(result))
    copy["metrics"].pop("runtime_ms")
    return copy


def _run(name: str, instance: Instance) -> dict:
    if name == "label_setting":
        return battery_state_optimizer.solve(instance.graph, instance.config, use_heuristic=False)
    if name == "astar":
        return battery_state_optimizer.solve(instance.graph, instance.config, use_heuristic=True)
    return exact_solver.solve(instance.graph, instance.config)


def test_golden_file_covers_the_sample_and_fifty_seeded_instances() -> None:
    golden = _golden()
    ids = [instance.instance_id for instance in _instances()]
    assert len(ids) == COUNT + 1
    assert ids[0] == "detroit-chicago"
    assert sorted(golden) == sorted(ids)
    for instance in _instances():
        expected = {"label_setting", "astar"}
        if exact_block_reason(instance.graph, instance.config) is None:
            expected.add("exact")
        assert set(golden[instance.instance_id]) == expected


@pytest.mark.parametrize("algorithm", ["label_setting", "astar", "exact"])
def test_results_match_the_pre_change_solvers(algorithm: str) -> None:
    golden = _golden()
    checked = 0
    for instance in _instances():
        recorded = golden[instance.instance_id].get(algorithm)
        if recorded is None:
            continue
        result = _run(algorithm, instance)
        context = f"{algorithm} on {instance.instance_id}"
        assert _without_runtime(result) == recorded, context
        # The fields called out by the S9 task, spelled out so a failure names them.
        assert result["status"] == recorded["status"], context
        for key in ("total_time_minutes", "number_of_charging_stops", "total_distance_miles"):
            assert result[key] == recorded[key], f"{context}: {key}"
        assert result["metrics"]["states_expanded"] == recorded["metrics"]["states_expanded"]
        assert result["metrics"]["peak_states_stored"] == recorded["metrics"]["peak_states_stored"]
        checked += 1
    if algorithm == "exact":
        assert checked == COUNT
    else:
        assert checked == COUNT + 1
