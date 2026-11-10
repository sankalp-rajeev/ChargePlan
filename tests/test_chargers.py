"""AFDC snapshot loader and normalizer (H1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from data_pipeline.chargers import FALLBACK_RATES_KW, load_stations

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "afdc"
RAW = ROOT / "data" / "raw"


def test_load_stations_json_filters_and_applies_fallbacks() -> None:
    stations = load_stations(FIXTURES / "sample_stations.json")
    by_id = {station["id"]: station for station in stations}

    # Private, planned, non-ELEC, missing coords, and Level-1-only are dropped.
    assert set(by_id) == {"1001", "1002", "1008"}

    detroit = by_id["1001"]
    assert detroit["charger_class"] == "dc_fast"
    assert detroit["charge_rate_kw"] == 150.0
    assert detroit["used_fallback_rate"] is False
    assert detroit["network"] == "Electrify America"

    ann_arbor = by_id["1002"]
    assert ann_arbor["charger_class"] == "level2"
    assert ann_arbor["charge_rate_kw"] == FALLBACK_RATES_KW["level2"]
    assert ann_arbor["used_fallback_rate"] is True

    chicago = by_id["1008"]
    assert chicago["charger_class"] == "dc_fast"
    assert chicago["charge_rate_kw"] == FALLBACK_RATES_KW["dc_fast"]
    assert chicago["used_fallback_rate"] is True


def test_load_stations_csv_uses_class_fallbacks() -> None:
    stations = load_stations(FIXTURES / "sample_stations.csv")
    by_id = {station["id"]: station for station in stations}

    assert set(by_id) == {"2001", "2003"}
    assert by_id["2001"]["charger_class"] == "level2"
    assert by_id["2001"]["charge_rate_kw"] == FALLBACK_RATES_KW["level2"]
    assert by_id["2001"]["used_fallback_rate"] is True
    assert by_id["2003"]["charger_class"] == "dc_fast"
    assert by_id["2003"]["charge_rate_kw"] == FALLBACK_RATES_KW["dc_fast"]


def test_load_stations_rejects_missing_and_unsupported_files(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_stations(FIXTURES / "does_not_exist.json")

    bad = tmp_path / "stations.txt"
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported AFDC snapshot type"):
        load_stations(bad)


@pytest.mark.skipif(
    not any(RAW.glob("afdc_ev_midwest_*.json")),
    reason="local AFDC Midwest snapshot not present under data/raw",
)
def test_load_stations_midwest_snapshot_smoke() -> None:
    snapshot = sorted(RAW.glob("afdc_ev_midwest_*.json"))[-1]
    stations = load_stations(snapshot)
    assert len(stations) > 1000
    assert all(station["charge_rate_kw"] > 0 for station in stations)
    assert {station["charger_class"] for station in stations} <= set(FALLBACK_RATES_KW)
