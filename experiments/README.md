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
| A | `correctness.csv`, `correctness_summary.json` | 200 graphs, ids `exact-000` to `exact-199`, built from named categories (see Experiment A below) | 4–8 nodes, 1–5 chargers, at most 8 capacity steps |
| C | `soc_resolution.csv` | `soc_step_kwh` | roads, battery, initial SOC, reserve |
| D | `charger_scaling.csv`, `charger_scaling_summary.json` | how many stations of a seeded pool are kept: 10, 25, 50, 100, each over 10 pools (see Experiment D below) | origin, destination, battery, initial SOC, reserve, consumption, SOC step, and every kept station's coordinates and rate |
| E | `battery_capacity.csv` | capacity 40, 55, 70, 85, 100 kWh | sample roads, initial SOC 30 kWh, reserve, consumption |
| J | `astar_comparison.csv`, `astar_summary.json`, `astar_failures.json` (only on a mismatch) | its own 216 synthetic networks: geometry, charger count, road speeds, SOC step (see Experiment J below) | trip, vehicle, rate mix, and A* heuristic |

Exact search runs only when the graph has at most 8 nodes, 5 chargers, and 8 capacity steps. The sample is 70 steps, so its exact row stays `SKIPPED`.

## Experiment A: correctness against the exact solver

Experiment A asks whether label-setting, A*, and the exact solver return the same feasibility and the same full label `(total time, charging stops, distance)` on graphs small enough to enumerate. Section 21 asks for 100% agreement. Agreement shows that the search is correct on the discretized model. It does not measure discretization error; Experiment C does.

### Why the first benchmark was weak

The first generator drew roads of 1–4 miles at 1 kWh per mile, put them between 3–6 kWh batteries with a reserve of up to 2 kWh, and never let the origin charge. Of its 100 graphs, 93 were infeasible and 7 were feasible with zero charging stops. No graph needed a stop, so no graph tested a charge amount, a stop count, or a tie-break. Each graph is counted once, under the first cause below that applies:

| Why the graph was infeasible | Graphs |
| --- | --- |
| the initial energy was already below the discretized reserve | 21 |
| the random digraph had no path from origin to destination | 7 |
| every road path used an edge above capacity minus reserve | 45 |
| every edge fit the battery, but the starting energy and the randomly placed chargers could not carry the trip | 20 |
| feasible, with zero stops | 7 |

Only 39% of edges could be driven on one battery at all. All solvers said "infeasible" together, which is agreement but not evidence.

### Generator design

`experiments/exact_suite.py` builds the suite with seed 505 by default (`--seed`).

- Each instance has its own stream, `Random("chargeplan-experiment-a:<seed>:<index>")`. The first `n` instances are the same for any `--correctness` count, and the S6 tiny graphs use a different stream, so they are unchanged (their content hash matches `main`).
- The category is fixed by the index through a 20-slot schedule. Any multiple of 20 has exact category counts. The seed varies batteries, step sizes, consumption, charger rates, road lengths, speeds, coordinates, and decoys. It does not vary the mix.
- A feasible instance is built around a backbone path whose legs are chosen in discretized energy steps, so feasibility holds by construction. For example, the multi-stop categories give the first leg nearly all of the starting energy above the reserve and every later leg nearly a full battery above the reserve, then check that the total exceeds what one fewer stop can cover.
- Random decoy chargers and roads are added one edge at a time. An edge is kept only if the instance still has its category. That test is `experiments/certificate.py`, an energy-only reachability argument: it finds the fewest stops any plan needs, or proves there is none, using integer energy and nothing else. The label-setting optimizer and the exact solver are never consulted, and no optimal plan is written down.
- The same certificate is a third oracle at test time. It must agree with the exact solver on feasibility, and no solver may use fewer stops than it reports.
- Roads obey the contract: `distance_miles` is at least the straight line, and every instance passes `load_graph` and `load_config`.

| Category | Per 20 | What is built | Property by construction |
| --- | --- | --- | --- |
| `one_stop` | 3 | one charger between two legs that together exceed the starting energy; every third has a charging origin | needs exactly 1 stop |
| `multi_stop` | 4 | a chain of 2 stops (3 of 4) or 3 stops (1 of 4) with legs at the energy limit | needs exactly 2 or 3 stops |
| `speed_tradeoff` | 2 | a slow charger on the shorter route and a fast one on a longer route | needs 1 stop; either can win |
| `partial_chain` | 2 | slow charger, fast charger a short hop later, and a long bypass past the fast one | needs at least 1 stop; charge only enough, or deep at the slow one |
| `origin_charge` | 1 | the first road is out of range at a charging origin; the start is at the reserve or one step above | needs at least 1 stop |
| `no_charge` | 2 | a path through waypoints, with a charging origin or waypoint sometimes present; half end exactly at the reserve, half have slack | needs 0 stops |
| `tie_distance` | 1 | two no-charge routes with equal drive time and different miles | tie on time and stops, broken by distance |
| `tie_stops` | 1 | a no-charge route and a route that must charge, equal in total time | tie on time, broken by fewer stops |
| `infeasible` | 4 | cycles through `range_gap`, `no_path`, `stranded_origin`, `reserve_near_miss`, `below_reserve` | no plan exists |

