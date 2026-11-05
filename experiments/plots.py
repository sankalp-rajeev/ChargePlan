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
    "trip_time_vs_battery_capacity.png",
    "states_astar_vs_labelsetting.png",
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


def write_plots(rows_by_file: dict[str, list[dict[str, str]]], directory: Path) -> list[Path]:
    """Draw the charts that have measured rows. Return the paths written."""
    directory.mkdir(parents=True, exist_ok=True)
    soc = rows_by_file["soc_resolution.csv"]
    scaling = rows_by_file["charger_scaling.csv"]
    capacity = rows_by_file["battery_capacity.csv"]
    journal = rows_by_file["astar_comparison.csv"]
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
        _line_plot(
            directory / "runtime_vs_chargers.png",
            "RUNTIME VS CHARGERS",
            "CHARGERS",
            "RUNTIME MS",
            _algorithm_series(scaling, "chargers", "runtime_ms"),
            log_x=False,
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
        _journal_plot(directory / "states_astar_vs_labelsetting.png", journal),
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


def _algorithm_series(
    rows: list[dict[str, str]],
    x_key: str,
    y_key: str,
) -> list[tuple[str, tuple[int, int, int], list[tuple[float, float]]]]:
    series = []
    for algorithm, label, color in (
        ("label_setting", "LABEL SETTING", _LABEL),
        ("astar", "ASTAR", _ASTAR),
    ):
        points = [
            (float(row[x_key]), float(row[y_key]))
            for row in rows
            if row["algorithm"] == algorithm and row["status"] in {"FEASIBLE", "INFEASIBLE"}
        ]
        points.sort()
        if points:
            series.append((label, color, points))
    return series


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


def _journal_pairs(rows: list[dict[str, str]], prefix: str) -> list[tuple[str, int, int]]:
    grouped: dict[str, dict[str, str]] = {}
    for row in rows:
        if row["experiment"] != "J" or not row["instance_id"].startswith(prefix):
            continue
        if row["status"] not in {"FEASIBLE", "INFEASIBLE"}:
            continue
        grouped.setdefault(row["instance_id"], {})[row["algorithm"]] = row["states_expanded"]
    pairs = []
    for instance_id in sorted(grouped):
        algorithms = grouped[instance_id]
        if "label_setting" in algorithms and "astar" in algorithms:
            pairs.append(
                (instance_id, int(algorithms["label_setting"]), int(algorithms["astar"]))
            )
    return pairs


def _line_plot(
    path: Path,
    title: str,
    xlabel: str,
    ylabel: str,
    series: list[tuple[str, tuple[int, int, int], list[tuple[float, float]]]],
    *,
    log_x: bool,
    zero_y: bool = True,
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
    )
    canvas.save(path)
    return path


def _journal_plot(path: Path, rows: list[dict[str, str]]) -> Path:
    width, height = 880, 720
    canvas = _Canvas(width, height)
    canvas.text(48, 16, "ASTAR VS LABEL SETTING STATES", _INK, 2)
    canvas.bars(
        "CHARGERS",
        "STATES",
        _journal_pairs(rows, "scale-"),
        48,
        48,
        width - 36,
        360,
    )
    canvas.bars(
        "SOC SAMPLE",
        "STATES",
        _journal_pairs(rows, "soc-sample-"),
        48,
        390,
        width - 36,
        height - 40,
    )
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
    ) -> None:
        self.text(left, top - 28, title, _INK, 2)
        self._frame(left, top, right, bottom)
        points = [point for _, _, data in series for point in data]
        if not points:
            self.text(left + 8, top + 8, "NO MEASURED POINTS", _INK, 2)
            return
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
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
        for name, color, data in series:
            ordered = sorted(data)
            mapped = [
                self._map(x, y, left, top, right, bottom, x_min, x_max, y_min, y_max, log_x)
                for x, y in ordered
            ]
            for start, end in zip(mapped, mapped[1:], strict=False):
                self.line(start[0], start[1], end[0], end[1], color)
            for x, y in mapped:
                self.marker(x, y, color)
            del name
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

    def bars(
        self,
        xlabel: str,
        ylabel: str,
        pairs: list[tuple[str, int, int]],
        left: int,
        top: int,
        right: int,
        bottom: int,
    ) -> None:
        self._frame(left, top, right, bottom)
        if not pairs:
            self.text(left + 8, top + 8, "NO MEASURED POINTS", _INK, 2)
            return
        peak = max(max(label, guided) for _, label, guided in pairs)
        peak = peak * 1.1 if peak else 1
        slot = (right - left) / len(pairs)
        for index, (instance_id, label_states, astar_states) in enumerate(pairs):
            center = left + slot * (index + 0.5)
            self._bar(center - 8, label_states / peak, top, bottom, _LABEL)
            self._bar(center + 2, astar_states / peak, top, bottom, _ASTAR)
            short = (
                instance_id.replace("scale-", "")
                .replace("soc-sample-", "S")
                .replace("soc-roundup-", "R")
            )
            self.text(int(center) - 8, bottom + 6, short, _INK, 1)
        self.text(left, bottom + 20, xlabel, _INK, 1)
        self.text(left, top + 4, ylabel, _INK, 1)
        self.line(right - 150, top + 12, right - 134, top + 12, _LABEL)
        self.text(right - 130, top + 8, "LABEL", _INK, 1)
        self.line(right - 150, top + 28, right - 134, top + 28, _ASTAR)
        self.text(right - 130, top + 24, "ASTAR", _INK, 1)

    def _bar(
        self,
        x: int,
        fraction: float,
        top: int,
        bottom: int,
        color: tuple[int, int, int],
    ) -> None:
        y0 = bottom - round(fraction * (bottom - top))
        for y in range(y0, bottom):
            self.line(x, y, x + 6, y, color)

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
