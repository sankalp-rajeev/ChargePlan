# AFDC snapshot (H1)

ChargePlan does not call the AFDC API at experiment time. Download a dated
snapshot once, keep it under `data/raw/` (gitignored), and point
`data_pipeline.chargers.load_stations` at that file.

## Download

NREL Alternative Fuel Stations API (public electric, available only):

```bash
DATE=$(date -u +%Y-%m-%d)
curl -sL \
  "https://developer.nlr.gov/api/alt-fuel-stations/v1.json?api_key=DEMO_KEY&fuel_type=ELEC&access=public&status=E&state=MI,OH,IN,IL" \
  -o "data/raw/afdc_ev_midwest_${DATE}.json"
shasum -a 256 "data/raw/afdc_ev_midwest_${DATE}.json" \
  | tee "data/raw/afdc_ev_midwest_${DATE}.sha256"
```

Replace `DEMO_KEY` with a personal NREL key for production downloads. CSV
exports from the same endpoint (`v1.csv`) are also accepted.

The default real-data region is Michigan plus adjacent Midwest states used
by the Detroit–Chicago and Detroit–Cleveland corridors (`MI,OH,IN,IL`).

## Current local snapshot

| File | Stations (API total) | SHA-256 |
| --- | --- | --- |
| `data/raw/afdc_ev_midwest_2026-11-09.json` | 6708 | `17de833e429875479a947da57b71155aae468d9012b71593be74131f64a3fd5a` |

## Fallback rates

When the snapshot has no usable kW value, `load_stations` assigns:

| Charger class | Fallback kW |
| --- | ---: |
| `dc_fast` | 150.0 |
| `level2` | 7.2 |

Stations that used a fallback set `used_fallback_rate` to `true`. Experiment H
can sweep these constants later.

## H2 — corridor and road metrics

```python
from data_pipeline import load_stations, select_candidates, road_metrics

stations = load_stations("data/raw/afdc_ev_midwest_2026-11-09.json")
origin = {"lat": 42.3314, "lon": -83.0458}       # Detroit
destination = {"lat": 41.8781, "lon": -87.6298}  # Chicago
candidates = select_candidates(stations, origin, destination)  # 40 mi corridor

metrics = road_metrics(origin, destination, cache_dir=".osm_cache")
# metrics["distance_miles"], metrics["drive_time_minutes"]
```

`road_metrics` prefers a cache hit, then OSMnx when installed (`pip install osmnx`),
otherwise a haversine × 1.25 estimate at 55 mph. Edges longer than
`100 kWh / 0.30 kWh/mi` are dropped by `filter_edges_by_battery` (Experiment E max).
