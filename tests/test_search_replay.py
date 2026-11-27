"""Search trace (optimizer hook) and the replay payload built from it."""

import json
from pathlib import Path

import pytest

from optimizer.battery_state_optimizer import solve
from visualizer.search_replay import (
    build_payload,
    capture,
    column_order,
    optimal_state_path,
    replay_html,
)

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "synthetic"


@pytest.fixture
def sample():
    graph = json.loads((SAMPLE / "canonical_graph.json").read_text())
    config = json.loads((SAMPLE / "trip_config.json").read_text())
    return graph, config


@pytest.mark.parametrize("use_heuristic", [False, True])
def test_trace_observes_without_changing_the_search(sample, use_heuristic):
    graph, config = sample
    plain = solve(graph, config, use_heuristic=use_heuristic)
    trace: list = []
    traced = solve(graph, config, use_heuristic=use_heuristic, trace=trace)
    for key in (
        "status",
        "total_time_minutes",
        "number_of_charging_stops",
        "total_distance_miles",
        "route_node_ids",
        "events",
    ):
        assert traced[key] == plain[key]
    assert traced["metrics"]["states_expanded"] == plain["metrics"]["states_expanded"]
    assert len(trace) == plain["metrics"]["states_expanded"]
    assert trace[0].state == (config["origin"], 54, 0) and trace[0].parent is None
    assert trace[-1].state[0] == config["destination"]


def test_trace_settles_each_state_once_in_label_order(sample):
    graph, config = sample
    _result, trace = capture(graph, config)
    states = [record.state for record in trace]
    assert len(states) == len(set(states))
    labels = [record.label for record in trace]
    assert labels == sorted(labels)


def test_optimal_state_path_matches_the_plan(sample):
    graph, config = sample
    result, trace = capture(graph, config)
    path = optimal_state_path(trace, trace[-1].state)
    assert path[0] == trace[0].state
    visited = []
    for node, _battery, _flag in path:
        if not visited or visited[-1] != node:
            visited.append(node)
    assert visited == result["route_node_ids"]
    assert [b for n, b, _f in path if n == "station_1"][-1] == 70


def test_payload_and_page(sample):
    graph, config = sample
    result, trace = capture(graph, config)
    order = column_order(graph, config)
    assert order[0] == config["origin"] and order[-1] == config["destination"]
    payload = build_payload(graph, config, result, trace)
    assert len(payload["steps"]) == len(trace)
    assert payload["capacity_steps"] == 70 and payload["reserve_steps"] == 7
    assert payload["optimal"][0] == [0, 54] and payload["optimal"][-1] == [len(order) - 1, 7]
    assert payload["total_time_minutes"] == result["total_time_minutes"]
    page = replay_html(payload)
    assert page.count("<script") == 2 and "__PAYLOAD__" not in page
    assert json.dumps(payload["nodes"][0]["id"]) in page


def test_explored_links_form_a_wave_from_the_origin(sample):
    from visualizer.search_replay import explored_links

    graph, config = sample
    _result, trace = capture(graph, config)
    links = explored_links(graph, config, trace)
    pairs = {(link["from"], link["to"]) for link in links}
    assert len(pairs) == len(links)
    assert all(link["start"] <= link["end"] for link in links)
    assert min(link["start"] for link in links) == 0
    assert not any(link["from"] == config["destination"] for link in links)
    # every drive the search settled travelled along an explored road
    for record in trace:
        if record.parent and record.parent[0] != record.state[0]:
            assert (record.parent[0], record.state[0]) in pairs


def test_map_layout_page(sample):
    graph, config = sample
    result, trace = capture(graph, config)
    page = replay_html(build_payload(graph, config, result, trace), layout="map")
    assert '<body class="maponly">' in page and "__LAYOUT__" not in page
    assert '<body class="">' in replay_html(build_payload(graph, config, result, trace))


def test_street_map_is_the_default_basemap(sample):
    graph, config = sample
    result, trace = capture(graph, config)
    payload = build_payload(graph, config, result, trace)
    assert "const START_BASEMAP = 'street' in THEMES" in replay_html(payload, layout="map")
    assert "const START_BASEMAP = 'dark' in THEMES" in replay_html(payload, basemap="dark")
