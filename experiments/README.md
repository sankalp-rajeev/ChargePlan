# Experiment runner

`python -m experiments.run_all` is the benchmark entry point. `make experiments` calls it with no arguments. CSV files land in `results/` and plots in `plots/`. Both directories are gitignored. The numbers below are produced by that command; they are not typed in by hand.

## Shared CSV contract

Every file uses the S6 header: experiment, instance id, algorithm, status, feasible, total time, charging stops, distance, runtime, states expanded, peak states stored, seed, nodes, chargers, capacity, SOC step, agreement, and note.

`agreement` is `true` when the solvers that ran on that instance share feasibility and the lexicographic label `(time, stops, distance)`. Runtime is not part of that check. Two runs with the same seed repeat every column except `runtime_ms`. Sweep rows store the median of 3 solver calls. States must be identical across those calls or the run stops.

## What runs

| Experiment | File | What changes | What stays fixed |
| --- | --- | --- | --- |
| S6 sample | `astar_comparison.csv` | nothing | Detroit–Chicago sample |
| S6 tiny | `correctness.csv`, `astar_comparison.csv` | 20 graphs from `random.Random(505)` | exact-legal bounds |
| A | `correctness.csv` | 100 graphs, ids `exact-000` to `exact-099` | 4–8 nodes, 1–5 chargers, capacity 3–6 kWh |
| C | `soc_resolution.csv` | `soc_step_kwh` | roads, battery, initial SOC, reserve |
| D | `charger_scaling.csv` | how many of 100 fixed stations are kept | origin, destination, battery, station coordinates |
| E | `battery_capacity.csv` | capacity 40, 55, 70, 85, 100 kWh | sample roads, initial SOC 30 kWh, reserve, consumption |
| J | `astar_comparison.csv` | the D graphs and the C steps | same inputs as D and C |

Experiment A uses its own `random.Random(505)`. It does not consume the draws that build the 20 S6 graphs, so that suite stays reproducible. The origin does not charge. Exact search runs only when the graph has at most 8 nodes, 5 chargers, and 8 capacity steps. The sample is 70 steps, so its exact row stays `SKIPPED`.

Experiment C uses two families:

- `sample`: 20%, 10%, 5%, 2%, and 1% of the 70 kWh pack (14, 7, 3.5, 1.4, 0.7 kWh) plus a 0.1 kWh reference. The reference is feasible. Coarser steps stay feasible and take longer; the error column in the note is that time minus the reference time.
- `roundup`: a direct road of about 9.2 miles on a 10 kWh battery. Steps are 6, 2, 1, and 0.1 kWh. The 0.1 kWh reference is feasible. The 6 kWh step is infeasible because the ceiling spends 12 kWh. The 20% step is still feasible on this edge, which is why 6 kWh is an extra bucket rather than a silent replacement of the percentage grid.

Experiment D keeps one corridor from longitude −83 to −87 at latitude 42. Counts are 10, 25, 50, and 100. Each selected station keeps the rate of its fixed slot, 150 kW or 50 kW. Edges run to the next three selected stations at the straight-line distance and at 50 mph. Fewer stations therefore means longer hops, not only a smaller graph. The battery is 40 kWh, initial SOC 30 kWh, reserve 2 kWh, consumption 0.30 kWh per mile, and the step is 1 kWh. Section 22's example of 200 stations is not run. `CHARGER_SCALE_MAX` rejects it.

Experiment E uses the sample roads and changes only capacity. Initial SOC is 30 kWh, not the sample's published 54 kWh, because 54 does not fit in a 40 kWh battery. These times are not the 322.5 minute demonstration plan. 40 kWh is infeasible. Larger capacities are feasible, and the trip time falls and then levels off.

Experiment J copies the measured D and C rows. On the charger corridor, A* expands fewer states than label-setting, and the gap grows with the candidate set. On the four-node sample, the two algorithms expand the same number of states at every SOC step. There is no real-data route yet, so J does not invent one.

## What does not run

These files stay a single `UNAVAILABLE` row. The default run does not replace them with measurements, and it does not replace C, D, or E with that placeholder:

- `greedy_comparison.csv` — FRC baseline, H4. No `greedy_vs_optimizer.png`.
- `initial_soc.csv`, `charger_density.csv`, `charger_speed.csv` — Experiments F, G, and H (H9). No `feasibility_vs_charger_density.png`.
- `real_routes.csv` — S9 and H5.

`greedy_baseline.solve` is not called.

## Plots

`plots/` receives the six measured charts: runtime and states against SOC step, objective error against SOC step, runtime against charger count, trip time against capacity, and A* against label-setting states. SOC-step axes are logarithmic. Trip time omits the infeasible 40 kWh point instead of drawing it as zero. The other two Section 23 names are omitted until H4 and H9 have rows.

## Commands

```text
make experiments
python -m experiments.run_all --out results --plots plots --seed 505 --tiny 20 --correctness 100 --repeats 3
```

`--no-sweeps` writes the S6 files only and leaves C, D, and E unmeasured. `--correctness 0` skips Experiment A. `--tiny` must be at least 1.
