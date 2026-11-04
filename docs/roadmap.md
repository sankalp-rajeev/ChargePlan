# Roadmap

Fall 2026. Check an item when the work is on `main`. Due dates are the phase milestones. Specification: [PRD.md](PRD.md).

The JSON schemas, samples, and repository skeleton are already on `main` (`v0.1-contracts-frozen`, Nov 3). A box is checked only when that task is on `main`.

S1 is on `main` via [PR #23](https://github.com/sankalp-rajeev/ChargePlan/pull/23) (`141d0ff`). Issue #1 is closed.

S2 is on `main` via [PR #24](https://github.com/sankalp-rajeev/ChargePlan/pull/24) (`22c3529`). Issue #2 is closed. Label-setting search, route reconstruction, and the Detroit–Chicago plan (322.5 minutes, two stops) are on `main`.

S3 is on `main` via [PR #25](https://github.com/sankalp-rajeev/ChargePlan/pull/25) (`38dc994`). Issue #3 is closed.

S4 is on `main` via [PR #26](https://github.com/sankalp-rajeev/ChargePlan/pull/26) (`0e96666`). T1–T14 and the exact-solver check are on `main`. T16 is not complete; it still waits on the FRC baseline (H4). Issue #4 stays open until that baseline exists.

S10 is on `main` via [PR #27](https://github.com/sankalp-rajeev/ChargePlan/pull/27) (`519779d`). Issue #10 is closed. A* matches label-setting on the hand cases, the 100 geographic graphs, and the Detroit–Chicago sample.

S5 is the complexity note in `docs/complexity-analysis.md` on `docs/complexity-analysis`. It is not on `main` yet, so its box stays open. Issue #5 stays open until that pull request merges.

## Sankalp

Optimizer, exact solver, A*, and the algorithm experiments.

| Done | Task | Due |
| --- | --- | --- |
| - [x] | [S1](https://github.com/sankalp-rajeev/ChargePlan/issues/1) Shared runtime, merged in [PR #23](https://github.com/sankalp-rajeev/ChargePlan/pull/23). `model.py` and `cli.py` load the frozen files, apply Section 7.1 rounding, and dispatch all four algorithm names. 1 minute = 60,000 ms. Initial SOC and reserve must be at most battery capacity. | Nov 5 |
| - [x] | [S2](https://github.com/sankalp-rajeev/ChargePlan/issues/2) Battery-state label-setting optimizer and route reconstruction, merged in [PR #24](https://github.com/sankalp-rajeev/ChargePlan/pull/24) (`22c3529`). Lexicographic labels, battery and reserve constraints, stop counting, and result reconstruction. | Nov 12 |
| - [x] | [S3](https://github.com/sankalp-rajeev/ChargePlan/issues/3) Exact backtracking solver, merged in [PR #25](https://github.com/sankalp-rajeev/ChargePlan/pull/25) (`38dc994`). Depth-first search chooses a whole charge amount at each visit and does not reuse the label-setting search. | Nov 12 |
| - [x] | [S4](https://github.com/sankalp-rajeev/ChargePlan/issues/4) Synthetic correctness tests, merged in [PR #26](https://github.com/sankalp-rajeev/ChargePlan/pull/26) (`0e96666`). T1–T14 and the exact 100-graph check are on `main`. T15 is verified against A* in [PR #27](https://github.com/sankalp-rajeev/ChargePlan/pull/27). T16 is not complete; it waits on the FRC baseline (H4). | Nov 12 |
| - [ ] | [S5](https://github.com/sankalp-rajeev/ChargePlan/issues/5) Complexity analysis in `docs/complexity-analysis.md`, on `docs/complexity-analysis`. Not on `main`. State space, label-setting and A* bounds, discretization, and the exact solver. Issue #5 stays open until the pull request merges. | Nov 17 |
| - [ ] | [S6](https://github.com/sankalp-rajeev/ChargePlan/issues/6) Experiment runner and metrics collection. | Nov 17 |
| - [x] | [S10](https://github.com/sankalp-rajeev/ChargePlan/issues/10) A* variant, merged in [PR #27](https://github.com/sankalp-rajeev/ChargePlan/pull/27) (`519779d`). Admissible floored straight-line heuristic. Same lexicographic label as label-setting on the hand cases, 100 geographic graphs, and the Detroit–Chicago sample. | Nov 17 |
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
