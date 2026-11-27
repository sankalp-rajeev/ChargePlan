"""Visualizer entry point.

Streamlit app that displays a precomputed ``algorithm_result.json`` on a map.
It reads result files and never recomputes a route. ``make demo`` calls this
module::

    python -m visualizer.app
    python -m visualizer.app --graph data/synthetic/canonical_graph.json \\
        --result visualizer/mock_results/algorithm_result.json

The command starts ``streamlit run`` on this file. Arguments after the module
name set the initial graph and result paths. The sidebar can change them. An
optional ``--trip`` file (``trip_config.json``) adds percent-of-battery values and
the capacity and reserve lines to the SOC panels.

Real results (H11): ``make demo-real`` or any of ``--scenarios``, ``--results``,
``--scenario``, or ``--optimizer`` opens the real-data scenarios. The app lists the H5
scenario folders in ``data/processed``, reads the result files the S9 runner wrote to
``results/real`` (``python -m experiments.real_data``), and lets the sidebar pick the
scenario and the optimizer (label_setting, astar, or exact). The greedy baseline is
``greedy.json`` in the same results folder::

    python -m visualizer.app --scenario multi_stop_detroit_chicago

The sidebar Source switch moves between that and file mode. With no arguments the app
starts in file mode on the mock results, so it works before any real result exists.

The page shows the route map (H6), the trip summary, the SOC chart, and the
drive/charge timeline (H7), plus the greedy versus optimized comparison (H8).

The comparison reads a second precomputed file, the ``greedy_frc`` baseline
result for the same trip. ``--greedy`` or the sidebar sets it. When left blank
the app looks for ``algorithm_result_greedy.json`` next to the result file. The
main result is the optimized side.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GRAPH = ROOT / "data" / "synthetic" / "canonical_graph.json"
DEFAULT_RESULT = ROOT / "visualizer" / "mock_results" / "algorithm_result.json"
DEFAULT_SCENARIOS_DIR = ROOT / "data" / "processed"
DEFAULT_RESULTS_DIR = ROOT / "results" / "real"
GREEDY_SIBLING = "algorithm_result_greedy.json"
MAP_HEIGHT = 650
SUMMARY_COLUMNS = 3


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m visualizer.app")
    parser.add_argument("--graph", default=str(DEFAULT_GRAPH), help="canonical_graph.json")
    parser.add_argument("--result", default=str(DEFAULT_RESULT), help="algorithm_result.json")
    parser.add_argument(
        "--greedy",
        default="",
        help="greedy_frc algorithm_result.json for the comparison (optional)",
    )
    parser.add_argument("--trip", default="", help="trip_config.json (optional)")
    parser.add_argument(
        "--scenarios", default="", help=f"H5 scenario folders (default {DEFAULT_SCENARIOS_DIR})"
    )
    parser.add_argument(
        "--results", default="", help=f"S9 runner output folders (default {DEFAULT_RESULTS_DIR})"
    )
    parser.add_argument("--scenario", default="", help="scenario id to open first")
    parser.add_argument("--optimizer", default="", help="label_setting, astar, or exact")
    args, _unknown = parser.parse_known_args(argv)
    return args


def find_greedy_result(result_path: str, greedy_path: str) -> Path | None:
    """Return the greedy baseline file to compare against, or ``None`` when there is none.

    An explicit ``greedy_path`` wins. Otherwise use ``algorithm_result_greedy.json`` in
    the same folder as the result file, if it exists.
    """
    if greedy_path.strip():
        return Path(greedy_path.strip())
    sibling = Path(result_path).parent / GREEDY_SIBLING
    return sibling if sibling.is_file() else None


def render_comparison_panel(st, result: dict, greedy_path: Path | None) -> None:
    from visualizer.comparison import (
        GREEDY_ALGORITHM,
        comparison_table,
        render_comparison,
    )
    from visualizer.data_io import VisualizerInputError, load_result

    st.subheader("Greedy vs optimized")
    if result["algorithm"] == GREEDY_ALGORITHM:
        st.info(
            "The main result is the greedy baseline. Load an optimizer result "
            "(label_setting, astar, or exact) as the main result to compare."
        )
        return
    if greedy_path is None:
        st.info(
            "No greedy baseline result to compare. Set the greedy result in the sidebar, "
            f"or put {GREEDY_SIBLING} next to the result file."
        )
        return
    try:
        greedy = load_result(greedy_path)
        comparison = render_comparison(greedy, result)
    except VisualizerInputError as exc:
        st.error(str(exc))
        return
    getattr(st, comparison["severity"])(comparison["verdict"])
    st.table(comparison_table(comparison))


REPLAY_HEIGHT = MAP_HEIGHT + 90
REPLAY_ALGORITHMS = ("label_setting", "astar")


def build_search_replay(
    st, graph: dict, trip: dict | None, result: dict, greedy_path: Path | None, road_lines: bool
) -> str | None:
    """Run the optimizer again with a trace and return the animated map page.

    Returns ``None`` (after explaining why) when the replay cannot be built.
    """
    from visualizer.data_io import VisualizerInputError, load_result
    from visualizer.map_view import road_geometry
    from visualizer.search_replay import build_payload, capture, replay_html

    if trip is None:
        st.info("The search animation needs the trip configuration (trip_config.json).")
        return None
    algorithm = result["algorithm"] if result["algorithm"] in REPLAY_ALGORITHMS else "label_setting"
    if result["algorithm"] not in REPLAY_ALGORITHMS:
        st.caption(f"The animation shows label_setting; {result['algorithm']} is not traced.")
    greedy = None
    if greedy_path is not None:
        try:
            greedy = load_result(greedy_path)
        except VisualizerInputError:
            greedy = None
    traced, trace = capture(graph, trip, use_heuristic=algorithm == "astar")
    with st.spinner("Preparing the animation (road shapes are fetched once, then cached)..."):
        payload = build_payload(
            graph,
            trip,
            traced,
            trace,
            greedy=greedy,
            leg_geometry=road_geometry if road_lines else None,
        )
    return replay_html(payload, layout="map")


@dataclass(frozen=True)
class SelectedInputs:
    """The files the page draws, from either input mode."""

    graph: str
    result: str
    trip: str
    greedy: Path | None
    notes: tuple[str, ...] = ()


def uses_scenario_mode(args: argparse.Namespace) -> bool:
    """True when the command line asked for the real scenario results."""
    return bool(args.scenarios or args.results or args.scenario or args.optimizer)


def select_scenario_inputs(st, args: argparse.Namespace, entries: list) -> SelectedInputs | None:
    """Draw the scenario and optimizer pickers. Return ``None`` when nothing can be drawn."""
    from visualizer.scenarios import overview_rows, provenance_lines

    if not entries:
        st.info(
            f"No scenario folders found under {args.scenarios}. Run the H5 pipeline "
            "(`python -m data_pipeline`) or switch the sidebar to Files."
        )
        return None
    ids = [entry.scenario_id for entry in entries]
    scenario_id = st.sidebar.selectbox(
        "Scenario",
        ids,
        index=ids.index(args.scenario) if args.scenario in ids else 0,
        format_func=lambda name: name.replace("_", " "),
    )
    entry = entries[ids.index(scenario_id)]
    with st.expander("All scenarios", expanded=False):
        st.table(overview_rows(entries))
    if entry.problems:
        for problem in entry.problems:
            st.warning(problem)
        for error in entry.manifest_errors:
            st.error(error)
    if not entry.optimizers:
        return None
    optimizer = st.sidebar.selectbox(
        "Optimizer result",
        entry.optimizers,
        index=entry.optimizers.index(args.optimizer) if args.optimizer in entry.optimizers else 0,
    )
    if entry.description:
        st.caption(entry.description)
    notes = [f"S9 run manifest: {entry.manifest_status or 'not found'}", *provenance_lines(entry)]
    return SelectedInputs(
        graph=str(entry.graph_path),
        result=str(entry.results[optimizer]),
        trip=str(entry.trip_path or ""),
        greedy=entry.greedy_path,
        notes=tuple(notes),
    )


def select_file_inputs(st, args: argparse.Namespace) -> SelectedInputs:
    graph_path = st.sidebar.text_input("Graph (canonical_graph.json)", value=args.graph)
    result_path = st.sidebar.text_input("Result (algorithm_result.json)", value=args.result)
    trip_path = st.sidebar.text_input(
        "Trip config (trip_config.json, optional)",
        value=args.trip,
        help="Adds percent of battery, the capacity line, and the reserve line.",
    )
    greedy_input = st.sidebar.text_input(
        "Greedy result (greedy_frc, optional)",
        value=args.greedy,
        help="Baseline result for the comparison. Blank uses algorithm_result_greedy.json "
        "next to the result file when it exists.",
    )
    return SelectedInputs(
        graph=graph_path,
        result=result_path,
        trip=trip_path,
        greedy=find_greedy_result(result_path, greedy_input),
    )


def run_app(argv: list[str]) -> None:
    """Draw the page. Runs inside ``streamlit run``."""
    import streamlit as st

    # `streamlit run` puts this file's folder on sys.path, not the repository root.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from visualizer.data_io import (
        VisualizerInputError,
        load_graph,
        load_result,
        load_trip_config,
    )
    from visualizer.map_view import render_map
    from visualizer.scenarios import discover_scenarios
    from visualizer.soc_chart import (
        build_trip_summary,
        render_soc_chart,
        render_timeline,
        render_timeline_diagram,
    )

    args = parse_args(argv)
    start_in_scenarios = uses_scenario_mode(args)
    args.scenarios = args.scenarios or str(DEFAULT_SCENARIOS_DIR)
    args.results = args.results or str(DEFAULT_RESULTS_DIR)
    st.set_page_config(page_title="ChargePlan", layout="wide")
    st.title("ChargePlan")

    scenario_label, file_label = "Real scenario results", "Files"
    with st.sidebar:
        st.header("Input")
        mode = st.radio(
            "Source",
            [scenario_label, file_label],
            index=0 if start_in_scenarios else 1,
            help="Real scenario results reads the S9 runner output for the H5 scenarios.",
        )
        if mode == scenario_label:
            scenarios_dir = st.text_input("Scenarios (H5 folders)", value=args.scenarios)
            results_dir = st.text_input("Results (S9 output)", value=args.results)
            args.scenarios, args.results = scenarios_dir, results_dir
        animate = st.toggle(
            "Animate the search on the map",
            value=False,
            help=(
                "Runs the optimizer on this trip now and plays its search back on the map: "
                "the roads it explores, in the order it explores them, then the optimal plan."
            ),
        )
        road_lines = st.checkbox(
            "Draw legs along roads",
            value=True,
            help=(
                "Display only: each leg is drawn along real roads (OSRM, cached in "
                ".route_cache). Trip times and distances still come from the graph's "
                "estimated road distances."
            ),
        )

    if mode == scenario_label:
        entries = discover_scenarios(args.scenarios, args.results)
        selected = select_scenario_inputs(st, args, entries)
        if selected is None:
            return
    else:
        selected = select_file_inputs(st, args)

    try:
        graph = load_graph(selected.graph)
        result = load_result(selected.result)
        trip = load_trip_config(selected.trip) if selected.trip.strip() else None
        fmap = render_map(graph, result, road_lines=road_lines)
    except VisualizerInputError as exc:
        st.error(str(exc))
        return

    if selected.notes:
        st.caption(" | ".join(selected.notes))
    if road_lines:
        st.caption(
            "Route legs are drawn along real roads for display; trip numbers use the "
            "model's estimated road distances."
        )
    if result["status"] == "FEASIBLE":
        st.caption(
            f"{result['algorithm']}: {result['number_of_charging_stops']} charging stop(s), "
            f"{result['total_distance_miles']:.1f} mi, {result['total_time_minutes']:.1f} min"
        )
    else:
        st.warning(f"{result['algorithm']} found no feasible plan for this trip.")
    replay = (
        build_search_replay(st, graph, trip, result, selected.greedy, road_lines)
        if animate
        else None
    )
    if replay is not None:
        st.iframe(replay, height=REPLAY_HEIGHT)
    else:
        st.iframe(fmap.get_root().render(), height=MAP_HEIGHT)

    st.subheader("Trip summary")
    summary = build_trip_summary(result, trip)
    for start in range(0, len(summary), SUMMARY_COLUMNS):
        for column, (label, value) in zip(
            st.columns(SUMMARY_COLUMNS), summary[start : start + SUMMARY_COLUMNS], strict=False
        ):
            column.metric(label, value)

    render_comparison_panel(st, result, selected.greedy)

    if not result["events"]:
        st.info("This result has no drive or charge events, so there is no SOC chart or timeline.")
        return

    chart_col, timeline_col = st.columns([3, 2])
    with chart_col:
        st.subheader("State of charge")
        x_axis = st.radio(
            "SOC against", ["distance", "time"], horizontal=True, format_func=str.capitalize
        )
        st.altair_chart(render_soc_chart(result, x_axis, trip), width="stretch")
    with timeline_col:
        st.subheader("Trip timeline")
        st.code("\n".join(render_timeline(result, trip)), language=None, wrap_lines=True)
        with st.expander("Timeline diagram"):
            st.code(render_timeline_diagram(result, trip), language=None, wrap_lines=True)


def main() -> None:
    """Start the visualizer."""
    argv = sys.argv[1:]
    if inside_streamlit():
        run_app(argv)
        return
    command = [sys.executable, "-m", "streamlit", "run", str(Path(__file__).resolve()), "--", *argv]
    raise SystemExit(subprocess.call(command, cwd=ROOT))


def inside_streamlit() -> bool:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
    except ImportError:
        return False
    return get_script_run_ctx() is not None


if __name__ == "__main__":
    main()
