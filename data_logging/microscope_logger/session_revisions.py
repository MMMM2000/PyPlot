"""Append measurement revisions without rewriting a capture's original image.

Completed revisions are immutable. ``latest.json`` is a generated atomic pointer;
failed writes leave the previous pointer valid and retain recovery evidence.
Callers serialize updates for a capture; optimistic pointer checking also rejects
an intervening completed update from another writer.
"""
from __future__ import annotations

import copy
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
import uuid

from .measurements import _png_size, _size, _validated_record, _publish_directory


_SHA = re.compile(r"[0-9a-f]{64}\Z")
_REVISION = re.compile(r"revisions/[0-9a-f]{32}/session\.json\Z")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _reference(sha: str, data: bytes) -> tuple[int, int]:
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise ValueError("Reference image keys must be lowercase SHA-256 values")
    dimensions = _png_size(data)
    if _digest(data) != sha:
        raise ValueError("Reference image SHA-256 does not match its original bytes")
    return dimensions


def _read_base(directory: Path):
    try:
        manifest_bytes = (directory / "session.json").read_bytes()
        base = json.loads(manifest_bytes)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("Not a readable microscope capture session") from exc
    if (not isinstance(base, dict) or base.get("application") != "PyPlot microscope"
            or base.get("schema_version") != 1
            or not isinstance(base.get("session_id"), str)
            or not re.fullmatch(r"[0-9a-f]{32}", base["session_id"])):
        raise ValueError("Not a supported microscope capture session")
    image = base.get("image")
    if not isinstance(image, dict) or image.get("file") != "image.png":
        raise ValueError("Capture session does not identify its original image")
    data = (directory / "image.png").read_bytes()
    dimensions = _png_size(data)
    if (image.get("sha256") != _digest(data)
            or _size((image.get("width"), image.get("height"))) != dimensions):
        raise ValueError("Capture original image failed its integrity check")
    references = {}
    for reference in base.get("reference_images", []):
        sha = reference.get("sha256")
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise ValueError("Capture reference manifest is invalid")
        expected_file = f"reference_{sha}.png"
        if reference.get("file") != expected_file:
            raise ValueError("Capture reference path is invalid")
        raw = (directory / expected_file).read_bytes()
        size = _reference(sha, raw)
        if _size((reference.get("width"), reference.get("height"))) != size:
            raise ValueError("Capture reference dimensions have changed")
        references[sha] = raw
    return base, manifest_bytes, dimensions, references


def _read_latest(directory: Path, base: Mapping[str, Any], base_sha: str) -> bytes | None:
    path = directory / "latest.json"
    if not path.exists():
        return None
    raw = path.read_bytes()
    try:
        pointer = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Existing latest pointer is not valid JSON") from exc
    if (not isinstance(pointer, dict) or pointer.get("application") != "PyPlot microscope revision pointer"
            or pointer.get("schema_version") != 1 or pointer.get("session_id") != base["session_id"]
            or pointer.get("base_manifest_sha256") != base_sha
            or not isinstance(pointer.get("manifest"), str)
            or not _REVISION.fullmatch(pointer["manifest"])):
        raise ValueError("Existing latest pointer does not match this capture session")
    revision_path = directory / pointer["manifest"]
    if not revision_path.is_file() or _digest(revision_path.read_bytes()) != pointer.get("manifest_sha256"):
        raise ValueError("Existing latest revision failed its integrity check")
    return raw


def _csv_text(records: Sequence[Mapping[str, Any]]) -> str:
    fields = ["measurement", "composition", "microwire", "specimen", "sample_uid", "source_kind", "source_path", "brittle", "brittle_observed_utc",
              "kind", "width_px", "width_um", "calibration_status", "review_status", "reviewed_utc", "objective",
              "zoom", "camera_id", "start_x_px", "start_y_px", "end_x_px", "end_y_px"]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for index, record in enumerate(records, 1):
        sample = record.get("sample") or {}
        if not isinstance(sample, Mapping):
            raise ValueError("Measurement sample must be a mapping")
        row = {key: record.get(key) for key in fields if key in record}
        row.update({key: sample.get(key, "") for key in
                    ("composition", "microwire", "specimen", "source_kind", "source_path")})
        identity = sample.get("physical_sample")
        row["sample_uid"] = identity.get("uid", "") if isinstance(identity, Mapping) else ""
        row.update(brittle=sample.get("brittle"), brittle_observed_utc=sample.get("brittle_observed_utc"),
                   measurement=index, start_x_px=record["start"][0], start_y_px=record["start"][1],
                   end_x_px=record["end"][0], end_y_px=record["end"][1])
        writer.writerow(row)
    return buffer.getvalue()


