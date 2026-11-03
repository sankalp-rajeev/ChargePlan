# PRD — EV Charging-Aware Route Optimizer

**Course:** CIS 505 — Algorithms Analysis and Design  
**Institution:** University of Michigan–Dearborn  
**Semester:** Fall 2026  
**Team:** Sankalp Rajeev (50%) and Shannon (50%)  
**Project deadline:** December 2, 2026  
**Primary language:** Python  
**Project type:** Automotive route optimization / EV trip planning  
**Status:** Revised draft v2 (Nov 3, 2026)

---

## 1. Executive Summary

We will build an **EV Charging-Aware Route Optimizer** that determines how a battery-electric vehicle should travel from an origin to a destination while jointly deciding:

1. which route to take,
2. which charging stations to stop at,
3. how much to charge at each stop,

with the primary objective of minimizing:

\[
\text{Total Trip Time}
=
\text{Driving Time}
+
\text{Charging Time}
\]

subject to battery-state and reserve constraints.

The key algorithmic idea is to expand the route-planning state from simply a road location to:

\[
(\text{location}, \text{battery state})
\]

The core optimizer will use a custom **greedy label-setting / priority-queue shortest-path algorithm** over this battery-expanded state graph, with an **A\* variant** (admissible straight-line-time heuristic) to measure how much search effort a heuristic saves. A **farthest-reachable-charger greedy heuristic** will provide a simple comparison, and an **exact exhaustive/backtracking solver** on tiny instances will validate the search against the same discretized model. A very fine SOC resolution serves as the reference for discretization error.

The final system will use real charging-station and road-network data for demonstration, while synthetic graphs will be used for controlled algorithm experiments.

The project will end with an interactive visualizer that shows the selected route, charging stations, battery state-of-charge (SOC), charging events, trip-time breakdown, and comparison against the greedy baseline.

The project is deliberately scoped so that the **algorithm is the intellectual centerpiece**, while mapping, data integration, and visualization support the final demonstration.

---

## 2. Course Requirement Mapping

The CIS 505 project requires a two-person team to solve an automotive/vehicular problem, apply one or more algorithm-design techniques taught in the course, analyze efficiency, implement the solution, evaluate it, discuss limitations, document individual contributions, and submit the required project deliverables by December 2, 2026.

This project satisfies those requirements as follows:

| CIS 505 requirement | How this project satisfies it |
|---|---|
| Automotive / vehicular problem | EV route planning and charging optimization |
| Interesting practical application | Long-distance EV trip planning under battery constraints |
| Algorithm-design technique | Greedy label-setting / shortest-path search over expanded states (Section 11) |
| Justification of technique | Non-negative costs, optimal substructure, and greedy-choice property (Section 11.2) |
| Additional course technique | Backtracking / exhaustive search for exact small-instance validation |
| Baseline technique | Farthest-reachable-charger greedy heuristic (Section 12) |
| Efficiency analysis | Worst-case asymptotic time and space analysis, plus A* search-effort comparison |
| Correctness | Proof sketch (Section 11.2) plus exact-solver agreement on 100+ random instances |
| Algorithm implementation | Core optimizer implemented from scratch in Python |
| Illustrative examples | Hand-built small graphs with traceable state transitions |
| Execution experiments | Runtime, memory, solution quality, state count, charger count, SOC resolution |
| Program screenshots | CLI results, experiment plots, and interactive route visualizer |
| Strengths / weaknesses | Explicit discussion of discretization, simplified charging model, scalability |
| Future improvements | Nonlinear charging, queues, traffic, weather, real-time charger availability |
| Team contribution | Explicit 50/50 Sankalp-Shannon split with separate ownership areas |
| Presentation | ~10-minute recorded (.mp4) algorithm + experiment + visualization demo |
| Peer feedback | Individual feedback form on other groups' videos, due December 7 |
| Source-code appendix | Repository source included or attached as required |

The course handout also expects discussion of time efficiency, space efficiency, implementation environment, observed runtime, pseudocode, sample executions, and member roles; each of these has a dedicated section in this PRD. The report structure in Section 37 follows the handout's element list one to one.

---

# 3. Problem Statement

## 3.1 Real-World Motivation

Traditional route planners primarily optimize driving time or distance.

That is insufficient for electric vehicles because route feasibility and total trip duration depend on:

- current battery state,
- battery capacity,
- minimum reserve,
- energy required to traverse each road segment,
- charger locations,
- charger speeds,
- charging time,
- whether partial charging is allowed.

A route that is shortest by road distance may be infeasible for a particular battery state.

A route that visits the nearest charger may also be slower than a route that drives farther to a faster charger.

Similarly, charging to 100% at every stop can waste time if only enough energy to reach a better future charger or the destination is needed.

Therefore, the problem requires **joint route and charging optimization**.

---

## 3.2 Primary Research Question

> How effectively can a battery-state routing algorithm minimize total EV trip time compared with a simple greedy charging strategy while satisfying battery constraints?

## 3.3 Secondary Research Question

> How does battery-state discretization affect solution quality, runtime, memory use, and explored state-space size?

## 3.4 Additional Evaluation Questions

1. How often does the greedy heuristic produce a suboptimal charging plan?
2. How does optimizer runtime scale as the number of charging stations increases?
3. How does charger density affect route feasibility and total trip time?
4. How does vehicle battery capacity affect the number of required stops?
5. How much computational cost is introduced by finer SOC resolution?
6. Does the optimizer agree with an exact exhaustive solver on small instances?
7. How much search effort does an admissible A* heuristic save relative to plain label-setting?
8. How large is the discretization error relative to a very fine-resolution reference solution?

---

# 4. Product Goals

## 4.1 Primary Goals

The project must:

- find a battery-feasible route from origin to destination,
- select charging stations,
- determine charging quantities,
- minimize total driving + charging time,
- enforce SOC and reserve constraints,
- reconstruct the full route and charging plan,
- compare against a greedy heuristic,
- verify optimality on small cases,
- measure time and space performance,
- operate on synthetic and real-data examples,
- provide an interactive final visualization.

## 4.2 Academic Goals

The project should clearly demonstrate:

- graph modeling,
- greedy algorithm design,
- state-space expansion,
- priority queues,
- exhaustive/backtracking validation,
- worst-case complexity analysis,
- empirical scaling,
- time-space-quality tradeoffs,
- experimental design.

## 4.3 Portfolio Goals

The finished repository should visibly demonstrate:

- algorithm ownership,
- optimization,
- automotive/EV domain knowledge,
- public-data integration,
- reproducible experiments,
- visualization,
- engineering separation between algorithm and UI.

---

# 5. Non-Goals

The Fall 2026 course version will **not** attempt to solve the full industrial EV-routing problem.

The following are explicitly out of scope for the MVP:

- multiple vehicles,
- delivery routing / fleet VRP,
- traffic prediction,
- real-time road traffic,
- charger queues,
- charger occupancy prediction,
- electricity price optimization,
- weather-aware energy models,
- elevation-aware energy consumption,
- battery degradation modeling,
- stochastic charger availability,
- nonlinear battery charging curves,
- reservations,
- reinforcement learning,
- multi-agent routing,
- mobile app deployment.

These are future-work opportunities, not required features.

---

# 6. Assumptions

For the course version:

1. One EV is routed at a time.
2. Battery capacity is fixed during a trip.
3. Energy consumption is deterministic.
4. Energy consumption is a fixed kWh-per-mile rate \(c\), so edge energy is \(e_{uv}=c\cdot d_{uv}\), computed by the optimizer from the trip configuration and not stored in the graph.
5. All road-edge travel times are non-negative.
6. Charging time is linear with energy added in the MVP.
7. Each station has a known effective charging rate.
8. Partial charging is allowed.
9. A configurable minimum reserve is enforced.
10. The road/charger graph is static during one optimization query.
11. SOC is discretized into multiples of \(\Delta E\) with conservative rounding: edge energy and reserve round up, initial energy and usable capacity round down (Section 7.1). Every plan accepted by the discretized model is therefore feasible in real energy.
12. Charger availability is assumed known and available.
13. Road preprocessing may use established libraries; the **battery-state optimization algorithm must be our implementation**.
14. The origin may have a charger if the graph gives it a non-null charging rate.

---

# 7. Formal Problem Definition

Let the reduced routing graph be:

\[
G=(V,E)
\]

where each relevant node represents:

- the origin,
- the destination, or
- a candidate charging station.

Let:

\[
C \subseteq V
\]

be the subset of nodes with a non-null charging rate. The origin may belong to \(C\) (for example, a home charger); the destination normally does not.

For every directed edge:

\[
(u,v)\in E
\]

the graph stores only:

