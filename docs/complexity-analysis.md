# Complexity Analysis

ChargePlan searches a battery-expanded graph. This note derives the state-space size and the time and space bounds from the implementations in `optimizer/battery_state_optimizer.py` and `optimizer/exact_solver.py`, then checks those bounds against Section 14 of the PRD. The farthest-reachable-charger procedure in Section 12 is included as a specified bound. `optimizer/greedy_baseline.py` still raises `NotImplementedError`, so that bound is not measured in code.

Notation:

- \(M = |V|\), the number of origin, destination, and charger nodes.
- \(|E|\) is the number of directed road edges, and \(\Delta\) is the maximum out-degree.
- \(B' = \lfloor B / \Delta E \rfloor\), from `discretize`.
- \(S = B' + 1\), the number of discrete SOC levels \(\{0, \ldots, B'\}\).

## State space

A search state is \((v, b, f)\):

- \(v\) is a graph node,
- \(b \in \{0, \ldots, B'\}\) is the battery in units of \(\Delta E\),
- \(f \in \{0, 1\}\) is 0 on arrival by driving and 1 after a charge step at \(v\).

The flag exists so the first charge step of a visit increments the stop count and later steps at the same visit do not. `solve` stores states as `(location, battery, flag)` tuples.

There are \(M\) choices for \(v\), \(S\) choices for \(b\), and 2 choices for \(f\), so the expanded graph has at most

\[
2MS = O(MS)
\]

states. Nodes that cannot charge never produce \(f = 1\), so the reachable set is smaller when only some nodes are chargers. The \(O(MS)\) form in Section 14 is the right bound; the factor of 2 is the exact count in the code.

The start state is \((\mathrm{origin}, b_0', 0)\). If \(b_0' < r'\), both solvers return `INFEASIBLE` before search and report zero expanded states. The goal is the first settled destination state. The destination is not charged.

On the Detroit–Chicago sample, \(M = 4\), \(\Delta E = 1\) kWh, and \(B = 70\) kWh, so \(B' = 70\), \(S = 71\), and the state ceiling is \(2 \cdot 4 \cdot 71 = 568\). Label-setting stores 148 states and expands 127. The ceiling is real, and a typical query stays well under it.

## Label-setting time

`battery_state_optimizer.solve` is Dijkstra on the expanded graph, implemented with `heapq`. Every drive and charge cost is non-negative: drive time comes from `minutes_to_ms`, charge time from `charge_step_minutes`, stop increments are 0 or 1, and distance is non-negative. A state is expanded only when the popped label equals `best[state]`. With non-negative costs, that happens at most once per state. Stale heap entries are skipped and are not counted in `states_expanded`.

From a settled state the algorithm tries:

- one drive successor per outgoing road whose arrival SOC is at least \(r'\),
- one charge successor, \(b \mapsto b+1\), when the node has a charging rate and \(b+1 \le B'\).

Drive edges in the expanded graph number at most \(2S|E|\): each road edge can be crossed from each feasible battery level and each flag. Charge edges number at most \(2MS\), and only at chargers. Section 14's dense-graph count matches this. If every node has \(\Theta(M)\) outgoing roads, \(|E| = \Theta(M^2)\) and the drive edges are \(O(M^2 S)\). The code does not build that dense graph. It iterates `outgoing[location]`, so a corridor graph with maximum out-degree \(\Delta\) produces \(O(\Delta MS)\) drive relaxations. The sparse bound is the one the implementation pays.

`heapq` has no decrease-key. A stricter label pushes a new entry and leaves the old one in the heap. Each expanded state relaxes each outgoing edge at most once, so the number of pushes is \(O(S|E| + MS)\). Every entry is popped. A binary-heap operation on a heap of that size costs \(O(\log(S|E| + MS))\).

Dense reduced graph, the Section 14 setting:

\[
O(M^2 S \log(MS))
\]

Sparse graph, the bound the loop actually implements:

\[
O((\Delta + 1) MS \log(MS))
\]

Precomputing the adjacency lists and, for A*, the heuristic is \(O(|E| + M)\) and does not change either bound. Reconstructing the parent chain is linear in the path, which is at most the number of settled states.

Section 14's \(O(M^2 S \log(MS))\) claim holds for this code on a dense graph. It is a worst-case ceiling. Early exit at the destination, infeasible drives, and sparse corridors all reduce the work. The claim should not be read as the cost of every query.

## Label-setting space

`best` and `parent` hold one entry per reached state, so those two dictionaries are \(O(MS)\). That is the quantity Section 14 calls "space for state labels and parents," and `peak_states_stored` reports `len(best)` only.

The heap is larger. Stale entries stay until they are popped, and there can be one of them per relaxation, \(O(S|E| + MS)\) entries. On a dense graph that is \(O(M^2 S)\), which dominates the label table. Total memory for the search is therefore

\[
O(S|E| + MS)
\]

plus the input graph, which is \(O(M + |E|)\) and \(O(M^2)\) when the reduced graph is dense. Section 14 is right about the label table and about graph storage. It understates search memory by leaving the stale heap out.

## How SOC discretization changes runtime, memory, and accuracy

`discretize` and `edge_energy_steps` implement Section 7.1:

\[
B' = \left\lfloor \frac{B}{\Delta E} \right\rfloor, \quad
b_0' = \left\lfloor \frac{b_0}{\Delta E} \right\rfloor, \quad
r' = \left\lceil \frac{r}{\Delta E} \right\rceil, \quad
\bar e_{uv} = \left\lceil \frac{c \cdot d_{uv}}{\Delta E} \right\rceil
\]

\(S = \lfloor B / \Delta E \rfloor + 1\). The input length of the battery parameters is logarithmic in \(B / \Delta E\), while the search is linear in \(S\). The algorithm is pseudo-polynomial in the battery data. Halving \(\Delta E\) roughly doubles \(S\), and both the dense and sparse time bounds scale linearly with \(S\). Memory scales the same way, because both the label table and the heap are proportional to \(S\). A full charge is also \(O(S)\) one-step transitions; those transitions are already inside the \(O(MS)\) charge-edge term, so they do not add a second factor of \(1/\Delta E\).

The same rounding is what makes a fine step more accurate and a coarse step cheaper:

- Capacity and initial energy round down, so the model can use less energy than the vehicle has.
- The reserve rounds up, so the model protects a slightly larger reserve.
- Each edge rounds energy up by less than one step. A long route can accumulate almost one extra \(\Delta E\) per edge.
- Charging adds exactly one step at a time. `charge_step_minutes` prices that step as \((\Delta E / p_v) \cdot 60\) minutes. A continuous plan could add a fraction of a step; this one cannot.

A plan the discrete model accepts stays at or above the real reserve: the search spends at least the true energy and starts from at most the true energy. The converse is false. A trip that is feasible with continuous energy can be `INFEASIBLE` after the ceilings, and a feasible discrete plan can charge longer than the continuous optimum because it bought whole steps. Reported SOC values are multiples of \(\Delta E\) taken from the discrete counters, so they sit at or below the energy the rounding started from.

Optimality is optimality inside this discrete model. Label-setting, A*, and the exact solver all use the same `discretize` and `edge_energy_steps`, so they agree with each other while sharing the same gap to continuous charging. Measuring that gap is Experiment C, which is outside this note.

Search time is stored as integer milliseconds, 60,000 ms per minute, half up in `minutes_to_ms`. That rounding is for lexicographic comparison. It is independent of \(\Delta E\) and moves a timestamp by less than half a millisecond.

## Exact solver

`exact_solver.solve` does not run Dijkstra. It is a depth-first enumeration. At a charger with flag 0 it chooses the whole charge in one decision, `added` from 0 through \(B' - b\), then tries every legal outgoing road. A positive charge costs `added` step-times and one stop. There is no `heapq`, and the module does not import the label-setting search.

Four cuts drop a branch:

- arrival below \(r'\), or a charge past \(B'\),
- a label that is already no better than a finished plan,
- a stored record for the same node and flag with at least as much battery and a lexicographically no-worse label.

Labels are totally ordered, so each triple \((\mathrm{node}, \mathrm{flag}, \mathrm{battery})\) keeps at most one label. The record table therefore holds at most \(2MS\) records. That is a space bound, not a time bound. A record can be expanded and later replaced by a better label, which expands it again. Dominance removes branches; it does not make the tree polynomial.

Without those cuts, a charger branches up to \(S\) ways and then up to \(\Delta\) ways. Over a sequence of \(k\) charger visits the charge-amount tuples alone are \(S^k\). The number of ordered charger sequences of distinct nodes is already exponential in the number of chargers, up to

\[
\sum_{k=0}^{C} P(C, k) \le \sum_{k=0}^{C} C^k
\]

for \(C \le M\) chargers, and each sequence multiplies by up to \(S^k\) charge choices. The search also allows a revisit until a stored record dominates it, so the simple-sequence count is a lower bound on the worst-case family rather than an upper bound on the tree. Section 14's statement, "exponential in the number of chargers and charge choices," matches this structure. A tighter closed form is not available from the pruning rules, and the code does not impose one.

Nothing in `exact_solver.py` rejects a large graph. The limit is practical. The 30-graph exact check uses 2–4 nodes and capacity 4–8. The 100-graph check uses 3–8 nodes, and the 6-charger cases keep capacity at 3 so \(S\) stays small. On the 4-node Detroit–Chicago sample the same solver is still cheap: it expands 1,477 states and stores 127 records, against 127 expansions for label-setting, and it returns the same 322.5-minute, two-stop plan. Add chargers and raise \(S\) together and the \(S^k\) branching leaves the validation regime. That is why the solver is a correctness check for tiny instances, not the router.

## A* heuristic

A* is the same search. `use_heuristic=True` changes only the heap key, from the label \((\mathrm{time\_ms}, \mathrm{stops}, \mathrm{distance})\) to

\[
(\mathrm{time\_ms} + h(v),\; \mathrm{stops},\; \mathrm{distance})
\]

`_heuristic_ms` sets

\[
h(v) = \left\lfloor \mathrm{haversine}(v, \mathrm{destination}) \cdot \frac{t^\*}{d^\*} \right\rfloor
\]

milliseconds, where \((d^\*, t^\*)\) is the road with the greatest speed. Speed is compared by cross-multiplying `Decimal` values of miles and the already-rounded integer edge time, so the chosen edge is the one that maximizes miles per millisecond. \(h(\mathrm{destination}) = 0\) because the haversine distance from a point to itself is 0.

### Admissibility

Assume every road is at least the straight-line distance between its endpoints, which `load_graph` enforces up to the frozen \(10^{-6}\) mile tolerance, and assume every positive-length edge has positive drive time. Great-circle distance satisfies the triangle inequality, so any path from \(v\) to the destination is at least \(\mathrm{haversine}(v, \mathrm{destination})\) miles. No edge is faster than \(d^\*/t^\*\), and \(t^\*\) is the integer millisecond cost the search will actually add. The continuous lower bound is therefore at most the integer time of every concrete path. Flooring preserves the inequality: if \(a \le c + b\) and \(c\) is a non-negative integer, then \(\lfloor a \rfloor \le c + \lfloor b \rfloor\). So \(h(v)\) is at most the time label of every feasible path from \(v\) to the destination.

Two implemented exceptions keep the bound honest:

- If any edge has positive length and zero drive time, speed is unbounded. `_heuristic_ms` returns 0 for every node. A positive \(h\) would be inadmissible on that edge.
- The loader allows a road to be up to \(10^{-6}\) miles shorter than the haversine distance. At ordinary road speeds that slack is a fraction of a millisecond and disappears when \(h\) is floored. If every edge were extremely slow, slower than a few miles per hour, the slack could reach a millisecond and the floor would not hide it. The admissibility proof is for roads that are truly at least the straight-line length. The search itself does not re-check the inequality; it trusts the graph.

### Consistency

For a drive \(u \to v\) with integer time \(t_{uv}\),

\[
h(u) \le t_{uv} + h(v)
\]

The continuous version follows from the triangle inequality, \(d_{uv}\) at least the straight-line length, and \(d^\*/t^\*\) at least the speed of this edge. Flooring preserves it by the same integer argument as above. A charge step stays at the same node, so \(h\) does not change, and the charge time is non-negative. The heuristic triple \((h(v), 0, 0)\) is therefore consistent for the lexicographic cost.

Consistency plus \(h(\mathrm{destination}) = 0\) is why the first popped destination still has the best label. Its priority equals its label. Any other destination state still in the heap has a priority at least as large, and consistency says that priority is at most the priority of any unfinished extension. Stops and distance are not modified by \(h\), so the lexicographic order among destination labels is the same order label-setting uses. A* and label-setting return the same \((\mathrm{time}, \mathrm{stops}, \mathrm{distance})\).

### What the heuristic does not change

The worst-case time and space bounds are unchanged. A consistent heuristic only reorders expansions. If every node is colocated with the destination, or if a zero-time positive-length edge forces \(h \equiv 0\), the heap key equals the label and A* expands the same states as label-setting.

\(h\) depends on the node only. It lower-bounds driving time and says nothing about charging time, stops, or the battery. When charging is the expensive part of the trip, the heuristic barely separates states. The Detroit–Chicago sample is one such case: both modes expand 127 states and store 148. The westward-chain unit test is the opposite shape, a fast chain of edges pointing away from the destination, and there A* expands strictly fewer states. Section 14 is right that the savings are empirical. Experiment J is where that comparison belongs; this note only records the bound and these two checked instances.

Flooring also weakens \(h\). The floored value is still admissible and consistent, and it can only expand more states than the unfloored real-valued heuristic would have.

## Check against Section 14

| Section 14 claim | What the code does |
| --- | --- |
| \(O(MS)\) states, including the charging flag | At most \(2MS\) tuples `(location, battery, flag)`. The big-O statement is correct. |
| \(O(M^2 S)\) drive transitions on a dense graph | Each of \(O(MS)\) states scans every outgoing road. Dense out-degree \(O(M)\) gives \(O(M^2 S)\). Sparse graphs cost \(O(\Delta MS)\) because the loop follows the adjacency list. |
| \(O(MS)\) charge transitions | One successor \(b \mapsto b+1\) per charger state with room left. \(O(MS)\). |
| Time \(O(M^2 S \log(MS))\) with a binary heap | Matches the dense case, including the lazy heap with stale entries. The sparse case is \(O(\Delta MS \log(MS))\). |
| Label and parent space \(O(MS)\) | `best` and `parent` are \(O(MS)\). `peak_states_stored` counts only `best`. |
| Graph space \(O(M^2)\) when dense | The input is linear in the number of nodes and edges, and quadratic in \(M\) when every pair is linked. |
| A* has the same worst-case bound | Same loop, same settle-once rule. Only the heap key changes. |
| Runtime is pseudo-polynomial in \(B/\Delta E\) | \(S = \lfloor B/\Delta E \rfloor + 1\), and time and memory are linear in \(S\). |
| Halving \(\Delta E\) roughly doubles the runtime | It roughly doubles \(S\), and the bounds are linear in \(S\). |
| FRC is \(O(M^2)\) | The Section 12 rule makes at most \(M\) moves, because each move is strictly closer to the destination, and each move scans at most \(M\) neighbors. The function that would run it still raises `NotImplementedError`. |
| Exact search is exponential in chargers and charge choices | Depth-first search branches by up to \(S\) charge amounts and then by out-degree. Dominance caps stored records at \(O(MS)\) and does not cap the tree. |

The one claim to sharpen in the report is space. Section 14's \(O(MS)\) figure is the label table. The implemented priority queue keeps stale labels, so peak memory on a dense graph is \(O(M^2 S)\).

## What this leaves for later measurements

Section 14 asks the report to separate four numbers: the worst-case bound, the observed runtime, the observed expansion count, and the behavior of the sparse graph. This note supplies the bounds and the expansion counts on the frozen sample. Runtime on that sample is milliseconds and depends on the machine, so it is not recorded here. Observed scaling with \(\Delta E\), with \(M\), and with A* against label-setting is Experiments C, D, and J.
