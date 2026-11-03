"""Shared command-line interface for every algorithm.

Frozen invocation (PRD Section 16.4)::

    python -m optimizer.cli --algorithm {label_setting,astar,greedy,exact} \\
        --graph canonical_graph.json --config trip_config.json \\
        --out algorithm_result.json

``greedy`` on the command line is the farthest-reachable-charger baseline.
Its result file sets ``algorithm`` to ``greedy_frc``.

The process exits 0 for both ``FEASIBLE`` and ``INFEASIBLE`` results. It exits
non-zero when an input cannot be read or validated.

TODO: parse arguments, load inputs through ``optimizer.model``, and dispatch
to ``solve`` (S1).
"""


def main(argv: list[str] | None = None) -> int:
    """Run one algorithm and write its result file.

    TODO: implement parsing and dispatch (S1).
    """
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
