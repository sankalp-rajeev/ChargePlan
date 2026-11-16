"""SOC chart, drive/charge timeline, and trip summary.

Owned by Shannon (H7). Driving decreases SOC. Charging increases it.
The timeline lists each event with duration and SOC before and after, and the
summary reports the trip totals and solver metrics (PRD 24.2 to 24.4).

Every function reads an ``algorithm_result.json`` dict. The result stores SOC in
kWh and does not store the battery size. Pass the matching ``trip_config.json``
dict as ``trip`` to also show percent of capacity, the capacity line, and the
reserve line. Without it everything is in kWh.
"""

from __future__ import annotations

from dataclasses import dataclass

DRIVE_COLOR = "#1565c0"
CHARGE_COLOR = "#ef6c00"
RESERVE_COLOR = "#c62828"
CAPACITY_COLOR = "#757575"

X_AXES = {
    "distance": ("distance_mi", "Trip distance (miles)"),
    "time": ("time_min", "Trip time (minutes)"),
}
UNAVAILABLE = "n/a"


@dataclass(frozen=True)
class Segment:
    """One drive or charge event placed on the running distance and time axes."""

    index: int
    kind: str
    node_from: str
    node_to: str
    distance_start: float
    distance_end: float
    time_start: float
    time_end: float
    soc_before_kwh: float
    soc_after_kwh: float
    event: dict


def build_segments(result: dict) -> list[Segment]:
    """Place each event on the cumulative distance and time of the trip.

    A drive adds its distance and duration. A charge adds only its duration, so it
    is a vertical rise on the distance axis. Events are taken in file order.
    """
    segments: list[Segment] = []
    distance = 0.0
    elapsed = 0.0
    for index, event in enumerate(result["events"]):
        if event["type"] == "drive":
            start, end = event["from"], event["to"]
            new_distance = distance + event["distance_miles"]
        else:
            start = end = event["station"]
            new_distance = distance
        new_elapsed = elapsed + event["duration_minutes"]
        segments.append(
            Segment(
                index=index,
                kind=event["type"],
                node_from=start,
                node_to=end,
                distance_start=distance,
                distance_end=new_distance,
                time_start=elapsed,
                time_end=new_elapsed,
                soc_before_kwh=event["soc_before_kwh"],
                soc_after_kwh=event["soc_after_kwh"],
                event=event,
            )
        )
        distance, elapsed = new_distance, new_elapsed
    return segments


def soc_series(result: dict, trip: dict | None = None) -> list[dict]:
    """Return the chart rows: two points (start, end) for every event.

    Each row has ``event`` (index), ``kind`` (``Drive`` or ``Charge``), ``point``
    (0 start, 1 end), ``distance_mi``, ``time_min``, ``soc_kwh``, ``soc_pct``
    (``None`` without a trip config), ``soc`` (the plotted value), and ``label``.
    """
    capacity = _capacity(trip)
    rows: list[dict] = []
    for seg in build_segments(result):
        label = _segment_label(seg)
        ends = (
            (0, seg.distance_start, seg.time_start, seg.soc_before_kwh),
            (1, seg.distance_end, seg.time_end, seg.soc_after_kwh),
        )
        for point, distance, elapsed, soc in ends:
            pct = None if capacity is None else 100.0 * soc / capacity
            rows.append(
                {
                    "event": seg.index,
                    "kind": seg.kind.capitalize(),
                    "point": point,
                    "distance_mi": round(distance, 4),
                    "time_min": round(elapsed, 4),
                    "soc_kwh": soc,
                    "soc_pct": pct,
                    "soc": soc if pct is None else pct,
                    "label": label,
                }
            )
    return rows