- \(d_{uv}\): road distance (miles),
- \(t_{uv}\): driving time (minutes).

Edge energy is **not** stored in the graph. The optimizer derives it from the trip configuration, so one graph can be reused for different vehicles. The graph must satisfy \(d_{uv} \ge\) the straight-line distance between \(u\) and \(v\) (checked in `test_contracts.py`); the A* heuristic in Section 11.3 depends on this.

Vehicle and trip inputs (from `trip_config.json`):

- \(B\): usable battery capacity (kWh),
- \(b_0\): initial battery energy (kWh),
- \(r\): minimum battery reserve (kWh),
- \(\Delta E\): SOC discretization step (kWh),
- \(c\): energy-consumption rate (kWh per mile).

For node \(v \in C\):

- \(p_v\): charging rate (kW).

## 7.1 Derived and Discretized Quantities

Edge energy:

\[
e_{uv}=c\cdot d_{uv}
\]

All battery quantities in the algorithm are integers counted in units of \(\Delta E\), using conservative rounding:

\[
B'=\left\lfloor \frac{B}{\Delta E}\right\rfloor,\quad
b_0'=\left\lfloor \frac{b_0}{\Delta E}\right\rfloor,\quad
r'=\left\lceil \frac{r}{\Delta E}\right\rceil,\quad
\bar e_{uv}=\left\lceil \frac{e_{uv}}{\Delta E}\right\rceil
\]

Capacity and initial energy round down; reserve and energy consumption round up. Under this rule, any plan accepted by the discretized model never drops below the reserve in real energy. SOC values reported in results use the discretized accounting, which is a lower bound on true SOC.

---

# 8. State-Space Model

Each optimization state is:

\[
(v,b,f)
\]

where:

