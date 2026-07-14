"""Explicit trusted-copy migration for legacy Builder pickle payloads.

This is the only production module that imports pickle.  Callers must opt in
with a distinct output path; ordinary Builder/UI/launcher reads never import or
call this module.
"""

from __future__ import annotations

import base64
import json
import os
import pickle
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from .safe_codec import (
    SafeCodecError,
    atomic_write_json,
    encode_envelope,
    read_json_file,
)


def _distinct_paths(source: Path, output: Path) -> tuple[Path, Path]:
    source_resolved = Path(source).expanduser().resolve()
    output_resolved = Path(output).expanduser().resolve()
    if source_resolved == output_resolved:
        raise SafeCodecError("Trusted migration output must differ from the source")
    return source_resolved, output_resolved


def _trusted_decode_pickle_envelope(payload: Any) -> Any:
    if not isinstance(payload, Mapping) or payload.get("encoding") != "pickle-base64":
        raise SafeCodecError("Legacy project payload is not pickle-base64")
    raw = payload.get("value")
    if not isinstance(raw, str) or not raw:
        raise SafeCodecError("Legacy project payload is missing pickle data")
    return pickle.loads(base64.b64decode(raw.encode("ascii"), validate=True))


def migrate_legacy_project_trusted(source: Path, output: Path) -> dict[str, Any]:
    """Migrate one explicitly trusted legacy `.pydpj` to a new v2 output."""

    source_path, output_path = _distinct_paths(source, output)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    payload = read_json_file(source_path)
    if not isinstance(payload, dict):
        raise SafeCodecError("Builder project root must be an object")
    sections = payload.get("sections")
    migrated_count = 0
    if isinstance(sections, Mapping):
        migrated_sections: dict[str, Any] = {}
        for section_name, raw_section in sections.items():
            if not isinstance(raw_section, Mapping):
                migrated_sections[str(section_name)] = raw_section
                continue
            section = dict(raw_section)
            raw_payloads = section.get("payloads")
            if isinstance(raw_payloads, Mapping):
                safe_payloads: dict[str, Any] = {}
                for name, encoded in raw_payloads.items():
                    if isinstance(encoded, Mapping) and encoded.get("encoding") == "pickle-base64":
                        safe_payloads[str(name)] = encode_envelope(
                            _trusted_decode_pickle_envelope(encoded)
                        )
                        migrated_count += 1
                    else:
                        safe_payloads[str(name)] = encoded
                section["payloads"] = safe_payloads
            migrated_sections[str(section_name)] = section
        payload["sections"] = migrated_sections
    payload["version"] = 2
    payload["trusted_migration"] = {
        "source": str(source_path),
        "migrated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "legacy_payloads_migrated": migrated_count,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_fd, temp_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent
    )
    os.close(temp_fd)
    temp_path = Path(temp_name)
    try:
        atomic_write_json(temp_path, payload)
        # Hard-link publication is create-if-absent: unlike os.replace, it cannot
        # overwrite a target created concurrently after the initial validation.
        os.link(temp_path, output_path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
    return {
        "kind": "builder_trusted_migration",
        "source": str(source_path),
        "output": str(output_path),
        "legacy_payloads_migrated": migrated_count,
    }


def _trusted_read_pickle(path: Path) -> Any:
    with Path(path).open("rb") as handle:
        return pickle.load(handle)


def _acquire_migration_lock(path: Path):
    """Acquire a nonblocking OS lock whose ownership is released on process exit."""

    handle = path.open("a+b")
    if handle.seek(0, os.SEEK_END) == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception as exc:
        handle.close()
        raise SafeCodecError(f"Another migration owns lock {path}") from exc
    return handle


def _release_migration_lock(handle: Any) -> None:
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def migrate_legacy_store_trusted(source_root: Path, output_root: Path) -> dict[str, Any]:
    """Migrate legacy Builder store pickles into a separate safe store root."""

    source_path, output_path = _distinct_paths(source_root, output_root)
    if output_path.is_relative_to(source_path):
        raise SafeCodecError("Trusted store migration output must be outside the source tree")
    source_base = source_path / "mini_databases"
    if not source_base.is_dir():
        raise FileNotFoundError(source_base)
    if output_path.exists():
        raise FileExistsError(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output_path.parent / f".{output_path.name}.migration.lock"
    lock_handle = _acquire_migration_lock(lock_path)
    temp_output: Path | None = None
    try:
        temp_output = Path(
            tempfile.mkdtemp(prefix=f".{output_path.name}.", dir=output_path.parent)
        )
        output_base = temp_output / "mini_databases"
        output_payload_dir = output_base / "payloads"
        output_payload_dir.mkdir(parents=True, exist_ok=False)

        sections: list[str] = []
        migrated_tables = 0
        migrated_payloads = 0
        for meta_path in sorted(source_base.glob("*.json")):
            if meta_path.name.endswith(".store.json"):
                continue
            section = meta_path.stem
            metadata = read_json_file(meta_path)
            if not isinstance(metadata, dict):
                metadata = {}
            table_path = source_base / f"{section}.pkl"
            table = _trusted_read_pickle(table_path) if table_path.exists() else pd.DataFrame()
            if table_path.exists():
                migrated_tables += 1
            stored = {
                "sources": metadata.get("sources", []),
                "processed": metadata.get("processed", {}),
                "extra": metadata.get("extra", {}),
                "table": table if isinstance(table, pd.DataFrame) else pd.DataFrame(),
            }
            atomic_write_json(output_base / f"{section}.store.json", encode_envelope(stored))
            sections.append(section)

        source_payload_dir = source_base / "payloads"
        handled: set[Path] = set()
        for section in sorted(sections, key=len, reverse=True):
            for payload_path in sorted(source_payload_dir.glob(f"{section}_*.pkl")):
                if payload_path in handled:
                    continue
                handled.add(payload_path)
                name = payload_path.stem[len(section) + 1 :]
                payload = _trusted_read_pickle(payload_path)
                atomic_write_json(
                    output_payload_dir / f"{section}_{name}.json",
                    encode_envelope(payload),
                )
                migrated_payloads += 1

        manifest = {
            "kind": "builder_store_trusted_migration",
            "source": str(source_path),
            "output": str(output_path),
            "sections": sections,
            "legacy_tables_migrated": migrated_tables,
            "legacy_payloads_migrated": migrated_payloads,
            "migrated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        atomic_write_json(temp_output / "trusted_migration_manifest.json", manifest)
        if output_path.exists():
            raise FileExistsError(output_path)
        os.rename(temp_output, output_path)
        return manifest
    except Exception:
        if temp_output is not None:
            shutil.rmtree(temp_output, ignore_errors=True)
        raise
    finally:
        _release_migration_lock(lock_handle)


__all__ = ["migrate_legacy_project_trusted", "migrate_legacy_store_trusted"]
