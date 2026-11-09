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
