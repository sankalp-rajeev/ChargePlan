"""Reproducible real-data trip scenarios (H5).

Owned by Shannon. Builds frozen corridor instances under ``data/processed/``
using the H1–H3 pipeline. Each scenario writes:

- ``canonical_graph.json``
- ``trip_config.json``

Categories required by the PRD:

- no-charge trip
- one-stop trip
- multi-stop trip
- mixed charger speeds
- sparse charger availability
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import jsonschema

from data_pipeline.export_graph import build_canonical_graph, export_graph
from data_pipeline.reduce_graph import (
    DEFAULT_CONSUMPTION_KWH_PER_MILE,
    DEFAULT_CORRIDOR_WIDTH_MILES,
    point_to_segment,
    select_candidates,
    to_local_miles,
)
from optimizer.model import haversine_miles

_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_SCHEMA = _ROOT / "contracts" / "trip_config.schema.json"
_DEFAULT_PROCESSED = _ROOT / "data" / "processed"
_DEFAULT_CACHE = _ROOT / ".osm_cache"
_DEFAULT_SNAPSHOT = _ROOT / "data" / "raw" / "afdc_ev_midwest_2026-11-09.json"

# Frozen Midwest places used by the demonstration corridors.
PLACES: dict[str, dict[str, float]] = {
    "detroit": {"lat": 42.3314, "lon": -83.0458},
    "ann_arbor": {"lat": 42.2808, "lon": -83.7430},
    "chicago": {"lat": 41.8781, "lon": -87.6298},
    "cleveland": {"lat": 41.4993, "lon": -81.6944},
}

RoadBackend = Literal["auto", "osmnx", "haversine"]


@dataclass(frozen=True)
class ScenarioSpec:
    """One named real-data scenario definition."""

    scenario_id: str
    category: str
    description: str
    origin_place: str
    destination_place: str
    initial_soc_kwh: float
    battery_capacity_kwh: float = 70.0
    reserve_kwh: float = 7.0
    consumption_kwh_per_mile: float = DEFAULT_CONSUMPTION_KWH_PER_MILE
    soc_step_kwh: float = 1.0
    max_chargers: int | None = 25
    corridor_width_miles: float = DEFAULT_CORRIDOR_WIDTH_MILES
    station_filter: Literal["all", "mixed_speeds", "sparse"] = "all"


def place(name: str) -> dict[str, float]:
    """Return a copy of a frozen Midwest place ``{lat, lon}``."""
    try:
        point = PLACES[name]
    except KeyError as exc:
        known = ", ".join(sorted(PLACES))
        raise KeyError(f"unknown place {name!r}; expected one of: {known}") from exc
    return {"lat": float(point["lat"]), "lon": float(point["lon"])}


def default_trip_config(
    *,
    origin_id: str = "origin",
    destination_id: str = "destination",
    battery_capacity_kwh: float = 70.0,
    initial_soc_kwh: float = 54.0,
    reserve_kwh: float = 7.0,
    consumption_kwh_per_mile: float = DEFAULT_CONSUMPTION_KWH_PER_MILE,
    soc_step_kwh: float = 1.0,
) -> dict[str, float | str]:
    """Build one ``trip_config.json`` object with the frozen field set."""
    return {
        "origin": origin_id,
        "destination": destination_id,
        "battery_capacity_kwh": float(battery_capacity_kwh),
        "initial_soc_kwh": float(initial_soc_kwh),
        "reserve_kwh": float(reserve_kwh),
        "consumption_kwh_per_mile": float(consumption_kwh_per_mile),
        "soc_step_kwh": float(soc_step_kwh),
    }


def validate_trip_config(config: dict) -> None:
    """Raise ``ValueError`` if ``config`` fails the frozen trip-config schema."""
    schema = json.loads(_CONFIG_SCHEMA.read_text(encoding="utf-8"))
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    errors = sorted(validator_cls(schema).iter_errors(config), key=lambda err: list(err.path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "<root>"
        raise ValueError(f"trip_config.schema.json: {location}: {first.message}")

    if config["initial_soc_kwh"] > config["battery_capacity_kwh"]:
        raise ValueError("initial_soc_kwh cannot exceed battery_capacity_kwh")
    if config["reserve_kwh"] > config["battery_capacity_kwh"]:
        raise ValueError("reserve_kwh cannot exceed battery_capacity_kwh")


def export_trip_config(config: dict, path: str | Path) -> None:
    """Validate and write ``trip_config.json``."""
    validate_trip_config(config)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def stations_between_endpoints(
    stations: list[dict],
    origin: dict,
    destination: dict,
    *,
    end_pad_miles: float = 1.0,
) -> list[dict]:
    """Keep stations whose along-track progress lies on the OD segment.

    Corridor selection alone can retain chargers behind the origin or past the
    destination (within the perpendicular band). Those break forward sparse
    graphs, so H5 clips to the segment before subsampling.
    """
    if end_pad_miles < 0:
        raise ValueError("end_pad_miles must be non-negative")
    origin_lat = float(origin["lat"])
    origin_lon = float(origin["lon"])
    dest_lat = float(destination["lat"])
    dest_lon = float(destination["lon"])
    od_miles = haversine_miles(origin_lat, origin_lon, dest_lat, dest_lon)
    kept: list[dict] = []
    for station in stations:
        along = along_track_miles(
            float(station["lat"]),
            float(station["lon"]),
            origin_lat,
            origin_lon,
            dest_lat,
            dest_lon,
        )
        if -end_pad_miles <= along <= od_miles + end_pad_miles:
            kept.append(station)
    return kept


def select_mixed_speed_stations(
    stations: list[dict],
    origin: dict,
    destination: dict,
    *,
    max_chargers: int,
    corridor_width_miles: float = DEFAULT_CORRIDOR_WIDTH_MILES,
) -> list[dict]:
    """Evenly space corridor stations between the endpoints, then ensure a speed mix."""
    if max_chargers < 2:
        raise ValueError("max_chargers must be at least 2 for mixed_speeds")

    candidates = stations_between_endpoints(
        select_candidates(
            stations,
            origin,
            destination,
            corridor_width_miles=corridor_width_miles,
        ),
        origin,
        destination,
    )
    dc_fast = [station for station in candidates if station.get("charger_class") == "dc_fast"]
    level2 = [station for station in candidates if station.get("charger_class") == "level2"]
    if not dc_fast or not level2:
        raise ValueError("mixed_speeds requires both dc_fast and level2 stations on the OD segment")

    selected = subsample_by_progress(candidates, origin, destination, max_chargers)
    classes = {station.get("charger_class") for station in selected}
    if classes >= {"dc_fast", "level2"}:
        return selected

    # Replace one station with the missing class, preferring a midpoint candidate.
    missing = "level2" if "level2" not in classes else "dc_fast"
    pool = level2 if missing == "level2" else dc_fast
    selected_ids = {str(station["id"]) for station in selected}
    ordered_pool = order_by_progress(pool, origin, destination)
    replacement = next(
        (
            station
            for station in ordered_pool
            if str(station["id"]) not in selected_ids
        ),
        pool[len(pool) // 2],
    )
    selected[-1] = replacement
    return order_by_progress(selected, origin, destination)


def select_sparse_stations(
    stations: list[dict],
    origin: dict,
    destination: dict,
    *,
    max_chargers: int,
    corridor_width_miles: float = DEFAULT_CORRIDOR_WIDTH_MILES,
) -> list[dict]:
    """Keep a small evenly spaced subset of chargers between the endpoints."""
    if max_chargers < 0:
        raise ValueError("max_chargers must be non-negative")
    candidates = stations_between_endpoints(
        select_candidates(
            stations,
            origin,
            destination,
            corridor_width_miles=corridor_width_miles,
        ),
        origin,
        destination,
    )
    return subsample_by_progress(candidates, origin, destination, max_chargers)


def filter_stations_for_scenario(
    stations: list[dict],
    spec: ScenarioSpec,
    origin: dict,
    destination: dict,
) -> list[dict]:
    """Apply the scenario's station filter before graph assembly."""
    if spec.station_filter == "all":
        candidates = stations_between_endpoints(
            select_candidates(
                stations,
                origin,
                destination,
                corridor_width_miles=spec.corridor_width_miles,
            ),
            origin,
            destination,
        )
        if spec.max_chargers is None:
            return candidates
        return subsample_by_progress(
            candidates,
            origin,
            destination,
            spec.max_chargers,
        )
    if spec.station_filter == "mixed_speeds":
        count = 12 if spec.max_chargers is None else spec.max_chargers
        return select_mixed_speed_stations(
            stations,
            origin,
            destination,
            max_chargers=count,
            corridor_width_miles=spec.corridor_width_miles,
        )
    if spec.station_filter == "sparse":
        count = 4 if spec.max_chargers is None else spec.max_chargers
        return select_sparse_stations(
            stations,
            origin,
            destination,
            max_chargers=count,
            corridor_width_miles=spec.corridor_width_miles,
        )
    raise ValueError(f"unknown station_filter {spec.station_filter!r}")


