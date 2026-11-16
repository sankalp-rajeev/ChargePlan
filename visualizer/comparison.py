"""Side-by-side greedy versus optimized comparison.

Owned by Shannon (H8, PRD 24.5). Compares total time, driving time, charging
time, charging stops, and distance for two precomputed ``algorithm_result.json``
dicts: the farthest-reachable-charger baseline (``greedy_frc``) and an optimizer
result (``label_setting``, ``astar``, or ``exact``). Nothing is recomputed here.

The greedy baseline can return INFEASIBLE on a trip the optimizer solves (T16).
An infeasible result reports no plan, so its metric cells show ``n/a`` and the
verdict says so instead of comparing zeros.
"""

from __future__ import annotations

from visualizer.data_io import VisualizerInputError
from visualizer.soc_chart import UNAVAILABLE, _duration

GREEDY_ALGORITHM = "greedy_frc"
TIE_TOLERANCE_MINUTES = 0.05

# (label, result field, kind). The row order is the PRD 24.5 table order.
METRICS = [
    ("Total time", "total_time_minutes", "minutes"),
    ("Driving time", "driving_time_minutes", "minutes"),
    ("Charging time", "charging_time_minutes", "minutes"),
    ("Stops", "number_of_charging_stops", "count"),
    ("Distance", "total_distance_miles", "miles"),
]


def render_comparison(greedy: dict, optimized: dict) -> dict:
    """Return the metrics shown in the comparison panel.

    The result has ``rows`` (one dict per PRD 24.5 metric with display strings
    ``greedy``, ``optimized``, ``difference`` and raw ``greedy_value``,
    ``optimized_value``, ``delta`` where ``delta`` is optimized minus greedy and
    is ``None`` unless both plans are feasible), the two statuses, a one-line
    ``verdict``, and a ``severity`` of ``success``, ``info``, or ``warning`` for
    how to show it.

    Raises ``VisualizerInputError`` when ``greedy`` is not a ``greedy_frc`` result
    or ``optimized`` is one.
    """
    if greedy["algorithm"] != GREEDY_ALGORITHM:
        raise VisualizerInputError(
            f"the greedy result must be a {GREEDY_ALGORITHM} result, not {greedy['algorithm']}"
        )
    if optimized["algorithm"] == GREEDY_ALGORITHM:
        raise VisualizerInputError(
            f"the optimized result must not be a {GREEDY_ALGORITHM} result; "
            "choose a label_setting, astar, or exact result"
        )

    both_feasible = greedy["status"] == "FEASIBLE" and optimized["status"] == "FEASIBLE"
    rows = [
        row(label, field, kind, greedy, optimized, both_feasible) for label, field, kind in METRICS
    ]
    verdict, severity = adjudication(greedy, optimized)
    return {
        "rows": rows,
        "greedy_algorithm": greedy["algorithm"],
        "optimized_algorithm": optimized["algorithm"],
        "greedy_status": greedy["status"],
        "optimized_status": optimized["status"],
        "verdict": verdict,
        "severity": severity,
    }


def comparison_table(comparison: dict) -> list[dict[str, str]]:
    """Return the PRD 24.5 table as display rows keyed by column title."""
    return [
        {
            "Metric": row["metric"],
            "Greedy (FRC)": row["greedy"],
            f"Optimized ({comparison['optimized_algorithm']})": row["optimized"],
            "Difference": row["difference"],
        }
        for row in comparison["rows"]
    ]


def row(
    label: str, field: str, kind: str, greedy: dict, optimized: dict, both_feasible: bool
) -> dict:
    greedy_value = value(greedy, field)
    optimized_value = value(optimized, field)
    delta = optimized_value - greedy_value if both_feasible else None
    return {
        "metric": label,
        "greedy": format(greedy_value, kind),
        "optimized": format(optimized_value, kind),
        "difference": UNAVAILABLE if delta is None else format_delta(delta, greedy_value, kind),
        "greedy_value": greedy_value,
        "optimized_value": optimized_value,
        "delta": delta,
    }


def value(result: dict, field: str) -> float | None:
    return result[field] if result["status"] == "FEASIBLE" else None


def adjudication(greedy: dict, optimized: dict) -> tuple[str, str]:
    greedy_ok = greedy["status"] == "FEASIBLE"
    optimized_ok = optimized["status"] == "FEASIBLE"
    if not greedy_ok and not optimized_ok:
        return "Neither the greedy baseline nor the optimizer found a feasible plan.", "info"
    if not greedy_ok:
        return (
            "The greedy baseline found no feasible plan for this trip. "
            f"The optimizer completes it in {_duration(optimized['total_time_minutes'])} "
            f"with {optimized['number_of_charging_stops']} charging stop(s).",
            "success",
        )
    if not optimized_ok:
        return (
            "The greedy baseline found a plan but the optimizer reports none. "
            "The optimizer should never do worse than the baseline; check the input files.",
            "warning",
        )

    saved = greedy["total_time_minutes"] - optimized["total_time_minutes"]
    if abs(saved) <= TIE_TOLERANCE_MINUTES:
        return "The optimized plan takes the same total time as the greedy baseline.", "info"
    if saved < 0:
        return (
            f"The optimized plan is {_duration(-saved)} slower than the greedy baseline. "
            "The optimizer should never do worse; check that both results are for the same trip.",
            "warning",
        )
    percent = 100.0 * saved / greedy["total_time_minutes"]
    return (
        f"The optimized plan saves {_duration(saved)} ({percent:.1f}%) "
        "of total time over the greedy baseline.",
        "success",
    )


def format(value: float | None, kind: str) -> str:
    if value is None:
        return UNAVAILABLE
    if kind == "minutes":
        return _duration(value)
    if kind == "miles":
        return f"{value:.1f} mi"
    return str(int(value))


def format_delta(delta: float, baseline: float, kind: str) -> str:
    if kind == "count":
        return f"{int(delta):+d}"
    unit = "min" if kind == "minutes" else "mi"
    if round(delta, 1) == 0:
        delta = 0.0
    text = f"{delta:+.1f} {unit}"
    if kind == "minutes" and baseline > 0:
        text += f" ({100.0 * delta / baseline:+.1f}%)"
    return text
