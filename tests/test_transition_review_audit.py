from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pytest

from microwire_data_builder.transition_review_audit import (
    audit_transition_reviews,
    write_transition_review_audit,
)


@dataclass(frozen=True)
class _Entry:
    record_id: str
    title: str
    auto_values: Mapping[str, Any]


def test_transition_review_audit_reports_missing_auto_and_loop_asymmetry() -> None:
    entries = [
        _Entry(
            "a",
            "sample a 60 mA",
            {"As1": 12.0, "Af1": 20.0, "Ms1": 18.0, "Mf1": 8.0},
        ),
        _Entry(
            "b",
            "sample b 70 mA",
            {"As1": 14.0, "Af1": 24.0, "Ms2": 17.0, "Mf2": 7.0},
        ),
    ]
    reviews = {
        "a": {
            "status": "manual_adjusted",
            "included": True,
            "final_values_mA": {"As1": 12.5, "Ms2": 18.2},
            "manual_values_mA": {"As1": 12.5, "Ms2": 18.2},
        },
        "b": {
            "status": "accepted_auto",
            "included": True,
            "final_values_mA": {"Ms2": 17.0, "Mf2": 7.0},
        },
    }

    summary = audit_transition_reviews(entries, reviews)

    assert summary["total_records"] == 2
    assert summary["reviewed_records"] == 2
    assert summary["manual_adjusted_records"] == 1
    assert summary["missing_auto_counts"] == {"Ms2": 1}
    assert summary["paired_loop_failures"]["Ms2_missing_when_Ms1_present"] == 1
    assert summary["paired_loop_failures"]["Mf2_missing_when_Mf1_present"] == 1
    assert summary["paired_loop_failures"]["Ms1_missing_when_Ms2_present"] == 1
    assert summary["paired_loop_failures"]["Mf1_missing_when_Mf2_present"] == 1
    assert summary["manual_deltas"][0]["delta_mA"] == pytest.approx(0.5)


def test_transition_review_audit_writes_machine_readable_artifacts(tmp_path: Path) -> None:
    entries = [_Entry("a", "sample a 60 mA", {"As1": 12.0})]
    reviews = {
        "a": {
            "status": "manual_adjusted",
            "included": True,
            "final_values_mA": {"As1": 13.0, "Af1": 22.0},
        }
    }

    artifact = write_transition_review_audit(entries, reviews, tmp_path)

    assert artifact.summary_path.exists()
    assert artifact.manual_delta_path.read_text(encoding="utf-8").splitlines()[1].endswith(
        ",As1,12.0,13.0,1.0,manual_adjusted"
    )
    assert "Af1" in artifact.missing_auto_path.read_text(encoding="utf-8")
