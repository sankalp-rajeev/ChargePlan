"""Charging-station ingest from a dated AFDC snapshot.

Owned by Shannon (H1). Download once, filter to public EV chargers, and
normalize location and charging rate. Where power is missing, assign a
documented fallback by charger class and record which stations used it.

TODO: implement the snapshot loader and normalizer (H1). No optimizer code.
"""


def load_stations(snapshot_path: str) -> list[dict]:
    """Return normalized station records from a local AFDC snapshot.

    TODO: implement filtering and fallback rates (H1).
    """
    raise NotImplementedError
