"""Charging-station ingest from a dated AFDC snapshot.

Owned by Shannon (H1). Download once, filter to public EV chargers, and
normalize location and charging rate. Where power is missing, assign a
documented fallback by charger class and record which stations used it.

Supported snapshot formats:

- NREL/AFDC JSON (``{"fuel_stations": [...]}`` or a bare station list)
- NREL/AFDC CSV (All Stations download / ``v1.csv``)

Filtering keeps stations that are electric (``ELEC``), public, available
(``status_code == E``), have coordinates, and have at least one Level 2 or
DC-fast port. Level-1-only sites are dropped as not useful for corridor trips.

Fallback rates when no kW value is present in the snapshot (PRD §17.1):

- ``dc_fast`` → 150 kW
- ``level2`` → 7.2 kW

When ``ev_charging_units`` reports connector ``power_kw`` values, the
effective rate is the maximum positive power across those connectors and
``used_fallback_rate`` is false. CSV snapshots have no power columns, so
they always use the class fallback.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

# Documented class fallbacks. Experiment H may sweep these values later.
FALLBACK_RATES_KW: dict[str, float] = {
    "dc_fast": 150.0,
    "level2": 7.2,
}

CSV_FIELDS_ALIASES: dict[str, str] = {
    "id": "id",
    "station name": "station_name",
    "station_name": "station_name",
    "street address": "street_address",
    "street_address": "street_address",
    "city": "city",
    "state": "state",
    "latitude": "latitude",
    "longitude": "longitude",
    "access code": "access_code",
    "access_code": "access_code",
    "status code": "status_code",
    "status_code": "status_code",
    "fuel type code": "fuel_type_code",
    "fuel_type_code": "fuel_type_code",
    "ev level1 evse num": "ev_level1_evse_num",
    "ev_level1_evse_num": "ev_level1_evse_num",
    "ev level2 evse num": "ev_level2_evse_num",
    "ev_level2_evse_num": "ev_level2_evse_num",
    "ev dc fast count": "ev_dc_fast_num",
    "ev_dc_fast_num": "ev_dc_fast_num",
    "ev network": "ev_network",
    "ev_network": "ev_network",
}


def load_stations(snapshot_path: str | Path) -> list[dict]:
    """Return normalized station records from a local AFDC snapshot.

    Each record has:

    - ``id`` (str), ``name``, ``lat``, ``lon``
    - ``charger_class`` (``dc_fast`` or ``level2``)
    - ``charge_rate_kw`` (always set after fallback)
    - ``used_fallback_rate`` (bool)
    - ``network``, ``city``, ``state``, ``street_address`` (optional strings)
    """
    path = Path(snapshot_path)
    if not path.is_file():
        raise FileNotFoundError(f"AFDC snapshot not found: {path}")

    suffix = path.suffix.lower()
    if suffix == ".json":
        raw_stations = load_json_stations(path)
    elif suffix == ".csv":
        raw_stations = load_csv_stations(path)
    else:
        raise ValueError(
            f"unsupported AFDC snapshot type {path.suffix!r}; expected .json or .csv"
        )

    normalized: list[dict] = []
    for raw in raw_stations:
        station = normalize_station(raw)
        if station is not None:
            normalized.append(station)
    return normalized


def load_json_stations(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        stations = payload.get("fuel_stations")
        if isinstance(stations, list):
            return [row for row in stations if isinstance(row, dict)]
    raise ValueError(
        "AFDC JSON snapshot must be a station list or an object with fuel_stations"
    )


def load_csv_stations(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("AFDC CSV snapshot has no header row")
        rows: list[dict[str, Any]] = []
        for row in reader:
            mapped: dict[str, Any] = {}
            for key, value in row.items():
                if key is None:
                    continue
                alias = CSV_FIELDS_ALIASES.get(key.strip().lower())
                if alias is None:
                    continue
                mapped[alias] = value
            rows.append(mapped)
        return rows


def normalize_station(raw: dict[str, Any]) -> dict | None:
    if not is_public_available_ev(raw):
        return None

    lat = as_float(raw.get("latitude"))
    lon = as_float(raw.get("longitude"))
    if lat is None or lon is None:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None

    station_charger_class = charger_class(raw)
    if station_charger_class is None:
        return None

    observed_kw = observed_charge_rate_kw(raw)
    if observed_kw is not None:
        charge_rate_kw = observed_kw
        used_fallback = False
    else:
        charge_rate_kw = FALLBACK_RATES_KW[station_charger_class]
        used_fallback = True

    station_id = raw.get("id")
    if station_id is None or str(station_id).strip() == "":
        return None

    return {
        "id": str(station_id).strip(),
        "name": as_string(raw.get("station_name")) or str(station_id).strip(),
        "lat": lat,
        "lon": lon,
        "charger_class": station_charger_class,
        "charge_rate_kw": charge_rate_kw,
        "used_fallback_rate": used_fallback,
        "network": as_string(raw.get("ev_network")),
        "city": as_string(raw.get("city")),
        "state": as_string(raw.get("state")),
        "street_address": as_string(raw.get("street_address")),
    }


def is_public_available_ev(raw: dict[str, Any]) -> bool:
    fuel = as_string(raw.get("fuel_type_code"))
    if fuel is not None and fuel.upper() != "ELEC":
        return False

    access = as_string(raw.get("access_code"))
    if access is None or access.lower() != "public":
        return False

    status = as_string(raw.get("status_code"))
    if status is None or status.upper() != "E":
        return False

    return True


def charger_class(raw: dict[str, Any]) -> str | None:
    dc_fast = as_int(raw.get("ev_dc_fast_num")) or 0
    level2 = as_int(raw.get("ev_level2_evse_num")) or 0

    if dc_fast <= 0 and level2 <= 0:
        # JSON snapshots sometimes omit the summary counts but still list units.
        for unit in raw.get("ev_charging_units") or []:
            if not isinstance(unit, dict):
                continue
            level = str(unit.get("charging_level", "")).lower()
            if level in {"dc_fast", "dcfast", "3", "dc"}:
                dc_fast += 1
            elif level in {"2", "level2", "level_2"}:
                level2 += 1

    if dc_fast > 0:
        return "dc_fast"
    if level2 > 0:
        return "level2"
    return None


def observed_charge_rate_kw(raw: dict[str, Any]) -> float | None:
    """Return the best explicit kW value from the raw AFDC record, if any."""
    powers: list[float] = []

    for key in (
        "ev_dc_fast_power_kw",
        "ev_level2_power_kw",
        "charge_rate_kw",
        "power_kw",
    ):
        value = as_float(raw.get(key))
        if value is not None and value > 0:
            powers.append(value)

    for unit in raw.get("ev_charging_units") or []:
        if not isinstance(unit, dict):
            continue
        connectors = unit.get("connectors") or {}
        if not isinstance(connectors, dict):
            continue
        for connector in connectors.values():
            if not isinstance(connector, dict):
                continue
            value = as_float(connector.get("power_kw"))
            if value is not None and value > 0:
                powers.append(value)

    return max(powers) if powers else None


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    number = as_float(value)
    if number is None:
        return None
    return int(number)


def as_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
