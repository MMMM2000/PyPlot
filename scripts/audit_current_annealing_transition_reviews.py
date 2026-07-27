"""Write a current-annealing transition-review audit artifact.

This script intentionally avoids opening or mutating a live ``.pydpj``.  It can
consume a JSON export of review entries/reviews, or write a small synthetic
artifact that documents the report contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from microwire_data_builder.transition_review_audit import write_transition_review_audit


@dataclass(frozen=True)
class _AuditEntry:
    record_id: str
    title: str
    auto_values: Mapping[str, Any]


def _load_entries(path: Path) -> tuple[list[_AuditEntry], dict[str, dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    entries = [
        _AuditEntry(
            record_id=str(row.get("record_id") or ""),
            title=str(row.get("title") or row.get("record_id") or ""),
            auto_values=dict(row.get("auto_values_mA") or row.get("auto_values") or {}),
        )
        for row in payload.get("entries", [])
        if row.get("record_id")
    ]
    reviews = {
        str(record_id): dict(review)
        for record_id, review in dict(payload.get("reviews", {})).items()
    }
    return entries, reviews


def _load_project(path: Path) -> tuple[list[_AuditEntry], dict[str, dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    sections = payload.get("sections")
    if not isinstance(sections, Mapping):
        return [], {}
    annealing = sections.get("annealing")
    if not isinstance(annealing, Mapping):
        return [], {}
    extra = annealing.get("extra")
    if not isinstance(extra, Mapping):
        return [], {}
    transition_reviews = extra.get("transition_reviews")
    if not isinstance(transition_reviews, Mapping):
        return [], {}
    raw_records = transition_reviews.get("records", transition_reviews)
    if not isinstance(raw_records, Mapping):
        return [], {}
    reviews = {
        str(record_id): dict(review)
        for record_id, review in raw_records.items()
        if isinstance(review, Mapping)
    }
    entries = [
        _AuditEntry(
            record_id=record_id,
            title=str(
                review.get("graph_label")
                or review.get("source_path")
                or review.get("sample_key")
                or record_id
            ),
            auto_values=dict(review.get("auto_values_mA") or {}),
        )
        for record_id, review in reviews.items()
    ]
    return entries, reviews


def _synthetic_entries() -> tuple[list[_AuditEntry], dict[str, dict[str, Any]]]:
    entries = [
        _AuditEntry(
            record_id="anneal:sample-a:60mA",
            title="Sample A 60 mA 2loops",
            auto_values={"As1": 12.0, "Af1": 25.0, "Ms1": 19.0, "Mf1": 8.0, "Ms2": 18.5, "Mf2": 7.5},
        ),
        _AuditEntry(
            record_id="anneal:sample-a:70mA",
            title="Sample A 70 mA 2loops",
            auto_values={"As1": 13.0, "Af1": 28.0, "Ms1": 20.0, "Mf1": 9.0},
        ),
        _AuditEntry(
            record_id="anneal:sample-b:80mA",
            title="Sample B 80 mA 2loops",
            auto_values={"As1": 18.0, "Af1": 39.0, "Ms2": 27.0, "Mf2": 12.0},
        ),
    ]
    reviews = {
        "anneal:sample-a:60mA": {
            "status": "manual_adjusted",
            "included": True,
            "final_values_mA": {"As1": 12.5, "Af1": 25.5, "Ms1": 19.0, "Mf1": 8.1},
            "manual_values_mA": {"As1": 12.5, "Af1": 25.5, "Ms1": 19.0, "Mf1": 8.1},
        },
        "anneal:sample-a:70mA": {
            "status": "manual_adjusted",
            "included": True,
            "final_values_mA": {"Ms2": 20.2, "Mf2": 9.2},
            "manual_values_mA": {"Ms2": 20.2, "Mf2": 9.2},
        },
        "anneal:sample-b:80mA": {
            "status": "accepted_auto",
            "included": True,
            "final_values_mA": {"Ms2": 27.0, "Mf2": 12.0},
        },
    }
    return entries, reviews


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit current annealing transition review records."
    )
    parser.add_argument(
        "--input-json",
        type=Path,
        help=(
            "JSON with {'entries': [{'record_id','title','auto_values_mA'}], "
            "'reviews': {record_id: payload}}. If omitted, a synthetic "
            "contract artifact is written."
        ),
    )
    parser.add_argument(
        "--project",
        type=Path,
        help=(
            "Saved .pydpj JSON project to audit. The script reads only the "
            "annealing transition_reviews payload and does not modify the file."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("artifacts/current_annealing_transition_review_audit"),
        help="Output directory for JSON/CSV audit artifacts.",
    )
    args = parser.parse_args()
    if args.input_json and args.project:
        parser.error("Use only one of --input-json or --project.")
    if args.input_json:
        entries, reviews = _load_entries(args.input_json)
    elif args.project:
        entries, reviews = _load_project(args.project)
    else:
        entries, reviews = _synthetic_entries()
    artifact = write_transition_review_audit(entries, reviews, args.out)
    print(f"Wrote {artifact.summary_path}")
    print(f"Wrote {artifact.manual_delta_path}")
    print(f"Wrote {artifact.missing_auto_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
