"""SOC chart and drive/charge timeline.

Owned by Shannon (H7). Driving decreases SOC. Charging increases it.
The timeline lists each event with duration and SOC before and after.

TODO: implement the chart and the timeline (H7).
"""


def render_soc_chart(result: dict):
    """Return an SOC-versus-distance or SOC-versus-time chart.

    TODO: implement the chart (H7).
    """
    raise NotImplementedError


def render_timeline(result: dict) -> list[str]:
    """Return one display line per drive or charge event.

    TODO: implement the timeline (H7).
    """
    raise NotImplementedError
