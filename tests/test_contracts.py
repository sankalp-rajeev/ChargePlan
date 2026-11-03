"""Validate frozen samples against the Phase 1 JSON schemas.

Also checks the Section 16.1 rule that each edge is at least as long as the
straight-line distance between its endpoints. That check belongs here, not
in the A* heuristic.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
SYNTHETIC = ROOT / "data" / "synthetic"
MOCK_RESULTS = ROOT / "visualizer" / "mock_results"

EARTH_RADIUS_MILES = 3958.7613

# Skeleton modules that must import without running a search.
_MODULES = (
    "optimizer.model",
    "optimizer.cli",
    "optimizer.battery_state_optimizer",
    "optimizer.greedy_baseline",
    "optimizer.exact_solver",
    "optimizer.reconstruction",
    "data_pipeline.chargers",
    "data_pipeline.road_graph",
    "data_pipeline.reduce_graph",
    "data_pipeline.export_graph",
    "visualizer.app",
    "visualizer.map_view",
    "visualizer.soc_chart",
    "visualizer.comparison",
    "experiments.correctness",
    "experiments.greedy_comparison",
    "experiments.soc_resolution",
    "experiments.scaling",
    "experiments.run_all",
)


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _validate(instance: dict, schema_name: str) -> None:
    schema = _load(CONTRACTS / schema_name)
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator_cls(schema).validate(instance)


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles. Used only for the contract check."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    haversine = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(haversine))


def test_canonical_graph_sample_matches_schema() -> None:
    _validate(_load(SYNTHETIC / "canonical_graph.json"), "canonical_graph.schema.json")


def test_trip_config_sample_matches_schema() -> None:
    _validate(_load(SYNTHETIC / "trip_config.json"), "trip_config.schema.json")


def test_algorithm_result_sample_matches_schema() -> None:
    _validate(_load(SYNTHETIC / "algorithm_result.json"), "algorithm_result.schema.json")


def test_mock_results_match_result_schema() -> None:
    mocks = sorted(MOCK_RESULTS.glob("*.json"))
    assert mocks, "visualizer/mock_results needs at least one result file"
    for path in mocks:
        _validate(_load(path), "algorithm_result.schema.json")


def test_infeasible_result_must_have_empty_plan() -> None:
    schema_name = "algorithm_result.schema.json"
    feasible = _load(SYNTHETIC / "algorithm_result.json")
    infeasible = dict(feasible)
    infeasible["status"] = "INFEASIBLE"
    infeasible["route_node_ids"] = []
    infeasible["events"] = []
    _validate(infeasible, schema_name)

    rejected = dict(infeasible)
    rejected["route_node_ids"] = ["origin"]
    schema = _load(CONTRACTS / schema_name)
    validator_cls = jsonschema.validators.validator_for(schema)
    errors = list(validator_cls(schema).iter_errors(rejected))
    assert errors


def test_edge_distances_cover_straight_line() -> None:
    graph = _load(SYNTHETIC / "canonical_graph.json")
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert len(nodes) == len(graph["nodes"])
    for edge in graph["edges"]:
        assert edge["from"] in nodes
        assert edge["to"] in nodes
        start = nodes[edge["from"]]
        end = nodes[edge["to"]]
        straight = haversine_miles(start["lat"], start["lon"], end["lat"], end["lon"])
        assert edge["distance_miles"] + 1e-6 >= straight


def test_skeleton_modules_import() -> None:
    for name in _MODULES:
        __import__(name)
