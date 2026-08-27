"""Restore current-annealing review metadata into a copied Builder project.

The source projects are opened read-only.  The output must not already exist,
and all non-annealing package entries are preserved from the current project.
Reviews are matched only by a unique, normalized full source path.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from microwire_data_builder.project_package import (
    inspect_project_package,
    write_project_package,
)
from microwire_data_builder.safe_codec import decode_envelope
from microwire_data_builder.ui import (
    AnnealingSection,
    TRANSITION_REVIEW_EXTRA_KEY,
    _transition_record_id_for_annealing_record,
)


def _path_key(value: object) -> str:
    return str(value or "").strip().replace("\\", "/").casefold()


def _review_records(section: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    extra = section.get("extra")
    if not isinstance(extra, Mapping):
        return {}
    envelope = extra.get(TRANSITION_REVIEW_EXTRA_KEY)
    if not isinstance(envelope, Mapping):
        return {}
    raw = envelope.get("records", envelope)
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(record_id): dict(review)
        for record_id, review in raw.items()
        if isinstance(review, Mapping)
    }


def recover(current: Path, history: Path, output: Path, manifest_path: Path) -> None:
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {output}")
    current_index = inspect_project_package(current, verify_entries=True)
    history_index = inspect_project_package(history, verify_entries=True)
    current_section = current_index.read_section("annealing", load_payloads=False)
    history_section = history_index.read_section("annealing", load_payloads=False)
    historical_reviews = _review_records(history_section)

    encoded_records = current_index.read_payload("annealing", "annealing_records")
    records = decode_envelope(encoded_records)
    if not isinstance(records, list):
        raise TypeError("Current annealing_records payload did not decode to a list")

    records_by_path: dict[str, list[object]] = {}
    for record in records:
        path = getattr(record, "path", None)
        records_by_path.setdefault(_path_key(path), []).append(record)

    helper = AnnealingSection.__new__(AnnealingSection)
    remapped: dict[str, dict[str, Any]] = {}
    unmatched: list[str] = []
    ambiguous: list[str] = []
    collisions: list[str] = []
    mapping: list[dict[str, str]] = []
    for old_id, review in historical_reviews.items():
        matches = records_by_path.get(_path_key(review.get("source_path")), [])
        if not matches:
            unmatched.append(old_id)
            continue
        if len(matches) != 1:
            ambiguous.append(old_id)
            continue
        record = matches[0]
        new_id = _transition_record_id_for_annealing_record(record)
        if new_id in remapped:
            collisions.append(new_id)
            continue
        cleaned = helper._clean_transition_review_payload(new_id, review)
        if not cleaned:
            unmatched.append(old_id)
            continue
        preserved_updated_at = cleaned.get("updated_at")
        cleaned.update(helper._transition_review_metadata(new_id, record))
        if preserved_updated_at:
            cleaned["updated_at"] = preserved_updated_at
        remapped[new_id] = cleaned
        mapping.append({"old_id": old_id, "new_id": new_id, "source_path": str(record.path)})

    if unmatched or ambiguous or collisions or len(remapped) != len(historical_reviews):
        raise RuntimeError(
            "Recovery is not one-to-one: "
            f"historical={len(historical_reviews)} remapped={len(remapped)} "
            f"unmatched={len(unmatched)} ambiguous={len(ambiguous)} "
            f"collisions={len(collisions)}"
        )

    extra = current_section.get("extra")
    current_section["extra"] = dict(extra) if isinstance(extra, Mapping) else {}
    current_section["extra"][TRANSITION_REVIEW_EXTRA_KEY] = {
        "schema_version": 1,
        "records": remapped,
    }
    payload = current_index.project_header()
    payload["sections"] = {"annealing": current_section}
    recovered_index = write_project_package(
        output,
        payload,
        replace_existing=False,
        source_index=current_index,
        loaded_sections={"annealing"},
    )

    preserved_entries = {
        path: descriptor.sha256
        for path, descriptor in current_index.entries.items()
        if not path.startswith("sections/annealing/")
    }
    recovered_preserved = {
        path: recovered_index.entries[path].sha256 for path in preserved_entries
    }
    if recovered_preserved != preserved_entries:
        raise RuntimeError("A non-annealing package entry changed during recovery")

    check_section = recovered_index.read_section("annealing", load_payloads=False)
    check_reviews = _review_records(check_section)
    if check_reviews != remapped:
        raise RuntimeError("Recovered review metadata failed round-trip validation")
    check_records = decode_envelope(
        recovered_index.read_payload("annealing", "annealing_records")
    )
    if not isinstance(check_records, list) or len(check_records) != len(records):
        raise RuntimeError("Annealing measurement payload changed during recovery")

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "current_project": str(current.resolve()),
        "history_project": str(history.resolve()),
        "output_project": str(output.resolve()),
        "measurement_records": len(records),
        "historical_reviews": len(historical_reviews),
        "restored_reviews": len(remapped),
        "status_counts": dict(Counter(str(row.get("status") or "") for row in remapped.values())),
        "preserved_non_annealing_entries": len(preserved_entries),
        "mapping": mapping,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in manifest.items() if key != "mapping"}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Restore annealing transition reviews into a copied .pydpj."
    )
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    recover(args.current, args.history, args.output, args.manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
