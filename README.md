# ChargePlan

EV charging-aware route optimizer for CIS 505 (Fall 2026). The search minimizes driving time plus charging time over a battery-expanded state graph. Sankalp Rajeev owns the optimizer and experiments. Shannon owns the data pipeline, the farthest-reachable-charger baseline, and the visualizer.

The shared runtime is on `main` ([PR #23](https://github.com/sankalp-rajeev/ChargePlan/pull/23), issue #1 closed). Label-setting search is on `main` ([PR #24](https://github.com/sankalp-rajeev/ChargePlan/pull/24), issue #2 closed). The exact solver is in [PR #25](https://github.com/sankalp-rajeev/ChargePlan/pull/25) and is waiting for review. A* and the greedy baseline are still stubs.

Full specification: [docs/PRD.md](docs/PRD.md). Task list: [docs/roadmap.md](docs/roadmap.md).

## Layout

```text
contracts/          JSON schemas (graph, trip config, algorithm result)
optimizer/          model.py and cli.py (shared), label-setting search, exact solver, FRC stub
data_pipeline/      AFDC stations, OSM routing, canonical-graph export
visualizer/         map, SOC chart, comparison; mock_results/ until real output exists
data/synthetic/     schema samples used by the contract tests
data/raw/           local snapshots (gitignored)
data/processed/     reduced graphs (gitignored)
experiments/        runners for Experiments A–J
tests/              contract tests, label-setting cases, and exact-solver agreement
results/            experiment CSVs (gitignored)
plots/              experiment figures (gitignored)
docs/PRD.md         product requirements
```

Shannon edits `optimizer/greedy_baseline.py` and does not edit the other files under `optimizer/`.

## Commands

```text
python -m pip install -r requirements.txt
make test            # pytest, including schema checks
make experiments     # python -m experiments.run_all (stub)
make demo            # python -m visualizer.app (stub)
```

Shared optimizer entry point:

```text
python -m optimizer.cli --algorithm {label_setting,astar,greedy,exact} \
    --graph canonical_graph.json --config trip_config.json \
    --out algorithm_result.json
```

`greedy` on the command line is the FRC baseline and must write `"algorithm": "greedy_frc"`. Exit code 0 covers both feasible and infeasible trips. Unreadable or invalid inputs exit non-zero. `label_setting` runs the battery-state search. `exact` runs the tiny-instance backtracking solver. `astar` and `greedy` still exit non-zero.

## Status

S1 is merged to `main` (`141d0ff`). Issue #1 is closed.

- Load and validate `canonical_graph.json`, `trip_config.json`, and `algorithm_result.json`.
- Section 7.1 rounding: capacity and initial energy round down, reserve and edge energy round up.
- Initial SOC and reserve must be at most battery capacity, including after that rounding.
- Label time converts at 60,000 ms per minute, half up.
- One CLI dispatches `label_setting`, `astar`, `greedy`, and `exact`.

S2 is merged to `main` (`22c3529`). Issue #2 is closed.

- Plain label-setting over `(location, battery steps, charge flag)`.
- Labels are `(time_ms, stops, distance)` and compare lexicographically.
- A drive is legal only when arrival SOC stays at or above the reserve. A charge step is legal only at a charger and only up to capacity.
- The first charge step of a visit counts as one stop. Consecutive steps become one charge event.
- `use_heuristic=True` raises `NotImplementedError` until S10.

The Detroit–Chicago sample is feasible: origin → station_1 → station_2 → destination, 322.5 minutes, two stops. The fast 150 kW stop is filled to 70 kWh so less time is spent at the 50 kW stop.

S3 is implemented in [PR #25](https://github.com/sankalp-rajeev/ChargePlan/pull/25) and is not on `main` yet. Issue #3 stays open until that pull request merges.

- Depth-first enumeration. Each charger visit picks the whole charge amount, from 0 through the remaining capacity, in one decision.
- The search does not import the label-setting module, `heapq`, or route reconstruction.
- Branches stop for the reserve, for capacity, for a label that cannot beat the incumbent, and for a dominated `(node, flag)` record with at least as much battery.
- On every hand-built case, on 30 seeded graphs of at most 4 nodes and battery capacity 8, and on the Detroit–Chicago sample, exact and label-setting agree on feasibility and on `(time, stops, distance)`.

This branch is Ruff-clean, with pytest at 57 passed and 2 skipped (greedy baseline and the S4 suite placeholder).

## Phase 1 freeze (v0.1)

Schemas in `contracts/` and the samples in `data/synthetic/` are frozen. A contract change needs both teammates and a version bump in this README.

Label time is stored as integer milliseconds inside the search, with 1 minute = 60,000 ms, so `(time_ms, stops, distance)` compares with ordinary tuple order and no float tolerance. Result JSON still reports minutes as numbers. Milliseconds are converted at the result boundary.

The real-data region is Michigan and the adjacent Midwest, with Detroit–Chicago and Detroit–Cleveland as the demonstration corridors. Candidate chargers stay inside a corridor around the origin–destination line.

Experiments A through J and the metrics in PRD Section 22 are the frozen experiment list.