`range_gap` puts one road one or two steps above a full battery. `reserve_near_miss` needs exactly one step more than the energy above the reserve, with its only charger out of range. `stranded_origin` has a non-charging origin whose every road exceeds the starting energy. `no_path` has no road into the destination from the reachable part of the graph. `below_reserve` starts under the discretized reserve.

Parameters per instance:

- SOC step ΔE from 0.5, 1, 2, 2.5, 5, 10 kWh, and consumption from 0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.75, 1.0 kWh per mile. Road lengths are rounded down to 0.001 mile, so the Section 7.1 ceiling is exact.
- 4–8 capacity steps, with a fraction of a step added to capacity and initial energy (rounded down) and taken off the reserve (rounded up). Reserve is 0, 1, or 2 steps. Road energies sit on a step boundary or a fraction below it.
- Charger rates from 20, 30, 50, 60, 100, 150 kW. Drive speeds 28–68 mph.
- Up to three decoy nodes, with random decoy roads at 1.05–1.6 times the straight line. Node and edge order are shuffled.

### Sample at seed 505, 200 graphs

All numbers are from `results/correctness_summary.json` and were re-counted from `correctness.csv`.

| Property | Count |
| --- | --- |
| Feasible, infeasible | 160, 40 |
| Optimal plan uses 0, 1, 2, 3 stops | 40, 66, 44, 10 |
| Feasible and requiring charging (certificate ≥ 1) | 120 |
| Requiring 2 or more stops (certificate) | 46 |
| Plans with a partial charge (ends below a full battery) | 93 of 120 charging plans |
| Charge events: partial, full | 109, 75 |
| Nodes 4, 5, 6, 7, 8 | 76, 46, 48, 26, 4 |
| Chargers 1, 2, 3, 4, 5 | 11, 67, 54, 44, 24 |
| SOC step 0.5, 1, 2, 2.5, 5, 10 kWh | 30, 36, 37, 34, 30, 33 |
| Consumption 0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.75, 1.0 | 26, 22, 27, 29, 27, 23, 21, 25 |
| Capacity steps 4, 5, 6, 7, 8 | 18, 43, 51, 48, 40 |
| Reserve steps 0, 1, 2 | 45, 107, 48 |
| Battery capacity | 2 to 87.5 kWh |
| Instances with a charging origin | 37 |

A plan with more stops than the certificate's minimum is not a bug. Time comes first in the label, so a faster plan may charge more often (2 of 30 `one_stop` graphs do).

**46 versus 54.** Two counts in this table look alike but measure different things. "Requiring 2 or more stops" (46) is the certificate: the fewest stops any energy-feasible route needs. "Optimal plan uses 2 or more stops" (44 + 10 = 54) is the number of stops in the plan the solvers actually return, which minimizes time first. The optimal plan can never use fewer stops than the certificate, and 0 of the 200 do. It can use more, and 8 do: those graphs need only 1 stop but their fastest plan makes 2 (`partial_chain` 4, `origin_charge` 2, `one_stop` 2). 46 + 8 = 54. The 46 describes how hard the instance is, and the 54 describes how the optimizer behaves on it. Nothing about Experiment A's methodology changed.

Before and after, same seed:

| | First generator (100 graphs) | This generator (200 graphs) |
| --- | --- | --- |
| Feasible | 7 | 160 |
| Infeasible | 93 | 40 |
| Feasible needing a charging stop | 0 | 120 |
| Needing two or more stops | 0 | 46 |
| Partial charging in the optimal plan | 0 | 93 |
| SOC steps used | 1 kWh | six values, 0.5 to 10 kWh |
| Consumption values | 1 kWh per mile | eight values, 0.2 to 1.0 |
| Seeded optimizer bugs caught (see below) | 6 to 11 of 100 | 116 to 149 of 200 |

### Checks and failure handling

For every graph the runner records one `correctness.csv` row each for label-setting, A*, and exact, with the shared `agreement` column. It compares the status and the whole label, with exact equality on time, stops, and distance. If any pair differs, or if the certificate disagrees with the exact solver, the graph is listed, the row says `agreement=false`, and the run exits 1. Nothing is skipped. `correctness_summary.json` holds the same comparison per graph and rolled up: counts by category and variant, the distributions above, pairwise mismatch counts, the largest time, distance, and stop gaps, whether any graph is outside the exact bound, and runtime totals. Everything in it except the `runtime_ms` entries repeats for a fixed seed. It is written only when `--correctness` is above 0.

At seed 505 and 200 graphs, all three solvers agree on all 200: 0 objective mismatches, 0 feasibility mismatches, largest gaps 0, 0 certificate conflicts, 0 graphs outside the bound. A separate sweep over ten seeds (1, 2, 3, 7, 42, 99, 505, 506, 1234, 99999) at 400 graphs each (4,000 graphs) also had 0 mismatches and 0 certificate conflicts; that sweep is not part of the test suite. A* expands no more states than label-setting on any graph.

Agreement is only meaningful if a wrong optimizer would disagree. Re-running label-setting with a seeded bug and comparing against the unchanged exact solver, the first benchmark caught the bug on 11, 7, and 6 of 100 graphs (energy one step low, reserve ignored, energy floored instead of ceiled). This suite catches it on 149, 116, and 142 of 200. `test_exact_suite.py` keeps two of those checks as regression tests.

### Exact bounds and runtime

