"""H12: compare the haversine road estimates with OSMnx routes on one H5 scenario.

The H5 scenarios use the haversine fallback (straight-line distance times 1.25 at
55 mph). The PRD names OSMnx as the primary road source, so this module measures how
far apart the two are on one scenario and whether the optimizer's answer changes.

One run:

1. builds the scenario with the H5 pipeline and ``--backend haversine`` (the baseline);
2. downloads one OSM drive graph that covers every scenario node (a single Overpass
   request, retried with backoff, saved as GraphML so reruns are offline), routes
   every ordered node pair on it, and stores the results in the route cache as OSMnx
   entries, which is exactly what ``road_metrics(backend="osmnx")`` would have cached;
3. builds the same scenario again with the H5 pipeline and ``--backend osmnx``, which
   now reads those cache entries and never calls Overpass;
4. compares routed distance and drive time against the haversine estimate edge by edge
   and reports the median ratio and range;
5. runs ``experiments.real_data`` on both versions (label-setting, A*, greedy, with the
   independent plan checker) and reports whether the plan, the stops, or the times change.

It writes, under ``<--out>/<scenario id>`` (``--out`` defaults to ``results/h12_osm``)::

    osm_validation.json  osm_validation.md  edge_comparison.csv  osm_drive_graph.graphml
    route_cache/                         road_metrics cache for both backends
    scenarios_haversine/<id>/  scenarios_osmnx/<id>/    H5 scenario folders
    results_haversine/  results_osmnx/                  S9 result files

The visualizer reads the OSM version with::

    python -m visualizer.app \\
        --scenarios results/h12_osm/<id>/scenarios_osmnx \\
        --results results/h12_osm/<id>/results_osmnx

Run it with::

    python -m experiments.osm_validation --scenario no_charge_detroit_ann_arbor
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

from data_pipeline.chargers import load_stations
from data_pipeline.road_graph import (
    annotate_osm_graph,
    cache_name,
    graph_from_bbox,
    route_on_osm_graph,
    write_cache,
)
from data_pipeline.scenarios import (
    DEFAULT_SNAPSHOT,
    build_scenario,
    get_scenario_spec,
    write_scenario,
)
from experiments import real_data
from optimizer.model import haversine_miles

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "results" / "h12_osm"
DEFAULT_SCENARIO = "no_charge_detroit_ann_arbor"
DEFAULT_PAD_DEG = 0.1
DEFAULT_RETRIES = 5
DEFAULT_MAX_SNAP_MILES = 3.0
DEFAULT_WAIT_SECONDS = 30.0
MAX_WAIT_SECONDS = 300.0
PLAN_ALGORITHMS = ("label_setting", "greedy")
GRAPHML_NAME = "osm_drive_graph.graphml"
FILTERED_XML_NAME = "osm_drive_filtered.osm"
REPORT_JSON = "osm_validation.json"
REPORT_MD = "osm_validation.md"
EDGE_CSV = "edge_comparison.csv"
EDGE_COLUMNS = [
    "from",
    "to",
    "haversine_miles",
    "osmnx_miles",
    "distance_ratio",
    "haversine_minutes",
    "osmnx_minutes",
    "time_ratio",
]


class ValidationError(Exception):
    """The comparison could not be completed. The message says what to do."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m experiments.osm_validation",
        description="Compare haversine road estimates with OSMnx routes on one H5 scenario.",
    )
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO, help="H5 scenario id")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT, help="AFDC snapshot")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="output root; each scenario writes to <out>/<scenario id>",
    )
    parser.add_argument(
        "--graphml",
        type=Path,
        default=None,
        help="use this OSM drive graph (GraphML) instead of downloading one",
    )
    parser.add_argument(
        "--osm-xml",
        type=Path,
        default=None,
        help="build the drive graph from this OSM XML extract (any size) instead of downloading",
    )
    parser.add_argument(
        "--max-snap-miles",
        type=float,
        default=DEFAULT_MAX_SNAP_MILES,
        help="fail if a scenario node is farther than this from the nearest OSM graph node",
    )
    parser.add_argument(
        "--pad", type=float, default=DEFAULT_PAD_DEG, help="degrees of padding around the nodes"
    )
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="download retries")
    parser.add_argument(
        "--wait", type=float, default=DEFAULT_WAIT_SECONDS, help="first retry wait in seconds"
    )
    parser.add_argument(
        "--overpass-url",
        default=None,
        help="Overpass API endpoint to use instead of OSMnx's default",
    )
    parser.add_argument(
        "--refresh", action="store_true", help="download again even if the GraphML is saved"
    )
    args = parser.parse_args(argv)
    if args.overpass_url:
        import osmnx

        osmnx.settings.overpass_url = args.overpass_url
    try:
        summary = run_validation(
            args.scenario,
            args.snapshot,
            args.out / args.scenario,
            graphml=args.graphml,
            osm_xml=args.osm_xml,
            max_snap_miles=args.max_snap_miles,
            pad_deg=args.pad,
            retries=args.retries,
            wait_seconds=args.wait,
            refresh=args.refresh,
        )
    except (ValidationError, KeyError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(format_report(summary))
    return 0


def run_validation(
    scenario_id: str,
    snapshot: str | Path,
    out: str | Path,
    *,
    graphml: str | Path | None = None,
    osm_xml: str | Path | None = None,
    max_snap_miles: float = DEFAULT_MAX_SNAP_MILES,
    osm_graph: Any = None,
    pad_deg: float = DEFAULT_PAD_DEG,
    retries: int = DEFAULT_RETRIES,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    refresh: bool = False,
    repeats: int = 1,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Run the comparison and return (and write) the summary.

    ``osm_graph`` is an already annotated OSMnx graph and skips both the download
    and the GraphML file, which lets tests supply a small synthetic network.
    """
    spec = get_scenario_spec(scenario_id)
    snapshot_path = Path(snapshot)
    if not snapshot_path.is_file():
        raise ValidationError(f"AFDC snapshot not found: {snapshot_path}")
    out_dir = Path(out)
    cache_dir = out_dir / "route_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    stations = load_stations(snapshot_path)

    baseline = build_scenario(
        spec, stations, cache_dir, road_backend="haversine", snapshot_path=snapshot_path
    )
    nodes = baseline["graph"]["nodes"]

    ox = import_osmnx()
    source = "provided graph"
    if osm_graph is None:
        osm_graph, source = obtain_drive_graph(
            ox,
            nodes,
            out_dir / GRAPHML_NAME,
            graphml=graphml,
            osm_xml=osm_xml,
            filtered_xml_path=out_dir / FILTERED_XML_NAME,
            pad_deg=pad_deg,
            retries=retries,
            wait_seconds=wait_seconds,
            refresh=refresh,
            sleep=sleep,
        )
    snap = check_snap_distances(ox, osm_graph, nodes, max_snap_miles)
    prewarm_osm_cache(ox, osm_graph, nodes, cache_dir)

    routed = build_scenario(
        spec, stations, cache_dir, road_backend="osmnx", snapshot_path=snapshot_path
    )
    counts = routed["edge_count_by_backend"]
    if counts.get("haversine", 0) or not counts.get("osmnx", 0):
        raise ValidationError(f"the OSM build did not use OSMnx for every edge: {counts}")

    edges = compare_edges(baseline["graph"]["edges"], routed["graph"]["edges"])
    write_edge_csv(out_dir / EDGE_CSV, edges["rows"])

    plans = {}
    for backend, scenario in (("haversine", baseline), ("osmnx", routed)):
        scenarios_dir = out_dir / f"scenarios_{backend}"
        write_scenario(scenario, scenarios_dir)
        results_dir = out_dir / f"results_{backend}"
        reports = real_data.run_scenarios(
            scenarios_dir, results_dir, only=[scenario_id], repeats=repeats
        )
        failed = [error for report in reports for error in report.errors]
        if failed:
            raise ValidationError(f"S9 runner failed on the {backend} scenario: {failed}")
        plans[backend] = load_plan_summaries(results_dir / scenario_id)

    summary = {
        "scenario_id": scenario_id,
        "generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "afdc_snapshot": baseline["afdc_snapshot"],
        "osmnx_version": getattr(ox, "__version__", "unknown"),
        "osm_graph": {"source": source, **graph_size(osm_graph), **snap},
        "nodes": len(nodes),
        "edges": {k: v for k, v in edges.items() if k != "rows"},
        "plans": plan_comparison(plans),
    }
    (out_dir / REPORT_JSON).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (out_dir / REPORT_MD).write_text(format_report(summary) + "\n", encoding="utf-8")
    return summary


def import_osmnx():
    try:
        import osmnx as ox
    except ImportError as exc:
        raise ValidationError(
            "osmnx is not installed; pip install osmnx (see the roads extra in pyproject.toml)"
        ) from exc
    return ox


def obtain_drive_graph(
    ox: Any,
    nodes: list[dict],
    graphml_path: Path,
    *,
    graphml: str | Path | None,
    osm_xml: str | Path | None = None,
    filtered_xml_path: Path | None = None,
    pad_deg: float,
    retries: int,
    wait_seconds: float,
    refresh: bool,
    sleep: Callable[[float], None],
) -> tuple[Any, str]:
    """Return an annotated OSM drive graph and a note on where it came from."""
    if graphml is not None:
        path = Path(graphml)
        if not path.is_file():
            raise ValidationError(f"--graphml file not found: {path}")
        return annotate_osm_graph(ox, ox.load_graphml(path)), f"file {path}"
    if graphml_path.is_file() and not refresh:
        return annotate_osm_graph(ox, ox.load_graphml(graphml_path)), f"saved {graphml_path.name}"
    if osm_xml is not None:
        source_path = Path(osm_xml)
        if not source_path.is_file():
            raise ValidationError(f"--osm-xml file not found: {source_path}")
        filtered = filtered_xml_path or graphml_path.with_name(FILTERED_XML_NAME)
        counts = filter_osm_xml_for_driving(source_path, filtered)
        if not counts["ways"]:
            raise ValidationError(f"no drivable ways found in {source_path}")
        graph = ox.graph_from_xml(filtered, simplify=True)
        ox.save_graphml(graph, graphml_path)
        return annotate_osm_graph(ox, graph), f"OSM XML {source_path.name}"
    graph = download_drive_graph(
        ox, nodes, pad_deg=pad_deg, retries=retries, wait_seconds=wait_seconds, sleep=sleep
    )
    ox.save_graphml(graph, graphml_path)
    return annotate_osm_graph(ox, graph), "Overpass download"


# OSMnx's "drive" network filter (osmnx.settings.default_access plus the drive query), as
# patterns searched anywhere in the tag value like Overpass's !~ operator. A missing tag
# passes. Keep in step with osmnx._overpass._get_network_filter("drive").
DRIVE_EXCLUDED_HIGHWAY = re.compile(
    "abandoned|bridleway|bus_guideway|construction|corridor|cycleway|elevator|escalator|"
    "footway|no|path|pedestrian|planned|platform|proposed|raceway|razed|rest_area|service|"
    "services|steps|track"
)
DRIVE_EXCLUDED_SERVICE = re.compile("alley|driveway|emergency_access|parking|parking_aisle|private")
DRIVE_TAG_EXCLUSIONS = {
    "area": re.compile("yes"),
    "access": re.compile("private"),
    "motor_vehicle": re.compile("no"),
    "motorcar": re.compile("no"),
    "service": DRIVE_EXCLUDED_SERVICE,
}


def is_drivable_way(tags: dict[str, str]) -> bool:
    """True when OSMnx's ``network_type="drive"`` filter would keep a way with these tags."""
    highway = tags.get("highway")
    if highway is None or DRIVE_EXCLUDED_HIGHWAY.search(highway):
        return False
    return not any(
        key in tags and pattern.search(tags[key]) for key, pattern in DRIVE_TAG_EXCLUSIONS.items()
    )


def stream_osm_elements(path: Path):
    """Yield each top-level node, way, and relation, then free it so memory stays flat."""
    context = ET.iterparse(path, events=("start", "end"))
    _event, root = next(context)
    for event, element in context:
        if event == "end" and element.tag in ("node", "way", "relation"):
            yield element
            root.clear()


def filter_osm_xml_for_driving(source: Path, destination: Path) -> dict[str, int]:
    """Stream a large OSM XML extract into a small file holding only the drive network.

    ``ox.graph_from_xml`` keeps every element of a file in memory, which a multi-gigabyte
    state extract cannot afford. Pass 1 reads the ways and keeps the drivable ones. Pass 2
    reads the nodes (they come before the ways in an OSM file) and keeps those the kept
    ways use. Nothing but the files' elements is held in memory beyond the kept node ids.
    """
    ways: list[tuple[str, list[str], dict[str, str]]] = []
    needed: set[str] = set()
    for element in stream_osm_elements(source):
        if element.tag == "way":
            tags = {t.get("k"): t.get("v") for t in element.findall("tag")}
            if is_drivable_way(tags):
                refs = [nd.get("ref") for nd in element.findall("nd")]
                ways.append((element.get("id"), refs, tags))
                needed.update(refs)

    node_count = 0
    with destination.open("w", encoding="utf-8") as out:
        out.write("<?xml version='1.0' encoding='UTF-8'?>\n<osm version=\"0.6\">\n")
        for element in stream_osm_elements(source):
            if element.tag == "node":
                if element.get("id") in needed:
                    out.write(
                        f"<node id={quoteattr(element.get('id'))} "
                        f"lat={quoteattr(element.get('lat'))} "
                        f"lon={quoteattr(element.get('lon'))}/>\n"
                    )
                    node_count += 1
            elif element.tag == "way":
                break
        for way_id, refs, tags in ways:
            out.write(f"<way id={quoteattr(way_id)}>\n")
            for ref in refs:
                out.write(f"<nd ref={quoteattr(ref)}/>\n")
            for key, value in tags.items():
                out.write(f"<tag k={quoteattr(key)} v={quoteattr(value)}/>\n")
            out.write("</way>\n")
        out.write("</osm>\n")
    return {"ways": len(ways), "nodes": node_count}


def check_snap_distances(
    ox: Any, graph: Any, nodes: list[dict], max_miles: float
) -> dict[str, float]:
    """Fail when a scenario node is far from every OSM graph node (the extract is too small).

    A node outside the extract would snap to its edge and every route to it would be wrong.
    """
    lons = [float(node["lon"]) for node in nodes]
    lats = [float(node["lat"]) for node in nodes]
    _ids, meters = ox.distance.nearest_nodes(graph, lons, lats, return_dist=True)
    miles = [float(m) / 1609.344 for m in meters]
    far = [
        f"{node['id']} ({distance:.1f} mi)"
        for node, distance in zip(nodes, miles, strict=True)
        if distance > max_miles
    ]
    if far:
        raise ValidationError(
            f"{len(far)} scenario node(s) are more than {max_miles:g} mi from the nearest OSM "
            f"graph node: {', '.join(far)}. The OSM data does not cover them. Use an extract "
            "or download that covers every scenario node (see the bounding box in the "
            "README), or raise --max-snap-miles if the gap is real."
        )
    return {"max_snap_miles": max(miles)}


def download_drive_graph(
    ox: Any,
    nodes: list[dict],
    *,
    pad_deg: float,
    retries: int,
    wait_seconds: float,
    sleep: Callable[[float], None],
):
    """One padded-bbox drive download covering every node, retried with backoff."""
    lats = [float(node["lat"]) for node in nodes]
    lons = [float(node["lon"]) for node in nodes]
    box = {
        "north": max(lats) + pad_deg,
        "south": min(lats) - pad_deg,
        "east": max(lons) + pad_deg,
        "west": min(lons) - pad_deg,
    }
    last: Exception | None = None
    for attempt in range(max(retries, 0) + 1):
        try:
            return graph_from_bbox(ox, **box)
        except Exception as exc:  # Overpass failures surface as several exception types
            last = exc
            if attempt < retries:
                sleep(min(wait_seconds * 2**attempt, MAX_WAIT_SECONDS))
    raise ValidationError(
        f"could not download the OSM drive graph after {retries + 1} attempt(s): "
        f"{str(last).splitlines()[0] if last else 'unknown error'}. "
        "The public Overpass server may be at capacity. Retry later with --retries, or "
        "download the area elsewhere and pass it with --graphml."
    )


def prewarm_osm_cache(ox: Any, graph: Any, nodes: list[dict], cache_dir: Path) -> int:
    """Route every ordered node pair on ``graph`` and write the OSMnx cache entries.

    The entries match what ``road_metrics(backend="osmnx")`` stores (including the
    great-circle floor on distance), so the H5 pipeline reads them as ordinary hits.
    """
    written = 0
    for start in nodes:
        for end in nodes:
            if start["id"] == end["id"]:
                continue
            o_lat, o_lon = float(start["lat"]), float(start["lon"])
            d_lat, d_lon = float(end["lat"]), float(end["lon"])
            try:
                metrics = route_on_osm_graph(ox, graph, o_lat, o_lon, d_lat, d_lon)
            except RuntimeError as exc:
                raise ValidationError(
                    f"no OSM drive path from {start['id']} to {end['id']}: {exc}"
                ) from exc
            straight = haversine_miles(o_lat, o_lon, d_lat, d_lon)
            write_cache(
                cache_dir / cache_name(o_lat, o_lon, d_lat, d_lon, "osmnx"),
                {
                    "distance_miles": max(float(metrics["distance_miles"]), straight),
                    "drive_time_minutes": max(float(metrics["drive_time_minutes"]), 0.0),
                    "backend": "osmnx",
                    "cached": False,
                    "origin": {"lat": o_lat, "lon": o_lon},
                    "destination": {"lat": d_lat, "lon": d_lon},
                },
            )
            written += 1
    return written


def graph_size(graph: Any) -> dict[str, int]:
    return {
        "graph_nodes": int(graph.number_of_nodes()),
        "graph_edges": int(graph.number_of_edges()),
    }


def compare_edges(haversine_edges: list[dict], osm_edges: list[dict]) -> dict:
    """Compare the edges both builds kept, and count the ones only one build kept."""
    osm_by_pair = {(edge["from"], edge["to"]): edge for edge in osm_edges}
    hav_by_pair = {(edge["from"], edge["to"]): edge for edge in haversine_edges}
    rows = []
    for pair, hav in hav_by_pair.items():
        osm = osm_by_pair.get(pair)
        if osm is None:
            continue
        rows.append(
            {
                "from": pair[0],
                "to": pair[1],
                "haversine_miles": float(hav["distance_miles"]),
                "osmnx_miles": float(osm["distance_miles"]),
                "distance_ratio": ratio(osm["distance_miles"], hav["distance_miles"]),
                "haversine_minutes": float(hav["drive_time_minutes"]),
                "osmnx_minutes": float(osm["drive_time_minutes"]),
                "time_ratio": ratio(osm["drive_time_minutes"], hav["drive_time_minutes"]),
            }
        )
    return {
        "haversine_edges": len(hav_by_pair),
        "osmnx_edges": len(osm_by_pair),
        "compared_edges": len(rows),
        "only_in_haversine": len(set(hav_by_pair) - set(osm_by_pair)),
        "only_in_osmnx": len(set(osm_by_pair) - set(hav_by_pair)),
        "distance_ratio": ratio_stats([row["distance_ratio"] for row in rows]),
        "time_ratio": ratio_stats([row["time_ratio"] for row in rows]),
        "rows": rows,
    }


def ratio(numerator: float, denominator: float) -> float:
    return float(numerator) / float(denominator)


def ratio_stats(values: list[float]) -> dict[str, float | int | None]:
    """Median, range, and 10th/90th percentiles of osmnx divided by haversine."""
    if not values:
        return {"n": 0, "median": None, "min": None, "max": None, "p10": None, "p90": None}
    ordered = sorted(values)
    if len(ordered) >= 2:
        deciles = statistics.quantiles(ordered, n=10, method="inclusive")
        p10, p90 = deciles[0], deciles[-1]
    else:
        p10 = p90 = ordered[0]
    return {
        "n": len(ordered),
        "median": statistics.median(ordered),
        "min": ordered[0],
        "max": ordered[-1],
        "p10": p10,
        "p90": p90,
    }


def write_edge_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EDGE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def load_plan_summaries(results_folder: Path) -> dict[str, dict]:
    """Status, totals, and the stop sequence of each compared algorithm's result."""
    plans = {}
    for name in PLAN_ALGORITHMS:
        result = json.loads((results_folder / f"{name}.json").read_text(encoding="utf-8"))
        plans[name] = plan_summary(result)
    return plans


def plan_summary(result: dict) -> dict:
    feasible = result["status"] == "FEASIBLE"
    stops: list[str] = []
    for event in result["events"]:
        if event["type"] != "charge":
            continue
        if not stops or stops[-1] != event["station"]:
            stops.append(event["station"])
    return {
        "algorithm": result["algorithm"],
        "status": result["status"],
        "total_time_minutes": result["total_time_minutes"] if feasible else None,
        "total_distance_miles": result["total_distance_miles"] if feasible else None,
        "number_of_charging_stops": result["number_of_charging_stops"] if feasible else None,
        "stops": stops,
        "route_node_ids": result["route_node_ids"],
    }


def plan_comparison(plans: dict[str, dict[str, dict]]) -> dict:
    """Per algorithm: the plan on each backend and whether the plan or stops changed."""
    comparison = {}
    for name in PLAN_ALGORITHMS:
        hav, osm = plans["haversine"][name], plans["osmnx"][name]
        comparison[name] = {
            "haversine": hav,
            "osmnx": osm,
            "status_changed": hav["status"] != osm["status"],
            "route_changed": hav["route_node_ids"] != osm["route_node_ids"],
            "stops_changed": hav["stops"] != osm["stops"],
            "stop_count_changed": hav["number_of_charging_stops"]
            != osm["number_of_charging_stops"],
        }
    return comparison


def format_report(summary: dict) -> str:
    """Markdown for the comparison, ready to paste into ``experiments/README.md``."""
    edges = summary["edges"]
    lines = [
        f"H12 haversine vs OSMnx: {summary['scenario_id']}",
        "",
        f"AFDC snapshot {summary['afdc_snapshot'].get('date')}, OSMnx "
        f"{summary['osmnx_version']}, OSM graph from {summary['osm_graph']['source']} "
        f"({summary['osm_graph']['graph_nodes']} nodes, {summary['osm_graph']['graph_edges']} "
        f"edges). {summary['nodes']} scenario nodes.",
        "",
        f"Edges kept: {edges['haversine_edges']} haversine, {edges['osmnx_edges']} OSMnx, "
        f"{edges['compared_edges']} in both ({edges['only_in_haversine']} only haversine, "
        f"{edges['only_in_osmnx']} only OSMnx).",
        "",
        "| OSMnx / haversine | median | min | max | p10 | p90 |",
        "| --- | --- | --- | --- | --- | --- |",
        stats_row("distance", edges["distance_ratio"]),
        stats_row("drive time", edges["time_ratio"]),
        "",
        "| Algorithm | Backend | Status | Time (min) | Stops | Stop stations |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name, entry in summary["plans"].items():
        for backend in ("haversine", "osmnx"):
            plan = entry[backend]
            minutes = fmt(plan["total_time_minutes"], 1)
            stops = fmt(plan["number_of_charging_stops"], 0)
            stations = ", ".join(plan["stops"]) or "-"
            lines.append(
                f"| {name} | {backend} | {plan['status']} | {minutes} | {stops} | {stations} |"
            )
    lines.append("")
    for name, entry in summary["plans"].items():
        changed = [
            label
            for key, label in (
                ("status_changed", "status"),
                ("route_changed", "route"),
                ("stops_changed", "stop stations"),
                ("stop_count_changed", "stop count"),
            )
            if entry[key]
        ]
        lines.append(
            f"- {name}: " + (f"changed ({', '.join(changed)})" if changed else "unchanged")
        )
    return "\n".join(lines)


def stats_row(label: str, stats: dict) -> str:
    cells = [fmt(stats[key], 3) for key in ("median", "min", "max", "p10", "p90")]
    return f"| {label} | " + " | ".join(cells) + " |"


def fmt(value: float | int | None, digits: int) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


if __name__ == "__main__":
    raise SystemExit(main())
