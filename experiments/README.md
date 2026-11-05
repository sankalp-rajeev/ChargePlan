# Experiment runner

`python -m experiments.run_all` is the S6 benchmark entry point. `make experiments` calls it with no arguments. Outputs land in `results/` and are gitignored. Plots are not produced here.

## What runs

| Input | Solvers | Exact |
| --- | --- | --- |
| `data/synthetic/canonical_graph.json` with `trip_config.json` (Detroit–Chicago) | label-setting, A* | Skipped. Capacity is 70 steps, above the bound. |
| 20 tiny graphs from `random.Random(505)` | label-setting, A*, exact | Run. Each graph is built inside the bound. |

Tiny graphs use 3–5 nodes, 1–3 chargers, and capacity 4–8 kWh with a 1 kWh step. Nodes sit a few miles apart around latitude 42, longitude −83. A road that would be shorter than the straight-line distance is lengthened to that distance, and drive times are 1–6 minutes. The generator checks the frozen haversine rule before the solvers see the graph.

The exact solver runs only when all three hold:

- at most 8 nodes,
- at most 5 nodes with a non-null `charge_rate_kw`,
- at most 8 capacity steps after Section 7.1 rounding.

Otherwise the row is `SKIPPED` and `exact_solver.solve` is not called.

## What does not run

These Section 23 files are written with one `UNAVAILABLE` row and no measurements:

- `greedy_comparison.csv` — FRC baseline is still H4.
- `soc_resolution.csv`, `charger_scaling.csv`, `battery_capacity.csv` — sweeps for Experiments C, D, and E (S7).
- `initial_soc.csv`, `charger_density.csv`, `charger_speed.csv` — Experiments F, G, and H (H9).
- `real_routes.csv` — no real-data benchmark is configured (S9, H5).

`greedy_baseline.solve` is not called. Asking the dispatcher for `greedy` raises `InputError`.

## CSV columns

Every file uses the same header: experiment, instance id, algorithm, status, feasible, total time, charging stops, distance, runtime, states expanded, peak states stored, seed, nodes, chargers, capacity, SOC step, agreement, and note.

`agreement` is `true` when label-setting, A*, and exact (if it ran) share a feasibility bit and the same `(time, stops, distance)` label. Runtime is recorded and is not part of that check. Two runs with the same seed repeat every column except `runtime_ms`.

`correctness.csv` holds the skipped sample row and every tiny-instance row. `astar_comparison.csv` holds label-setting and A* for the sample and the tiny graphs. A mismatch exits with status 1 after the files are written.

## Commands

```text
make experiments
python -m experiments.run_all --out results --seed 505 --tiny 20
```

`--tiny` must be at least 1. `--seed` rebuilds only the tiny graphs. The Detroit–Chicago sample does not depend on the seed.