Exact search stays inside the documented bound: at most 8 nodes, 5 chargers, and 8 capacity steps. This suite reaches all three limits (8, 5, 8), and 0 graphs are `SKIPPED`. Each solver runs once per graph (`timing=single run`). On the development machine the 200 graphs took 21 ms (label-setting), 21 ms (A*), and 44 ms (exact) in total, with a median of about 0.09 ms per solve and a worst exact solve of 11 ms. Runtime depends on the machine and is not asserted by a test, so the suite could be made larger; 200 is the Section 21 recommendation of 100 doubled.

### Limitations

- This is a designed mixture, not a uniform sample of graphs. Percentages describe this suite, not all tiny graphs.
- Every graph has at most 8 nodes and 8 capacity steps. The 70-step Detroit–Chicago sample is outside the exact bound, so no exact comparison covers it.
- Backbone-plus-decoy graphs are sparser and more structured than random digraphs. Decoys add at most three nodes.
- The certificate, the solvers, and the generator share the Section 7.1 rounding in `optimizer/model.py`. A bug in that rounding would not show up as a disagreement here. T13 and the rounding tests cover it.
- Distances are compared with exact float equality. The generator does not build three-way ties between different routes with equal float distance, because their float sums could differ in the last bit and be reported as a spurious mismatch.
- A trip that starts below the discretized reserve is infeasible for both solvers even at a charging origin. `below_reserve` only exercises a non-charging origin.
- Partial-charging counts come from the exact solver's plan. Different plans can share one label, so a count is a property of that plan.
- Runtime is one solve per graph on one machine. It checks that the exact solver is practical on this suite and is not a performance result.

Experiment C uses two families:

- `sample`: 20%, 10%, 5%, 2%, and 1% of the 70 kWh pack (14, 7, 3.5, 1.4, 0.7 kWh) plus a 0.1 kWh reference. The reference is feasible. Coarser steps stay feasible and take longer; the error column in the note is that time minus the reference time.
- `roundup`: a direct road of about 9.2 miles on a 10 kWh battery. Steps are 6, 2, 1, and 0.1 kWh. The 0.1 kWh reference is feasible. The 6 kWh step is infeasible because the ceiling spends 12 kWh. The 20% step is still feasible on this edge, which is why 6 kWh is an extra bucket rather than a silent replacement of the percentage grid.

Experiment D has its own section below.

Experiment E uses the sample roads and changes only capacity. Initial SOC is 30 kWh, not the sample's published 54 kWh, because 54 does not fit in a 40 kWh battery. These times are not the 322.5 minute demonstration plan. 40 kWh is infeasible. Larger capacities are feasible, and the trip time falls and then levels off.

Experiment J has its own section below. It no longer reuses the D or C rows.

## Experiment D: charger-count scaling

Experiment D asks how the search grows as more candidate chargers are added. Section 14 gives the state bound `2·M·S` (nodes `M`, SOC levels `S = B' + 1`) and runtimes of `O((Δ+1)·M·S·log(MS))` on a sparse graph and `O(M²·S·log(MS))` on a dense one. Label-setting and A* run on the same graph and config objects, and the run fails unless their full `(time, stops, distance)` label agrees.

### Why the first benchmark was weak

I measured the first generator before replacing it.

| Problem | Measured on the first generator |
| --- | --- |
| Count changed the hop structure | Each station was linked to the next three, so the edge count was exactly `3·count` and a smaller subset had longer hops. Count and graph shape moved together. |
| Rates followed the list index | The rate was 150 kW at even slots and 50 kW at odd slots. A subsample by `int(i·100/count)` then picked slots by arithmetic. The fast-charger share was 100%, 100%, 100%, and 50% at 10, 25, 50, and 100. |
| One straight line | Every station was at latitude 42.0, so every route was along one line and there was no detour to choose. |
| One instance per count | No spread could be reported, and a single outlier would have been the whole result. |

### Generator

The generator is `experiments/scaling_suite.py`. It is independent of `data_pipeline` and does not touch the real-data graph exporter.

- **Trip.** The origin is Detroit (42.3314, −83.0458) and the destination is Chicago (41.8781, −87.6298), about 237 straight-line miles. They are the same for every instance. The vehicle is also fixed for every instance: 40 kWh battery, 30 kWh initial SOC, 2 kWh reserve, 0.30 kWh per mile, 1 kWh SOC step (so `S = 41`).
- **Replicate and seed.** A replicate is one station pool. Pool `r` for seed `s` is drawn from `Random("chargeplan-experiment-d:<s>:<r>:pool")`, and a road between two nodes from its own stream keyed by the pair. Same seed gives the same graphs; a different seed gives different pools. The run uses `--seed` for D as well as for A, so `--seed 506` changes both. 10 replicates are the default (`--scaling-replicates`).
- **Pool.** The pool has 200 stations built in blocks of 10, 15, 25, 50, and 100. Each block puts one station in each equal slice of the corridor, with a random position inside the slice and a random cross-track offset (normal, σ 6 miles for 80% of stations and σ 22 miles for a 20% minority of detour candidates, clipped to ±45 miles). The stations are not collinear and are spread along the trip, whatever prefix is kept.
- **Matched subsets.** A count is a prefix of the pool: 10 ⊂ 25 ⊂ 50 ⊂ 100 ⊂ 200. A station keeps its id, coordinates, and rate at every count, and a road between two stations is the same at every count that contains both. The pool is generated block by block, so asking for a larger pool never changes a station that a smaller pool already had.
- **Rates.** Each block gets 30% 50 kW, 40% 150 kW, and 30% 250 kW, assigned to a shuffled list by largest remainder. A rate depends on neither the pool index nor the coordinates. Every count has the same share of each rate to within one station, and 70% of stations are 150 kW or faster in every instance (the recorded `fast_share`).
- **Roads.** A directed road runs from the origin or a station to any station or to the destination when its discretized energy, `ceil(c·d/ΔE)`, fits in a full battery above the reserve. Nothing runs into the origin or out of the destination. Road length is the straight line times a factor between 1.05 and 1.35, rounded up to 0.001 mile, so it is never shorter than the straight line. Speed is between 45 and 70 mph, so drive times are positive and plausible.
- **Meaningful charging.** The direct trip is at least 237 miles. The starting 30 kWh gives about 93 miles above the reserve and a full battery gives about 127 miles, so a route needs charging, and in these instances at least 2 stops. `min_stops` in each note is the energy-only certificate from Experiment A, `experiments/certificate.py`, and it is 2 for every one of the 40 instances. All 40 are feasible. No instance is trivially direct and none is a stranded start.

