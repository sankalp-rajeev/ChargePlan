"""Visualizer entry point.

Streamlit app that displays a precomputed ``algorithm_result.json`` on a map.
It reads result files and never recomputes a route. ``make demo`` calls this
module::

    python -m visualizer.app
    python -m visualizer.app --graph data/synthetic/canonical_graph.json \\
        --result visualizer/mock_results/algorithm_result.json

The command starts ``streamlit run`` on this file. Arguments after the module
name set the initial graph and result paths. The sidebar can change them.

TODO: the SOC chart, timeline, trip summary (H7), and comparison panel (H8).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GRAPH = ROOT / "data" / "synthetic" / "canonical_graph.json"
DEFAULT_RESULT = ROOT / "visualizer" / "mock_results" / "algorithm_result.json"
MAP_HEIGHT = 650


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m visualizer.app")
    parser.add_argument("--graph", default=str(DEFAULT_GRAPH), help="canonical_graph.json")
    parser.add_argument("--result", default=str(DEFAULT_RESULT), help="algorithm_result.json")
    args, _unknown = parser.parse_known_args(argv)
    return args


def run_app(argv: list[str]) -> None:
    """Draw the page. Runs inside ``streamlit run``."""
    import streamlit as st

    # `streamlit run` puts this file's folder on sys.path, not the repository root.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from visualizer.data_io import VisualizerInputError, load_graph, load_result
    from visualizer.map_view import render_map

    args = _parse_args(argv)
    st.set_page_config(page_title="ChargePlan", layout="wide")
    st.title("ChargePlan route map")

    with st.sidebar:
        st.header("Input files")
        graph_path = st.text_input("Graph (canonical_graph.json)", value=args.graph)
        result_path = st.text_input("Result (algorithm_result.json)", value=args.result)

    try:
        graph = load_graph(graph_path)
        result = load_result(result_path)
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


def main() -> None:
    """Start the visualizer."""
    argv = sys.argv[1:]
    if _inside_streamlit():
        run_app(argv)
        return
    command = [sys.executable, "-m", "streamlit", "run", str(Path(__file__).resolve()), "--", *argv]
    raise SystemExit(subprocess.call(command, cwd=ROOT))


def _inside_streamlit() -> bool:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
    except ImportError:
        return False
    return get_script_run_ctx() is not None


if __name__ == "__main__":
    main()