def render_soc_chart(result: dict, x_axis: str = "distance", trip: dict | None = None):
    """Return an Altair chart of SOC versus trip distance or trip time.

    ``x_axis`` is ``"distance"`` or ``"time"``. Driving segments are blue and fall;
    charging segments are orange and rise. With ``trip`` the y axis is percent of
    battery capacity and dashed lines mark full capacity and the reserve.
    An infeasible result has no events and gives an empty chart with that title.
    """
    import altair as alt

    if x_axis not in X_AXES:
        raise ValueError(f"x_axis must be one of {sorted(X_AXES)}, not {x_axis!r}")
    x_field, x_title = X_AXES[x_axis]
    capacity = _capacity(trip)
    y_title = "State of charge (kWh)" if capacity is None else "State of charge (% of battery)"

    rows = soc_series(result, trip)
    x = alt.X(f"{x_field}:Q", title=x_title)
    y = alt.Y("soc:Q", title=y_title, scale=alt.Scale(domainMin=0))
    color = alt.Color(
        "kind:N",
        title="Event",
        scale=alt.Scale(domain=["Drive", "Charge"], range=[DRIVE_COLOR, CHARGE_COLOR]),
    )
    tooltip = [
        alt.Tooltip("label:N", title="Event"),
        alt.Tooltip("distance_mi:Q", title="Distance (mi)", format=".1f"),
        alt.Tooltip("time_min:Q", title="Time (min)", format=".1f"),
        alt.Tooltip("soc_kwh:Q", title="SOC (kWh)", format="~g"),
    ]
    if capacity is not None:
        tooltip.append(alt.Tooltip("soc_pct:Q", title="SOC (%)", format=".1f"))

    data = alt.Data(values=rows)
    base = alt.Chart(data)
    lines = base.mark_line(strokeWidth=3).encode(
        x=x, y=y, color=color, detail="event:N", order="point:Q", tooltip=tooltip
    )
    points = base.mark_point(filled=True, size=45).encode(x=x, y=y, color=color, tooltip=tooltip)
    layers = [lines, points]

    if capacity is not None:
        layers.append(_threshold_rule(100.0, "Battery capacity", CAPACITY_COLOR))
        reserve = trip.get("reserve_kwh") if trip else None
        if reserve:
            layers.append(_threshold_rule(100.0 * reserve / capacity, "Reserve", RESERVE_COLOR))

    title = "SOC along the trip" if rows else "No plan to chart (no events in this result)"
    return alt.layer(*layers).properties(title=title, height=320)


def render_timeline(result: dict, trip: dict | None = None) -> list[str]:
    """Return one display line per drive or charge event, in trip order.

    Example lines::

        1. Drive origin -> station_1: 55.2 mi, 51.3 min, SOC 54 -> 37 kWh
        2. Charge at station_1: 4.8 min, SOC 37 -> 49 kWh (+12 kWh)

    An infeasible result has no events and returns an empty list.
    """
    capacity = _capacity(trip)
    lines: list[str] = []
    for number, seg in enumerate(build_segments(result), start=1):
        event = seg.event
        soc = _soc_range(event["soc_before_kwh"], event["soc_after_kwh"], capacity)
        if seg.kind == "drive":
            lines.append(
                f"{number}. Drive {seg.node_from} -> {seg.node_to}: "
                f"{event['distance_miles']:.1f} mi, {event['duration_minutes']:.1f} min, {soc}"
            )
        else:
            gained = event["soc_after_kwh"] - event["soc_before_kwh"]
            lines.append(
                f"{number}. Charge at {seg.node_from}: "
                f"{event['duration_minutes']:.1f} min, {soc} ({gained:+g} kWh)"
            )
    return lines


