# ChargePlan

EV charging-aware route optimizer for CIS 505 (Fall 2026). The search minimizes driving time plus charging time over a battery-expanded state graph. Sankalp Rajeev owns the optimizer and experiments. Shannon owns the data pipeline, the farthest-reachable-charger baseline, and the visualizer.

The shared runtime is on `main` ([PR #23](https://github.com/sankalp-rajeev/ChargePlan/pull/23), issue #1 closed). Label-setting search is on `main` ([PR #24](https://github.com/sankalp-rajeev/ChargePlan/pull/24), issue #2 closed). The exact solver is on `main` ([PR #25](https://github.com/sankalp-rajeev/ChargePlan/pull/25), issue #3 closed). The synthetic suite is on `main` ([PR #26](https://github.com/sankalp-rajeev/ChargePlan/pull/26), issue #4 still open because T16 waits on H4). A* is on `main` ([PR #27](https://github.com/sankalp-rajeev/ChargePlan/pull/27), issue #10 closed). The complexity note is on `main` ([PR #28](https://github.com/sankalp-rajeev/ChargePlan/pull/28), issue #5 closed). The experiment runner is on `main` ([PR #29](https://github.com/sankalp-rajeev/ChargePlan/pull/29), issue #6 closed). Algorithm sweeps A, C, D, E, and J are on `experiments/algorithm-sweeps` and are not merged yet. The greedy baseline is still a stub.

Full specification: [docs/PRD.md](docs/PRD.md). Task list: [docs/roadmap.md](docs/roadmap.md).

## Layout

```text
contracts/          JSON schemas (graph, trip config, algorithm result)
optimizer/          model.py and cli.py (shared), label-setting and A*, exact solver, FRC stub
data_pipeline/      AFDC stations, OSM routing, canonical-graph export
visualizer/         map, SOC chart, comparison; mock_results/ until real output exists
data/synthetic/     schema samples used by the contract tests
data/raw/           local snapshots (gitignored)
data/processed/     reduced graphs (gitignored)
experiments/        benchmark runner (S6) and sweeps A, C, D, E, J (S7)
tests/              contract, exact, synthetic T1–T16, and A* agreement tests
results/            experiment CSVs (gitignored)
plots/              experiment figures (gitignored)
docs/PRD.md         product requirements
docs/complexity-analysis.md   state space, time, space, and discretization
```

Shannon edits `optimizer/greedy_baseline.py` and does not edit the other files under `optimizer/`.

## Commands

```text
python -m pip install -r requirements.txt
make test            # pytest, including schema checks
make experiments     # python -m experiments.run_all
make demo            # python -m visualizer.app (stub)
```

Shared optimizer entry point:

```text
python -m optimizer.cli --algorithm {label_setting,astar,greedy,exact} \
    --graph canonical_graph.json --config trip_config.json \
    --out algorithm_result.json
```

`greedy` on the command line is the FRC baseline and must write `"algorithm": "greedy_frc"`. Exit code 0 covers both feasible and infeasible trips. Unreadable or invalid inputs exit non-zero. `label_setting` runs the battery-state search. `astar` runs the same search with the straight-line heuristic. `exact` runs the tiny-instance backtracking solver. `greedy` still exits non-zero.

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
- `use_heuristic=True` runs A* (S10). The label itself is unchanged; only the queue order uses `h`.

The Detroit–Chicago sample is feasible: origin → station_1 → station_2 → destination, 322.5 minutes, two stops. The fast 150 kW stop is filled to 70 kWh so less time is spent at the 50 kW stop.

S3 is merged to `main` (`38dc994`). Issue #3 is closed.

- Depth-first enumeration. Each charger visit picks the whole charge amount, from 0 through the remaining capacity, in one decision.
- The search does not import the label-setting module, `heapq`, or route reconstruction.
- Branches stop for the reserve, for capacity, for a label that cannot beat the incumbent, and for a dominated `(node, flag)` record with at least as much battery.
- On the hand-built cases, on 30 seeded graphs of at most 4 nodes and battery capacity 8, and on the Detroit–Chicago sample, exact and label-setting agree on feasibility and on `(time, stops, distance)`.

S4 is on `main` via [PR #26](https://github.com/sankalp-rajeev/ChargePlan/pull/26) (`0e96666`). Issue #4 stays open because T16 is not complete.

- T1–T14: direct route, one mandatory stop, nearer slow charger, partial charging, infeasible destination, reserve, capacity boundary, fast-charger detour, two stops, stop-then-distance ties, low starting SOC, no extra charge, conservative rounding, and stop counting.
- A second visit to the same charger is not the unique optimum when that departure is already feasible after the first fill. Five steps at one charger stay one stop, and two chargers stay two stops.
- 100 graphs from `random.Random(505)`, with 3–8 nodes and 1–6 chargers, match the exact solver on feasibility and `(time, stops, distance)`.
- T15 compares A* with label-setting on the direct trip and the Detroit–Chicago sample. The 100-graph geographic sweep is in `test_astar.py`.
- T16 is not complete. The failure graph is fixed, and label-setting finds a one-stop plan. `greedy_baseline.solve` still raises `NotImplementedError` (H4).

S10 is on `main` via [PR #27](https://github.com/sankalp-rajeev/ChargePlan/pull/27) (`519779d`). Issue #10 is closed.

- Queue priority is `(time_ms + h, stops, distance)`. `h(v)` is the straight-line distance to the destination divided by the fastest edge speed, floored to milliseconds.
- Flooring and the integer edge times keep `h` admissible and consistent. `h` is zero at the destination, and it is zero everywhere if any edge has positive length and zero time.
- The 100 A* graphs are spread over a few miles, every road covers the straight-line distance, and `h(origin)` is positive on all 100. A* matches label-setting and expands no more states. Both expand 127 states and store 148 on the Detroit–Chicago sample. A westward chain expands fewer states.
- No canonical graph is in `data/processed` yet, so the real-data half of T15 is skipped until S9.

S5 is on `main` via [PR #28](https://github.com/sankalp-rajeev/ChargePlan/pull/28) (`0b7ea74`). Issue #5 is closed.

- The expanded graph has at most \(2|V|S\) states. Dense label-setting and A* are \(O(|V|^2 S \log(|V|S))\) time. The stale heap makes dense memory \(O(|V|^2 S)\), above the \(O(|V|S)\) label table.
- Halving the SOC step roughly doubles \(S\) and therefore time and memory. Accepted plans stay feasible in real energy; the discrete optimum can still differ from continuous charging.
- The exact solver branches on up to \(S\) charge amounts per charger visit. Dominance stores \(O(|V|S)\) records and leaves the tree exponential, so it stays a tiny-instance check.

S6 is on `main` via [PR #29](https://github.com/sankalp-rajeev/ChargePlan/pull/29) (`0ac0d1e`). Issue #6 is closed.

- `make experiments` writes the Section 23 CSV files. Label-setting and A* run on the Detroit–Chicago sample and on 20 tiny graphs from `random.Random(505)`.
- Exact search runs only at or below 8 nodes, 5 chargers, and 8 capacity steps. The sample is skipped at 70 steps.
- Greedy and real-data rows stay `UNAVAILABLE`.

S7 is the algorithm sweeps on `experiments/algorithm-sweeps`. It is not on `main` yet. Issue #7 stays open until that pull request merges. Issue #4 stays open until H4.

- Experiment A adds 100 exact-legal graphs. Label-setting, A*, and exact search agree on feasibility and the lexicographic label.
- Experiment C changes only the SOC step. The 0.1 kWh sample is the reference. A separate 10 kWh edge is feasible at 0.1 kWh and infeasible at 6 kWh.
- Experiment D keeps one corridor and uses 10, 25, 50, and 100 of 100 fixed stations. 200 stations are refused. Fewer stations also means longer hops.
- Experiment E changes only battery capacity on the sample roads. Initial SOC stays 30 kWh. 40 kWh is infeasible.
- Experiment J copies those measured rows. A* expands fewer states on the corridor and the same number on the four-node sample.
- Greedy, charger density, initial SOC, charger speed, and real routes stay `UNAVAILABLE`. Their two plots are not written.
- Inputs, the hop-length confound, and the commands are in `experiments/README.md`.

This branch is Ruff-clean, with pytest at 94 passed and 2 skipped (the greedy baseline and the missing real-data graph).

## Phase 1 freeze (v0.1)

Schemas in `contracts/` and the samples in `data/synthetic/` are frozen. A contract change needs both teammates and a version bump in this README.

Label time is stored as integer milliseconds inside the search, with 1 minute = 60,000 ms, so `(time_ms, stops, distance)` compares with ordinary tuple order and no float tolerance. Result JSON still reports minutes as numbers. Milliseconds are converted at the result boundary.

The real-data region is Michigan and the adjacent Midwest, with Detroit–Chicago and Detroit–Cleveland as the demonstration corridors. Candidate chargers stay inside a corridor around the origin–destination line.

Experiments A through J and the metrics in PRD Section 22 are the frozen experiment list.
