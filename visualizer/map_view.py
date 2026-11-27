"""Map of the route, endpoints, and chargers.

Owned by Shannon (H6). Shows the origin, the destination, unused chargers,
selected charging stops, and the optimized route with distinct markers. The map
is drawn from a ``canonical_graph.json`` and an ``algorithm_result.json``; it
never runs a search.

The graph stores node coordinates but no road geometry, so each leg of the
route is drawn as a straight line between consecutive route nodes.

Write a standalone HTML file with::

    python -m visualizer.map_view --graph canonical_graph.json \\
        --result algorithm_result.json --out route_map.html
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from visualizer.data_io import VisualizerInputError, load_graph, load_result

# Marker colors, shared by the markers and the legend.
ORIGIN_COLOR = "#2e7d32"
DESTINATION_COLOR = "#c62828"
STOP_COLOR = "#ef6c00"
ROUTE_COLOR = "#1565c0"
PASS_THROUGH_COLOR = "#6a1b9a"
UNUSED_COLOR = "#757575"

# Road-following leg geometry is for drawing only. The optimizer's distances and
# times come from canonical_graph.json and are never changed by it.
ROAD_CACHE_DIR = Path(".route_cache")
OSRM_ROUTE_URL = (
    "https://router.project-osrm.org/route/v1/driving/"
    "{lon1},{lat1};{lon2},{lat2}?overview=full&geometries=geojson"
)


@dataclass(frozen=True)
class ChargingStop:
    """A node where the plan charges, numbered in the order it is first visited."""

    order: int
    node: dict
    charges: tuple[dict, ...]


@dataclass(frozen=True)
class MapModel:
    """Everything the map draws, with node roles resolved."""

    feasible: bool
    endpoints: tuple[dict, ...]
    stops: tuple[ChargingStop, ...]
    pass_through: tuple[dict, ...]
    unused: tuple[dict, ...]
    route: tuple[dict, ...]
    legs: tuple[dict | None, ...] = field(default_factory=tuple)
    charges_at_endpoints: dict[str, tuple[dict, ...]] = field(default_factory=dict)


def build_map_model(graph: dict, result: dict) -> MapModel:
    """Resolve each graph node to a role for the given result.

    Roles: ``endpoints`` (origin and destination nodes), ``stops`` (nodes with a
    charge event), ``pass_through`` (route nodes where the plan does not
    charge), and ``unused`` (every other node that can charge).

    Raises ``VisualizerInputError`` when the result names a node that is not in
    the graph.
    """
    nodes = {node["id"]: node for node in graph["nodes"]}

    def lookup(node_id: str, where: str) -> dict:
        if node_id not in nodes:
            raise VisualizerInputError(f"{where} names node {node_id!r}, which is not in the graph")
        return nodes[node_id]

    route = tuple(lookup(node_id, "route_node_ids") for node_id in result["route_node_ids"])

    charges: dict[str, list[dict]] = {}
    drives: list[dict] = []
    for event in result["events"]:
        if event["type"] == "charge":
            lookup(event["station"], "a charge event")
            charges.setdefault(event["station"], []).append(event)
        else:
            lookup(event["from"], "a drive event")
            lookup(event["to"], "a drive event")
            drives.append(event)

    legs: list[dict | None] = []
    for index in range(max(len(route) - 1, 0)):
        drive = drives[index] if index < len(drives) else None
        matches = (
            drive is not None
            and drive["from"] == route[index]["id"]
            and drive["to"] == route[index + 1]["id"]
        )
        legs.append(drive if matches else None)

    endpoints = tuple(n for n in graph["nodes"] if n["type"] in ("origin", "destination"))
    endpoint_ids = {n["id"] for n in endpoints}

    stops = tuple(
        ChargingStop(order=order, node=nodes[node_id], charges=tuple(events))
        for order, (node_id, events) in enumerate(charges.items(), start=1)
        if node_id not in endpoint_ids
    )
    stop_ids = {stop.node["id"] for stop in stops}

    pass_through: list[dict] = []
    seen: set[str] = set()
    for node in route:
        if node["id"] in endpoint_ids or node["id"] in stop_ids or node["id"] in seen:
            continue
        seen.add(node["id"])
        pass_through.append(node)

    used_ids = endpoint_ids | stop_ids | seen
    unused = tuple(
        n for n in graph["nodes"] if n["id"] not in used_ids and n["charge_rate_kw"] is not None
    )

    return MapModel(
        feasible=result["status"] == "FEASIBLE",
        endpoints=endpoints,
        stops=stops,
        pass_through=tuple(pass_through),
        unused=unused,
        route=route,
        legs=tuple(legs),
        charges_at_endpoints={
            node_id: tuple(events) for node_id, events in charges.items() if node_id in endpoint_ids
        },
    )


def road_geometry(
    start: dict, end: dict, cache_dir: Path = ROAD_CACHE_DIR, timeout: float = 8.0
) -> list[tuple[float, float]] | None:
    """Road-following (lat, lon) points from ``start`` to ``end``, or ``None``.

    Display only. Fetched once from the public OSRM router and cached under
    ``cache_dir``; any network or parse failure returns ``None`` so the caller
    falls back to a straight segment.
    """
    key = f"{start['lat']:.5f},{start['lon']:.5f}->{end['lat']:.5f},{end['lon']:.5f}"
    path = Path(cache_dir) / (hashlib.sha1(key.encode()).hexdigest() + ".json")
    if path.is_file():
        try:
            return [(lat, lon) for lat, lon in json.loads(path.read_text())]
        except (ValueError, TypeError):
            pass
    url = OSRM_ROUTE_URL.format(
        lon1=start["lon"], lat1=start["lat"], lon2=end["lon"], lat2=end["lat"]
    )
    request = urllib.request.Request(url, headers={"User-Agent": "ChargePlan-visualizer"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            coords = json.load(response)["routes"][0]["geometry"]["coordinates"]
    except (
        urllib.error.URLError,
        OSError,
        TimeoutError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
    ):
        return None
    points = [(float(lat), float(lon)) for lon, lat in coords]
    if len(points) < 2:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(points))
    except OSError:
        pass
    return points


def render_map(graph: dict, result: dict, *, road_lines: bool = False):
    """Return a ``folium.Map`` for the selected route.

    Layers: route, selected charging stops, unused chargers, origin and
    destination. An infeasible result draws no route and says so on the map.
    With ``road_lines`` each leg is drawn along real roads (display only, see
    ``road_geometry``); a leg whose geometry is unavailable stays straight.
    """
    import folium

    model = build_map_model(graph, result)
    fmap = folium.Map(tiles="OpenStreetMap", control_scale=True)

    route_group = folium.FeatureGroup(name="Optimized route", show=True)
    for index, leg in enumerate(model.legs):
        start, end = model.route[index], model.route[index + 1]
        points = road_geometry(start, end) if road_lines else None
        line = folium.PolyLine(
            points or [(start["lat"], start["lon"]), (end["lat"], end["lon"])],
            color=ROUTE_COLOR,
            weight=5,
            opacity=0.85,
        )
        line.add_child(folium.Tooltip(leg_text(start, end, leg)))
        route_group.add_child(line)
    fmap.add_child(route_group)

    unused_group = folium.FeatureGroup(name="Unused chargers", show=True)
    for node in model.unused:
        unused_group.add_child(
            folium.CircleMarker(
                (node["lat"], node["lon"]),
                radius=4,
                color=UNUSED_COLOR,
                weight=1,
                fill=True,
                fill_color=UNUSED_COLOR,
                fill_opacity=0.6,
                tooltip=charger_text(node, "Unused charger"),
            )
        )
    for node in model.pass_through:
        unused_group.add_child(
            folium.CircleMarker(
                (node["lat"], node["lon"]),
                radius=6,
                color=PASS_THROUGH_COLOR,
                weight=2,
                fill=True,
                fill_color="#ffffff",
                fill_opacity=1.0,
                tooltip=charger_text(node, "On route, no charge"),
            )
        )
    fmap.add_child(unused_group)

    stops_group = folium.FeatureGroup(name="Selected charging stops", show=True)
    for stop in model.stops:
        stops_group.add_child(
            folium.Marker(
                (stop.node["lat"], stop.node["lon"]),
                icon=folium.DivIcon(
                    icon_size=(28, 28),
                    icon_anchor=(14, 14),
                    html=stop_badge(stop.order),
                ),
                tooltip=f"Charging stop {stop.order}: {html.escape(stop.node['id'])}",
                popup=folium.Popup(stop_popup(stop), max_width=300),
                z_index_offset=500,
            )
        )
    fmap.add_child(stops_group)

    endpoint_group = folium.FeatureGroup(name="Origin and destination", show=True)
    for node in model.endpoints:
        is_origin = node["type"] == "origin"
        endpoint_group.add_child(
            folium.Marker(
                (node["lat"], node["lon"]),
                icon=folium.Icon(
                    color="green" if is_origin else "red",
                    icon="play" if is_origin else "flag",
                    prefix="fa",
                ),
                tooltip=f"{'Origin' if is_origin else 'Destination'}: {html.escape(node['id'])}",
                popup=folium.Popup(
                    endpoint_popup(node, model.charges_at_endpoints.get(node["id"], ())),
                    max_width=300,
                ),
                z_index_offset=1000,
            )
        )
    fmap.add_child(endpoint_group)

    folium.LayerControl(collapsed=False).add_to(fmap)
    fmap.get_root().html.add_child(folium.Element(legend_html(model)))
    fmap.fit_bounds(bounds(graph))
    return fmap


def bounds(graph: dict) -> list[list[float]]:
    lats = [n["lat"] for n in graph["nodes"]]
    lons = [n["lon"] for n in graph["nodes"]]
    return [[min(lats), min(lons)], [max(lats), max(lons)]]


def leg_text(start: dict, end: dict, drive: dict | None) -> str:
    head = f"{html.escape(start['id'])} &rarr; {html.escape(end['id'])}"
    if drive is None:
        return head
    return (
        f"{head}<br>Drive {drive['distance_miles']:.1f} mi, {drive['duration_minutes']:.1f} min"
        f"<br>SOC {drive['soc_before_kwh']:g} &rarr; {drive['soc_after_kwh']:g} kWh"
    )


def charger_text(node: dict, label: str) -> str:
    return f"{label}: {html.escape(node['id'])} ({node['charge_rate_kw']:g} kW)"


def charge_lines(charges: tuple[dict, ...]) -> str:
    return "".join(
        f"<br>Charge {c['soc_before_kwh']:g} &rarr; {c['soc_after_kwh']:g} kWh"
        f" ({c['duration_minutes']:.1f} min)"
        for c in charges
    )


def stop_badge(order: int) -> str:
    return (
        f'<div style="width:28px;height:28px;line-height:24px;border-radius:50%;'
        f"background:{STOP_COLOR};color:#fff;border:2px solid #fff;text-align:center;"
        f'font:bold 14px sans-serif;box-shadow:0 0 4px rgba(0,0,0,.6)">{order}</div>'
    )


def stop_popup(stop: ChargingStop) -> str:
    node = stop.node
    rate = "" if node["charge_rate_kw"] is None else f" ({node['charge_rate_kw']:g} kW)"
    return (
        f"<b>Charging stop {stop.order}</b><br>{html.escape(node['id'])}{rate}"
        f"{charge_lines(stop.charges)}"
    )


def endpoint_popup(node: dict, charges: tuple[dict, ...]) -> str:
    label = "Origin" if node["type"] == "origin" else "Destination"
    return f"<b>{label}</b><br>{html.escape(node['id'])}{charge_lines(charges)}"


def legend_html(model: MapModel) -> str:
    def swatch(color: str, shape: str = "circle") -> str:
        radius = "50%" if shape == "circle" else "2px"
        return (
            f'<span style="display:inline-block;width:12px;height:12px;border-radius:{radius};'
            f'background:{color};margin-right:6px;vertical-align:middle"></span>'
        )

    rows = [
        f"{swatch(ORIGIN_COLOR, 'square')}Origin",
        f"{swatch(DESTINATION_COLOR, 'square')}Destination",
        f"{swatch(STOP_COLOR)}Selected charging stop",
        f"{swatch(ROUTE_COLOR, 'square')}Optimized route",
        f"{swatch(UNUSED_COLOR)}Unused charger",
    ]
    if model.pass_through:
        rows.append(f"{swatch(PASS_THROUGH_COLOR)}On route, no charge")
    banner = ""
    if not model.feasible:
        banner = (
            '<div style="position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:9999;'
            "background:#c62828;color:#fff;padding:6px 14px;border-radius:4px;"
            'font:bold 14px sans-serif">INFEASIBLE: no route to draw</div>'
        )
    return (
        '<div style="position:fixed;bottom:28px;left:12px;z-index:9999;background:#fff;'
        "padding:8px 10px;border:1px solid #888;border-radius:4px;"
        f'font:12px/1.6 sans-serif">{"<br>".join(rows)}</div>{banner}'
    )


def main(argv: list[str] | None = None) -> int:
    """Write the route map for a graph and a result file to a standalone HTML file."""
    parser = argparse.ArgumentParser(prog="python -m visualizer.map_view", description=main.__doc__)
    parser.add_argument("--graph", required=True, help="canonical_graph.json")
    parser.add_argument("--result", required=True, help="algorithm_result.json")
    parser.add_argument("--out", required=True, help="HTML file to write")
    args = parser.parse_args(argv)
    try:
        fmap = render_map(load_graph(args.graph), load_result(args.result))
    except VisualizerInputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fmap.save(str(out))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