def render_timeline_diagram(result: dict, trip: dict | None = None) -> str:
    """Return the vertical origin-to-destination diagram of PRD 24.2 as text.

    Each node shows the SOC when the trip reaches it, and each arrow shows the
    drive or the charge between two nodes. A gap between events (an event that
    does not start where the previous one ended) starts a new block.
    """
    capacity = _capacity(trip)
    segments = build_segments(result)
    if not segments:
        return ""
    route = result["route_node_ids"]

    def node_line(node: str) -> str:
        tags = []
        if route and node == route[0]:
            tags.append("origin")
        if route and node == route[-1] and len(route) > 1:
            tags.append("destination")
        return f"{node} ({', '.join(tags)})" if tags else node

    lines: list[str] = []
    previous: Segment | None = None
    for seg in segments:
        contiguous = (
            previous is not None
            and previous.node_to == seg.node_from
            and previous.soc_after_kwh == seg.soc_before_kwh
        )
        if not contiguous:
            if previous is not None:
                lines.append("")
            lines += [node_line(seg.node_from), f"SOC {_soc(seg.soc_before_kwh, capacity)}"]
        event = seg.event
        if seg.kind == "drive":
            action = f"Drive {event['distance_miles']:.1f} mi, {event['duration_minutes']:.1f} min"
        else:
            action = (
                f"Charge {_soc(seg.soc_before_kwh, capacity)} -> "
                f"{_soc(seg.soc_after_kwh, capacity)}, {event['duration_minutes']:.1f} min"
            )
        lines += [
            "   |",
            f"   | {action}",
            "   v",
            node_line(seg.node_to),
            f"SOC {_soc(seg.soc_after_kwh, capacity)}",
        ]
        previous = seg
    return "\n".join(lines)


def build_trip_summary(result: dict, trip: dict | None = None) -> list[tuple[str, str]]:
    """Return the PRD 24.4 summary as ordered ``(label, value)`` display pairs.

    Totals come straight from the result fields. Arrival SOC is the SOC after the
    last event when that event ends at the destination (the last route node), and
    ``n/a`` when the result has no events or its events stop short of the destination.
    """
    capacity = _capacity(trip)
    metrics = result["metrics"]
    arrival = _arrival_soc(result)
    return [
        ("Total time", _duration(result["total_time_minutes"])),
        ("Driving time", _duration(result["driving_time_minutes"])),
        ("Charging time", _duration(result["charging_time_minutes"])),
        ("Distance", f"{result['total_distance_miles']:.1f} mi"),
        ("Charging stops", str(result["number_of_charging_stops"])),
        ("Arrival SOC", UNAVAILABLE if arrival is None else _soc(arrival, capacity)),
        ("Algorithm runtime", f"{metrics['runtime_ms']:.1f} ms"),
        ("States expanded", f"{metrics['states_expanded']:,}"),
    ]


def _arrival_soc(result: dict) -> float | None:
    if result["status"] != "FEASIBLE" or not result["events"] or not result["route_node_ids"]:
        return None
    last = result["events"][-1]
    end = last["to"] if last["type"] == "drive" else last["station"]
    if end != result["route_node_ids"][-1]:
        return None
    return last["soc_after_kwh"]


def _capacity(trip: dict | None) -> float | None:
    if not trip:
        return None
    capacity = trip.get("battery_capacity_kwh")
    return float(capacity) if capacity else None


def _threshold_rule(percent: float, label: str, color: str):
    import altair as alt

    return (
        alt.Chart(alt.Data(values=[{"soc": percent, "label": label}]))
        .mark_rule(color=color, strokeDash=[6, 4])
        .encode(y="soc:Q", tooltip=[alt.Tooltip("label:N", title="Line")])
    )


def _segment_label(seg: Segment) -> str:
    if seg.kind == "drive":
        return f"Drive {seg.node_from} -> {seg.node_to}"
    return f"Charge at {seg.node_from}"


def _soc(kwh: float, capacity: float | None) -> str:
    if capacity is None:
        return f"{kwh:g} kWh"
    return f"{kwh:g} kWh ({100.0 * kwh / capacity:.0f}%)"


def _soc_range(before: float, after: float, capacity: float | None) -> str:
    if capacity is None:
        return f"SOC {before:g} -> {after:g} kWh"
    return (
        f"SOC {before:g} -> {after:g} kWh "
        f"({100.0 * before / capacity:.0f}% -> {100.0 * after / capacity:.0f}%)"
    )


def _duration(minutes: float) -> str:
    if minutes < 60:
        return f"{minutes:.1f} min"
    hours, rest = divmod(round(minutes), 60)
    return f"{minutes:.1f} min ({hours} h {rest:02d} min)"