def update_session(directory: str | Path, records: Sequence[Mapping[str, Any]],
                   metadata: Mapping[str, Any], *,
                   reference_images: Mapping[str, bytes] | None = None) -> Path:
    """Append an immutable revision and atomically publish its ``latest.json`` pointer.

    Return the new revision directory. The original ``image.png``, initial
    ``session.json`` and ``measurements.csv`` remain unchanged. Reference images
    live once at the capture root; revision paths are portable and relative.
    """
    root = Path(directory).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Select a microscope capture session directory")
    base, base_bytes, dimensions, references = _read_base(root)
    base_sha = _digest(base_bytes)
    previous_pointer = _read_latest(root, base, base_sha)
    if "image_size" in metadata and _size(metadata["image_size"]) != dimensions:
        raise ValueError("Session image_size does not match the original PNG")
    checked = [copy.deepcopy(_validated_record(record, dimensions)) for record in records]
    checked_metadata = copy.deepcopy(dict(metadata))
    for sha, data in (reference_images or {}).items():
        _reference(sha, data)
        references[sha] = data
    for evidence in [checked_metadata.get("calibration_evidence"),
                     *[record.get("calibration_evidence") for record in checked]]:
        if evidence is not None:
            if not isinstance(evidence, dict):
                raise ValueError("Calibration evidence must be a mapping")
            sha = evidence.get("image_sha256")
            if not isinstance(sha, str) or not _SHA.fullmatch(sha):
                raise ValueError("Calibration evidence needs a valid reference SHA-256")
            if sha not in references:
                reference_path = root / f"reference_{sha}.png"
                if not reference_path.is_file():
                    raise ValueError("Calibration reference image is missing")
                references[sha] = reference_path.read_bytes()
                _reference(sha, references[sha])
            evidence["image_file"] = f"../../reference_{sha}.png"
    reference_inventory = []
    for sha, data in sorted(references.items()):
        size = _reference(sha, data)
        path = root / f"reference_{sha}.png"
        if path.exists() and path.read_bytes() != data:
            raise ValueError("Existing calibration reference image has changed")
        reference_inventory.append({"file": f"../../reference_{sha}.png", "sha256": sha,
                                    "width": size[0], "height": size[1]})
    revision_id = uuid.uuid4().hex
    stamp = datetime.now(timezone.utc).isoformat()
    payload = {"schema_version": 1, "application": "PyPlot microscope",
               "session_id": base["session_id"], "revision_id": revision_id,
               "created_utc": stamp, "base_manifest_sha256": base_sha,
               "image": dict(base["image"], file="../../image.png"),
               "metadata": checked_metadata, "measurements": checked}
    if reference_inventory:
        payload["reference_images"] = reference_inventory
    manifest_data = _json_bytes(payload)
    csv_data = _csv_text(checked)
    pointer = {"schema_version": 1, "application": "PyPlot microscope revision pointer",
               "session_id": base["session_id"], "base_manifest_sha256": base_sha,
               "revision_id": revision_id, "created_utc": stamp,
               "manifest": f"revisions/{revision_id}/session.json",
               "manifest_sha256": _digest(manifest_data)}
    pointer_data = _json_bytes(pointer)
    revisions = root / "revisions"
    revisions.mkdir(exist_ok=True)
    pending = revisions / f".pending_{revision_id}"
    pending.mkdir(exist_ok=False)
    with (pending / "session.json").open("xb") as stream:
        stream.write(manifest_data)
    with (pending / "measurements.csv").open("x", encoding="utf-8", newline="") as stream:
        stream.write(csv_data)
    for sha, data in references.items():
        path = root / f"reference_{sha}.png"
        if not path.exists():
            with path.open("xb") as stream:
                stream.write(data)
        elif path.read_bytes() != data:
            raise ValueError("Existing calibration reference image has changed")
    completed = revisions / revision_id
    _publish_directory(pending, completed)
    pointer_temp = root / f".latest_{revision_id}.json"
    with pointer_temp.open("xb") as stream:
        stream.write(pointer_data)
    current_pointer = (root / "latest.json").read_bytes() if (root / "latest.json").exists() else None
    if current_pointer != previous_pointer:
        raise ValueError("Capture session was updated by another writer; reload before saving")
    # Atomic replacement only touches our generated pointer, after all data exists.
    os.replace(pointer_temp, root / "latest.json")
    return completed


__all__ = ["update_session"]