The pool seed, trip, vehicle, and rate mix are constants in `scaling_suite.py`. The vehicle is the one the first Experiment D used, so only the station layout changed. Profiling 3 replicates at each count from 10 to 200 showed every instance feasible and needing charging, so I kept it and did not tune it further.

### What the sweep cannot isolate

Adding stations also adds roads. With range-based roads, more stations means a node has more neighbors in range, shorter typical gaps between stations, and more choices, so node count, edge count, and out-degree all grow together. The measurements describe the algorithm on that growing family. They are not the effect of the node count alone with the density held fixed, and the original version did not isolate it either, because its hop length changed with the count.

### Measurements

Default run: counts 10, 25, 50, 100; 10 replicates each; 3 timing repeats; seed 505; 80 rows. All 40 instances are feasible, all 40 pairs agree on the full objective, and A* expands fewer states than label-setting on all 40 (an observation on this family, not an assertion; Experiment J measures when it does not hold). States and peak states are deterministic. Runtime is a median of 3 solves on one machine and varies by about 10% between runs.

Label-setting, median (interquartile range) over 10 pools:

| Chargers | Nodes | Edges | States expanded | State ceiling `2MS` | States / ceiling | Peak states stored | Runtime ms | A* states | A*/LS states |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10 | 12 | 69 | 643 (621–686) | 984 | 0.65 | 684 | 11.1 (10.6–11.7) | 532 | 0.85 |
| 25 | 27 | 428.5 | 1,741 (1,729–1,807) | 2,214 | 0.79 | 1,804 | 76.7 (74.1–79.2) | 1,464.5 | 0.86 |
| 50 | 52 | 1,707 | 3,542.5 (3,489–3,606) | 4,264 | 0.83 | 3,632.5 | 299 (294–304) | 2,805 | 0.81 |
| 100 | 102 | 6,852.5 | 7,183.5 (7,116–7,220) | 8,364 | 0.86 | 7,322.5 | 1,213 (1,208–1,220) | 5,659.5 | 0.79 |

Also per count: every count has 10 feasible and 0 infeasible instances. The plan uses 2 to 4 stops (median 3, 3, 2.5, 3). The median trip time falls as chargers are added: 284, 259, 249, and 242 minutes. A* median runtime was 10.0, 68.1, 254, and 1,024 ms. `charger_scaling_summary.json` holds all of these with quartiles and the numbers for A*.

Local log-log slopes between consecutive counts (a description of two medians, not an asymptotic fit):

| From → to | Edges | States expanded | Peak states | Runtime |
| --- | --- | --- | --- | --- |
| 10 → 25 | 1.99 | 1.09 | 1.06 | 2.11 |
| 25 → 50 | 1.99 | 1.02 | 1.01 | 1.96 |
| 50 → 100 | 2.01 | 1.02 | 1.01 | 2.02 |

### Comparison with the Section 14 bounds

- **States.** The expanded states stay below the `2·M·S` ceiling at every count (65% to 86% of it) and grow roughly linearly in the node count (slope about 1.0 to 1.1). That agrees with the linear-in-`M` bound. The fraction of the ceiling rises slowly with the count (0.65, 0.79, 0.83, 0.86). I did not isolate why, so I do not offer an explanation.
- **Peak states stored.** `peak_states_stored` is the size of the best-label table, so it tracks the expanded states closely (2–6% more). It does not include the stale entries in the heap that the `O(S·|E| + M·S)` memory bound refers to, so it cannot confirm that bound.
- **Runtime.** Runtime tracks the edge count and grows with slope about 2 in the node count. This family has a dense graph: the mean out-degree is about 5.8, 16, 33, and 67 at the four counts, roughly two thirds of the node count. The runtime therefore matches the dense `O(M²·S·log(MS))` regime, and the sparse `O((Δ+1)·M·S·log(MS))` regime is not tested, because `Δ` grows with `M` here. Runtime per `(edge + node)·SOC-step` rises slightly, 3.5, 4.2, 4.2, and 4.4 µs. That is the direction a `log` factor and cache effects would give, but the sizes are too few to show it. I did not fit it.
- **Spread.** The interquartile ranges are narrow (a few percent of the median for states). The pools are independent draws from one distribution and the reachable state space is large, so the replicates agree. Ten pools show that a result is not one lucky layout. They are not a test of unusual layouts such as a sparse region with a large gap, which this generator rarely creates.
- **A\*.** A* expands 15–21% fewer states on these instances. This is a measured fact about this family, not a claim about other graphs.

