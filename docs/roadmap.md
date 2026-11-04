# Roadmap

Fall 2026. Check an item when the work is on `main`. Due dates are the phase milestones. Specification: [PRD.md](PRD.md).

The JSON schemas, samples, and repository skeleton are already on `main` (`v0.1-contracts-frozen`, Nov 3). A box is checked only when that task is on `main`.

S1 is on `main` via [PR #23](https://github.com/sankalp-rajeev/ChargePlan/pull/23) (`141d0ff`). Issue #1 is closed.

S2 is implemented on `opt/label-setting` in [PR #24](https://github.com/sankalp-rajeev/ChargePlan/pull/24) (`9ac46ac`) and is not on `main` yet, so its box stays open. Pytest on that branch: 51 passed, 3 skipped. Ruff was clean. The skipped tests are the exact solver, the greedy baseline, and the S4 suite placeholder. Remaining step: merge PR #24. Issue #2 stays open until that merge.

## Sankalp

Optimizer, exact solver, A*, and the algorithm experiments.

| Done | Task | Due |
| --- | --- | --- |
| - [x] | [S1](https://github.com/sankalp-rajeev/ChargePlan/issues/1) Shared runtime, merged in [PR #23](https://github.com/sankalp-rajeev/ChargePlan/pull/23). `model.py` and `cli.py` load the frozen files, apply Section 7.1 rounding, and dispatch all four algorithm names. 1 minute = 60,000 ms. Initial SOC and reserve must be at most battery capacity. | Nov 5 |
| - [ ] | [S2](https://github.com/sankalp-rajeev/ChargePlan/issues/2) Battery-state label-setting optimizer and route reconstruction, in [PR #24](https://github.com/sankalp-rajeev/ChargePlan/pull/24). Lexicographic labels, battery and reserve constraints, stop counting, and result reconstruction are implemented. A* still raises `NotImplementedError`. Tests pass (51 passed, 3 skipped). Remaining: merge to `main`. | Nov 12 |
| - [ ] | [S3](https://github.com/sankalp-rajeev/ChargePlan/issues/3) Exact backtracking solver for tiny instances. | Nov 12 |
| - [ ] | [S4](https://github.com/sankalp-rajeev/ChargePlan/issues/4) Synthetic correctness tests T1–T16. | Nov 12 |
| - [ ] | [S5](https://github.com/sankalp-rajeev/ChargePlan/issues/5) Complexity analysis for the report. | Nov 17 |
| - [ ] | [S6](https://github.com/sankalp-rajeev/ChargePlan/issues/6) Experiment runner and metrics collection. | Nov 17 |
| - [ ] | [S10](https://github.com/sankalp-rajeev/ChargePlan/issues/10) A* variant, admissibility argument, and `test_astar.py`. | Nov 17 |
| - [ ] | [S9](https://github.com/sankalp-rajeev/ChargePlan/issues/9) Run the real-data graph through the optimizer and write result files. | Nov 20 |
| - [ ] | [S7](https://github.com/sankalp-rajeev/ChargePlan/issues/7) Experiments A, C, D, E, and J. | Nov 24 |
| - [ ] | [S8](https://github.com/sankalp-rajeev/ChargePlan/issues/8) Algorithm sections of the report. | Dec 2 |

## Shannon

Charging-station data, road network, farthest-reachable-charger baseline, visualizer, and the real-data experiments.

| Done | Task | Due |
| --- | --- | --- |
| - [ ] | [H1](https://github.com/sankalp-rajeev/ChargePlan/issues/11) AFDC charging-station snapshot and normalizer. | Nov 12 |
| - [ ] | [H2](https://github.com/sankalp-rajeev/ChargePlan/issues/12) OSM routing and corridor reduction. | Nov 12 |
| - [ ] | [H3](https://github.com/sankalp-rajeev/ChargePlan/issues/13) Export `canonical_graph.json`. | Nov 12 |
| - [ ] | [H4](https://github.com/sankalp-rajeev/ChargePlan/issues/14) Farthest-reachable-charger baseline. | Nov 12 |
| - [ ] | [H5](https://github.com/sankalp-rajeev/ChargePlan/issues/15) Real-data trip scenarios. | Nov 17 |
| - [ ] | [H6](https://github.com/sankalp-rajeev/ChargePlan/issues/16) Map view of the route and chargers. | Nov 17 |
| - [ ] | [H7](https://github.com/sankalp-rajeev/ChargePlan/issues/17) SOC chart, timeline, and trip summary. | Nov 17 |
| - [ ] | [H8](https://github.com/sankalp-rajeev/ChargePlan/issues/18) Greedy versus optimized comparison panel. | Nov 20 |
| - [ ] | [H11](https://github.com/sankalp-rajeev/ChargePlan/issues/21) Visualizer on real optimizer result files, plus demo screenshots. | Nov 20 |
| - [ ] | [H9](https://github.com/sankalp-rajeev/ChargePlan/issues/19) Experiments B, F, G, H, and I. | Nov 24 |
| - [ ] | [H10](https://github.com/sankalp-rajeev/ChargePlan/issues/20) Data and visualization sections of the report. | Dec 2 |