def scenario_specs() -> list[ScenarioSpec]:
    """Return the frozen H5 scenario list."""
    return [
        ScenarioSpec(
            scenario_id="no_charge_detroit_ann_arbor",
            category="no_charge",
            description="Short Detroit–Ann Arbor hop that stays feasible without charging.",
            origin_place="detroit",
            destination_place="ann_arbor",
            initial_soc_kwh=60.0,
            max_chargers=8,
            station_filter="all",
        ),
        ScenarioSpec(
            scenario_id="one_stop_detroit_cleveland",
            category="one_stop",
            description="Detroit–Cleveland with moderate SOC, aimed at a single charge stop.",
            origin_place="detroit",
            destination_place="cleveland",
            initial_soc_kwh=35.0,
            max_chargers=20,
            station_filter="all",
        ),
        ScenarioSpec(
            scenario_id="multi_stop_detroit_chicago",
            category="multi_stop",
            description="Detroit–Chicago corridor that needs multiple charging decisions.",
            origin_place="detroit",
            destination_place="chicago",
            initial_soc_kwh=40.0,
            max_chargers=25,
            station_filter="all",
        ),
        ScenarioSpec(
            scenario_id="mixed_speeds_detroit_chicago",
            category="mixed_speeds",
            description="Detroit–Chicago with interleaved DC-fast and Level 2 chargers.",
            origin_place="detroit",
            destination_place="chicago",
            initial_soc_kwh=40.0,
            max_chargers=12,
            station_filter="mixed_speeds",
        ),
        ScenarioSpec(
            scenario_id="sparse_detroit_chicago",
            category="sparse",
            description="Detroit–Chicago with only a few corridor chargers retained.",
            origin_place="detroit",
            destination_place="chicago",
            initial_soc_kwh=55.0,
            max_chargers=6,
            station_filter="sparse",
        ),
    ]


