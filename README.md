# ChargePlan

EV charging-aware route optimizer for CIS 505 (Fall 2026). The search minimizes driving time plus charging time over a battery-expanded state graph. Sankalp Rajeev owns the optimizer and experiments. Shannon owns the data pipeline, the farthest-reachable-charger baseline, and the visualizer.

The algorithm is not implemented yet. This tag is the Phase 1 skeleton: frozen JSON contracts, importable stubs, and the repository layout from the PRD.

Full specification: [docs/PRD.md](docs/PRD.md). Task list: [docs/roadmap.md](docs/roadmap.md).

## Layout

```text
contracts/          JSON schemas (graph, trip config, algorithm result)
optimizer/          model.py and cli.py (shared, frozen), search, FRC, exact solver
data_pipeline/      AFDC stations, OSM routing, canonical-graph export
visualizer/         map, SOC chart, comparison; mock_results/ until real output exists
data/synthetic/     schema samples used by the contract tests
data/raw/           local snapshots (gitignored)
data/processed/     reduced graphs (gitignored)
experiments/        runners for Experiments A–J
tests/              contract tests now; algorithm tests later
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

Shared optimizer entry point, once the CLI is implemented:

```text
python -m optimizer.cli --algorithm {label_setting,astar,greedy,exact} \
    --graph canonical_graph.json --config trip_config.json \
    --out algorithm_result.json
```

`greedy` on the command line writes `"algorithm": "greedy_frc"`. Exit code 0 covers both feasible and infeasible trips. Unreadable or invalid inputs exit non-zero.

## Phase 1 freeze (v0.1)

Schemas in `contracts/` and the samples in `data/synthetic/` are frozen. A contract change needs both teammates and a version bump in this README.

Label time is stored as integer milliseconds inside the search so `(time_ms, stops, distance)` compares with ordinary tuple order and no float tolerance. Result JSON still reports minutes as numbers. Milliseconds are converted at the result boundary.

The real-data region is Michigan and the adjacent Midwest, with Detroit–Chicago and Detroit–Cleveland as the demonstration corridors. Candidate chargers stay inside a corridor around the origin–destination line.

Experiments A through J and the metrics in PRD Section 22 are the frozen experiment list.
