"""Route-level reserve and capacity cases (S4).

The 16.56 kWh edge that must consume 17 units is covered in ``test_model.py``.
Accepting or rejecting a whole trip still waits for the search.
"""

import pytest


@pytest.mark.skip(reason="TODO: route-level reserve and capacity cases are not implemented (S4)")
def test_constraints_not_implemented() -> None:
    """Placeholder until the battery-state search exists."""