Four sizes cannot support an asymptotic claim, and I make none. The slopes are consistent with the bounds above and no more.

### 200 stations

Section 22 lists 200 stations. It is supported, and it is not in the default run because 200 stations cost about three times as much as the other four counts together. With `--charger-counts 10,25,50,100,200` (10 pools, 3 repeats, `--correctness 0`) the run took about 7.4 minutes in total on the development machine. At 200 stations the median is 14,640 states expanded (ceiling 16,564, 88%), 27,486 edges, and a runtime of about 5.7 s per solve (IQR 5.1–6.9 s). A* expands a median 0.81 of those states. The slopes from 100 to 200 are 2.0 (edges), 1.03 (states), and 2.1 (runtime), the same as below 100. Counts above 200 are rejected, because 200 is the pool size.

### Limitations

- This is a designed family. It is one synthetic corridor, one battery, one SOC step, and one rate mix. Different batteries, steps, or corridors can behave differently, and Experiment E varies the battery separately.
- Density and degree grow with the count (see above), so the sweep does not separate node count from graph shape.
- The roads are range-based and not drawn from a real road network. The factor and speed are random, and a pair's road is the same at every count.
- All 40 instances are feasible, so the sweep does not measure how the cost of an infeasible query depends on the count.
- Four counts, and 200 only on request.
- Runtime is one machine and varies by about 10% between runs. The states and the objectives are the reproducible measurements.
- The charger-scaling plots show the median, the interquartile whiskers, and each replicate. They do not claim significance.

## Experiment J: A* against label-setting

Experiment J asks two questions about the A* search of Section 14. Does it return the same label as label-setting on every instance? And how much search does the heuristic save, and when does it save little or nothing? The first is a correctness requirement. The second is an empirical question.

### Why the first benchmark was weak

- **It measured nothing of its own.** The first J file copied the A* and label-setting rows from Experiment D and Experiment C. Its runtimes were the D and C runtimes, and it had no factors J had chosen.
- **It inherited D's corridor.** Those D stations were all at one latitude and every road was a straight line, so the heuristic (straight-line distance over top speed) was nearly exact. I measured 27.6–30.4% fewer states on that corridor against 15–21% on the later non-collinear D data. A benefit measured on collinear stations overstates what the heuristic does on a bent corridor.
- **It asserted a universal.** The tests and README said A* never expands more states. The heuristic is admissible and consistent, and that guarantees the label, not a smaller search on every instance. Search size is measured here; it is not assumed.

### Design

`experiments/astar_suite.py` builds the suite and `experiments/astar_summary.py` summarizes it. `experiment_j` in `experiments/sweeps.py` runs it. J is independent of `data_pipeline`, D, and C.

| Factor | Levels | Why |
| --- | --- | --- |
| Geometry | `straight`, `curved`, `detour` | the heuristic is exact on a straight line and loose on a bent or detoured road |
| Chargers | 10, 25, 50 | size |
| Road speeds | `homogeneous` (every road 55 mph), `heterogeneous` (each road 35–75 mph) | `h` divides by the fastest road speed, so a slow average road loosens it |
| SOC step | 1 kWh and 4 kWh (`S` = 41 and 11) | resolution |

3 × 3 × 2 × 2 = 36 cells, 6 replicates each, 216 instances. A full Cartesian product over more levels was not used; every cell has the same number of replicates, and the replicate counts are in the summary's `design` block.

Controlled and fixed: the Detroit–Chicago trip (about 237 straight-line miles), a 40 kWh battery with 28 kWh initial SOC, a 4 kWh reserve, 0.30 kWh per mile, and a rate mix of 30% 50 kW, 40% 150 kW, and 30% 250 kW by largest remainder, shuffled so a rate depends on neither the index nor the position. Initial SOC and reserve are multiples of both SOC steps, so they discretize to the same energy at 1 kWh (28 and 4 steps of 40) and at 4 kWh (7 and 1 steps of 10); the step changes resolution and nothing else about the vehicle. The starting charge covers about 80 miles above the reserve and a full battery about 120, so the direct trip cannot be driven. Every instance needs charging, and `min_stops` in each note is the energy-only certificate from Experiment A. It is 2 for 130 of the 216 instances and 3 for 86. The optimal plans use 2 to 5 stops.

| Geometry | Corridor bulge | Station scatter (σ) | Wide-scatter share | Road length over straight line |
| --- | --- | --- | --- | --- |
| `straight` | none | 3 miles | 0% | 1.02–1.10 |
| `curved` | 40-mile arc | 6 miles | 10% (σ 15 miles) | 1.08–1.25 |
| `detour` | 25-mile arc | 15 miles | 35% (σ 45 miles) | 1.20–1.55 |

