"""Portable transition-review records shared by loggers and Builder.

The raw measurement remains immutable.  This module owns the small reviewed
derivative stored beside it and deliberately has no Qt or Builder dependency.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd


SCHEMA_NAME = "microwire_transition_review"
SCHEMA_VERSION = 1
SIDECAR_NAME = "transition_review.json"
EXPERIMENT_FAMILIES = {"current_annealing", "tma"}
REVIEW_STATUSES = {
    "unreviewed",
    "accepted_auto",
    "manual_adjusted",
    "no_transition",
    "excluded",
    "needs_attention",
}
INCLUDED_STATUSES = {"accepted_auto", "manual_adjusted"}
ANALYSIS_INCLUDED_STATUSES = {
    "accepted_auto",
    "manual_adjusted",
    "no_transition",
}
TRANSITION_LABELS = {
    "As",
    "Af",
    "Ms",
    "Mf",
    "As1",
    "Af1",
    "Ms1",
    "Mf1",
    "As2",
    "Af2",
    "Ms2",
    "Mf2",
}


class TransitionReviewError(ValueError):
    """Raised when a portable transition-review record is invalid."""


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dataframe_fingerprint(
    frame: pd.DataFrame,
    *,
    namespace: str,
    columns: Sequence[str] | None = None,
) -> str:
    """Return a path-independent identity for normalized measurement values."""

    selected = list(columns) if columns is not None else list(frame.columns)
    selected = [column for column in selected if column in frame.columns]
    canonical = frame.loc[:, selected].copy()
    for column in canonical.columns:
        numeric = pd.to_numeric(canonical[column], errors="coerce")
        if int(numeric.notna().sum()) == int(canonical[column].notna().sum()):
            canonical[column] = numeric.astype("float64")
        else:
            canonical[column] = canonical[column].astype("string").fillna("<NA>")
    digest = hashlib.sha256()
    digest.update(str(namespace).encode("utf-8"))
    digest.update(b"\0")
    digest.update(json.dumps(selected, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    digest.update(b"\0")
    digest.update(repr(tuple(str(dtype) for dtype in canonical.dtypes)).encode("utf-8"))
    digest.update(repr(tuple(int(value) for value in canonical.shape)).encode("ascii"))
    hashed = pd.util.hash_pandas_object(canonical, index=True, categorize=True)
    digest.update(hashed.to_numpy(copy=False).tobytes())
    return f"sha256:{digest.hexdigest()}"


def target_id(family: str, measurement_fingerprint: str, target_key: str) -> str:
    digest = hashlib.sha256()
    for value in (family, measurement_fingerprint, str(target_key).strip()):
        digest.update(value.encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return f"target:{digest.hexdigest()[:24]}"


def _finite_values(values: object) -> dict[str, float]:
    if not isinstance(values, Mapping):
        return {}
    cleaned: dict[str, float] = {}
    for raw_label, raw_value in values.items():
        label = str(raw_label).strip()
        if label not in TRANSITION_LABELS:
            continue
        try:
            numeric = float(raw_value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            cleaned[label] = numeric
    return cleaned


def make_target(
    *,
    family: str,
    measurement_fingerprint: str,
    target_key: str,
    status: str = "unreviewed",
    auto_values: Mapping[str, object] | None = None,
    manual_values: Mapping[str, object] | None = None,
    final_values: Mapping[str, object] | None = None,
    cleared_labels: Iterable[str] = (),
    note: str = "",
) -> dict[str, Any]:
    if family not in EXPERIMENT_FAMILIES:
        raise TransitionReviewError(f"Unsupported experiment family: {family!r}")
    if status not in REVIEW_STATUSES:
        raise TransitionReviewError(f"Unsupported review status: {status!r}")
    cleared = sorted({str(label).strip() for label in cleared_labels} & TRANSITION_LABELS)
    target: dict[str, Any] = {
        "target_id": target_id(family, measurement_fingerprint, target_key),
        "target_key": str(target_key).strip(),
        "status": status,
        "included": status in INCLUDED_STATUSES,
        "analysis_included": status in ANALYSIS_INCLUDED_STATUSES,
        "quantity": "current",
        "unit": "mA",
        "auto_values": _finite_values(auto_values),
        "manual_values": _finite_values(manual_values),
        "final_values": _finite_values(final_values),
        "cleared_labels": cleared,
    }
    if note.strip():
        target["note"] = note.strip()
    return target


def make_review(
    *,
    family: str,
    measurement_fingerprint: str,
    targets: Sequence[Mapping[str, Any]],
    source_files: Sequence[Mapping[str, Any]] = (),
    sample: Mapping[str, Any] | None = None,
    analysis: Mapping[str, Any] | None = None,
    authored_by: str = "",
    site: str = "",
    revision: int = 1,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "experiment_family": family,
        "measurement_fingerprint": measurement_fingerprint,
        "review_revision": max(1, int(revision)),
        "updated_utc": utc_now_text(),
        "source_files": [dict(item) for item in source_files],
        "sample": dict(sample or {}),
        "analysis": dict(analysis or {}),
        "targets": [dict(item) for item in targets],
    }
    if authored_by.strip():
        payload["authored_by"] = authored_by.strip()
    if site.strip():
        payload["site"] = site.strip()
    return validate_review(payload)


def validate_review(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != SCHEMA_NAME:
        raise TransitionReviewError("Not a microwire transition-review record.")
    if int(payload.get("schema_version", 0) or 0) != SCHEMA_VERSION:
        raise TransitionReviewError(
            f"Unsupported transition-review schema version: {payload.get('schema_version')!r}"
        )
    family = str(payload.get("experiment_family") or "").strip()
    if family not in EXPERIMENT_FAMILIES:
        raise TransitionReviewError(f"Unsupported experiment family: {family!r}")
    fingerprint = str(payload.get("measurement_fingerprint") or "").strip()
    if not fingerprint.startswith("sha256:") or len(fingerprint) != 71:
        raise TransitionReviewError("measurement_fingerprint must be a sha256 identity.")
    raw_targets = payload.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise TransitionReviewError("A review must contain at least one target.")
    cleaned_targets: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in raw_targets:
        if not isinstance(raw, Mapping):
            raise TransitionReviewError("Each review target must be an object.")
        status = str(raw.get("status") or "").strip()
        if status not in REVIEW_STATUSES:
            raise TransitionReviewError(f"Unsupported review status: {status!r}")
        target_key = str(raw.get("target_key") or "").strip()
        expected_id = target_id(family, fingerprint, target_key)
        stored_id = str(raw.get("target_id") or expected_id).strip()
        if stored_id != expected_id:
            raise TransitionReviewError(f"Target identity does not match target_key: {target_key!r}")
        if stored_id in seen_ids:
            raise TransitionReviewError(f"Duplicate transition-review target: {target_key!r}")
        seen_ids.add(stored_id)
        cleaned = dict(raw)
        cleaned["target_id"] = stored_id
        cleaned["target_key"] = target_key
        cleaned["status"] = status
        cleaned["included"] = status in INCLUDED_STATUSES
        cleaned["analysis_included"] = status in ANALYSIS_INCLUDED_STATUSES
        cleaned["quantity"] = "current"
        cleaned["unit"] = "mA"
        for key in ("auto_values", "manual_values", "final_values"):
            cleaned[key] = _finite_values(raw.get(key))
        if status == "no_transition":
            cleaned["final_values"] = {}
        cleaned["cleared_labels"] = sorted(
            {str(label).strip() for label in raw.get("cleared_labels", ())} & TRANSITION_LABELS
        )
        cleaned_targets.append(cleaned)
    cleaned_payload = dict(payload)
    cleaned_payload["experiment_family"] = family
    cleaned_payload["measurement_fingerprint"] = fingerprint
    cleaned_payload["targets"] = cleaned_targets
    cleaned_payload["review_revision"] = max(1, int(payload.get("review_revision", 1) or 1))
    cleaned_payload.setdefault("updated_utc", utc_now_text())
    cleaned_payload.setdefault("source_files", [])
    cleaned_payload.setdefault("sample", {})
    cleaned_payload.setdefault("analysis", {})
    return cleaned_payload


def load_review(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise TransitionReviewError("Transition-review JSON root must be an object.")
    return validate_review(payload)


def atomic_write_review(path: Path, payload: Mapping[str, Any]) -> Path:
    """Validate and atomically publish a review without touching raw data."""

    target = Path(path)
    cleaned = validate_review(payload)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(cleaned, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def sidecar_path_for_measurement(path: Path, *, family: str) -> Path:
    source = Path(path)
    if family == "tma":
        run_dir = source if source.is_dir() or not source.suffix else source.parent
        return run_dir / SIDECAR_NAME
    if family != "current_annealing":
        raise TransitionReviewError(f"Unsupported experiment family: {family!r}")
    if source.name.casefold() == "measurement.txt":
        return source.parent / SIDECAR_NAME
    return source.with_name(f"{source.stem}.transition-review.json")


def source_file_entry(path: Path, *, relative_to: Path | None = None) -> dict[str, Any]:
    source = Path(path)
    relative = source.name
    if relative_to is not None:
        try:
            relative = source.relative_to(relative_to).as_posix()
        except ValueError:
            relative = source.name
    return {
        "path": relative,
        "sha256": file_sha256(source),
        "size_bytes": source.stat().st_size,
    }


__all__ = [
    "ANALYSIS_INCLUDED_STATUSES",
    "EXPERIMENT_FAMILIES",
    "INCLUDED_STATUSES",
    "REVIEW_STATUSES",
    "SCHEMA_NAME",
    "SCHEMA_VERSION",
    "SIDECAR_NAME",
    "TransitionReviewError",
    "atomic_write_review",
    "dataframe_fingerprint",
    "file_sha256",
    "load_review",
    "make_review",
    "make_target",
    "sidecar_path_for_measurement",
    "source_file_entry",
    "target_id",
    "utc_now_text",
    "validate_review",
]
