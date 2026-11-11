"""Additive measurement identity provenance; never allocate or infer piece IDs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from data_logging.sample_identity import sample_number, wire_key

PHYSICAL_SAMPLE_COLUMN = "Physical sample measurements"
PHYSICAL_SAMPLE_SCOPE_COLUMN = "Physical sample scope"


def validated_physical_sample(value: Any, *, composition: str | None = None,
                              wire: str | None = None) -> dict | None:
    if not isinstance(value, dict):
        return None
    if any(not isinstance(value.get(key), str) or not value[key].strip()
           for key in ("composition", "wire", "label")):
        return None
    if sample_number(value["label"]) is None:
        return None
    if "uid" in value:
        try:
            if not isinstance(value["uid"], str):
                return None
            uid = str(UUID(value["uid"]))
        except ValueError:
            return None
    if composition is not None and value["composition"].strip() != composition.strip():
        return None
    if wire is not None and wire_key(value["wire"]) != wire_key(wire):
        return None
    # Retain only the existing logger identity fields, never arbitrary metadata.
    result = {key: value[key] for key in ("composition", "wire", "label", "uid", "source", "status", "coverage", "parent_uid")
              if key in value and (isinstance(value[key], str) or (key == "parent_uid" and value[key] is None))}
    if "uid" in result:
        result["uid"] = uid
    result["composition"] = result["composition"].strip()
    result["wire"] = wire_key(result["wire"])
    return result


def physical_sample_from_metadata_file(path: Path, *, composition: str | None = None,
                                       wire: str | None = None) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return validated_physical_sample(payload.get("physical_sample"), composition=composition, wire=wire)


def measurement_identity_ledger(records) -> str:
    """Keep each source's identity separate, including unknown companion runs."""
    entries = []
    for record in records:
        metadata = getattr(record, "metadata", record)
        identity = validated_physical_sample(getattr(metadata, "physical_sample", None))
        entries.append({"source": str(record.path), "physical_sample": identity})
    if not any(entry["physical_sample"] is not None for entry in entries):
        return ""
    return json.dumps(entries, ensure_ascii=False, sort_keys=True)


def parse_identity_ledger(value) -> list[dict]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return []
    if not isinstance(value, str):
        raise ValueError("Physical sample measurements must be JSON text")
    entries = json.loads(value)
    if not isinstance(entries, list):
        raise ValueError("Physical sample measurements must contain a list")
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("source"), str):
            raise ValueError("Invalid physical sample measurement source")
        identity = entry.get("physical_sample")
        if identity is not None and validated_physical_sample(identity) is None:
            raise ValueError("Invalid physical sample measurement identity")
    return entries



def measurement_preview_cache_key(record) -> str:
    """Keep explicit piece claims and source-specific titles out of hash-only caches."""
    metadata = getattr(record, "metadata", None)
    content_hash = getattr(metadata, "measurement_id", None)
    path = str(getattr(record, "path", ""))
    identity = validated_physical_sample(getattr(metadata, "physical_sample", None))
    if identity is not None:
        return json.dumps([content_hash, path, identity], ensure_ascii=False, sort_keys=True)
    return content_hash if isinstance(content_hash, str) and content_hash else path
