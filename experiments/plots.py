"""PNG charts for the measurements in the Section 23 CSV files.

Greedy-versus-optimizer and feasibility-versus-density are not drawn. Those
series are Experiments B and G, and this run has no measurements for them.
Matplotlib is not a project dependency. The charts are written with the
standard library.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

from experiments.astar_summary import instance_pairs
from experiments.scaling import quartiles

# 5x7 glyphs, row-major, '1' is ink. Unknown characters leave a blank cell.
_FONT = {
    " ": ("00000",) * 7,
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00111", "00010", "00010", "00010", "00010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
}

MEASURED_PLOTS = (
    "runtime_vs_soc_resolution.png",
    "states_vs_soc_resolution.png",
    "objective_error_vs_soc_resolution.png",
    "runtime_vs_chargers.png",
    "states_vs_chargers.png",
    "trip_time_vs_battery_capacity.png",
    "states_astar_vs_labelsetting.png",
    "astar_savings_by_factor.png",
)

# Experiments B and G have no measured rows, so these Section 23 names are not written.
OMITTED_PLOTS = (
    "greedy_vs_optimizer.png",
    "feasibility_vs_charger_density.png",
)

_LABEL = (20, 90, 160)
_ASTAR = (200, 90, 30)
_ROUNDUP = (36, 120, 72)
_INK = (35, 35, 35)
_GRID = (226, 226, 226)
_WHITE = (255, 255, 255)
Series = list[tuple[str, tuple[int, int, int], list[tuple[float, float]]]]
# (color, [(x, y)], pixel shift) and (color, [(x, low, high)], pixel shift).
Scatter = tuple[tuple[int, int, int], list[tuple[float, float]], int]
Whisker = tuple[tuple[int, int, int], list[tuple[float, float, float]], int]


def write_plots(rows_by_file: dict[str, list[dict[str, str]]], directory: Path) -> list[Path]:
    """Draw the charts that have measured rows. Return the paths written."""
    directory.mkdir(parents=True, exist_ok=True)
    soc = rows_by_file["soc_resolution.csv"]
    scaling = rows_by_file["charger_scaling.csv"]
    capacity = rows_by_file["battery_capacity.csv"]
    astar = rows_by_file["astar_comparison.csv"]
    written = [
        _line_plot(
            directory / "runtime_vs_soc_resolution.png",
            "RUNTIME VS SOC STEP",
            "SOC STEP KWH",
            "RUNTIME MS",
            _soc_series(soc, "runtime_ms"),
            log_x=True,
        ),
        _line_plot(
            directory / "states_vs_soc_resolution.png",
            "STATES VS SOC STEP",
            "SOC STEP KWH",
            "STATES",
            _soc_series(soc, "states_expanded"),
            log_x=True,
        ),
        _line_plot(
            directory / "objective_error_vs_soc_resolution.png",
            "OBJECTIVE ERROR VS SOC STEP",
            "SOC STEP KWH",
            "ERROR MIN",
            _error_series(soc),
            log_x=True,
        ),
        _scaling_plot(
            directory / "runtime_vs_chargers.png",
            "MEDIAN RUNTIME VS CHARGERS",
            "RUNTIME MS",
            scaling,
            "runtime_ms",
        ),
        _scaling_plot(
            directory / "states_vs_chargers.png",
            "MEDIAN STATES VS CHARGERS",
            "STATES",
            scaling,
            "states_expanded",
        ),
        _line_plot(
            directory / "trip_time_vs_battery_capacity.png",
            "TRIP TIME VS CAPACITY",
            "CAPACITY KWH",
            "TRIP TIME MIN",
            _feasible_series(capacity, "capacity_kwh", "total_time_minutes"),
            log_x=False,
            zero_y=False,
        ),
        _astar_scatter_plot(directory / "states_astar_vs_labelsetting.png", astar),
        _astar_savings_plot(directory / "astar_savings_by_factor.png", astar),
    ]
    return written


def _soc_series(rows: list[dict[str, str]], field: str) -> Series:
    series = []
    for family, color in (("sample", _LABEL), ("roundup", _ROUNDUP)):
        points = []
        for row in rows:
            if row["algorithm"] != "label_setting":
                continue
            if _note(row).get("family") != family:
                continue
            if row["status"] not in {"FEASIBLE", "INFEASIBLE"}:
                continue
            points.append((float(row["soc_step_kwh"]), float(row[field])))
        points.sort()
        if points:
            series.append((family.upper(), color, points))
    return series


def _error_series(rows: list[dict[str, str]]) -> Series:
    series = []
    for family, color in (("sample", _LABEL), ("roundup", _ROUNDUP)):
        points = []
        for row in rows:
            if row["algorithm"] != "label_setting":
                continue
            info = _note(row)
            if info.get("family") != family:
                continue
            raw = info.get("objective_error_minutes", "")
            if raw == "":
                continue
            points.append((float(row["soc_step_kwh"]), float(raw)))
        points.sort()
        if points:
            series.append((family.upper(), color, points))
    return series


def _scaling_plot(
    path: Path,
    title: str,
    ylabel: str,
    rows: list[dict[str, str]],
    field: str,
) -> Path:
    """Plot the per-count median, interquartile whiskers, and every replicate.

    Each charger count has several instances. A line through raw points would
    hide that, so the line joins the medians and the dots show each replicate.
    """
    series: Series = []
    scatter: list[Scatter] = []
    whiskers: list[Whisker] = []
    for algorithm, label, color, shift in (
        ("label_setting", "LABEL SETTING", _LABEL, -6),
        ("astar", "ASTAR", _ASTAR, 6),
    ):
        grouped: dict[float, list[float]] = {}
        for row in rows:
            if row["algorithm"] != algorithm or row["status"] not in {"FEASIBLE", "INFEASIBLE"}:
                continue
            grouped.setdefault(float(row["chargers"]), []).append(float(row[field]))
        if not grouped:
            continue
        medians, bars, dots = [], [], []
        for count in sorted(grouped):
            low, middle, high = quartiles(grouped[count])
            medians.append((count, middle))
            bars.append((count, low, high))
            dots.extend((count, value) for value in grouped[count])
        series.append((label, color, medians))
        whiskers.append((color, bars, shift))
        scatter.append((_light(color), dots, shift))
    return _line_plot(
        path,
        title,
        "CHARGERS",
        ylabel,
        series,
        log_x=False,
        scatter=scatter,
        whiskers=whiskers,
    )


def _light(color: tuple[int, int, int]) -> tuple[int, int, int]:
    red, green, blue = (round(channel + (255 - channel) * 0.55) for channel in color)
    return red, green, blue


def _feasible_series(
    rows: list[dict[str, str]],
    x_key: str,
    y_key: str,
) -> list[tuple[str, tuple[int, int, int], list[tuple[float, float]]]]:
    points = [
        (float(row[x_key]), float(row[y_key]))
        for row in rows
        if row["algorithm"] == "label_setting" and row["status"] == "FEASIBLE"
    ]
    points.sort()
    if not points:
        return []
    return [("LABEL SETTING", _LABEL, points)]


def _line_plot(
    path: Path,
    title: str,
    xlabel: str,
    ylabel: str,
    series: list[tuple[str, tuple[int, int, int], list[tuple[float, float]]]],
    *,
    log_x: bool,
    zero_y: bool = True,
    scatter: list[Scatter] | None = None,
    whiskers: list[Whisker] | None = None,
) -> Path:
    width, height = 880, 520
    canvas = _Canvas(width, height)
    canvas.panel(
        title,
        xlabel,
        ylabel,
        series,
        48,
        36,
        width - 36,
        height - 56,
        log_x=log_x,
        zero_y=zero_y,
        scatter=scatter,
        whiskers=whiskers,
    )
    canvas.save(path)
    return path


_GEOMETRY_COLORS = {"straight": _LABEL, "curved": _ROUNDUP, "detour": _ASTAR}
_DIAGONAL = (120, 120, 120)


def _astar_scatter_plot(path: Path, rows: list[dict[str, str]]) -> Path:
    """One point per Experiment J instance. A point below the diagonal means A* expanded fewer."""
    pairs = instance_pairs(rows)
    series: Series = []
    for geometry, color in _GEOMETRY_COLORS.items():
        points = [
            (float(p["ls_states"]), float(p["astar_states"]))
            for p in pairs
            if p["geometry"] == geometry
        ]
        if points:
            series.append((geometry.upper(), color, points))
    if series:
        top = max(max(x for x, _ in data) for _, _, data in series)
        series.append(("SAME STATES", _DIAGONAL, [(0.0, 0.0), (top, top)]))
    width, height = 880, 520
    canvas = _Canvas(width, height)
    canvas.panel(
        "ASTAR VS LABEL SETTING STATES",
        "LABEL SETTING STATES",
        "ASTAR STATES",
        series,
        48,
        36,
        width - 36,
        height - 56,
        log_x=False,
        markers_only=frozenset(geometry.upper() for geometry in _GEOMETRY_COLORS),
    )
    canvas.save(path)
    return path


def _astar_savings_plot(path: Path, rows: list[dict[str, str]]) -> Path:
    """Median state reduction and interquartile range for every level of every factor."""
    pairs = [p for p in instance_pairs(rows) if p["feasible"] and p["reduction"] is not None]
    groups = (
        ("N", _LABEL, [(str(n), lambda p, n=n: p["size"] == n) for n in (10, 25, 50)]),
        (
            "",
            _ROUNDUP,
            [(g.upper(), lambda p, g=g: p["geometry"] == g) for g in _GEOMETRY_COLORS],
        ),
        ("SOC", _ASTAR, [(s, lambda p, s=s: p["soc_step"] == s) for s in ("1", "4")]),
        (
            "",
            _DIAGONAL,
            [
                ("HOMOG", lambda p: p["speed"] == "homogeneous"),
                ("HETERO", lambda p: p["speed"] == "heterogeneous"),
            ],
        ),
    )
    bars = []
    for prefix, color, levels in groups:
        for label, test in levels:
            values = [p["reduction"] for p in pairs if test(p)]
            if values:
                low, middle, high = quartiles(values)
                bars.append((f"{prefix} {label}".strip(), color, middle, low, high))
    width, height = 880, 520
    canvas = _Canvas(width, height)
    canvas.text(48, 8, "MEDIAN STATE REDUCTION BY FACTOR", _INK, 2)
    canvas.reduction_bars(bars, 48, 48, width - 36, height - 56)
    canvas.save(path)
    return path


class _Canvas:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.rows = [bytearray([255, 255, 255]) * width for _ in range(height)]

    def save(self, path: Path) -> None:
        path.write_bytes(_png(self.width, self.height, self.rows))

    def dot(self, x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            start = x * 3
            self.rows[y][start : start + 3] = bytes(color)

    def text(self, x: int, y: int, message: str, color: tuple[int, int, int], scale: int) -> None:
        cursor = x
        for character in message:
            glyph = _FONT.get(character, _FONT[" "])
            for row, bits in enumerate(glyph):
                for col, bit in enumerate(bits):
                    if bit != "1":
                        continue
                    for dy in range(scale):
                        for dx in range(scale):
                            self.dot(cursor + col * scale + dx, y + row * scale + dy, color)
            cursor += 6 * scale

    def line(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        color: tuple[int, int, int],
    ) -> None:
        start_x, start_y = round(x0), round(y0)
        end_x, end_y = round(x1), round(y1)
        steps = max(abs(end_x - start_x), abs(end_y - start_y), 1)
        for step in range(steps + 1):
            x = round(start_x + (end_x - start_x) * step / steps)
            y = round(start_y + (end_y - start_y) * step / steps)
            self.dot(x, y, color)

    def marker(self, x: int, y: int, color: tuple[int, int, int]) -> None:
        for dy in range(-3, 4):
            for dx in range(-3, 4):
                if dx * dx + dy * dy <= 8:
                    self.dot(x + dx, y + dy, color)

    def panel(
        self,
        title: str,
        xlabel: str,
        ylabel: str,
        series: list[tuple[str, tuple[int, int, int], list[tuple[float, float]]]],
        left: int,
        top: int,
        right: int,
        bottom: int,
        *,
        log_x: bool,
        zero_y: bool = True,
        scatter: list[Scatter] | None = None,
        whiskers: list[Whisker] | None = None,
        markers_only: frozenset[str] = frozenset(),
    ) -> None:
        """Draw ``series`` as lines. ``scatter`` dots and ``whiskers`` mark each x's spread.

        A series named in ``markers_only`` is drawn as points with no connecting line.
        """
        scatter = scatter or []
        whiskers = whiskers or []
        self.text(left, top - 28, title, _INK, 2)
        self._frame(left, top, right, bottom)
        points = [point for _, _, data in series for point in data]
        if not points:
            self.text(left + 8, top + 8, "NO MEASURED POINTS", _INK, 2)
            return
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        ys.extend(y for _, data, _ in scatter for _, y in data)
        ys.extend(high for _, data, _ in whiskers for _, _, high in data)
        ys.extend(low for _, data, _ in whiskers for _, low, _ in data)
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = (0.0, max(ys) * 1.1) if zero_y else (min(ys) * 0.98, max(ys) * 1.02)
        if math.isclose(y_min, y_max):
            y_max = y_min + 1
        if log_x:
            x_min = math.log10(min(value for value in xs if value > 0))
            x_max = math.log10(max(xs))
        if math.isclose(x_min, x_max):
            x_max = x_min + 1
        for fraction in (0.25, 0.5, 0.75):
            y = round(bottom - fraction * (bottom - top))
            self.line(left, y, right, y, _GRID)
        for color, data, shift in scatter:
            for x, y in data:
                px, py = self._map(
                    x, y, left, top, right, bottom, x_min, x_max, y_min, y_max, log_x
                )
                self.dot(px + shift, py, color)
                self.dot(px + shift + 1, py, color)
                self.dot(px + shift, py + 1, color)
        for color, data, shift in whiskers:
            for x, low, high in data:
                px, low_y = self._map(
                    x, low, left, top, right, bottom, x_min, x_max, y_min, y_max, log_x
                )
                _, high_y = self._map(
                    x, high, left, top, right, bottom, x_min, x_max, y_min, y_max, log_x
                )
                self.line(px + shift, low_y, px + shift, high_y, color)
                self.line(px + shift - 3, low_y, px + shift + 3, low_y, color)
                self.line(px + shift - 3, high_y, px + shift + 3, high_y, color)
        for name, color, data in series:
            ordered = sorted(data)
            mapped = [
                self._map(x, y, left, top, right, bottom, x_min, x_max, y_min, y_max, log_x)
                for x, y in ordered
            ]
            if name not in markers_only:
                for start, end in zip(mapped, mapped[1:], strict=False):
                    self.line(start[0], start[1], end[0], end[1], color)
            if name == "SAME STATES":
                continue
            for x, y in mapped:
                self.marker(x, y, color)
        self.text(
            left,
            bottom + 8,
            f"{xlabel}  {_axis_num(min(xs))}-{_axis_num(max(xs))}",
            _INK,
            1,
        )
        self.text(
            left,
            top + 4,
            f"{ylabel}  {_axis_num(min(ys))}-{_axis_num(max(ys))}",
            _INK,
            1,
        )
        legend_x = right - 150
        for index, (name, color, _) in enumerate(series):
            y = top + 8 + index * 16
            self.line(legend_x, y + 4, legend_x + 16, y + 4, color)
            self.text(legend_x + 20, y, name, _INK, 1)

    def reduction_bars(
        self,
        bars: list[tuple[str, tuple[int, int, int], float, float, float]],
        left: int,
        top: int,
        right: int,
        bottom: int,
    ) -> None:
        """Bars at ``median`` with interquartile whiskers. Each item is
        ``(label, color, median, q1, q3)``. A negative median hangs below the zero line.
        """
        self._frame(left, top, right, bottom)
        if not bars:
            self.text(left + 8, top + 8, "NO MEASURED POINTS", _INK, 2)
            return
        low = min(0.0, min(item[3] for item in bars))
        high = max(item[4] for item in bars) * 1.15
        if math.isclose(low, high):
            high = low + 1

        def row(value: float) -> int:
            return bottom - round((value - low) / (high - low) * (bottom - top))

        zero = row(0.0)
        self.line(left, zero, right, zero, _INK)
        slot = (right - left) / len(bars)
        for index, (label, color, middle, first, third) in enumerate(bars):
            center = round(left + slot * (index + 0.5))
            for y in range(min(zero, row(middle)), max(zero, row(middle)) + 1):
                self.line(center - 10, y, center + 10, y, color)
            self.line(center, row(first), center, row(third), _INK)
            self.line(center - 4, row(first), center + 4, row(first), _INK)
            self.line(center - 4, row(third), center + 4, row(third), _INK)
            value = _axis_num(middle)
            self.text(center - 3 * len(value), row(third) - 12, value, _INK, 1)
            self.text(center - 3 * len(label), bottom + 8, label, _INK, 1)
        self.text(left + 4, top + 4, "STATES REDUCTION PCT  BAR MEDIAN  WHISKERS IQR", _INK, 1)

    def _frame(self, left: int, top: int, right: int, bottom: int) -> None:
        self.line(left, top, left, bottom, _INK)
        self.line(left, bottom, right, bottom, _INK)

    def _map(
        self,
        x: float,
        y: float,
        left: int,
        top: int,
        right: int,
        bottom: int,
        x_min: float,
        x_max: float,
        y_min: float,
        y_max: float,
        log_x: bool,
    ) -> tuple[int, int]:
        position = math.log10(x) if log_x else x
        px = left + round((position - x_min) / (x_max - x_min) * (right - left))
        py = bottom - round((y - y_min) / (y_max - y_min) * (bottom - top))
        return px, py


def _axis_num(value: float) -> str:
    return format(value, ".4g")


def _note(row: dict[str, str]) -> dict[str, str]:
    found = {}
    for part in row.get("note", "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        found[key.strip()] = value.strip()
    return found


def _png(width: int, height: int, rows: list[bytearray]) -> bytes:
    def chunk(tag: bytes, payload: bytes) -> bytes:
        check = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", check)

    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
