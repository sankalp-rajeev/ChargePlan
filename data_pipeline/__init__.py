"""AFDC charging-station pipeline.

Shannon owns this package (H1–H3). It writes ``canonical_graph.json`` and
does not import the optimizer search.
"""

from data_pipeline.chargers import FALLBACK_RATES_KW, load_stations

__all__ = ["FALLBACK_RATES_KW", "load_stations"]