def get_scenario_spec(scenario_id: str) -> ScenarioSpec:
    """Look up one frozen scenario by id."""
    for spec in scenario_specs():
        if spec.scenario_id == scenario_id:
            return spec
    known = ", ".join(spec.scenario_id for spec in scenario_specs())
    raise KeyError(f"unknown scenario {scenario_id!r}; expected one of: {known}")


def build_scenario(
    spec: ScenarioSpec,
    stations: list[dict],
    cache_dir: str | Path,
    *,
    road_backend: RoadBackend = "haversine",
) -> dict[str, Any]:
    """Assemble graph, trip config, and metadata for one scenario."""
    origin = place(spec.origin_place)
    destination = place(spec.destination_place)
    filtered = filter_stations_for_scenario(stations, spec, origin, destination)
    graph = build_canonical_graph(
        origin,
        destination,
        filtered,
        cache_dir,
        corridor_width_miles=spec.corridor_width_miles,
        max_chargers=None,  # already filtered/subsampled above
        road_backend=road_backend,
        max_battery_kwh=spec.battery_capacity_kwh,
        consumption_kwh_per_mile=spec.consumption_kwh_per_mile,
    )
    config = default_trip_config(
        battery_capacity_kwh=spec.battery_capacity_kwh,
        initial_soc_kwh=spec.initial_soc_kwh,
        reserve_kwh=spec.reserve_kwh,
        consumption_kwh_per_mile=spec.consumption_kwh_per_mile,
        soc_step_kwh=spec.soc_step_kwh,
    )
    validate_trip_config(config)
    return {
        "scenario_id": spec.scenario_id,
        "category": spec.category,
        "description": spec.description,
        "graph": graph,
        "config": config,
        "charger_count": sum(1 for node in graph["nodes"] if node["type"] == "charger"),
        "origin_place": spec.origin_place,
        "destination_place": spec.destination_place,
    }