Graph rules. The stations are in 2-D space. A directed road runs from every node except the destination to every node except the origin when its energy in kWh (`0.30 × miles`) fits the 36 kWh of a full battery above the reserve. The test is in kWh, not in steps, so the physical road graph (nodes, roads, distances, drive times) is identical at 1 kWh and 4 kWh for a matched instance, and the SOC step changes only the battery discretization. A test checks this for every match. Its length is the straight line times the geometry's factor, rounded up to 0.001 mile, so it is never shorter than the straight line. Drive time is `miles / mph`, at least 0.01 minute, so it is positive. All instances pass `load_graph` and `load_config`. These are synthetic roads. They are not OpenStreetMap or any real network, and they are not described as one.

Seeds and replication. Replicate `r` of geometry `g` draws a 50-station pool from `Random("chargeplan-experiment-j:<seed>:<g>:<r>:pool")` in nested blocks, so a size is a prefix of the pool (10 ⊂ 25 ⊂ 50). A road's length factor and speed draw come from a stream keyed by the node pair and are reused by every condition that contains both nodes, so the speed and SOC conditions of one replicate share their geometry. Same seed gives the same inputs. A different seed gives different pools. `--seed` applies to J, and `--astar-replicates` changes the replicate count.

Heuristic assumptions. `h(v)` is the straight-line distance from `v` to the destination divided by the fastest edge speed, rounded down to a millisecond. It is admissible and consistent because every road is at least the straight line and no edge is faster than the fastest edge. It is zero if any edge has zero drive time, and J's edges never do. It counts driving time only. It ignores charging time and stops. `h_origin_minutes` in each note is its value at the origin.

Fair comparison. Label-setting (`use_heuristic=False`) and A* (`True`) get the same graph and config objects, and a fingerprint of the inputs is checked before and after. Each instance is timed with 3 interleaved repeats that alternate which algorithm goes first. The median runtime is reported. The label, expanded states, and peak states must be identical across repeats or the run stops. Each algorithm is compared on feasibility, status, total time, stop count, and distance. A disagreement fails the run and writes `astar_failures.json` with the instance id, the seed, and both labels.

