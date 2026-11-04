# ChargePlan

EV charging-aware route optimizer for CIS 505 (Fall 2026). The search minimizes driving time plus charging time over a battery-expanded state graph. Sankalp Rajeev owns the optimizer and experiments. Shannon owns the data pipeline, the farthest-reachable-charger baseline, and the visualizer.

The shared runtime is on `main` ([PR #23](https://github.com/sankalp-rajeev/ChargePlan/pull/23), issue #1 closed). Label-setting search is implemented on `opt/label-setting` and is waiting for review. A*, the greedy baseline, and the exact solver are still stubs.

Full specification: [docs/PRD.md](docs/PRD.md). Task list: [docs/roadmap.md](docs/roadmap.md).

## Layout

```text
contracts/          JSON schemas (graph, trip config, algorithm result)
optimizer/          model.py and cli.py (shared), label-setting search, FRC and exact stubs
data_pipeline/      AFDC stations, OSM routing, canonical-graph export
visualizer/         map, SOC chart, comparison; mock_results/ until real output exists
data/synthetic/     schema samples used by the contract tests
data/raw/           local snapshots (gitignored)
data/processed/     reduced graphs (gitignored)
experiments/        runners for Experiments A–J
tests/              contract tests, shared runtime, and label-setting cases
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

`greedy` on the command line is the FRC baseline and must write `"algorithm": "greedy_frc"`. Exit code 0 covers both feasible and infeasible trips. Unreadable or invalid inputs exit non-zero. `label_setting` runs the battery-state search. `astar`, `greedy`, and `exact` still exit non-zero.

## Status

S1 is merged to `main` (`141d0ff`). Issue #1 is closed.

- Load and validate `canonical_graph.json`, `trip_config.json`, and `algorithm_result.json`.
- Section 7.1 rounding: capacity and initial energy round down, reserve and edge energy round up.
- Initial SOC and reserve must be at most battery capacity, including after that rounding.
- Label time converts at 60,000 ms per minute, half up.
- One CLI dispatches `label_setting`, `astar`, `greedy`, and `exact`.

S2 is implemented on `opt/label-setting` and is not on `main` yet. Issue #2 stays open until the pull request merges.

- Plain label-setting over `(location, battery steps, charge flag)`.
- Labels are `(time_ms, stops, distance)` and compare lexicographically.
- A drive is legal only when arrival SOC stays at or above the reserve. A charge step is legal only at a charger and only up to capacity.
- The first charge step of a visit counts as one stop. Consecutive steps become one charge event.
- `use_heuristic=True` raises `NotImplementedError` until S10.

The Detroit–Chicago sample is feasible: origin → station_1 → station_2 → destination, 322.5 minutes, two stops. The fast 150 kW stop is filled to 70 kWh so less time is spent at the 50 kW stop. This branch is Ruff-clean, with pytest at 51 passed and 3 skipped (exact solver, greedy baseline, and the S4 suite placeholder).

## Phase 1 freeze (v0.1)

Schemas in `contracts/` and the samples in `data/synthetic/` are frozen. A contract change needs both teammates and a version bump in this README.

Label time is stored as integer milliseconds inside the search, with 1 minute = 60,000 ms, so `(time_ms, stops, distance)` compares with ordinary tuple order and no float tolerance. Result JSON still reports minutes as numbers. Milliseconds are converted at the result boundary.

The real-data region is Michigan and the adjacent Midwest, with Detroit–Chicago and Detroit–Cleveland as the demonstration corridors. Candidate chargers stay inside a corridor around the origin–destination line.

Experiments A through J and the metrics in PRD Section 22 are the frozen experiment list.
