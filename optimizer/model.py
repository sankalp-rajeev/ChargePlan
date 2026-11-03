"""Shared graph, trip-config, discretization, and result I/O.

This is the only shared module. Sankalp owns it. It is frozen with the
Phase 1 contracts: later changes need both teammates and a README version bump.

Discretization (PRD Section 7.1), once implemented:

- capacity ``B`` and initial energy ``b0`` round down to multiples of ``ΔE``,
- reserve ``r`` rounds up,
- edge energy ``c * distance_miles`` rounds up at the call site,
- a plan accepted after that rounding stays at or above the reserve in real kWh.

TODO: implement the loaders, rounding, and result writer (S1). No search here.
"""


def load_graph(path: str) -> dict:
    """Load a ``canonical_graph.json`` file.

    TODO: read the file and validate it against ``contracts/canonical_graph.schema.json``.
    """
    raise NotImplementedError


def load_config(path: str) -> dict:
    """Load a ``trip_config.json`` file.

    TODO: read the file and validate it against ``contracts/trip_config.schema.json``.
    """
    raise NotImplementedError


def discretize(config: dict) -> tuple[int, int, int, float]:
    """Return ``(B', b0', r', ΔE)`` in units of the SOC step.

    ``B'`` and ``b0'`` use floor. ``r'`` uses ceil. ``ΔE`` is ``soc_step_kwh``.

    TODO: implement Section 7.1 rounding (S1).
    """
    raise NotImplementedError


def edge_energy_steps(distance_miles: float, config: dict) -> int:
    """Return edge energy in ``ΔE`` steps, rounded up.

    TODO: implement ``ceil(c * distance_miles / ΔE)`` (S1).
    """
    raise NotImplementedError


def write_result(result: dict, path: str) -> None:
    """Write an ``algorithm_result.json`` file.

    TODO: validate against ``contracts/algorithm_result.schema.json`` and write JSON.
    """
    raise NotImplementedError