- \(v\) is the current graph node,
- \(b\in\{0,\dots,B'\}\) is the battery level in units of \(\Delta E\),
- \(f\in\{0,1\}\) records whether the last action was a charge at \(v\) (\(f=1\)) or the vehicle just arrived by driving (\(f=0\)).

The flag \(f\) exists so that a charging stop is counted once, no matter how many \(\Delta E\) increments are added during that stop. This is required by the tie-breaking rule in Section 10.

With \(S=B'+1\) battery levels, the expanded graph contains at most:

\[
2|V|S = O(|V|S)
\]

states.

Example state:

```text
(Charger_17, b=35, f=0)
```

means the vehicle is at Charger 17 with \(35\Delta E\) kWh remaining (35 kWh when \(\Delta E = 1\) kWh), having just arrived by driving.

---

# 9. Allowed State Transitions

## 9.1 Drive Transition

\[
(u,b,f)\rightarrow(v,\;b-\bar e_{uv},\;0)
\]

is valid only if:

\[
b-\bar e_{uv}\geq r'
\]

Transition cost (a triple, see Section 9.3):

\[
(t_{uv},\;0,\;d_{uv})
\]

---

## 9.2 Charge Transition

At a node \(v\) with a non-null charging rate:

\[
(v,b,f)\rightarrow(v,\;b+1,\;1)
\]

provided:

\[
b+1\leq B'
\]

For the linear charging model, one step adds \(\Delta E\) kWh and takes:

\[
t_{charge}
=
\frac{\Delta E}{p_v}\times60
\]

minutes.

Transition cost:

\[
(t_{charge},\;\mathbb{1}[f=0],\;0)
\]

The stop counter increases only on the first charge step of a visit (\(f=0\)). Charging at the origin counts as a stop.

---

## 9.3 Lexicographic Cost Labels

Every state carries a label:

\[
(\text{time},\;\text{stops},\;\text{distance})
\]

Labels add component-wise and are compared lexicographically. All components are non-negative, so this ordering is compatible with addition, and the shortest-path argument in Section 11.2 holds unchanged. In code, Python tuples already compare lexicographically, so `heapq` works directly. Time comparisons use a tolerance of \(10^{-9}\) minutes, or times are stored as integer milliseconds; the choice is recorded in the README.

---

# 10. Objective Function

The primary optimization objective is:

\[
\boxed{
\min(\text{Driving Time}+\text{Charging Time})
}
\]

Secondary quantities reported:

- total distance,
- number of charging stops,
- total charging time,
- total driving time,
- arrival SOC,
- minimum SOC reached,
- number of expanded states,
- algorithm runtime.

Tie-breaking rule:

1. lower total time,
2. fewer charging stops,
3. shorter road distance.

This ordering is implemented as a lexicographic cost label (time, stops, distance); see Section 9.3.

---

# 11. Main Algorithm

## 11.1 Battery-State Label-Setting Optimizer

The main algorithm will be implemented from scratch using Python's `heapq`.

Initial state:

\[
(origin,\;b_0',\;0)
\]

Goal condition: any state \((destination, b, f)\). The reserve is enforced on every drive transition, so \(b\geq r'\) holds automatically.

Because all transition costs are non-negative, the algorithm repeatedly finalizes the currently cheapest reachable state.

### High-Level Pseudocode

```text
function optimize(graph, config, use_heuristic):

    (B', b0', r', dE) = discretize(config)
    if b0' < r': return INFEASIBLE

    start = (origin, b0', 0)
    best[start] = (0, 0, 0)                  # (time, stops, distance)
    parent[start] = null
    pq = min_priority_queue()
    pq.push((priority(best[start], origin), best[start], start))

    while pq is not empty:

        (_, label, state) = pq.pop_min()
        (loc, b, f) = state

        if label != best[state]:             # stale entry
            continue

        if loc == destination:
            return reconstruct_solution(state)

        for next_loc in neighbors(loc):
            e = ceil(c * distance(loc, next_loc) / dE)
            if b - e >= r':
                relax((next_loc, b - e, 0),
                      label + (drive_time(loc, next_loc), 0,
                               distance(loc, next_loc)))

        if charge_rate(loc) is not null and b + 1 <= B':
            new_stop = 1 if f == 0 else 0
            relax((loc, b + 1, 1),
                  label + (60 * dE / charge_rate(loc), new_stop, 0))

    return INFEASIBLE


function priority(label, loc):
    h = heuristic(loc) if use_heuristic else 0
    return (label.time + h, label.stops, label.distance)


function relax(next_state, new_label):
    if next_state not in best or new_label < best[next_state]:   # lexicographic
        best[next_state] = new_label
        parent[next_state] = state
        pq.push((priority(new_label, next_state.loc), new_label, next_state))
```

With `use_heuristic = false` this is the plain label-setting optimizer. With `use_heuristic = true` it is the A* variant (Section 11.3). Both live in `battery_state_optimizer.py`.

---

## 11.2 Technique Justification and Correctness

### Why a greedy label-setting technique

- **Non-negative costs:** every drive and charge transition has a non-negative cost vector.
- **Optimal substructure:** a prefix of an optimal plan to a state is itself an optimal plan to that state.
- **Greedy-choice property:** the unsettled state with the smallest label already has its final optimal label.

The same recurrence can be read as dynamic programming over the expanded state graph (the framing used by Sweda & Klabjan, Section 25.3); the priority queue is the greedy ordering that avoids evaluating states in arbitrary order. Backtracking is used separately as the independent validator (Section 13).

### Correctness argument

**Lemma 1 (feasibility).** Every transition is checked against \(r'\) and \(B'\) before it is relaxed, so every returned plan respects the reserve and capacity constraints. By the conservative rounding in Section 7.1, it also respects them in real energy.

**Lemma 2 (optimality).** When a non-stale state \(s\) is popped, \(best[s]\) is the minimum label over all valid transition sequences from the start to \(s\).

*Proof sketch.* Suppose a path \(P\) to \(s\) has a smaller label. Let \(y\) be the first state on \(P\) not yet settled; its predecessor on \(P\) is settled, so \(y\) has been relaxed and \(best[y]\le\) label of the prefix of \(P\) up to \(y\). Costs are non-negative, so that prefix label is at most the full label of \(P\), which is smaller than \(best[s]\). Then \(y\) would be popped before \(s\), contradicting that \(s\) is popped now. \(\square\)

**Lemma 3 (termination).** The state space is finite (at most \(2|V|S\) states) and each state is settled at most once, so the algorithm terminates.

**Optimality of the returned plan.** The first popped destination state has the minimum label among all destination states, so it minimizes time, then stops, then distance, within the discretized model.

**Scope of the claim.** Optimality holds for the discretized model. The gap to the continuous-charging optimum is measured empirically in Experiment C.

---

## 11.3 A* Variant

The A* variant uses the same state graph and the same relaxation. Only the queue priority changes: it adds a heuristic to the time component.

\[
h(v)=\frac{\text{haversine}(v,\;destination)}{v_{max}}
\]

where \(v_{max}=\max_{(u,w)\in E} d_{uw}/t_{uw}\) (miles per minute).

- **Admissible:** any path from \(v\) to the destination has road length at least the straight-line distance (because \(d_{uw}\ge\) straight-line distance on every edge and the triangle inequality holds), and each edge is traversed at speed at most \(v_{max}\).
- **Consistent:** \(h(u)\le t_{uv}+h(v)\) follows from the same two facts. Charge transitions do not move the vehicle, so \(h\) is unchanged and their cost is non-negative.
- **Tie-breaking preserved:** \(h\) affects only the time component and is zero at the destination, so the first popped destination state still has the lexicographically smallest label.

A* must return exactly the same label as the plain optimizer on every instance (checked in `test_astar.py` and Experiment J). Its purpose is to quantify how many fewer states a heuristic expands.

---

# 12. Greedy Baseline

A deliberately simple heuristic, called the **farthest-reachable-charger (FRC) heuristic**, will be implemented for comparison. The name distinguishes it from the greedy label-setting optimizer, which is exact.

The definition below is frozen at the end of Phase 1.

## 12.1 FRC Definition

Start at the origin with \(b_0'\). Repeat at the current node \(v\) with battery \(b\):

1. Compute the reachable set: nodes \(w\) with a direct edge \(v\rightarrow w\) and \(\bar e_{vw}\le \text{cap}-r'\), where \(\text{cap}=B'\) if \(v\) has a charger (the vehicle can charge first) and \(\text{cap}=b\) otherwise.
2. If the destination is in the reachable set, the target is the destination.
3. Otherwise, among chargers in the reachable set that are strictly closer (straight-line) to the destination than \(v\), choose the one closest to the destination. Ties: higher charging rate, then lexicographically smaller node id. If none exists, return INFEASIBLE.
4. If \(b-\bar e_{vw}\ge r'\), drive to the target. Otherwise charge in \(\Delta E\) steps at \(v\) until \(b\ge r'+\bar e_{vw}\), then drive.
5. Continue from the target.

Strict progress toward the destination guarantees termination. The heuristic is deterministic and uses the same discretization rules as the optimizer.

## 12.2 Properties

The baseline is not meant to be state-of-the-art. It is:

- simple,
- explainable,
- deterministic,
- plausible,
- capable of demonstrating cases where local decisions differ from globally optimal ones (it can be slower, pick a slow charger, or fail to find a route that exists).

It emits the same `algorithm_result.json` schema as the optimizer with `"algorithm": "greedy_frc"`. A different greedy rule (for example, minimizing immediate drive time plus required charge time) is a stretch item, not part of the frozen baseline.

---

# 13. Exact Validation Solver

For small synthetic graphs only, we will implement an exact solver using exhaustive search / backtracking.

To make the validation meaningful, the solver is structurally different from the optimizer:

- it is a depth-first search, not a priority-queue search,
- at each charger visit it enumerates the charge amount directly (0 to \(B'-b\) steps in one decision), rather than one \(\Delta E\) step per transition,
- it shares only the graph/config loader and the discretization function from `model.py`, not search code.

The exact solver will enumerate feasible:

- charging-station sequences,
- charge amounts at each visit, in discretized SOC units,

while pruning branches that:

- violate battery reserve,
- exceed battery capacity,
- already exceed the best known solution label,
- revisit a dominated state (same node and flag, at least as much battery, and a lexicographically no-worse label).

This solver will be intentionally limited to small graphs.

Its purpose is **correctness validation of the search**, not production routing. It runs on the same discretized model, so it does not measure discretization error; that is handled by the fine-resolution reference in Experiment C.

For every tiny validation instance:

\[
Label_{optimizer}=Label_{A^*}=Label_{exact}
\]

(time, stops, and distance) must hold, along with agreement on feasibility. Any mismatch is considered a correctness failure.

---

# 14. Complexity Analysis Plan

Let:

- \(M=|V|\): relevant origin/destination/charging nodes,
- \(S=B'+1\): number of SOC levels.

Number of states (including the charging flag):

\[
O(MS)
\]

With a dense reduced graph, each location may have up to \(O(M)\) drive transitions per battery state.

Therefore, drive transitions are bounded by approximately:

\[
O(M^2S)
\]

Charging transitions add:

\[
O(MS)
\]

Using a binary heap priority queue, a conservative upper bound is:

\[
O(M^2S\log(MS))
\]

for the dense reduced-graph formulation. The A* variant has the same worst-case bound; its benefit is empirical (fewer states expanded).

Space for state labels and parents:

\[
O(MS)
\]

plus graph storage, \(O(M^2)\) for a dense graph.

**Pseudo-polynomial note.** \(S=\lfloor B/\Delta E\rfloor+1\) depends on the numeric value of \(B/\Delta E\), not on the input length, so the runtime is pseudo-polynomial in the battery parameters. Halving \(\Delta E\) roughly doubles \(S\) and therefore the runtime. This is the theoretical basis for Experiment C.

**Baselines.** The FRC heuristic makes at most \(M\) decisions, each scanning up to \(M\) neighbors: \(O(M^2)\). The exact solver is exponential in the number of chargers and charge choices and is documented as such.

The report will distinguish:

- theoretical worst-case complexity,
- observed runtime,
- observed states expanded,
- actual sparse graph behavior.

---

# 15. System Architecture

```text
                 ┌─────────────────────┐
                 │  Real Data Sources  │
                 │ AFDC / OSM road data│
                 └──────────┬──────────┘
                            │
                   Shannon data pipeline
                            │
                            ▼
              ┌─────────────────────────────┐
              │ canonical_graph.json        │
              │ trip_config.json            │◀── Synthetic tests
              └─────────────┬───────────────┘
                            │
          ┌─────────────────┼──────────────────┐
          ▼                 ▼                  ▼
┌──────────────────┐ ┌──────────────┐ ┌─────────────────┐
│ Sankalp:         │ │ Sankalp:     │ │ Shannon:        │
│ label-setting +  │ │ exact solver │ │ greedy (FRC)    │
│ A* optimizer     │ │ (tiny only)  │ │ baseline        │
└────────┬─────────┘ └──────┬───────┘ └────────┬────────┘
         └──────────────────┼──────────────────┘
                            ▼
                 ┌──────────────────────┐
                 │ algorithm_result.json│  (one file per algorithm)
                 └──────────┬───────────┘
                            │
                   Shannon visualizer
                            │
                            ▼
                 ┌──────────────────────┐
                 │ Interactive EV Demo  │
                 └──────────────────────┘
```

---

# 16. Independence Contract Between Team Members

The two team members work independently by agreeing on frozen file contracts, one shared module, and one command-line interface. Everything is frozen at the end of Phase 1.

## 16.1 Input Contract: `canonical_graph.json`

```json
{
  "nodes": [
    {
      "id": "origin",
      "type": "origin",
      "lat": 42.3314,
      "lon": -83.0458,
      "charge_rate_kw": null
    },
    {
      "id": "station_1",
      "type": "charger",
      "lat": 42.2808,
      "lon": -83.7430,
      "charge_rate_kw": 150
    },
    {
      "id": "destination",
      "type": "destination",
      "lat": 41.8781,
      "lon": -87.6298,
      "charge_rate_kw": null
    }
  ],
  "edges": [
    {
      "from": "origin",
      "to": "station_1",
      "distance_miles": 55.2,
      "drive_time_minutes": 51.3
    }
  ]
}
```

Rules:

- A node can charge if and only if `charge_rate_kw` is non-null, whatever its `type`. An origin with a home charger therefore sets a rate.
- Edges are directed and store road distance and driving time only. Energy is derived from `trip_config.json`.
- `distance_miles` must be at least the straight-line distance between the endpoints.
- Edges whose energy exceeds the largest battery capacity in the experiment plan may be omitted to keep the graph small.

## 16.2 Trip Configuration: `trip_config.json`

```json
{
  "origin": "origin",
  "destination": "destination",
  "battery_capacity_kwh": 70,
  "initial_soc_kwh": 54,
  "reserve_kwh": 7,
  "consumption_kwh_per_mile": 0.30,
  "soc_step_kwh": 1.0
}
```

Experiments that vary initial SOC in percent convert to kWh before writing this file.

## 16.3 Output Contract: `algorithm_result.json`

```json
{
  "status": "FEASIBLE",
  "algorithm": "label_setting",
  "total_time_minutes": 286.4,
  "driving_time_minutes": 251.0,
  "charging_time_minutes": 35.4,
  "total_distance_miles": 282.5,
  "number_of_charging_stops": 2,
  "soc_step_kwh": 1.0,
  "route_node_ids": [
    "origin",
    "station_1",
    "station_2",
    "destination"
  ],
  "events": [
    {
      "type": "drive",
      "from": "origin",
      "to": "station_1",
      "distance_miles": 55.2,
      "duration_minutes": 51.3,
      "energy_kwh_discretized": 17,
      "soc_before_kwh": 54,
      "soc_after_kwh": 37
    },
    {
      "type": "charge",
      "station": "station_1",
      "soc_before_kwh": 37,
      "soc_after_kwh": 49,
      "duration_minutes": 4.8
    }
  ],
  "metrics": {
    "runtime_ms": 18.4,
    "states_expanded": 2143,
    "peak_states_stored": 5120
  }
}
```

`algorithm` is one of `label_setting`, `astar`, `greedy_frc`, `exact`. `status` is `FEASIBLE` or `INFEASIBLE`; an infeasible result has empty `route_node_ids` and `events` but still reports `metrics`. SOC values use the discretized accounting from Section 7.1.

## 16.4 Shared Module and Command-Line Interface

`optimizer/model.py` is the only shared code. It contains the graph and config loaders, the discretization function, and the result writer. Sankalp owns it; it is frozen at the end of Phase 1.

All four algorithms are runnable through one interface:

```text
python -m optimizer.cli --algorithm {label_setting,astar,greedy,exact} \
    --graph canonical_graph.json --config trip_config.json --out algorithm_result.json
```

Each algorithm module exposes `solve(graph, config) -> result` and the CLI dispatches to it. The process exits with code 0 for both feasible and infeasible instances; failures to read or validate inputs exit non-zero.

## 16.5 Freeze Rule

After the Phase 1 freeze, any contract change requires both members' agreement and a version bump noted in the README.

---

# 17. Data Sources

## 17.1 Charging Stations

Primary source:

**U.S. Department of Energy Alternative Fuels Data Center (AFDC) Station Locator / API**

Expected fields:

- station name,
- address,
- latitude and longitude,
- charger type (Level 2, DC fast),
- connector types, when available,
- charging power, when available,
- network,
- availability status.

AFDC data is frequently missing charger power. Where `charge_rate_kw` is absent, the pipeline assigns a documented fallback rate by charger class and records which nodes used a fallback. Experiment H includes a sensitivity run on these fallback values.

AFDC data will be downloaded once as a dated snapshot and committed (or its checksum recorded) so every experiment is reproducible without live API access.

## 17.2 Road Network

Primary approach:

- OSM road network through **OSMnx**,
- shortest-time road paths between selected nodes,
- precomputed road distances and driving times.

Preprocessing may use established libraries. The battery-state optimization itself remains our implementation.

## 17.3 Region and Candidate Selection

The default real-data region is Michigan and adjacent Midwest corridors (for example, Detroit to Chicago and Detroit to Cleveland). The final region list is frozen at the end of Phase 1.

To keep the all-pairs road-routing precomputation tractable:

- candidate chargers are limited to a corridor around the origin-destination line,
- edges whose energy exceeds the largest battery capacity in the experiment plan are omitted,
- computed routes are cached on disk.

---

# 18. Functional Requirements

## FR-1 — Trip Input

The system must support:

- origin,
- destination,
- battery capacity,
- initial SOC,
- reserve SOC,
- energy-consumption rate,
- SOC discretization.

---

## FR-2 — Battery Feasibility

The optimizer must never return a route where:

\[
SOC < reserve
\]

after any driving transition.

---

## FR-3 — Charging

The optimizer must support partial charging.

It must not require the battery to reach 100% at every stop.

---

## FR-4 — Route Reconstruction

The optimizer must return:

- ordered route,
- ordered charging stops,
- SOC before/after every event,
- charge amount,
- charging duration,
- driving duration.

---

## FR-5 — Infeasible Trips

If no legal battery-feasible route exists, return:

```text
INFEASIBLE
```

rather than an invalid route.

---

## FR-6 — Greedy Comparison

The same input instance must be runnable through:

- optimized algorithm,
- greedy baseline.

---

## FR-7 — Exact Validation

Small instances must be runnable through:

- optimizer,
- exhaustive/backtracking solver.

---

## FR-8 — Metrics

Every run should record:

- total time,
- driving time,
- charging time,
- distance,
- number of stops,
- runtime,
- states expanded,
- peak state count.

---

## FR-9 — Reproducibility

Experiment instances and results must be reproducible from committed data/configuration files.

---

## FR-10 — Visualization

The final visualizer must be able to display an `algorithm_result.json` without recomputing the algorithm.

---

## FR-11 — A* Variant

The optimizer must support an A* mode (`--algorithm astar`) that uses the heuristic in Section 11.3 and returns exactly the same label as the plain optimizer. It must report states expanded so the two can be compared.

---

## FR-12 — Uniform Interface

Every algorithm (label-setting, A*, greedy, exact) must be runnable through the shared CLI in Section 16.4 and must emit the `algorithm_result.json` schema, so the visualizer and experiment scripts treat them identically.

---

# 19. Non-Functional Requirements

## Correctness

No route may violate:

- battery reserve,
- capacity,
- graph connectivity.

## Reproducibility

Synthetic test generation must support a fixed random seed.

## Modularity

Algorithm, data preprocessing, experiments, and visualization must be separable modules.

## Explainability

Every output route should be traceable as a sequence of drive and charge actions.

## Offline Capability

Course-critical experiments should not require a live web API after data snapshots are prepared.

## Cost

Target project cost:

\[
\$0
\]

using public data and open-source tools.

---

# 20. Test Plan

## 20.1 Unit Tests

### T1 — Direct Route, No Charging Required

```text
A -------- B
```

Battery is sufficient to reach B directly.

Expected:

- zero charging stops,
- direct route,
- correct arrival SOC.

---

### T2 — Exactly One Mandatory Charging Stop

```text
A ---- C ---- B
      charger
```

A → B is impossible on one charge.

Expected:

- C selected,
- exactly one charging stop,
- reserve never violated.

---

### T3 — Nearest Charger Is Suboptimal

```text
              Fast charger
             /
A ----------X------------ B
 \
  Slow charger
```

The slow charger is geographically closer.

Expected:

- greedy may choose slow charger,
- optimizer chooses the route with lower total trip time.

This is a key illustrative case for the report.

---

### T4 — Partial Charging Beats Full Charging

At an intermediate station, full charging requires unnecessary time.

Expected:

- optimizer charges only enough to make the next optimal transition,
- partial-charge plan beats forced-full-charge baseline.

---

### T5 — Infeasible Destination

No charger or route lies within vehicle range.

Expected:

```text
INFEASIBLE
```

---

### T6 — Reserve Constraint

A route is physically reachable only if the vehicle arrives below the configured reserve.

Expected:

- route rejected.

---

### T7 — Battery Capacity Boundary

Edge energy exactly equals:

\[
battery-reserve
\]

Expected:

- transition accepted.

One discretization step more:

- rejected.

---

### T8 — Faster Charger Requires Detour

Two options:

- short detour to slow charger,
- longer detour to high-power charger.

Expected:

- optimizer considers total trip time rather than distance alone.

---

### T9 — Multiple Charging Stops

Battery is intentionally small.

Expected:

- two or more charging stops,
- correct SOC accounting after every segment.

---

### T10 — Equal-Cost Tie

Two paths have identical total travel time.

Expected:

- deterministic tie-breaker,
- fewer stops preferred.

---

### T11 — Start With Low SOC

Starting battery is below what is needed to leave origin.

If the origin node has a non-null `charge_rate_kw`:

- charge first (counted as one stop).

Otherwise:

- infeasible.

---

### T12 — Destination Reachable Without Extra Charging

Vehicle arrives at a charger while destination is already reachable.

Expected:

- algorithm does not add unnecessary charging.

---

### T13: Conservative Energy Rounding

With \(\Delta E = 1\) kWh and an edge energy of 16.56 kWh, the transition must consume 17 units. A trip whose rounded energy leaves exactly the reserve must be accepted; one unit less must be rejected.

---

### T14: Charging-Stop Counting

Charging five consecutive \(\Delta E\) steps at one charger must count as one stop. Leaving and charging at a second charger counts as two. Charging at the same charger on two separate visits counts as two.

---

### T15: A* and Label-Setting Agreement

On every synthetic and real-data instance in the test suite, the A* variant must return a label identical to the plain optimizer's, with states expanded no greater.

---

### T16: Greedy Failure Case

Construct an instance where the FRC heuristic returns INFEASIBLE (or a strictly slower plan) while the optimizer finds a feasible, faster plan. This case is also used in the report as an illustrative example.

---

# 21. Exact-Correctness Experiment

Generate many tiny random graphs with approximately:

- 4–8 relevant nodes,
- 1–5 chargers,
- small battery-state space.

For each instance:

1. run the label-setting optimizer,
2. run the A* optimizer,
3. run the exact solver,
4. compare feasibility,
5. compare the optimal lexicographic objective (time, stops, distance).

Success condition:

\[
100\%
\]

agreement on all validated instances.

If exact and optimizer costs differ, investigate before any performance experiment proceeds.

Recommended count:

- at least 100 tiny random instances.

Scope of the claim: agreement shows the search is correct on the discretized model. It does not measure discretization error; Experiment C does.

---

# 22. Main Experimental Plan

## Experiment A — Correctness vs Exact Solver

### Variable

Random tiny graph instance.

### Metrics

- feasibility agreement,
- objective agreement,
- route-cost difference.

### Goal

Demonstrate correctness.

---

## Experiment B — Optimizer vs Greedy Baseline

### Variables

- charger layout,
- charger speed,
- initial SOC,
- battery capacity.

### Metrics

- total trip time,
- charging time,
- number of stops,
- percentage improvement.

### Goal

Determine when global optimization outperforms local greedy decisions.

---

## Experiment C — SOC Discretization Tradeoff

Test SOC increments such as:

```text
20%
10%
5%
2%
1%
```

or equivalent kWh buckets, plus a very fine reference resolution (for example 0.1 kWh) treated as the near-continuous baseline.

Because rounding is conservative (assumption 11), report discretization error as the gap between each resolution's objective and the reference objective, and the fraction of instances that become infeasible at coarse resolution.

### Measure

- objective value,
- runtime,
- memory,
- states expanded.

### Expected relationship

Finer resolution:

- larger state space,
- greater computational cost,
- potentially better solution quality.

This is one of the most important course-analysis experiments.

---

## Experiment D — Number of Charging Stations

Increase candidate charging nodes, for example:

```text
10
25
50
100
200
```

### Measure

- runtime,
- memory,
- states expanded.

### Goal

Empirically evaluate scaling against the theoretical complexity.

---

## Experiment E — Battery Capacity

Run the same trip with different capacities.

Example relative capacities:

```text
40 kWh
55 kWh
70 kWh
85 kWh
100 kWh
```

### Measure

- stops,
- total time,
- optimizer runtime.

### Goal

Show how physical range changes the optimization structure.

---

## Experiment F — Initial SOC

Run the same route with:

```text
20%
40%
60%
80%
100%
```

### Measure

- route selected,
- charging stops,
- charging time,
- total trip time.

---

## Experiment G — Charger Density

Create variants where charging stations are:

- sparse,
- medium density,
- dense.

### Measure

- feasibility rate,
- total time,
- states explored.

---

## Experiment H — Charger-Speed Heterogeneity

Compare networks with:

- identical charger speed,
- mixed slow/fast chargers.

### Goal

Demonstrate why route optimization must jointly consider charging speed.

---

## Experiment I — Real-World Demonstration Routes

Select approximately 3 representative trips from the prepared road/charger snapshot.

Suggested categories:

1. medium-distance route requiring no stop under high initial SOC,
2. route requiring one charging stop,
3. longer route requiring multiple charging decisions.

Exact routes should be frozen only after the real-data pipeline is working.

For each:

- optimized route,
- greedy route,
- total trip time,
- charging stops,
- SOC profile.

---

## Experiment J — A* vs Label-Setting

### Variables

- instance (synthetic and real-data routes),
- number of charging stations,
- SOC resolution.

### Metrics

- states expanded,
- runtime,
- objective-label equality (must always hold).

### Goal

Quantify how much search effort the admissible straight-line-time heuristic saves, and identify where it helps least (for example, dense charger corridors with large detours).

---

# 23. Experiment Output Artifacts

Automatically generate:

```text
results/
├── correctness.csv
├── greedy_comparison.csv
├── soc_resolution.csv
├── charger_scaling.csv
├── battery_capacity.csv
├── initial_soc.csv
├── charger_density.csv
├── charger_speed.csv
├── astar_comparison.csv
└── real_routes.csv
```

Plots:

```text
plots/
├── runtime_vs_soc_resolution.png
├── states_vs_soc_resolution.png
├── runtime_vs_chargers.png
├── greedy_vs_optimizer.png
├── trip_time_vs_battery_capacity.png
├── feasibility_vs_charger_density.png
├── objective_error_vs_soc_resolution.png
└── states_astar_vs_labelsetting.png
```

---

# 24. Visualizer Requirements

The visualizer is the final presentation layer.

Recommended implementation:

- Streamlit,
- Folium or PyDeck,
- Plotly/Matplotlib for SOC/time charts.

The visualizer must consume precomputed result files.

It must **not** contain the optimizer logic.

---

## 24.1 Main Map

Display:

- origin,
- destination,
- all candidate chargers,
- selected charging stations,
- optimized route.

Visual distinction:

- origin marker,
- destination marker,
- unused chargers,
- selected chargers.

---

## 24.2 Trip Timeline

Example (assuming 0.5% SOC per mile):

```text
Origin
SOC 80%
   |
   | Drive 72 mi
   v
Station A
SOC 44%
   |
   | Charge 44% -> 63%
   v
Station A
SOC 63%
   |
   | Drive 100 mi
   v
Station B
SOC 13%
```

---

## 24.3 SOC Chart

Show:

\[
SOC \text{ vs trip distance/time}
\]

Driving:

- SOC decreases.

Charging:

- SOC increases.

---

## 24.4 Trip Summary

Display:

```text
Total time
Driving time
Charging time
Distance
Charging stops
Arrival SOC
Algorithm runtime
States expanded
```

---

## 24.5 Greedy vs Optimized Comparison

Side-by-side panel:

| Metric | Greedy | Optimized |
|---|---:|---:|
| Total time | ... | ... |
| Driving time | ... | ... |
| Charging time | ... | ... |
| Stops | ... | ... |
| Distance | ... | ... |

---

## 24.6 Optional Animation

If core functionality is complete early:

- animate a vehicle marker along route,
- update SOC as the marker moves,
- pause at charging stations,
- visually raise battery level during charging.

This is a stretch feature only.

Do not sacrifice experiments or report quality to build the animation.

---

# 25. Literature Survey

## 25.1 Overview

EV routing extends ordinary shortest-path planning because battery-electric vehicles introduce:

- limited range,
- battery-capacity constraints,
- charging-station decisions,
- charging duration,
- partial charging,
- charger heterogeneity,
- potentially nonlinear charging,
- potentially uncertain station availability.

The literature progresses from energy-constrained shortest paths toward joint route-and-charging optimization and increasingly realistic operational constraints.

Our course project intentionally implements a simplified, explainable version of this research problem.

---

## 25.2 Foundational Energy-Constrained Routing

### Artmeier et al. (2010) — *The Shortest Path Problem Revisited: Optimal Routing for Electric Vehicles*

Artmeier, Haselmayr, Leucker, and Sachenbacher formalized energy-efficient EV routing as a constrained shortest-path problem.

The work is important because it establishes that EV routing cannot always be treated as an ordinary shortest-path problem. Battery capacity becomes a path-dependent constraint, and recuperation can complicate conventional non-negative edge assumptions.

**Relevance to this project:**

- supports modeling EV routing as a constrained graph problem,
- establishes battery capacity as an algorithmic routing state,
- motivates moving beyond standard shortest-distance routing.

DOI: `10.1007/978-3-642-16111-7_35`

---

### Sachenbacher et al. (2011) — *Efficient Energy-Optimal Routing for Electric Vehicles*

This work examines energy-optimal routing where recuperation can create negative energy costs and battery constraints make simple edge-cost summation insufficient.

The authors develop an A*-based approach and demonstrate it using real road networks.

**Relevance:**

- reinforces that EV routing requires specialized state reasoning,
- demonstrates the connection between classical graph search and EV-specific constraints,
- provides historical context for route-planning algorithm design.

DOI: `10.1609/aaai.v25i1.7803`

---

## 25.3 Dynamic Programming and Charging Decisions

### Sweda & Klabjan (2012) — *Finding Minimum-Cost Paths for Electric Vehicles*

This paper is especially relevant to our project.

It models minimum-cost EV routing with recharging as a **dynamic program** and proves that the state and control spaces can be made discrete under mild assumptions.

Two solution methods are discussed:

- backward recursion on a discrete state space,
- approximate dynamic programming for more general instances.

**Relevance:**

Our battery-state formulation:

\[
(location, SOC)
\]

is conceptually aligned with the idea that location and charge jointly define the routing state.

The paper gives strong academic support for:

- battery-state discretization,
- charging as an explicit decision,
- dynamic/state-space optimization.

Source:
`https://ccitt.northwestern.edu/documents/2012_Sweda.Klabjan_IEVC2012_minimum.pdf`

---

## 25.4 Partial Charging

### Keskin & Çatay (2016) — *Partial Recharge Strategies for the Electric Vehicle Routing Problem with Time Windows*

Keskin and Çatay extend EV routing to permit **partial recharging** instead of forcing every vehicle to fully recharge.

Their results show that partial charging can substantially improve routing solutions.

**Relevance:**

This directly supports our decision to allow:

\[
SOC_{after} < 100\%
\]

when a full recharge is unnecessary.

This creates more interesting algorithmic choices than a simple "always fill the battery" policy.

Note that this paper addresses vehicle routing with customer time windows, a different problem from ours, so it is cited only for its partial-charging result.

DOI: `10.1016/j.trc.2016.01.013`

---

## 25.5 Optimal Recharging Policies

### Sweda, Dolinskaya & Klabjan (2016/2017) — *Optimal Recharging Policies for Electric Vehicles*

This work examines how much an EV should recharge along a given path.

It emphasizes that recharging decisions are nontrivial because charging cost depends on battery state and station characteristics.

The authors present exact algorithms and heuristic approaches for deterministic and extended settings.

**Relevance:**

- supports charge quantity as an optimization decision,
- shows that route selection and charging policy can be studied separately or jointly,
- motivates our simplified linear charging model while leaving more realistic charging behavior as future work.

DOI: `10.1287/trsc.2015.0638`

---

## 25.6 Realistic Route Planning With Charging Stops

### Baum et al. (2019) — *Shortest Feasible Paths with Charging Stops for Battery Electric Vehicles*

Baum, Dibbelt, Gemsa, Wagner, and Zündorf study minimizing overall trip time for BEVs in road networks while explicitly accounting for required charging stops.

Their CHArge approach incorporates realistic charging-station models and demonstrates optimal routing on large road networks.

**Relevance:**

This is one of the closest papers to our project objective:

\[
\min(\text{drive time}+\text{charge time})
\]

It confirms that charging-aware shortest-path planning is a legitimate and practically important algorithm problem.

Our project is intentionally smaller and more educational:

- reduced graph,
- discretized SOC,
- linear charging,
- custom priority-queue algorithm,
- controlled complexity experiments.

DOI: `10.1287/trsc.2018.0889`

---

## 25.7 Broader EV Routing Literature

### Erdelić & Carić (2019) — *A Survey on the Electric Vehicle Routing Problem: Variants and Solution Approaches*

This survey reviews EV routing variants and solution methods.

It identifies major extensions involving:

- constrained shortest paths,
- charging,
- battery swapping,
- charging functions,
- heuristic and exact methods.

**Relevance:**

It places our work within the broader EV-routing landscape and shows the progression from energy-constrained routing toward richer charging-aware models.

DOI: `10.1155/2019/5075671`

---

## 25.8 Current Literature Direction

### Niyomphon et al. (2026) — *A Survey on the Electric Vehicle Routing Problem and Its Extensions: Variants and Algorithms*

This 2026 systematic review categorizes modern EVRP work by:

- objective functions,
- constraints,
- solution approaches.

The review reports continued emphasis on battery limitations, charging infrastructure, energy use, exact algorithms, metaheuristics, reinforcement learning, and hybrid approaches.

It also identifies continuing challenges around:

- charging realism,
- congestion and capacity,
- energy-consumption modeling,
- scalability,
- richer real-world constraints.

**Relevance:**

This helps position our project correctly.

We are not claiming to solve the entire modern EVRP.

Instead, we isolate a core algorithmic problem:

> joint route and charging optimization under battery-state constraints.

That is appropriate for an Algorithms Analysis and Design course.

DOI: `10.1080/21693277.2026.2728787`

> **TODO (verify before submission):** confirm that this DOI, the author list, and the journal details resolve to a real article. Remove this subsection and the matching reference if they do not.

---

## 25.9 Closest Prior Work on Expanded-State Search

> **TODO (verify before submission):** confirm the details of both entries below against the original papers.

### Eisner, Funke & Storandt (2011) — *Optimal Route Planning for Electric Vehicles in Large Networks*

**Summary:**

This AAAI paper treats battery-constrained EV routing on large road networks as a constrained shortest-path problem and solves it with Dijkstra-style search.

**Relevance:**

It is close to our approach of running label-setting search under a battery constraint. Our project adds explicit charging decisions at stations on a reduced graph.

### Strehler, Merting & Schwan (2017) — *Energy-Efficient Shortest Routes for Electric and Hybrid Vehicles*

**Summary:**

This Transportation Research Part B paper studies shortest routes for electric and hybrid vehicles with intermediate charging stops under battery constraints.

**Relevance:**

It is the closest formulation to our battery-expanded state graph. Our project is a simplified, reproducible instance of this family of models, with an emphasis on empirical validation and visualization.

---

# 26. Literature Survey Synthesis

The literature suggests the following evolution:

```text
Ordinary shortest path
        ↓
Energy-constrained shortest path
        ↓
Battery state becomes part of routing
        ↓
Charging stations included
        ↓
Route + charging decisions optimized jointly
        ↓
Partial / nonlinear charging
        ↓
Queues, traffic, availability, pricing
        ↓
Large-scale exact, heuristic, hybrid, and learning methods
```

Our project deliberately targets the middle of this progression:

```text
Battery state
+ charging station choice
+ partial charging
+ total-time minimization
+ algorithm analysis
```

This scope is complex enough to demonstrate meaningful algorithm design without becoming an operations-research or ML research project.

---

# 27. Project Contribution Relative to Literature

The project does **not** claim a novel state-of-the-art EV routing algorithm.

Its contribution is educational and engineering-focused:

1. formulate EV routing using a battery-expanded state graph,
2. implement the core algorithm ourselves,
3. compare against a simple greedy heuristic,
4. independently validate small cases using exact search,
5. quantify SOC-discretization tradeoffs,
6. demonstrate scaling behavior,
7. integrate real U.S. charging and road data,
8. present the result through an understandable interactive visualization,
9. measure the search-effort savings of an admissible A* heuristic.

This framing is both accurate and suitable for CIS 505.

---

# 28. Why Not Just Use NetworkX Dijkstra?

Calling:

```python
nx.shortest_path(...)
```

on the final problem would weaken the academic contribution.

Instead:

- OSMnx / NetworkX may be used to preprocess road distance/time,
- our code constructs and explores the battery-state search problem,
- our code maintains labels,
- our code performs priority-queue relaxation,
- our code reconstructs charging decisions,
- our code enforces battery feasibility.

NetworkX can optionally be used as a validation tool on simplified graphs, but not as the primary implementation of the course algorithm.

---

# 29. Repository Structure

Recommended structure:

```text
ev-route-optimizer/
│
├── README.md
├── requirements.txt
├── prd.md
│
├── contracts/
│   ├── canonical_graph.schema.json
│   └── algorithm_result.schema.json
│
├── optimizer/
│   ├── model.py                  # shared, frozen in Phase 1
│   ├── cli.py
│   ├── battery_state_optimizer.py  # label-setting and A* (heuristic flag)
│   ├── greedy_baseline.py        # owned by Shannon
│   ├── exact_solver.py
│   └── reconstruction.py
│
├── data_pipeline/
│   ├── chargers.py
│   ├── road_graph.py
│   ├── reduce_graph.py
│   └── export_graph.py
│
├── visualizer/
│   ├── app.py
│   ├── mock_results/
│   ├── map_view.py
│   ├── soc_chart.py
│   └── comparison.py
│
├── data/
│   ├── synthetic/
│   ├── raw/
│   └── processed/
│
├── experiments/
│   ├── correctness.py
│   ├── greedy_comparison.py
│   ├── soc_resolution.py
│   ├── scaling.py
│   └── run_all.py
│
├── tests/
│   ├── test_optimizer.py
│   ├── test_greedy.py
│   ├── test_exact.py
│   ├── test_constraints.py
│   └── test_contracts.py
│
├── results/
└── plots/
```

---

# 30. Team Work Split

The project will be divided **50/50** between Sankalp and Shannon.

The split is based on separate ownership areas rather than counting individual files or commits. Both members are expected to contribute comparably to implementation, testing, documentation, integration, and final submission quality.

The architecture is designed so that both members can work independently for most of the project.

---

## Sankalp — 50%

**Primary role: Optimization & Evaluation Lead**

Sankalp owns the core algorithmic and experimental side of the project.

### S1 — Mathematical Formulation

- finalize the optimization problem,
- define the battery-state representation,
- define drive and charge transitions,
- define the objective function,
- document assumptions and constraints,
- own the shared `optimizer/model.py` and the CLI, frozen in Phase 1.

### S2 — Core Battery-State Optimizer

Implement:

- battery-state graph search,
- priority queue,
- state relaxation,
- stale-state handling,
- conservative energy discretization (ceil/floor rules),
- lexicographic (time, stops, distance) labels and stop counting,
- battery feasibility checks,
- parent tracking,
- route and charging-plan reconstruction.

### S3 — Exact Validation Solver

Implement the small-instance exhaustive / backtracking solver used to verify optimality.

Responsibilities:

- enumerate feasible small-instance solutions,
- prune infeasible or dominated branches,
- compare exact cost with optimizer cost.

### S4 — Synthetic Correctness Test Suite

Own:

- hand-designed correctness cases,
- edge cases,
- adversarial cases,
- random small-instance generation,
- exact-solver agreement tests.

### S5 — Complexity Analysis

Own:

- state-space derivation,
- worst-case time complexity,
- worst-case space complexity,
- discussion of exact-search complexity,
- explanation of the SOC-discretization tradeoff.

### S6 — Core Experiment Framework

Implement the reusable experiment runner and metrics collection for:

- runtime,
- states expanded,
- memory/state count,
- objective value,
- feasibility,
- number of charging stops.

### S7 — Algorithm-Focused Experiments

Primary responsibility for:

- exact-solver correctness experiment,
- SOC-resolution experiment,
- candidate-station scaling experiment,
- battery-capacity experiment,
- A* vs label-setting experiment (Experiment J).

### S8 — Algorithm-Focused Report Content

Primary responsibility for drafting:

- formal problem statement,
- algorithm design,
- pseudocode,
- correctness proof and justification of the design technique,
- complexity analysis,
- algorithm experiment methodology and results.

### S9 — Integration Responsibility

Validate that Shannon's canonical real-data graph is accepted by the optimizer and generate the final result JSON files consumed by the visualizer.

### S10 — A* Variant

- implement the heuristic in Section 11.3 as a flag in the optimizer,
- prove admissibility and consistency for the report,
- write `test_astar.py`,
- run Experiment J.

---

### Sankalp effort target

Approximately **50% of total project effort**.

---

## Shannon — 50%

**Primary role: Data, Baseline & Visualization Lead**

Shannon owns the real-data, comparison, and presentation-layer side of the project.

### H1 — Charging-Station Data Pipeline

- obtain a reproducible AFDC charging-station snapshot,
- filter relevant public EV chargers,
- normalize station records,
- extract location and charging-rate information,
- document fallback behavior for incomplete fields.

### H2 — Road-Network Preprocessing

- obtain OpenStreetMap road-network data,
- map origin/destination/charger locations,
- calculate or preprocess travel distance and driving time,
- reduce the road network to the canonical optimizer graph.

### H3 — Canonical Graph Exporter

Implement generation of:

```text
canonical_graph.json
```

matching the frozen input contract.

### H4 — Greedy Baseline

Implement and document the frozen farthest-reachable-charger (FRC) heuristic from Section 12, emitting the standard `algorithm_result.json`.

Responsibilities:

- charger-selection rule,
- charge-amount rule,
- route reconstruction,
- baseline metrics,
- baseline tests,
- the implementation lives in `optimizer/greedy_baseline.py`; Shannon imports the frozen `optimizer/model.py` but edits no other file under `optimizer/`.

### H5 — Real-Data Test Scenarios

Prepare reproducible real-data instances covering:

- no-charge trip,
- one-stop trip,
- multi-stop trip,
- mixed charger speeds,
- sparse charger availability where practical.

### H6 — Visualization Application

Build the result visualizer that consumes:

```text
algorithm_result.json
```

and displays:

- route,
- origin/destination,
- available chargers,
- selected charging stops,
- trip metrics.

### H7 — SOC and Trip Visualization

Implement:

- SOC progression chart,
- drive/charge event timeline,
- charging-event display,
- trip summary panel.

### H8 — Greedy vs Optimized Comparison UI

Build a side-by-side comparison showing:

- total time,
- drive time,
- charging time,
- distance,
- number of charging stops.

### H9 — Comparison and Real-Data Experiments

Primary responsibility for:

- optimizer-vs-greedy experiment,
- initial-SOC experiment,
- charger-density experiment,
- charger-speed heterogeneity experiment,
- real-world demonstration runs.

Sankalp's optimizer will be invoked as a black-box executable through the shared CLI (Section 16.4) and the frozen contracts; Shannon does not need to modify its implementation.

### H10 — Data / Visualization Report Content

Primary responsibility for drafting:

- literature-survey integration related to real-world EV routing,
- data sources,
- preprocessing,
- implementation environment,
- greedy baseline,
- visualization,
- real-data experiments,
- data limitations and attribution.

### H11 — Integration Responsibility

Validate that the visualizer works with Sankalp's final optimizer result files and prepare the final real-data screenshots/demo outputs.

### Shannon effort target

Approximately **50% of total project effort**.

---

## Shared Final Responsibilities

The following are deliberately shared equally:

- final integration review,
- final result verification,
- report editing,
- contribution statement,
- README cleanup,
- source-code appendix preparation,
- final required project submission materials.

Each member separately submits the individual feedback form on other groups' videos (due December 7).

Neither teammate should be described as merely supporting the other. Each owns a substantial independent technical subsystem.

---

# 31. Why the Work Is Not Interdependent

Sankalp does **not** need Shannon's data pipeline to build or validate the optimizer.

Sankalp uses:

```text
data/synthetic/*.json
```

that conform to the frozen input schemas (graph and trip configuration).

Shannon does **not** need Sankalp's completed optimizer to build the visualizer.

Shannon uses:

```text
visualizer/mock_results/*.json
```

that conform to the frozen result schema.

Only the final integration step replaces:

```text
mock graph -> real graph
mock result -> real optimizer result
```

No major redesign should be necessary.

This is a hard architectural requirement. The only shared code is the frozen `optimizer/model.py`.

---

# 32. Suggested Git Workflow

```text
main
│
├── sankalp/optimizer
├── sankalp/experiments
│
├── shannon/data-pipeline
└── shannon/visualizer
```

Avoid both teammates editing the same core files. The one exception by design: Shannon owns `optimizer/greedy_baseline.py` and nothing else under `optimizer/`.

Shared files that should be frozen early:

```text
contracts/*
optimizer/model.py
README.md
requirements.txt
```

Integration happens through JSON artifacts rather than direct cross-module imports whenever practical.

---

# 33. Milestones

## Phase 1: Contract Freeze (Nov 3 to Nov 5)

Freeze:

- graph schema, trip-configuration schema, and result schema,
- `optimizer/model.py` and the shared CLI (Section 16.4),
- discretization and rounding rules (Section 7.1),
- lexicographic tie-breaking and stop counting,
- FRC baseline definition (Section 12),
- real-data region and candidate-charger corridor,
- experiment list and metrics.

## Phase 2: Core Implementation (Nov 6 to Nov 12)

Sankalp:

- label-setting optimizer,
- exact solver,
- synthetic tests T1 to T16.

Shannon:

- AFDC pipeline and OSM routing preprocessing,
- FRC greedy baseline,
- visualizer skeleton against mock results.

## Phase 3: Validation and First Integration (Nov 13 to Nov 17)

Sankalp:

- correctness validation against the exact solver (100+ instances),
- A* variant and `test_astar.py`,
- first scaling runs,
- start the correctness proof and complexity sections of the report.

Shannon:

- first real-data graph,
- static route visualization,
- start the data-pipeline and visualization sections of the report.

Report drafting begins now, in parallel with experiments.

## Phase 4: Full Integration (Nov 18 to Nov 20)

- real-data graphs run through all four algorithms,
- visualizer reads real `algorithm_result.json` files,
- end-to-end demo routes confirmed.

## Phase 5: Experiments and Feature Freeze (Nov 20 to Nov 24)

- run Experiments A through J,
- generate plots and CSVs,
- freeze features and experiment definitions by Nov 24,
- report at least 80% drafted by Nov 24.

## Phase 6: Report, Video, and Submission (Nov 25 to Dec 2)

Thanksgiving falls on Nov 26, and both members have other deliverables due around Dec 2. Only final assembly should remain in this phase:

- final report and source-code appendix,
- record the ~10-minute video by Dec 1, leaving a day of buffer,
- submit all deliverables by Dec 2.

## After Submission

- each member separately submits the individual feedback form on other groups' videos by Dec 7.

---

# 34. Success Criteria

## Required Success

Project is considered successful if:

- optimizer always obeys SOC constraints,
- optimizer matches exact solver on all selected tiny validation cases,
- A* variant returns labels identical to label-setting on all validation instances,
- greedy and optimized algorithms can be compared on identical inputs,
- time and space complexity are derived,
- scaling experiments are completed,
- at least three meaningful plots are produced,
- real-data graph can be optimized,
- route visualizer displays results,
- report includes all CIS 505 required sections.

---

## Strong Success

Additionally:

- greedy fails on at least one clear constructed example,
- SOC-resolution experiment reveals a measurable computation/quality tradeoff,
- real charging-station data produces an understandable route,
- visualization clearly shows battery changes,
- project can be explained in under 10 minutes without relying on implementation details.

---

## Stretch Success

Only after everything above is complete:

- animated vehicle movement,
- nonlinear charging curve,
- alternative charger-selection heuristic,
- landmark-based (ALT) heuristic for A*.

---

# 35. Failure Conditions

The project should be considered off-track if:

- most time is spent building the UI,
- optimizer is delegated to a library call,
- there is no independent optimality validation,
- only one demo route is tested,
- no runtime-scaling experiment exists,
- charging always forces 100% SOC,
- real API availability is required during the presentation,
- Sankalp and Shannon repeatedly block each other's work,
- new features are still being added during the final week.

---

# 36. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| OSM graph too large | High | Use reduced graph of origin/destination/chargers |
| Real charger data messy | Medium | Use cached filtered snapshot |
| Optimizer state explosion | High | Candidate filtering + configurable SOC resolution |
| Exact solver too slow | Low | Restrict to tiny instances |
| Greedy baseline accidentally strong | Low | Use multiple adversarial cases |
| Visualization takes too long | Medium | Static map first; animation optional |
| Charging rates incomplete | Medium | Define documented fallback rates |
| SOC discretization creates artifacts | Medium | Explicitly measure discretization error/tradeoff |
| Integration failure | High | Freeze JSON contracts immediately |
| Work dependency | High | Synthetic/mock interfaces from day one |
| Scope creep | High | Feature freeze by November 20–24 |
| Rounding makes plans infeasible or too conservative | Medium | Conservative rounding rule; measure error against a fine-resolution reference (Experiment C) |
| AFDC lacks charger power | Medium | Documented fallback rates by charger class; sensitivity run in Experiment H |
| All-pairs road routing too slow | High | Corridor-restricted candidates, omit out-of-range edges, cache routes |
| Thanksgiving week and other Dec 2 deadlines | High | Draft report from Nov 13, freeze experiments Nov 24, record video by Dec 1 |
| A* adds late scope | Low | Single heuristic flag in the same module; demote to stretch if Phase 3 slips |

---

# 37. Expected Report Structure

The report follows the CIS 505 handout's element list.

1. Title Page
2. Abstract
3. Application Background and Motivation
4. Literature Survey
5. Problem Specification and Parameters
6. Assumptions
7. Justification of the Algorithm-Design Technique
8. Algorithm Design and Pseudocode
9. Correctness Proof
10. A* Variant
11. Greedy Baseline (FRC)
12. Exact Validation Method
13. Illustrative Examples (hand-built instances with traced state transitions)
14. Time Complexity Analysis
15. Space Complexity Analysis
16. Implementation Design and Environment
17. Data Sources and Preprocessing
18. Experimental Methodology
19. Results and Observations on Execution Performance
20. Program Execution Screenshots
21. Real-World Demonstration
22. Visualization
23. Strengths
24. Weaknesses
25. Future Improvements
26. Team Roles and Contributions
27. Conclusion
28. References
29. Source Code Appendix

---

# 38. Future Work

Possible extensions:

1. nonlinear charging curves,
2. traffic-aware travel time,
3. elevation-aware energy consumption,
4. weather-aware consumption,
5. charger waiting queues,
6. live charger availability,
7. dynamic electricity prices,
8. stochastic travel conditions,
9. landmark-based (ALT) heuristics for A*,
10. multi-objective route planning,
11. multiple vehicles,
12. reinforcement-learning-assisted routing.

These should remain explicitly outside the required course implementation.

---

# 39. Literature References

1. **Artmeier, A., Haselmayr, J., Leucker, M., & Sachenbacher, M. (2010).**  
   *The Shortest Path Problem Revisited: Optimal Routing for Electric Vehicles.*  
   KI 2010: Advances in Artificial Intelligence, pp. 309–316.  
   DOI: https://doi.org/10.1007/978-3-642-16111-7_35

2. **Sachenbacher, M., Leucker, M., Artmeier, A., & Haselmayr, J. (2011).**  
   *Efficient Energy-Optimal Routing for Electric Vehicles.*  
   Proceedings of the AAAI Conference on Artificial Intelligence, 25(1), 1402–1407.  
   DOI: https://doi.org/10.1609/aaai.v25i1.7803

3. **Sweda, T. M., & Klabjan, D. (2012).**  
   *Finding Minimum-Cost Paths for Electric Vehicles.*  
   IEEE International Electric Vehicle Conference.  
   Source: https://ccitt.northwestern.edu/documents/2012_Sweda.Klabjan_IEVC2012_minimum.pdf

4. **Keskin, M., & Çatay, B. (2016).**  
   *Partial Recharge Strategies for the Electric Vehicle Routing Problem with Time Windows.*  
   Transportation Research Part C: Emerging Technologies, 65, 111–127.  
   DOI: https://doi.org/10.1016/j.trc.2016.01.013

5. **Sweda, T. M., Dolinskaya, I. S., & Klabjan, D. (2017).**  
   *Optimal Recharging Policies for Electric Vehicles.*  
   Transportation Science, 51(2), 457–479.  
   DOI: https://doi.org/10.1287/trsc.2015.0638

6. **Baum, M., Dibbelt, J., Gemsa, A., Wagner, D., & Zündorf, T. (2019).**  
   *Shortest Feasible Paths with Charging Stops for Battery Electric Vehicles.*  
   Transportation Science, 53(6), 1627–1655.  
   DOI: https://doi.org/10.1287/trsc.2018.0889

7. **Erdelić, T., & Carić, T. (2019).**  
   *A Survey on the Electric Vehicle Routing Problem: Variants and Solution Approaches.*  
   Journal of Advanced Transportation.  
   DOI: https://doi.org/10.1155/2019/5075671

8. **Niyomphon, K., Nakkiew, W., Sandhu, S., Nakkiew, W., & Woschank, M. (2026).**  
   *A Survey on the Electric Vehicle Routing Problem and Its Extensions: Variants and Algorithms.*  
   Production & Manufacturing Research, 14(1).  
   DOI: https://doi.org/10.1080/21693277.2026.2728787 **[verify before submission]**

9. Eisner, J., Funke, S., & Storandt, S. (2011). Optimal Route Planning for Electric Vehicles in Large Networks. *Proceedings of the AAAI Conference on Artificial Intelligence*. **[verify details before submission]**

10. Strehler, M., Merting, S., & Schwan, C. (2017). Energy-Efficient Shortest Routes for Electric and Hybrid Vehicles. *Transportation Research Part B: Methodological*. **[verify details before submission]**

---

# 40. Public Data / Tool References

- U.S. Department of Energy Alternative Fuels Data Center:  
  https://afdc.energy.gov/

- AFDC data downloads:  
  https://afdc.energy.gov/data_download

- OpenStreetMap:  
  https://www.openstreetmap.org/

- OSMnx documentation:  
  https://osmnx.readthedocs.io/

- NetworkX documentation:  
  https://networkx.org/documentation/stable/

- Streamlit:  
  https://streamlit.io/

---

# 41. Final Project Statement

> **Design, implement, analyze, and visualize an EV charging-aware route optimizer that minimizes total driving and charging time subject to battery-state constraints. The algorithm will be implemented over a battery-expanded state graph, compared with a greedy charging heuristic and an A* variant, validated against exhaustive search on small instances, evaluated through complexity and scaling experiments, and demonstrated using real U.S. charging-infrastructure and open road-network data.**

---

# 42. Final Scope Decision

For the Fall 2026 CIS 505 version, the mandatory feature set is:

```text
[REQUIRED]
Battery-state optimizer
A* variant (same module, heuristic flag)
Greedy baseline (FRC)
Exact small-instance validator
Synthetic correctness tests
Complexity analysis
Scaling experiments
Real charger/road data
Static interactive map
SOC visualization
Greedy-vs-optimized comparison
Report
Required presentation/video deliverable due December 2

[OPTIONAL]
Route animation
ALT landmark heuristic
Nonlinear charging
Additional heuristic
```

If optional work threatens the required deliverables, it will be dropped immediately.

The priority order is:

```text
Correctness
    >
Algorithm analysis
    >
Experiments
    >
Real-data integration
    >
Visualization
    >
Optional polish
```