def write_scenario(scenario: dict[str, Any], output_dir: str | Path) -> Path:
    """Write one scenario directory with graph + trip config. Returns the directory."""
    root = Path(output_dir) / str(scenario["scenario_id"])
    root.mkdir(parents=True, exist_ok=True)
    graph_path = root / "canonical_graph.json"
    export_graph(scenario["graph"]["nodes"], scenario["graph"]["edges"], graph_path)
    export_trip_config(scenario["config"], root / "trip_config.json")
    meta = {
        "scenario_id": scenario["scenario_id"],
        "category": scenario["category"],
        "description": scenario["description"],
        "origin_place": scenario["origin_place"],
        "destination_place": scenario["destination_place"],
        "charger_count": scenario["charger_count"],
    }
    (root / "scenario_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return root


def build_all_scenarios(
    stations: list[dict],
    cache_dir: str | Path,
    output_dir: str | Path,
    *,
    road_backend: RoadBackend = "haversine",
    scenario_ids: list[str] | None = None,
) -> list[Path]:
    """Build and write every requested H5 scenario. Returns written directories."""
    wanted = set(scenario_ids) if scenario_ids is not None else None
    written: list[Path] = []
    for spec in scenario_specs():
        if wanted is not None and spec.scenario_id not in wanted:
            continue
        scenario = build_scenario(spec, stations, cache_dir, road_backend=road_backend)
        written.append(write_scenario(scenario, output_dir))
    return written


def along_track_miles(
    lat: float,
    lon: float,
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
) -> float:
    origin_xy = (0.0, 0.0)
    dest_xy = to_local_miles(dest_lat, dest_lon, origin_lat, origin_lon)
    point_xy = to_local_miles(lat, lon, origin_lat, origin_lon)
    _distance, along = point_to_segment(point_xy, origin_xy, dest_xy)
    return along


def order_by_progress(
    stations: list[dict],
    origin: dict,
    destination: dict,
) -> list[dict]:
    return sorted(
        stations,
        key=lambda station: (
            along_track_miles(
                float(station["lat"]),
                float(station["lon"]),
                float(origin["lat"]),
                float(origin["lon"]),
                float(destination["lat"]),
                float(destination["lon"]),
            ),
            str(station.get("id", "")),
        ),
    )


def subsample_by_progress(
    stations: list[dict],
    origin: dict,
    destination: dict,
    max_chargers: int,
) -> list[dict]:
    ordered = order_by_progress(stations, origin, destination)
    if max_chargers <= 0 or len(ordered) <= max_chargers:
        return list(ordered) if max_chargers != 0 else []
    if max_chargers == 1:
        return [ordered[len(ordered) // 2]]
    indexes = {
        round(i * (len(ordered) - 1) / (max_chargers - 1)) for i in range(max_chargers)
    }
    return [ordered[index] for index in sorted(indexes)]


def main(argv: list[str] | None = None) -> int:
    """CLI: build H5 scenarios from the local AFDC snapshot."""
    import argparse

    from data_pipeline.chargers import load_stations

    parser = argparse.ArgumentParser(description="Build ChargePlan H5 real-data scenarios.")
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=_DEFAULT_SNAPSHOT,
        help="Path to the dated AFDC JSON/CSV snapshot",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_DEFAULT_PROCESSED,
        help="Directory that receives scenario subfolders",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=_DEFAULT_CACHE,
        help="Road-metrics cache directory",
    )
    parser.add_argument(
        "--backend",
        choices=("auto", "osmnx", "haversine"),
        default="haversine",
        help="Road-metrics backend",
    )
    parser.add_argument(
        "--only",
        nargs="*",
        default=None,
        help="Optional subset of scenario ids",
    )
    args = parser.parse_args(argv)

    if not args.snapshot.is_file():
        print(f"error: snapshot not found: {args.snapshot}")
        return 1

    stations = load_stations(args.snapshot)
    written = build_all_scenarios(
        stations,
        args.cache,
        args.out,
        road_backend=args.backend,
        scenario_ids=args.only,
    )
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
