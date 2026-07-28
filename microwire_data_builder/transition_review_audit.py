"""Diagnostics for Microwire Builder transition-review records."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


PHASE_LABELS: tuple[str, ...] = ("As1", "Af1", "Ms1", "Mf1", "As2", "Af2", "Ms2", "Mf2")
REVIEWED_STATUSES = {"accepted_auto", "manual_adjusted", "no_transition", "excluded"}
INCLUDED_STATUSES = {"accepted_auto", "manual_adjusted"}
LOOP_PAIRS = (("Ms1", "Ms2"), ("Mf1", "Mf2"))


class TransitionReviewEntryLike(Protocol):
    record_id: str
    title: str
    auto_values: Mapping[str, Any]


@dataclass(frozen=True)
class TransitionReviewAuditArtifact:
    summary_path: Path
    manual_delta_path: Path
    missing_auto_path: Path


def _coerce_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    if number in {float("inf"), float("-inf")}:
        return None
    return number


def _clean_values(values: Mapping[str, Any] | None) -> dict[str, float]:
    if not isinstance(values, Mapping):
        return {}
    cleaned: dict[str, float] = {}
    for label in PHASE_LABELS:
        number = _coerce_float(values.get(label))
        if number is not None:
            cleaned[label] = number
    return cleaned


def _status(payload: Mapping[str, Any]) -> str:
    return str(payload.get("status") or "unreviewed").strip() or "unreviewed"


def audit_transition_reviews(
    entries: Sequence[TransitionReviewEntryLike],
    reviews: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare reviewed transition values with current automatic candidates."""

    status_counts: dict[str, int] = {}
    missing_auto_counts = {label: 0 for label in PHASE_LABELS}
    paired_loop_failures: dict[str, int] = {}
    manual_deltas: list[dict[str, Any]] = []
    missing_auto_details: list[dict[str, Any]] = []
    loop_asymmetry_details: list[dict[str, Any]] = []

    reviewed_count = 0
    included_count = 0
    auto_candidate_count = 0
    manual_adjusted_count = 0
    for entry in entries:
        payload = dict(reviews.get(entry.record_id, {}) or {})
        status = _status(payload)
        status_counts[status] = status_counts.get(status, 0) + 1
        if status in REVIEWED_STATUSES:
            reviewed_count += 1
        if status in INCLUDED_STATUSES and bool(payload.get("included", True)):
            included_count += 1
        if status == "manual_adjusted":
            manual_adjusted_count += 1

        auto_values = _clean_values(entry.auto_values)
        if auto_values:
            auto_candidate_count += 1
        final_values = _clean_values(payload.get("final_values_mA"))
        manual_values = _clean_values(payload.get("manual_values_mA"))
        reference_values = final_values or manual_values

        for label, final_value in reference_values.items():
            auto_value = auto_values.get(label)
            if auto_value is None:
                missing_auto_counts[label] += 1
                missing_auto_details.append(
                    {
                        "record_id": entry.record_id,
                        "title": entry.title,
                        "label": label,
                        "reviewed_mA": final_value,
                        "status": status,
                    }
                )
                continue
            manual_deltas.append(
                {
                    "record_id": entry.record_id,
                    "title": entry.title,
                    "label": label,
                    "auto_mA": auto_value,
                    "reviewed_mA": final_value,
                    "delta_mA": final_value - auto_value,
                    "status": status,
                }
            )

        for left, right in LOOP_PAIRS:
            left_present = left in auto_values
            right_present = right in auto_values
            if left_present == right_present:
                continue
            missing = right if left_present else left
            present = left if left_present else right
            key = f"{missing}_missing_when_{present}_present"
            paired_loop_failures[key] = paired_loop_failures.get(key, 0) + 1
            loop_asymmetry_details.append(
                {
                    "record_id": entry.record_id,
                    "title": entry.title,
                    "present_label": present,
                    "present_mA": auto_values[present],
                    "missing_label": missing,
                    "status": status,
                }
            )

    missing_auto_counts = {
        label: count for label, count in missing_auto_counts.items() if count
    }
    return {
        "total_records": len(entries),
        "reviewed_records": reviewed_count,
        "included_records": included_count,
        "manual_adjusted_records": manual_adjusted_count,
        "auto_candidate_records": auto_candidate_count,
        "status_counts": status_counts,
        "missing_auto_counts": missing_auto_counts,
        "manual_delta_count": len(manual_deltas),
        "manual_delta_abs_mA": _manual_delta_stats(manual_deltas),
        "paired_loop_failures": paired_loop_failures,
        "missing_auto_details": missing_auto_details,
        "manual_deltas": manual_deltas,
        "loop_asymmetry_details": loop_asymmetry_details,
    }


def _manual_delta_stats(rows: Sequence[Mapping[str, Any]]) -> dict[str, float | None]:
    deltas = [
        abs(float(row["delta_mA"]))
        for row in rows
        if _coerce_float(row.get("delta_mA")) is not None
    ]
    if not deltas:
        return {"max": None, "mean": None}
    return {
        "max": max(deltas),
        "mean": sum(deltas) / len(deltas),
    }


def write_transition_review_audit(
    entries: Sequence[TransitionReviewEntryLike],
    reviews: Mapping[str, Mapping[str, Any]],
    out_dir: Path | str,
) -> TransitionReviewAuditArtifact:
    """Write a machine-readable review audit summary and detail tables."""

    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    summary = audit_transition_reviews(entries, reviews)
    summary_path = output / "transition_review_audit.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    manual_delta_path = output / "manual_deltas.csv"
    _write_rows(
        manual_delta_path,
        summary["manual_deltas"],
        ("record_id", "title", "label", "auto_mA", "reviewed_mA", "delta_mA", "status"),
    )
    missing_auto_path = output / "missing_auto_details.csv"
    _write_rows(
        missing_auto_path,
        summary["missing_auto_details"],
        ("record_id", "title", "label", "reviewed_mA", "status"),
    )
    return TransitionReviewAuditArtifact(
        summary_path=summary_path,
        manual_delta_path=manual_delta_path,
        missing_auto_path=missing_auto_path,
    )


def _write_rows(path: Path, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns))
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})
