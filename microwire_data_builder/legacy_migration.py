"""Explicit trusted-copy migration for legacy Builder pickle payloads.

This is the only production module that imports pickle.  Callers must opt in
with a distinct output path; ordinary Builder/UI/launcher reads never import or
call this module.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import pickle
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .safe_codec import (
    SafeCodecError,
    atomic_write_json,
    decode_envelope,
    encode_envelope,
    iterencode_envelope_with_blobs,
    read_json_file,
)
from .project_package import (
    StagedEncodedPayload,
    StagedProjectSection,
    write_project_package_streaming,
)
from .legacy_json_spool import SpoolLimits, spool_legacy_project


MAX_LEGACY_PICKLE_BYTES = 384 * 1024 * 1024


def _distinct_paths(source: Path, output: Path) -> tuple[Path, Path]:
    source_resolved = Path(source).expanduser().resolve()
    output_resolved = Path(output).expanduser().resolve()
    if source_resolved == output_resolved:
        raise SafeCodecError("Trusted migration output must differ from the source")
    return source_resolved, output_resolved


def _write_streaming_payload_value(
    value: Any,
    payload_json_path: Path,
    blob_dir: Path,
    result_path: Path,
) -> None:
    """Child-only streaming package encoding for one already decoded value."""

    blob_dir.mkdir(parents=True, exist_ok=True)
    referenced: dict[str, int] = {}

    def _blob_sink(buffer: memoryview) -> tuple[str, int]:
        size = len(buffer)
        if size > MAX_LEGACY_PICKLE_BYTES:
            raise SafeCodecError("Migrated Builder blob exceeds its safe size limit")
        digest = hashlib.sha256(buffer).hexdigest()
        target = blob_dir / digest
        if not target.exists():
            with target.open("xb") as handle:
                for offset in range(0, size, 1024 * 1024):
                    handle.write(buffer[offset : offset + 1024 * 1024])
                handle.flush()
                os.fsync(handle.fileno())
        referenced[digest] = size
        return digest, size

    digest = hashlib.sha256()
    encoded_bytes = 0
    with Path(payload_json_path).open("xb") as output:
        for piece in iterencode_envelope_with_blobs(value, _blob_sink):
            raw = piece.encode("utf-8")
            encoded_bytes += len(raw)
            if encoded_bytes > 64 * 1024 * 1024:
                raise SafeCodecError("Migrated Builder payload JSON exceeds 64 MiB")
            digest.update(raw)
            output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    del value
    gc.collect()
    with Path(result_path).open("x", encoding="utf-8") as result_handle:
        json.dump(
            {
                "payload_bytes": encoded_bytes,
                "payload_sha256": digest.hexdigest(),
                "blobs": referenced,
            },
            result_handle,
            separators=(",", ":"),
        )
        result_handle.flush()
        os.fsync(result_handle.fileno())


def _write_streaming_pickle_payload(
    pickle_path: Path,
    payload_json_path: Path,
    blob_dir: Path,
    result_path: Path,
) -> None:
    """Child-only trusted pickle execution and streaming package encoding."""

    with Path(pickle_path).open("rb") as handle:
        value = pickle.load(handle)
    _write_streaming_payload_value(value, payload_json_path, blob_dir, result_path)


def _write_streaming_safe_payload(
    envelope_path: Path,
    payload_json_path: Path,
    blob_dir: Path,
    result_path: Path,
) -> None:
    """Child-only safe-envelope decode and binary-externalizing re-encode."""

    source = Path(envelope_path)
    size = source.stat().st_size
    if size > SpoolLimits().payload_bytes:
        raise SafeCodecError("Legacy safe Builder payload exceeds its migration size limit")
    with source.open("r", encoding="utf-8") as handle:
        envelope = json.load(handle)
    value = decode_envelope(envelope)
    del envelope
    gc.collect()
    _write_streaming_payload_value(value, payload_json_path, blob_dir, result_path)


def _convert_payload_in_child(
    source_path: Path,
    payload_json_path: Path,
    blob_dir: Path,
    *,
    trusted_pickle: bool,
) -> StagedEncodedPayload:
    result_path = payload_json_path.with_suffix(".result.json")
    command = [
        sys.executable,
        "-m",
        "microwire_data_builder.legacy_migration",
        "--encode-trusted-pickle" if trusted_pickle else "--reencode-safe-envelope",
        str(source_path),
        str(payload_json_path),
        str(blob_dir),
        str(result_path),
    ]
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise SafeCodecError(
            f"Trusted Builder payload conversion child failed with exit code {completed.returncode}"
        )
    result = read_json_file(result_path)
    if not isinstance(result, dict) or not isinstance(result.get("blobs"), dict):
        raise SafeCodecError("Trusted Builder payload conversion result is malformed")
    blob_paths: dict[str, Path] = {}
    for digest, size in result["blobs"].items():
        path = blob_dir / str(digest)
        if (
            not isinstance(digest, str)
            or not isinstance(size, int)
            or not path.is_file()
            or path.stat().st_size != size
        ):
            raise SafeCodecError("Trusted Builder payload blob result is invalid")
        blob_paths[digest] = path
    return StagedEncodedPayload(payload_json_path, blob_paths)


def migrate_legacy_project_trusted(
    source: Path,
    output: Path,
    *,
    progress: Any = None,
    cancelled: Any = None,
) -> dict[str, Any]:
    """Stream one explicitly trusted legacy `.pydpj` into a distinct v3 package."""

    source_path, output_path = _distinct_paths(source, output)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    migrated_count = 0
    started = datetime.now(UTC)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_path.name}.migration.", dir=output_path.parent
    ) as temp_name:
        staging = Path(temp_name)

        def _spool_progress(stage: str, done: int, total: int) -> None:
            if callable(progress):
                progress({
                    "phase": stage,
                    "current": source_path.name,
                    "bytes_done": done,
                    "bytes_total": total,
                    "elapsed_seconds": (datetime.now(UTC) - started).total_seconds(),
                    "source": str(source_path),
                    "destination": str(output_path),
                })

        spooled = spool_legacy_project(
            source_path,
            staging / "legacy-spool",
            progress=_spool_progress,
            cancelled=cancelled,
        )
        blob_dir = staging / "encoded-blobs"
        blob_dir.mkdir()
        staged_sections: list[tuple[str, StagedProjectSection]] = []
        payload_total = sum(len(section.payloads) for section in spooled.sections)
        original_name = os.environ.get(
            "MICROWIRE_BUILDER_MIGRATION_ORIGINAL_NAME", source_path.name
        )
        expected_source_sha256 = os.environ.get(
            "MICROWIRE_BUILDER_MIGRATION_SOURCE_SHA256", spooled.source_sha256
        )
        expected_source_bytes_text = os.environ.get(
            "MICROWIRE_BUILDER_MIGRATION_SOURCE_BYTES", str(spooled.source_bytes)
        )
        try:
            expected_source_bytes = int(expected_source_bytes_text)
        except ValueError as exc:
            raise SafeCodecError("Trusted migration source byte provenance is invalid") from exc
        if (
            expected_source_sha256 != spooled.source_sha256
            or expected_source_bytes != spooled.source_bytes
        ):
            raise SafeCodecError(
                "Disposable Builder migration source does not match its verified original"
            )
        legacy_version = spooled.metadata.get("version", 1)
        if isinstance(legacy_version, bool) or not isinstance(legacy_version, int):
            raise SafeCodecError("Legacy Builder project version is invalid")
        payload_done = 0
        for section in spooled.sections:
            staged_payloads: dict[str, StagedEncodedPayload] = {}
            for payload_name, payload in section.payloads.items():
                if callable(cancelled) and cancelled():
                    raise SafeCodecError(
                        "Trusted Builder migration cancelled before the next payload"
                    )
                if callable(progress):
                    progress({
                        "phase": "convert_payload",
                        "current": f"{section.name}.{payload_name}",
                        "bytes_done": payload_done,
                        "bytes_total": payload_total,
                        "elapsed_seconds": (datetime.now(UTC) - started).total_seconds(),
                        "source": str(source_path),
                        "destination": str(output_path),
                    })
                if payload.encoding == "pickle-base64":
                    if payload.pickle_path is None:
                        raise SafeCodecError("Spooled legacy pickle payload is missing its decoded file")
                    encoded_path = payload.envelope_path.with_name(
                        f"{payload.envelope_path.stem}.safe.json"
                    )
                    staged_payloads[payload_name] = _convert_payload_in_child(
                        payload.pickle_path, encoded_path, blob_dir, trusted_pickle=True
                    )
                    migrated_count += 1
                else:
                    encoded_path = payload.envelope_path.with_name(
                        f"{payload.envelope_path.stem}.externalized.json"
                    )
                    staged_payloads[payload_name] = _convert_payload_in_child(
                        payload.envelope_path,
                        encoded_path,
                        blob_dir,
                        trusted_pickle=False,
                    )
                payload_done += 1
            staged_sections.append((
                section.name,
                StagedProjectSection(
                    section.state_path, section.table_path, staged_payloads
                ),
            ))
        index = write_project_package_streaming(
            output_path,
            staged_sections,
            source_saved_at=str(spooled.metadata.get("saved_at") or ""),
            replace_existing=False,
            migration_provenance={
                "source_name": original_name,
                "source_sha256": spooled.source_sha256,
                "source_bytes": spooled.source_bytes,
                "legacy_version": legacy_version,
                "section_count": len(spooled.sections),
                "payload_count": payload_total,
                "pickle_payload_count": migrated_count,
                "migrated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            },
            progress=(
                (lambda label: progress({
                    "phase": "package",
                    "current": label,
                    "bytes_done": payload_done,
                    "bytes_total": max(payload_total, 1),
                    "elapsed_seconds": (datetime.now(UTC) - started).total_seconds(),
                    "source": str(source_path),
                    "destination": str(output_path),
                }))
                if callable(progress)
                else None
            ),
        )
        return {
            "kind": "builder_trusted_migration",
            "source": str(source_path),
            "source_sha256": spooled.source_sha256,
            "source_bytes": spooled.source_bytes,
            "max_spool_buffer": spooled.max_internal_buffer,
            "output": str(output_path),
            "version": index.manifest["version"],
            "project_id": index.manifest["project_id"],
            "sections": len(spooled.sections),
            "payloads": payload_total,
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


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--encode-trusted-pickle", action="store_true")
    parser.add_argument("--reencode-safe-envelope", action="store_true")
    parser.add_argument("source_path", nargs="?")
    parser.add_argument("payload_json_path", nargs="?")
    parser.add_argument("blob_dir", nargs="?")
    parser.add_argument("result_path", nargs="?")
    args = parser.parse_args(argv)
    if args.encode_trusted_pickle == args.reencode_safe_envelope or not all(
        (args.source_path, args.payload_json_path, args.blob_dir, args.result_path)
    ):
        return 2
    try:
        writer = (
            _write_streaming_pickle_payload
            if args.encode_trusted_pickle
            else _write_streaming_safe_payload
        )
        writer(
            Path(args.source_path), Path(args.payload_json_path),
            Path(args.blob_dir), Path(args.result_path),
        )
        return 0
    except Exception as exc:
        print(f"trusted payload conversion failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


__all__ = ["migrate_legacy_project_trusted", "migrate_legacy_store_trusted"]


if __name__ == "__main__":
    raise SystemExit(_main())
