"""Shared command-line interface for every algorithm.

Frozen invocation (PRD Section 16.4)::

    python -m optimizer.cli --algorithm {label_setting,astar,greedy,exact} \\
        --graph canonical_graph.json --config trip_config.json \\
        --out algorithm_result.json

``greedy`` on the command line is the farthest-reachable-charger baseline.
Its result file must set ``algorithm`` to ``greedy_frc``.

The process exits 0 for both ``FEASIBLE`` and ``INFEASIBLE`` results. It exits
non-zero when an input cannot be read or validated, or when the selected
solver is not implemented yet.
"""

from __future__ import annotations

import argparse
import sys

from optimizer import battery_state_optimizer, exact_solver, greedy_baseline
from optimizer.model import InputError, load_config, load_graph, validate_query, write_result

_RESULT_ALGORITHM = {
    "label_setting": "label_setting",
    "astar": "astar",
    "greedy": "greedy_frc",
    "exact": "exact",
}


def main(argv: list[str] | None = None) -> int:
    """Load one query, run the selected algorithm, and write its result."""
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else 1

    try:
        graph = load_graph(args.graph)
        config = load_config(args.config)
        validate_query(graph, config)
        result = run_algorithm(args.algorithm, graph, config)
        write_result(result, args.out)
    except NotImplementedError:
        print("error: algorithm is not implemented yet", file=sys.stderr)
        return 1
    except (InputError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def run_algorithm(name: str, graph: dict, config: dict) -> dict:
    """Dispatch to one solver and require the frozen ``algorithm`` name."""
    if name == "label_setting":
        result = battery_state_optimizer.solve(graph, config, use_heuristic=False)
    elif name == "astar":
        result = battery_state_optimizer.solve(graph, config, use_heuristic=True)
    elif name == "greedy":
        result = greedy_baseline.solve(graph, config)
    elif name == "exact":
        result = exact_solver.solve(graph, config)
    else:
        raise InputError(f"unknown algorithm {name!r}")

    if not isinstance(result, dict):
        raise InputError(f"{name} returned {type(result).__name__}, expected an object")
    expected = _RESULT_ALGORITHM[name]
    actual = result.get("algorithm")
    if actual != expected:
        raise InputError(f"{name} must report algorithm {expected!r}, got {actual!r}")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m optimizer.cli",
        description="Run one ChargePlan algorithm and write algorithm_result.json.",
    )
    parser.add_argument(
        "--algorithm",
        required=True,
        choices=tuple(_RESULT_ALGORITHM),
        help="label_setting, astar, greedy (FRC), or exact",
    )
    parser.add_argument("--graph", required=True, help="Path to canonical_graph.json")
    parser.add_argument("--config", required=True, help="Path to trip_config.json")
    parser.add_argument("--out", required=True, help="Path to write algorithm_result.json")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
