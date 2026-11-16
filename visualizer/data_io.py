"""Load and validate the files the visualizer reads.

The visualizer reads ``canonical_graph.json`` and ``algorithm_result.json``, and
optionally ``trip_config.json`` for percent-of-battery displays.
It validates them against the frozen schemas and never runs an optimizer.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"


class VisualizerInputError(ValueError):
    """A graph or result file could not be read or failed validation."""


def load_graph(path: str | Path) -> dict:
    """Load a ``canonical_graph.json`` file and validate it against the schema."""
    return _load(path, "canonical_graph.schema.json")


def load_result(path: str | Path) -> dict:
    """Load an ``algorithm_result.json`` file and validate it against the schema."""
    return _load(path, "algorithm_result.schema.json")


def load_trip_config(path: str | Path) -> dict:
    """Load a ``trip_config.json`` file and validate it against the schema."""
    return _load(path, "trip_config.schema.json")


def _load(path: str | Path, schema_name: str) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise VisualizerInputError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except json.JSONDecodeError as exc:
        raise VisualizerInputError(f"{path} is not valid JSON: {exc}") from exc
    validate(payload, schema_name, label=str(path))
    return payload


def validate(payload: object, schema_name: str, *, label: str = "input") -> None:
    """Raise ``VisualizerInputError`` when ``payload`` violates the named schema."""
    schema = json.loads((CONTRACTS / schema_name).read_text(encoding="utf-8"))
    validator = jsonschema.validators.validator_for(schema)(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(map(str, e.absolute_path)))
    if errors:
        first = errors[0]
        where = "/".join(str(part) for part in first.absolute_path) or "<root>"
        raise VisualizerInputError(f"{label} violates {schema_name} at {where}: {first.message}")