Metrics. For each feasible instance, the state reduction is `100·(LS − A*)/LS` where `LS` and `A*` count expanded states, and it is undefined if `LS` is 0. It is negative if A* expands more. Peak reduction uses `peak_states_stored` the same way. Runtime is reported as the A*/LS ratio. The summary gives the median, quartiles, minimum, and maximum of the per-instance values, never an average of percentages, and the pooled figure `100·(ΣLS − ΣA*)/ΣLS` next to it. Infeasible instances are reported apart from feasible ones. `astar_summary.json` has the overall figures and the breakdowns by size, geometry, SOC step, speed, and geometry by speed. It also has Spearman rank correlations of the state reduction with `h_origin_minutes / total_time` (how much of the trip's time the heuristic covers) and with the route ratio (route miles over straight miles).

Rows. J's rows have `suite=J` in the note, with the factors, `edges`, `capacity_steps`, `straight_miles`, `h_origin_minutes`, `min_stops`, and `timing`. The A* row also carries `states_reduction_pct`. Rows of the S6 comparisons (the Detroit–Chicago sample and the tiny graphs) stay in the same file and are marked `suite=S6; set=sample` or `suite=S6; set=tiny`.

### Measurements

Default run: seed 505, 6 replicates per cell, 3 timing repeats. The whole `run_all` command took about 200 s on the development machine, about half in J.

- 216 instances, all feasible, so there are no infeasible J instances to report. The code handles them and unit tests cover that path, but this suite does not produce one.
- 0 objective mismatches. All 216 pairs agree on status, time, stops, and distance.
- State reduction: median 17.2% (interquartile range 13.1–23.3%, minimum 1.3%, maximum 43.2%), pooled 18.3%. 0 instances had a negative reduction or no reduction, so on these 216 instances A* never expanded more states than label-setting. That is a measurement on this suite, and no test asserts it.
- Peak states stored: median reduction 10.2% (IQR 7.0–14.5%, minimum 0%, maximum 31.3%), pooled 12.0%. On 6 of 216 instances (2.8%) A* stored no fewer states.
- Runtime: the median A*/LS ratio is 0.86 (IQR 0.78–0.97, range 0.52–3.09), and the pooled ratio is 0.85. A* was slower on 38 of 216 instances (17.6%). A* does extra work per state (computing `h`), so a small state saving can be a runtime loss. Runtime is also noisy. An earlier run of the same states with the previous vehicle settings had A* slower on 15 instances and a worst ratio of 1.25, and the 3.09 here is a single instance, so I read the slower-instance counts and the extremes as timing noise on top of the real per-state cost, not as a stable result. Runtime depends on the machine and no test asserts it.

State reduction by factor, median over feasible instances (pooled in the JSON):

| Factor | Level | Median reduction % |
| --- | --- | --- |
| Geometry | straight | 24.7 |
|  | curved | 14.1 |
|  | detour | 14.9 |
| Road speeds | homogeneous | 20.9 |
|  | heterogeneous | 14.6 |
| SOC step | 1 kWh | 17.4 |
|  | 4 kWh | 16.7 |
| Chargers | 10 | 17.0 |
|  | 25 | 15.9 |
|  | 50 | 18.1 |

By geometry and speed, median reduction (range): straight/homogeneous 27.2% (21.3–36.4), straight/heterogeneous 19.4% (14.4–43.2), curved/homogeneous 15.2% (11.7–24.7), detour/homogeneous 19.3% (11.7–28.0), curved/heterogeneous 11.1% (3.3–18.5), and detour/heterogeneous 9.8% (1.3–20.9). The share of instances where A* ran slower than label-setting was 33% for detour/heterogeneous, 22% for curved/heterogeneous, 19% for curved/homogeneous, 17% for detour/homogeneous, 11% for straight/heterogeneous, and 3% for straight/homogeneous.

Where A* helps most and least. The biggest savings are on straight corridors with one road speed, where the heuristic is nearly exact. The smallest are on winding or detoured roads with mixed speeds, where `h` is loose. That agrees with the recorded quantities: the median `h_origin_minutes / total_time` is 0.92 for straight/homogeneous and 0.61 for detour/heterogeneous, and the median route ratio is 1.03 against 1.38. Across all 216 instances the state reduction has a Spearman correlation of +0.69 with the heuristic ratio and −0.63 with the route ratio. This is an association in this suite. I did not isolate a cause. Size and SOC step matter little compared with geometry and speed: the size medians span 15.9–18.1% and are not monotonic, so I draw no size trend. The 1 kWh step is slightly higher than the 4 kWh step (17.4% against 16.7%), a difference smaller than the spread within a cell. Because the road graph is identical at both steps, this difference comes from the discretization alone.

Memory. `peak_states_stored` is the size of the best-label table, not the heap, so it is only a proxy for the `O(S·|E| + M·S)` bound. A* lowers it by less than it lowers the expanded states (10.2% against 17.2%). I did not isolate why.

Plots. `states_astar_vs_labelsetting.png` is one point per instance, colored by geometry, with the equal-states diagonal. `astar_savings_by_factor.png` shows the median reduction by size, geometry, SOC step, and speed, with interquartile whiskers.

### Limitations

- This is a designed synthetic family, with one trip, one battery, and one rate mix. Percentages describe this suite and are not a claim about other graphs.
- The roads are range-based and synthetic, with random length factors and speeds. Real roads, charger siting, and speed limits can behave differently. The comparison on the real OpenStreetMap-based graph is not done here. It will be done in the later S9 integration, and J reports no real-world result.
- Every instance is feasible and needs charging. Infeasible queries, which stop quickly, are not measured.
- Three sizes and two SOC steps are too few to support a scaling claim.
- Runtime comes from one machine and varies between runs. Expanded states, peak states, and the objectives are the reproducible measurements.
- The geometry explanation rests on the recorded heuristic and route ratios and on rank correlations. No test of the cause was run.

## S9 real-data runner

`experiments/real_data.py` runs every algorithm on the real-data scenarios that H5 (`python -m data_pipeline`) writes, checks the answers against each other and against an independent plan checker, and writes the result files the visualizer reads. It is separate from `run_all`, which still writes its single `UNAVAILABLE` placeholder for `real_routes.csv` in `results/`.

```text
python -m experiments.real_data --scenarios data/processed --out results/real
python -m experiments.real_data --scenarios data/processed --out results/real --only multi_stop_detroit_chicago sparse_detroit_chicago --repeats 5
```

`--scenarios` defaults to `data/processed` and `--out` to `results/real`. `--only` takes scenario ids. `--repeats` (default 3) is the number of runs per algorithm. The exit code is 0 only if every selected scenario passes.

### Inputs

One folder per scenario, taken in sorted order:

| File | Required | Used for |
| --- | --- | --- |
| `canonical_graph.json` | yes | loaded and validated with `optimizer.model.load_graph` |
| `trip_config.json` | yes | loaded with `load_config`, then `validate_query` |
| `scenario_meta.json` | no | copied into the manifest; `category` and `road_backend` go into the CSV note |

Every field of `scenario_meta.json` is optional. If it has `graph_sha256` or `config_sha256` (hex, with or without a `sha256:` prefix), each must equal the SHA-256 of that file as read. A mismatch fails that scenario with a message naming the key and both hashes. The files are also hashed again after loading, so a file edited mid-run is caught.

### What runs

Label-setting, A*, and greedy always run, through `optimizer.cli.run_algorithm`. Exact runs only inside the bound in `experiments/instances.py` (at most 8 nodes, 5 chargers, 8 capacity steps). Outside it the exact row is `SKIPPED` with the reason in the note, and the solver is not called. A 70 kWh battery at a 1 kWh step is 70 steps, so every H5 scenario at the default settings skips exact. Each algorithm runs `--repeats` times. The label, route, events, `states_expanded`, and `peak_states_stored` must be identical on every repeat or the scenario fails. The reported `runtime_ms` is the median.

### Outputs

```text
<out>/<scenario_id>/label_setting.json
                    astar.json
                    greedy.json           (algorithm "greedy_frc" inside the file)
                    exact.json            (only when exact ran)
                    run_manifest.json
<out>/real_routes.csv
```

Each result is validated against `algorithm_result.schema.json` before any of the scenario's files is written. `run_manifest.json` holds the scenario id, the SHA-256 of the graph and config as read, a copy of `scenario_meta.json` (or `null`), the git commit (`unknown` outside a checkout), the Python version, the platform, a UTC timestamp, the repeat count, the node, edge, and charger counts, and for each algorithm its status, time, stops, distance, `runtime_ms`, `states_expanded`, `peak_states_stored`, plan-check result, and cross-check outcomes.

`real_routes.csv` uses the shared metrics header with experiment `I` and one row per algorithm per scenario. The note holds `road_backend=...` and `category=...` when the meta file has them, and the skip reason on an exact row. `agreement` is `true` on label-setting, A*, and exact rows when they matched, and blank on greedy rows (greedy is allowed to differ). The file is updated, not blindly appended: rows for the scenarios in this run replace any earlier rows for them, and other scenarios' rows stay. Re-running never duplicates a row, and `--only` leaves the rest of the file alone. A failed scenario contributes no rows and its old rows are removed.

### Cross-checks

A failure anywhere names the scenario and exits 1. The other scenarios still run. A failed scenario keeps only a `run_manifest.json` with `"status": "FAILED"` and its errors, and any `<algorithm>.json` left by an earlier run is deleted so the visualizer cannot pick up stale results.

1. Label-setting and A* have the same status and exactly the same `(total_time_minutes, number_of_charging_stops, total_distance_miles)`.
2. If exact ran, it matches label-setting the same way.
3. Greedy is never strictly better than label-setting under the lexicographic order `(time, stops, distance)`. Greedy `INFEASIBLE` while label-setting is `FEASIBLE` is allowed, and is recorded as `infeasible_while_optimal_feasible` in the manifest and in the CSV note. Greedy `FEASIBLE` where label-setting is `INFEASIBLE` fails.
4. Every result passes `experiments/plan_check.py`.

The manifest also records whether A* expanded no more states than label-setting. That is information and not a check, because the heuristic guarantees the label and not a smaller search (see Experiment J).

### Independent plan checker

`experiments/plan_check.py` does not import `battery_state_optimizer`, `exact_solver`, `greedy_baseline`, `reconstruction`, or `optimizer.model`. It re-derives a result from its events, the graph, and the config with exact rational arithmetic: capacity and initial SOC floored to the step, reserve and each edge's energy ceiled; events form one walk from the origin to the destination over existing edges with the stated distances; `route_node_ids` equals that walk; SOC chains from the initial energy; no drive arrives below the reserve; no charge passes capacity or happens at a node without a charger; a charge is a new stop unless the event before it was a charge at the same node; total, driving, and charging time match the recomputed integer milliseconds within 1 ms and distance matches within 1e-6 miles; an `INFEASIBLE` result has no route and no events. `tests/test_plan_check.py` has a valid plan and one corrupted copy per rule, and also requires that the checker accepts all four solvers on the Detroit–Chicago sample and 50 seeded Experiment A graphs.

### Road distances

H5's default backend is `haversine`. Its road distances are straight-line distance times 1.25 at 55 mph, an estimate and not a routed distance. Treat every real-data time and distance as that estimate unless `scenario_meta.json` says another `road_backend`. The runner does not change the backend and records it only when the meta file names it.

### Speed

The solvers compute each edge's discretized energy and integer drive time once, before the search, instead of on every relaxation. Labels, status, `states_expanded`, and `peak_states_stored` are unchanged; `tests/test_precompute_equivalence.py` compares them with outputs recorded from the earlier code on the sample and 50 seeded graphs. On the 50-charger Experiment D instance (1,700 edges) a label-setting solve fell from about 0.28 s to 0.04 s.

## What does not run

These files stay a single `UNAVAILABLE` row. The default run does not replace them with measurements, and it does not replace C, D, or E with that placeholder:

- `greedy_comparison.csv` — FRC baseline, H4. No `greedy_vs_optimizer.png`.
- `initial_soc.csv`, `charger_density.csv`, `charger_speed.csv` — Experiments F, G, and H (H9). No `feasibility_vs_charger_density.png`.
- `real_routes.csv` in `results/` — `run_all` still writes the placeholder. Real rows come from `python -m experiments.real_data` (see S9 above) into `results/real/real_routes.csv`.

`greedy_baseline.solve` is not called.

## Plots

`plots/` receives the eight measured charts: runtime and states against SOC step, objective error against SOC step, runtime and states against charger count, trip time against capacity, A* against label-setting states (one point per J instance), and `astar_savings_by_factor.png` (median J state reduction by factor). SOC-step axes are logarithmic. The two charger-count charts draw the median per count as a line, the interquartile range as whiskers, and each replicate as a light dot, so the replicates are not collapsed or overwritten. Trip time omits the infeasible 40 kWh point instead of drawing it as zero. The other two Section 23 names are omitted until H4 and H9 have rows.

## Commands

```text
make experiments
python -m experiments.run_all --out results --plots plots --seed 505 --tiny 20 --correctness 200 --repeats 3
```

`--charger-counts 10,25,50,100,200` and `--scaling-replicates N` change Experiment D (200 is the most the pool holds, 10 replicates is the default). `--astar-replicates N` changes Experiment J (6 per cell is the default). A run with sweeps also writes `charger_scaling_summary.json` and `astar_summary.json`. `--no-sweeps` writes the S6 files only and leaves C, D, E, and J unmeasured; it deletes any `charger_scaling_summary.json`, `astar_summary.json`, and `astar_failures.json` left in the output directory by an earlier run. `--correctness 0` skips Experiment A, does not write `correctness_summary.json`, and deletes an old one. `--tiny` must be at least 1.
