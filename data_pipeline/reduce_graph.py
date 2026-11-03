"""Reduce a corridor to the optimizer's node set.

Owned by Shannon (H2). Keep the origin, the destination, and candidate
chargers near the origin–destination line. Omit edges whose energy exceeds
the largest battery capacity in the experiment plan.

TODO: implement corridor filtering (H2).
"""


def select_candidates(stations: list[dict], origin: dict, destination: dict) -> list[dict]:
    """Return the charger subset that will become graph nodes.

    TODO: implement the corridor rule (H2).
    """
    raise NotImplementedError
