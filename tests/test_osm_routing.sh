cd /Users/sam/Code/ChargePlan
.venv/bin/python <<'PY'
from pathlib import Path
from data_pipeline.road_graph import haversine_metrics, road_metrics

DETROIT = {"lat": 42.3314, "lon": -83.0458}
DEARBORN = {"lat": 42.3223, "lon": -83.1763}
cache = Path(".osm_cache_osmnx_verify_run")
cache.mkdir(exist_ok=True)
for p in cache.glob("route_*.json"):
    p.unlink()

osm = road_metrics(DETROIT, DEARBORN, cache, backend="osmnx")
print(osm)
print(haversine_metrics(DETROIT["lat"], DETROIT["lon"], DEARBORN["lat"], DEARBORN["lon"]))
PY