from __future__ import annotations

import json
from pathlib import Path

from plotting.plugins.dma_iso_stress.parser import parse_dma_txt
from plotting.plugins.vsm_temperature_scan.core import VSMTemperatureScanProcessor


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_dma_iso_stress_fixture_matches_expected_output() -> None:
    fixture = FIXTURES_DIR / "dma_iso_stress" / "minimal_iso_stress.txt"
    expected = _load_json(FIXTURES_DIR / "dma_iso_stress" / "minimal_iso_stress_expected.json")

    parsed = parse_dma_txt(fixture)
    normalized = {
        str(stress): {"temperatures": list(temps), "strains": list(strains)}
        for stress, (temps, strains) in parsed.items()
    }

    assert normalized == expected


def test_vsm_temperature_scan_fixture_matches_expected_output() -> None:
    fixture = FIXTURES_DIR / "vsm_temperature_scan" / "minimal_scan.txt"
    expected = _load_json(FIXTURES_DIR / "vsm_temperature_scan" / "minimal_scan_expected.json")

    processor = VSMTemperatureScanProcessor()
    entries = processor.load([fixture])
    assert len(entries) == 1
    entry = entries[0]

    rows = entry.dataframe.sort_values(
        by=["section_index", "temperature"], kind="stable"
    ).to_dict(orient="records")
    normalized_rows = [
        {
            "temperature": float(row["temperature"]),
            "field": float(row["field"]),
            "signal": float(row["signal"]),
            "section": int(row["section"]),
            "section_index": int(row["section_index"]),
        }
        for row in rows
    ]

    assert entry.sample == expected["sample"]
    assert normalized_rows == expected["rows"]
