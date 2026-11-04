"""Route-level reserve and capacity cases.

The search covers these in ``test_optimizer.py`` (reserve floor, capacity cap,
and the 55.2-mile trip that rounds to 17 kWh). This file stays skipped until
S4 gathers the full T1–T16 suite, including A* agreement and the greedy
failure case.
"""

import pytest


@pytest.mark.skip(
    reason="S4 will collect T1-T16; route-level cases already run in test_optimizer.py"
)
def test_constraints_not_implemented() -> None:
    """S4 placeholder. Reserve and capacity trips already run in test_optimizer.py."""
