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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GRAPH = ROOT / "data" / "synthetic" / "canonical_graph.json"
DEFAULT_RESULT = ROOT / "visualizer" / "mock_results" / "algorithm_result.json"
GREEDY_SIBLING = "algorithm_result_greedy.json"
MAP_HEIGHT = 650
SUMMARY_COLUMNS = 4


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
    from visualizer.soc_chart import (
        build_trip_summary,
        render_soc_chart,
        render_timeline,
        render_timeline_diagram,
    )

    args = parse_args(argv)
    st.set_page_config(page_title="ChargePlan", layout="wide")
    st.title("ChargePlan")

    with st.sidebar:
        st.header("Input files")
        graph_path = st.text_input("Graph (canonical_graph.json)", value=args.graph)
        result_path = st.text_input("Result (algorithm_result.json)", value=args.result)
        trip_path = st.text_input(
            "Trip config (trip_config.json, optional)",
            value=args.trip,
            help="Adds percent of battery, the capacity line, and the reserve line.",
        )
        greedy_input = st.text_input(
            "Greedy result (greedy_frc, optional)",
            value=args.greedy,
            help="Baseline result for the comparison. Blank uses algorithm_result_greedy.json "
            "next to the result file when it exists.",
        )

    try:
        graph = load_graph(graph_path)
        result = load_result(result_path)
        trip = load_trip_config(trip_path) if trip_path.strip() else None
        fmap = render_map(graph, result)
    except VisualizerInputError as exc:
        st.error(str(exc))
        return

    if result["status"] == "FEASIBLE":
        st.caption(
            f"{result['algorithm']}: {result['number_of_charging_stops']} charging stop(s), "
            f"{result['total_distance_miles']:.1f} mi, {result['total_time_minutes']:.1f} min"
        )
    else:
        st.warning(f"{result['algorithm']} found no feasible plan for this trip.")
    st.iframe(fmap.get_root().render(), height=MAP_HEIGHT)

    st.subheader("Trip summary")
    summary = build_trip_summary(result, trip)
    for start in range(0, len(summary), SUMMARY_COLUMNS):
        for column, (label, value) in zip(
            st.columns(SUMMARY_COLUMNS), summary[start : start + SUMMARY_COLUMNS], strict=False
        ):
            column.metric(label, value)

    render_comparison_panel(st, result, find_greedy_result(result_path, greedy_input))

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
        st.code("\n".join(render_timeline(result, trip)), language=None)
        with st.expander("Timeline diagram"):
            st.code(render_timeline_diagram(result, trip), language=None)


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
