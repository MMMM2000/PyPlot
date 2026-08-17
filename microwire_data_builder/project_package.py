"""Safe ZIP64 project container for Microwire Data Builder.

Ordinary reads in this module are data-only.  Pickle support exists only in
``legacy_migration`` and is never imported here.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

from .safe_codec import (
    SafeCodecError,
    decode_envelope,
    iterencode_envelope_with_blobs,
    read_json_file,
)


PACKAGE_FORMAT = "microwire-builder-package"
PACKAGE_VERSION = 3
PROJECT_KIND = "MicrowireDataBuilder"
MIMETYPE_PATH = "mimetype"
MIMETYPE = "application/vnd.pyplot.microwire-builder+zip"
MANIFEST_PATH = "manifest.json"
CODEC_ID = "microwire-json/2"

MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 50_000
MAX_SECTIONS = 64
MAX_JSON_ENTRY_BYTES = 256 * 1024 * 1024
MAX_PAYLOAD_JSON_BYTES = 64 * 1024 * 1024
MAX_BLOB_BYTES = 256 * 1024 * 1024
MAX_AGGREGATE_BYTES = 4 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
STREAM_CHUNK_BYTES = 1024 * 1024


def _safe_zip_compression_for_chunks(chunks: Iterable[bytes], raw_size: int) -> int:
    """Compress entries unless doing so would violate our anti-zip-bomb ratio."""
    compressor = zlib.compressobj(level=6, wbits=-zlib.MAX_WBITS)
    compressed_size = 0
    for chunk in chunks:
        compressed_size += len(compressor.compress(chunk))
    compressed_size += len(compressor.flush())
    if raw_size > max(1, compressed_size) * MAX_COMPRESSION_RATIO:
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED


def _safe_zip_compression_for_bytes(raw: bytes) -> int:
    return _safe_zip_compression_for_chunks((raw,), len(raw))


def _safe_zip_compression_for_file(path: Path) -> int:
    size = path.stat().st_size

    def chunks() -> Iterable[bytes]:
        with path.open("rb") as handle:
            while chunk := handle.read(STREAM_CHUNK_BYTES):
                yield chunk

    return _safe_zip_compression_for_chunks(chunks(), size)


def _safe_zip_compression_for_archive_entry(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
) -> int:
    def chunks() -> Iterable[bytes]:
        with archive.open(info, "r") as handle:
            while chunk := handle.read(STREAM_CHUNK_BYTES):
                yield chunk

    return _safe_zip_compression_for_chunks(chunks(), info.file_size)


DELETED_PAYLOADS_KEY = "__deleted_payloads__"

SUPPORTED_REQUIRED_FEATURES = frozenset({"split-sections", "sha256", "content-blobs"})
DEFAULT_REQUIRED_FEATURES = tuple(sorted(SUPPORTED_REQUIRED_FEATURES))
MIGRATION_PROVENANCE_FIELDS = frozenset({
    "source_name", "source_sha256", "source_bytes", "legacy_version",
    "section_count", "payload_count", "pickle_payload_count", "migrated_at",
})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_PROJECT_ID_RE = re.compile(r"^[0-9a-f]{32}$")

ALLOWED_SECTION_KEYS = frozenset(
    {
        "annealing", "assemble", "compare", "current_density",
        "dma_iso_stress", "fabrication", "fmr", "microscope", "mini_dma",
        "shape_memory_stress_strain", "strain", "transition_temps", "videos",
        "vsm_hysteresis", "vsm_temperature_scan",
    }
)


@dataclass(frozen=True)
class EntryDescriptor:
    path: str
    role: str
    media_type: str
    sha256: str
    bytes: int


@dataclass(frozen=True)
class StagedEncodedPayload:
    """Already encoded payload JSON and its content-addressed blob files."""

    json_path: Path
    blobs: Mapping[str, Path]


@dataclass(frozen=True)
class StagedProjectSection:
    """Pre-split state/table JSON plus already encoded payload descriptors."""

    state_path: Path
    table_path: Path
    payloads: Mapping[str, StagedEncodedPayload]


def stage_payload_value(
    value: Any,
    staging_dir: Path,
    *,
    progress: Callable[[], None] | None = None,
) -> StagedEncodedPayload:
    """Encode one loaded payload to bounded files without base64 materialization."""

    root = Path(staging_dir)
    root.mkdir(parents=True, exist_ok=False)
    blob_dir = root / "blobs"
    blob_dir.mkdir()
    blob_paths: dict[str, Path] = {}
    last_progress = time.monotonic()

    def _yield_progress(*, force: bool = False) -> None:
        nonlocal last_progress
        if progress is None:
            return
        now = time.monotonic()
        if force or now - last_progress >= 0.1:
            progress()
            last_progress = now

    def _blob_sink(buffer: memoryview) -> tuple[str, int]:
        size = len(buffer)
        if size > MAX_BLOB_BYTES:
            raise SafeCodecError("Builder codec blob exceeds its safe size limit")
        digest = hashlib.sha256(buffer).hexdigest()
        target = blob_dir / digest
        if not target.exists():
            with target.open("xb") as handle:
                for offset in range(0, size, STREAM_CHUNK_BYTES):
                    handle.write(buffer[offset : offset + STREAM_CHUNK_BYTES])
                    _yield_progress()
                handle.flush()
                os.fsync(handle.fileno())
        blob_paths[digest] = target
        return digest, size

    payload_path = root / "payload.json"
    size = 0
    with payload_path.open("xb") as handle:
        for piece in iterencode_envelope_with_blobs(value, _blob_sink):
            raw = piece.encode("utf-8")
            size += len(raw)
            if size > MAX_PAYLOAD_JSON_BYTES:
                raise SafeCodecError("Builder package payload JSON exceeds its limit")
            handle.write(raw)
            _yield_progress()
        handle.flush()
        os.fsync(handle.fileno())
    _yield_progress(force=True)
    return StagedEncodedPayload(payload_path, blob_paths)


@dataclass(frozen=True)
class FileFingerprint:
    device: int
    inode: int
    size: int
    mtime_ns: int

    @classmethod
    def capture(cls, path: Path) -> "FileFingerprint":
        try:
            result = Path(path).stat()
        except OSError as exc:
            raise SafeCodecError(f"Cannot stat Builder project: {path}") from exc
        return cls(result.st_dev, result.st_ino, result.st_size, result.st_mtime_ns)


class ReadBudget:
    """Cumulative decompression budget shared by one logical load operation."""

    def __init__(self, limit: int = MAX_AGGREGATE_BYTES) -> None:
        self.limit = int(limit)
        self.bytes = 0
        self._lock = threading.Lock()

    def consume(self, count: int) -> None:
        with self._lock:
            self.bytes += int(count)
            if self.bytes > self.limit:
                raise SafeCodecError("Builder package cumulative decompression budget exceeded")


@dataclass(frozen=True)
class ProjectIndex:
    path: Path
    manifest: Mapping[str, Any]
    entries: Mapping[str, EntryDescriptor]
    sections: Mapping[str, Mapping[str, Any]]
    blobs: Mapping[str, Mapping[str, Any]]
    fingerprint: FileFingerprint
    manifest_sha256: str

    def assert_unchanged(self) -> None:
        if FileFingerprint.capture(self.path) != self.fingerprint:
            raise SafeCodecError(
                "Builder project changed on disk after it was indexed; reopen it before reading or saving"
            )

    def project_header(self) -> dict[str, Any]:
        return {
            "format": PACKAGE_FORMAT,
            "kind": PROJECT_KIND,
            "version": PACKAGE_VERSION,
            "project_id": self.manifest["project_id"],
            "revision": self.manifest["revision"],
            "created_at": self.manifest["created_at"],
            "saved_at": self.manifest["saved_at"],
            "sections": {},
        }

    def read_entry(self, path: str) -> bytes:
        with self.open_reader() as reader:
            return reader.read_entry(path)

    def open_reader(self, *, budget: ReadBudget | None = None) -> "ProjectReader":
        return ProjectReader(self, budget=budget)

    def read_section(
        self,
        key: str,
        *,
        load_payloads: bool = True,
        budget: ReadBudget | None = None,
    ) -> dict[str, Any]:
        with self.open_reader(budget=budget) as reader:
            return reader.read_section(key, load_payloads=load_payloads)

    def read_payload(
        self, section_key: str, payload_id: str, *, budget: ReadBudget | None = None
    ) -> Any:
        with self.open_reader(budget=budget) as reader:
            return reader.read_payload(section_key, payload_id)

    def materialize(self) -> dict[str, Any]:
        payload = self.project_header()
        with self.open_reader() as reader:
            payload["sections"] = {
                key: reader.read_section(key, load_payloads=True) for key in self.sections
            }
        return payload


class ProjectReader:
    """One operation-scoped archive handle, info map, blob cache, and budget."""

    def __init__(self, index: ProjectIndex, *, budget: ReadBudget | None = None) -> None:
        self.index = index
        self.budget = budget or ReadBudget()
        self.archive: zipfile.ZipFile | None = None
        self.info_map: dict[str, zipfile.ZipInfo] = {}
        self.blob_cache: dict[str, bytes] = {}

    def __enter__(self) -> "ProjectReader":
        self.index.assert_unchanged()
        self.archive = _open_archive(self.index.path)
        # Build once per operation, never once per entry.
        self.info_map = _validated_info_map(self.archive)
        if set(self.info_map) != set(self.index.entries) | {MIMETYPE_PATH, MANIFEST_PATH}:
            self.close()
            raise SafeCodecError("Builder package entry set changed after indexing")
        manifest_info = self.info_map[MANIFEST_PATH]
        current_manifest = _read_entry_bounded(
            self.archive, manifest_info, limit=MAX_MANIFEST_BYTES
        )
        if hashlib.sha256(current_manifest).hexdigest() != self.index.manifest_sha256:
            self.close()
            raise SafeCodecError("Builder package manifest changed after indexing")
        return self

    def __exit__(self, exc_type: object, *_exc: object) -> None:
        try:
            if exc_type is None:
                self.index.assert_unchanged()
        finally:
            self.close()

    def close(self) -> None:
        if self.archive is not None:
            self.archive.close()
        self.archive = None
        self.info_map = {}

    def _require_open(self) -> zipfile.ZipFile:
        if self.archive is None:
            raise RuntimeError("Builder package reader is not open")
        return self.archive

    def read_entry(self, path: str) -> bytes:
        descriptor = self.index.entries.get(path)
        info = self.info_map.get(path)
        if descriptor is None or info is None:
            raise SafeCodecError(f"Builder package entry is missing or undeclared: {path}")
        raw = _read_entry_bounded(
            self._require_open(),
            info,
            limit=_entry_limit(descriptor.role),
            expected=descriptor.bytes,
            budget=self.budget,
        )
        if hashlib.sha256(raw).hexdigest() != descriptor.sha256:
            raise SafeCodecError(f"Builder package entry checksum failed: {path}")
        return raw

    def verify_entry(self, path: str) -> None:
        descriptor = self.index.entries.get(path)
        info = self.info_map.get(path)
        if descriptor is None or info is None:
            raise SafeCodecError(f"Builder package entry is missing or undeclared: {path}")
        digest = _stream_entry_digest(
            self._require_open(), info, limit=_entry_limit(descriptor.role), budget=self.budget
        )
        if digest != descriptor.sha256:
            raise SafeCodecError(f"Builder package entry checksum failed: {path}")

    def read_blob(self, digest: str) -> bytes:
        cached = self.blob_cache.get(digest)
        if cached is not None:
            return cached
        descriptor = self.index.blobs.get(digest)
        if descriptor is None:
            raise SafeCodecError("Builder package blob reference is undeclared")
        raw = self.read_entry(descriptor["path"])
        self.blob_cache[digest] = raw
        return raw

    def read_payload(self, section_key: str, payload_id: str) -> Any:
        encoded = self.read_payload_with_blob_refs(section_key, payload_id)
        return _restore_blob_refs(encoded, self)

    def read_payload_with_blob_refs(self, section_key: str, payload_id: str) -> Any:
        section = self.index.sections.get(section_key)
        if section is None or payload_id not in section["payloads"]:
            raise SafeCodecError(f"Builder package payload is not declared: {section_key}.{payload_id}")
        raw = self.read_entry(section["payloads"][payload_id])
        encoded = _read_json_bytes(raw, f"{section_key}.{payload_id}")
        _validate_tree_limits(encoded)
        return encoded

    def read_section(self, key: str, *, load_payloads: bool) -> dict[str, Any]:
        descriptor = self.index.sections.get(str(key))
        if descriptor is None:
            return {}
        state = _read_json_bytes(self.read_entry(descriptor["state"]), f"{key} state")
        table = _read_json_bytes(self.read_entry(descriptor["table"]), f"{key} table")
        _validate_tree_limits(state)
        _validate_tree_limits(table)
        if not isinstance(state, dict) or not isinstance(table, dict):
            raise SafeCodecError(f"Builder package section {key} must contain JSON objects")
        if set(state) & {"columns", "rows", "index", "payloads"}:
            raise SafeCodecError(f"Builder package state duplicates split fields: {key}")
        if set(table) != {"columns", "rows", "index"}:
            raise SafeCodecError(f"Builder package table schema is invalid: {key}")
        payload = dict(state)
        payload.update(table)
        if load_payloads and descriptor["payloads"]:
            payload["payloads"] = {
                payload_id: self.read_payload(key, payload_id)
                for payload_id in descriptor["payloads"]
            }
        return payload


class ProjectPayloadResolver:
    """Thread-safe on-demand decoder sharing one cumulative project budget."""

    def __init__(self, index: ProjectIndex, *, budget: ReadBudget | None = None) -> None:
        self.index = index
        self.budget = budget or ReadBudget()
        self._lock = threading.RLock()
        self._encoded_payload_cache: dict[tuple[str, str], Any] = {}

    def _encoded_payload(self, section_key: str, payload_id: str, reader: ProjectReader) -> Any:
        key = (str(section_key), str(payload_id))
        encoded = self._encoded_payload_cache.get(key)
        if encoded is None:
            encoded = reader.read_payload_with_blob_refs(*key)
            self._encoded_payload_cache[key] = encoded
        return encoded

    def load(self, section_key: str, payload_id: str) -> Any:
        with self._lock:
            with self.index.open_reader(budget=self.budget) as reader:
                encoded = self._encoded_payload(section_key, payload_id, reader)

                def _resolve(digest: str, size: int) -> bytes:
                    descriptor = reader.index.blobs.get(digest)
                    if descriptor is None or descriptor["bytes"] != size:
                        raise SafeCodecError(
                            "Builder codec blob reference does not match its manifest"
                        )
                    return reader.read_blob(digest)

                return decode_envelope(
                    encoded,
                    blob_resolver=_resolve,
                )

    def load_records_for_paths(
        self,
        section_key: str,
        payload_id: str,
        source_paths: Iterable[str | Path],
    ) -> list[Any]:
        """Decode only list records whose encoded ``path`` matches a table row.

        Large overview payloads may contain thousands of independently stored
        array blobs.  The table already carries each row's source paths, so a
        preview can remain portable while reading only the blobs required for
        the visible row.
        """

        wanted = {
            os.path.normcase(os.path.normpath(str(path)))
            for path in source_paths
            if str(path).strip()
        }
        if not wanted:
            return []
        with self._lock:
            with self.index.open_reader(budget=self.budget) as reader:
                encoded = self._encoded_payload(section_key, payload_id, reader)
                if not isinstance(encoded, dict):
                    raise SafeCodecError("Builder record payload envelope is malformed")
                value = encoded.get("value")
                if not isinstance(value, dict) or value.get("$type") != "list":
                    raise SafeCodecError("Builder record payload is not an encoded list")
                items = value.get("items")
                if not isinstance(items, list):
                    raise SafeCodecError("Builder record payload list is malformed")

                selected: list[Any] = []
                for item in items:
                    if not isinstance(item, dict) or item.get("$type") != "dataclass":
                        continue
                    state = item.get("state")
                    pairs = state.get("items") if isinstance(state, dict) else None
                    if not isinstance(pairs, list):
                        continue
                    encoded_path: str | None = None
                    for pair in pairs:
                        if not isinstance(pair, list) or len(pair) != 2 or pair[0] != "path":
                            continue
                        path_value = pair[1]
                        if (
                            isinstance(path_value, dict)
                            and path_value.get("$type") == "path"
                            and isinstance(path_value.get("value"), str)
                        ):
                            encoded_path = path_value["value"]
                        break
                    if encoded_path is None:
                        continue
                    normalised = os.path.normcase(os.path.normpath(encoded_path))
                    if normalised in wanted:
                        selected.append(item)

                subset = dict(encoded)
                subset_value = dict(value)
                subset_value["items"] = selected
                subset["value"] = subset_value

                def _resolve(digest: str, size: int) -> bytes:
                    descriptor = reader.index.blobs.get(digest)
                    if descriptor is None or descriptor["bytes"] != size:
                        raise SafeCodecError(
                            "Builder codec blob reference does not match its manifest"
                        )
                    return reader.read_blob(digest)

                decoded = decode_envelope(subset, blob_resolver=_resolve)
                if not isinstance(decoded, list):
                    raise SafeCodecError("Builder record subset did not decode to a list")
                return decoded


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SafeCodecError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise SafeCodecError(f"Non-finite JSON number is not allowed: {value}")


def _read_json_bytes(raw: bytes, label: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"), object_pairs_hook=_json_pairs, parse_constant=_reject_constant
        )
    except SafeCodecError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SafeCodecError(f"Invalid JSON in Builder package {label}") from exc


def _json_bytes(value: Any, label: str) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SafeCodecError(f"Builder package {label} is not safe JSON") from exc


def _safe_entry_path(name: str) -> bool:
    if (
        not name
        or "\\" in name
        or "\x00" in name
        or ":" in name
        or unicodedata.normalize("NFC", name) != name
    ):
        return False
    candidate = PurePosixPath(name)
    return not candidate.is_absolute() and all(
        part not in {"", ".", ".."} for part in candidate.parts
    )


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return info.create_system == 3 and stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK


def _open_archive(path: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(Path(path), "r", allowZip64=True)
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise SafeCodecError(f"Invalid packaged Builder project: {path}") from exc


def _validated_info_map(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise SafeCodecError("Builder package contains too many entries")
    result: dict[str, zipfile.ZipInfo] = {}
    canonical_names: set[str] = set()
    aggregate = 0
    for info in infos:
        name = info.filename
        if name in result:
            raise SafeCodecError(f"Duplicate Builder package entry: {name}")
        canonical_name = unicodedata.normalize("NFC", name).casefold()
        if canonical_name in canonical_names:
            raise SafeCodecError(f"Case or Unicode-colliding Builder package entry: {name}")
        canonical_names.add(canonical_name)
        if not _safe_entry_path(name) or info.is_dir() or _is_symlink(info):
            raise SafeCodecError(f"Unsafe Builder package entry: {name!r}")
        if info.flag_bits & 1:
            raise SafeCodecError(f"Encrypted Builder package entry is not allowed: {name}")
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise SafeCodecError(f"Unsupported Builder package compression: {name}")
        aggregate += info.file_size
        if aggregate > MAX_AGGREGATE_BYTES:
            raise SafeCodecError("Builder package exceeds the aggregate decompression limit")
        if (
            info.file_size > STREAM_CHUNK_BYTES
            and info.file_size > max(1, info.compress_size) * MAX_COMPRESSION_RATIO
        ):
            raise SafeCodecError(f"Suspicious Builder package compression ratio: {name}")
        result[name] = info
    return result


def _validate_archive_boundaries(path: Path, infos: list[zipfile.ZipInfo]) -> None:
    """Reject prepended/trailing polyglots and overlapping local entry spans."""

    try:
        raw_size = Path(path).stat().st_size
        with Path(path).open("rb") as handle:
            if handle.read(4) != b"PK\x03\x04":
                raise SafeCodecError("Builder package has prepended data or an invalid ZIP signature")
            tail_size = min(raw_size, 65_557)
            handle.seek(raw_size - tail_size)
            tail = handle.read(tail_size)
            eocd = tail.rfind(b"PK\x05\x06")
            if eocd < 0 or eocd + 22 > len(tail):
                raise SafeCodecError("Builder package ZIP footer is missing")
            comment_size = int.from_bytes(tail[eocd + 20 : eocd + 22], "little")
            if eocd + 22 + comment_size != len(tail):
                raise SafeCodecError("Builder package has trailing data")
            spans: list[tuple[int, int, str]] = []
            for info in infos:
                handle.seek(info.header_offset)
                header = handle.read(30)
                if len(header) != 30 or header[:4] != b"PK\x03\x04":
                    raise SafeCodecError(f"Builder package local header is invalid: {info.filename}")
                name_size = int.from_bytes(header[26:28], "little")
                extra_size = int.from_bytes(header[28:30], "little")
                data_start = info.header_offset + 30 + name_size + extra_size
                spans.append((info.header_offset, data_start + info.compress_size, info.filename))
            offsets = [start for start, _end, _name in spans]
            if not offsets or offsets[0] != 0 or offsets != sorted(offsets):
                raise SafeCodecError(
                    "Builder package central and local entry ordering do not match"
                )
            spans.sort()
            for (_, end, name), (next_start, _, _) in zip(spans, spans[1:]):
                if end > next_start:
                    raise SafeCodecError(f"Builder package entries overlap: {name}")
    except SafeCodecError:
        raise
    except OSError as exc:
        raise SafeCodecError(f"Failed to validate Builder package boundaries: {path}") from exc


def _read_entry_bounded(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    limit: int,
    expected: int | None = None,
    budget: ReadBudget | None = None,
) -> bytes:
    if info.file_size > limit:
        raise SafeCodecError(f"Builder package entry exceeds its limit: {info.filename}")
    chunks: list[bytes] = []
    size = 0
    try:
        with archive.open(info, "r") as handle:
            while True:
                chunk = handle.read(min(STREAM_CHUNK_BYTES, limit + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if budget is not None:
                    budget.consume(len(chunk))
                if size > limit:
                    raise SafeCodecError(f"Builder package entry exceeds its limit: {info.filename}")
                chunks.append(chunk)
    except SafeCodecError:
        raise
    except (OSError, EOFError, RuntimeError, zipfile.BadZipFile) as exc:
        raise SafeCodecError(f"Failed to decompress Builder package entry: {info.filename}") from exc
    if size != info.file_size or (expected is not None and size != expected):
        raise SafeCodecError(f"Builder package entry size mismatch: {info.filename}")
    return b"".join(chunks)


def _stream_entry_digest(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    limit: int,
    budget: ReadBudget | None = None,
) -> str:
    if info.file_size > limit:
        raise SafeCodecError(f"Builder package entry exceeds its limit: {info.filename}")
    digest = hashlib.sha256()
    size = 0
    try:
        with archive.open(info, "r") as handle:
            while True:
                chunk = handle.read(STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise SafeCodecError(f"Builder package entry exceeds its limit: {info.filename}")
                if budget is not None:
                    budget.consume(len(chunk))
                digest.update(chunk)
    except SafeCodecError:
        raise
    except (OSError, EOFError, RuntimeError, zipfile.BadZipFile) as exc:
        raise SafeCodecError(f"Failed to verify Builder package entry: {info.filename}") from exc
    if size != info.file_size:
        raise SafeCodecError(f"Builder package entry size mismatch: {info.filename}")
    return digest.hexdigest()


def _entry_limit(role: str) -> int:
    if role == "payload":
        return MAX_PAYLOAD_JSON_BYTES
    if role == "blob":
        return MAX_BLOB_BYTES
    return MAX_JSON_ENTRY_BYTES


def _descriptor_from_json(value: Any) -> EntryDescriptor:
    if not isinstance(value, dict) or set(value) != {
        "path", "role", "media_type", "sha256", "bytes"
    }:
        raise SafeCodecError("Builder package entry descriptor is malformed")
    path, role, media, digest, size = (
        value["path"], value["role"], value["media_type"], value["sha256"], value["bytes"]
    )
    if not all(isinstance(item, str) and item for item in (path, role, media)):
        raise SafeCodecError("Builder package entry descriptor text is invalid")
    if not _safe_entry_path(path) or not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise SafeCodecError("Builder package entry descriptor path/checksum is invalid")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0 or size > _entry_limit(role):
        raise SafeCodecError("Builder package entry descriptor size is invalid")
    return EntryDescriptor(path, role, media, digest, size)


def _deep_freeze(value: Any) -> Any:
    """Make parsed manifest/index structures immutable after validation."""

    if isinstance(value, dict):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _validated_migration_provenance(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != MIGRATION_PROVENANCE_FIELDS:
        raise SafeCodecError("Builder package migration provenance is malformed")
    result = dict(value)
    if (
        not isinstance(result["source_name"], str)
        or not result["source_name"]
        or len(result["source_name"]) > 4096
        or not isinstance(result["source_sha256"], str)
        or not _SHA256_RE.fullmatch(result["source_sha256"])
        or not isinstance(result["migrated_at"], str)
        or not result["migrated_at"]
    ):
        raise SafeCodecError("Builder package migration provenance text is invalid")
    for key in (
        "source_bytes", "legacy_version", "section_count", "payload_count",
        "pickle_payload_count",
    ):
        number = result[key]
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            raise SafeCodecError("Builder package migration provenance count is invalid")
    if result["source_bytes"] > MAX_PACKAGE_BYTES:
        raise SafeCodecError("Builder package migration source size is invalid")
    return result


def inspect_project_package(path: Path, *, verify_entries: bool = False) -> ProjectIndex:
    target = Path(path)
    fingerprint = FileFingerprint.capture(target)
    if fingerprint.size > MAX_PACKAGE_BYTES:
        raise SafeCodecError("Builder package exceeds the 2 GiB package limit")
    with _open_archive(target) as archive:
        infos = archive.infolist()
        _validate_archive_boundaries(target, infos)
        info_map = _validated_info_map(archive)
        total_uncompressed = sum(info.file_size for info in infos)
        total_compressed = sum(info.compress_size for info in infos)
        if (
            total_uncompressed > STREAM_CHUNK_BYTES
            and total_uncompressed > max(1, total_compressed) * MAX_COMPRESSION_RATIO
        ):
            raise SafeCodecError("Suspicious aggregate Builder package compression ratio")
        if len(infos) < 2 or infos[0].filename != MIMETYPE_PATH or infos[1].filename != MANIFEST_PATH:
            raise SafeCodecError("Builder package must start with mimetype then manifest.json")
        if infos[0].compress_type != zipfile.ZIP_STORED or infos[1].compress_type != zipfile.ZIP_STORED:
            raise SafeCodecError("Builder package mimetype and manifest must be uncompressed")
        mimetype = _read_entry_bounded(archive, infos[0], limit=256)
        if mimetype.decode("ascii", errors="strict") != MIMETYPE:
            raise SafeCodecError("Builder package mimetype is unsupported")
        manifest_raw = _read_entry_bounded(archive, infos[1], limit=MAX_MANIFEST_BYTES)
    manifest = _read_json_bytes(manifest_raw, "manifest")
    if not isinstance(manifest, dict):
        raise SafeCodecError("Builder package manifest must be an object")
    required_keys = {
        "format", "version", "kind", "project_id", "revision", "created_at",
        "saved_at", "writer", "codec", "required_features", "optional_features",
        "entries", "sections", "blobs", "migration",
    }
    if set(manifest) != required_keys:
        raise SafeCodecError("Builder package manifest has missing or unexpected fields")
    if manifest["format"] != PACKAGE_FORMAT or manifest["version"] != PACKAGE_VERSION or manifest["kind"] != PROJECT_KIND:
        raise SafeCodecError("Unsupported Builder package format, version, or kind")
    if not isinstance(manifest["project_id"], str) or not _PROJECT_ID_RE.fullmatch(manifest["project_id"]):
        raise SafeCodecError("Builder package project_id is invalid")
    if isinstance(manifest["revision"], bool) or not isinstance(manifest["revision"], int) or manifest["revision"] < 1:
        raise SafeCodecError("Builder package revision is invalid")
    for key in ("created_at", "saved_at", "writer", "codec"):
        if not isinstance(manifest[key], str) or not manifest[key]:
            raise SafeCodecError(f"Builder package {key} is invalid")
    if manifest["codec"] != CODEC_ID:
        raise SafeCodecError("Unsupported Builder package codec")
    _validated_migration_provenance(manifest["migration"])
    required_features = manifest["required_features"]
    optional_features = manifest["optional_features"]
    if not isinstance(required_features, list) or not all(isinstance(x, str) for x in required_features):
        raise SafeCodecError("Builder package required_features is invalid")
    if set(required_features) != SUPPORTED_REQUIRED_FEATURES or len(required_features) != len(
        SUPPORTED_REQUIRED_FEATURES
    ):
        raise SafeCodecError(
            "Builder package required feature set has a missing or unsupported feature"
        )
    if not isinstance(optional_features, list):
        raise SafeCodecError("Builder package optional_features is invalid")
    optional_entry_owners: dict[str, str] = {}
    optional_feature_ids: set[str] = set()
    for feature in optional_features:
        if not isinstance(feature, dict) or set(feature) != {"id", "entries"}:
            raise SafeCodecError("Builder package optional feature descriptor is malformed")
        feature_id = feature["id"]
        feature_entries = feature["entries"]
        if (
            not isinstance(feature_id, str)
            or not _ID_RE.fullmatch(feature_id)
            or feature_id in optional_feature_ids
            or not isinstance(feature_entries, list)
            or not feature_entries
        ):
            raise SafeCodecError("Builder package optional feature descriptor is invalid")
        optional_feature_ids.add(feature_id)
        for entry_path in feature_entries:
            if (
                not isinstance(entry_path, str)
                or not _safe_entry_path(entry_path)
                or entry_path in optional_entry_owners
            ):
                raise SafeCodecError("Builder package optional feature entry ownership is invalid")
            optional_entry_owners[entry_path] = feature_id

    raw_entries = manifest["entries"]
    if not isinstance(raw_entries, list):
        raise SafeCodecError("Builder package entries must be a list")
    entries: dict[str, EntryDescriptor] = {}
    for value in raw_entries:
        descriptor = _descriptor_from_json(value)
        if descriptor.path in entries:
            raise SafeCodecError(f"Duplicate Builder manifest entry: {descriptor.path}")
        entries[descriptor.path] = descriptor
    archive_paths = set(info_map) - {MIMETYPE_PATH, MANIFEST_PATH}
    if archive_paths != set(entries):
        raise SafeCodecError("Builder package contains missing or unlisted ZIP entries")
    for entry in entries.values():
        info = info_map[entry.path]
        if info.file_size != entry.bytes:
            raise SafeCodecError(f"Builder package central size mismatch: {entry.path}")

    raw_sections = manifest["sections"]
    if not isinstance(raw_sections, dict) or len(raw_sections) > MAX_SECTIONS:
        raise SafeCodecError("Builder package sections are invalid")
    if not set(raw_sections).issubset(ALLOWED_SECTION_KEYS):
        raise SafeCodecError("Builder package contains an unknown section")
    sections: dict[str, dict[str, Any]] = {}
    referenced: set[str] = set()
    for key, value in raw_sections.items():
        if not isinstance(value, dict) or set(value) != {"state", "table", "payloads"}:
            raise SafeCodecError(f"Builder package section descriptor is malformed: {key}")
        state_path = f"sections/{key}/state.json"
        table_path = f"sections/{key}/table.json"
        if value["state"] != state_path or value["table"] != table_path:
            raise SafeCodecError(f"Builder package section paths are not canonical: {key}")
        payloads = value["payloads"]
        if not isinstance(payloads, dict):
            raise SafeCodecError(f"Builder package payload map is invalid: {key}")
        canonical_payload_ids: set[str] = set()
        for payload_id, payload_path in payloads.items():
            if not isinstance(payload_id, str) or not _ID_RE.fullmatch(payload_id):
                raise SafeCodecError(f"Builder package payload id is invalid: {key}")
            canonical_id = unicodedata.normalize("NFC", payload_id).casefold()
            if canonical_id in canonical_payload_ids:
                raise SafeCodecError(f"Builder package payload ids collide: {key}")
            canonical_payload_ids.add(canonical_id)
            if payload_path != f"sections/{key}/payloads/{payload_id}.json":
                raise SafeCodecError(f"Builder package payload path is not canonical: {key}.{payload_id}")
        paths = {state_path, table_path, *payloads.values()}
        if any(path not in entries for path in paths):
            raise SafeCodecError(f"Builder package section references a missing entry: {key}")
        if entries[state_path].role != "state" or entries[table_path].role != "table" or any(entries[path].role != "payload" for path in payloads.values()):
            raise SafeCodecError(f"Builder package section entry role is invalid: {key}")
        referenced.update(paths)
        sections[key] = {"state": state_path, "table": table_path, "payloads": dict(payloads)}

    raw_blobs = manifest["blobs"]
    if not isinstance(raw_blobs, dict):
        raise SafeCodecError("Builder package blobs must be an object")
    blobs: dict[str, dict[str, Any]] = {}
    for digest, value in raw_blobs.items():
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise SafeCodecError("Builder package blob digest is invalid")
        if not isinstance(value, dict) or set(value) != {"path", "bytes", "media_type"}:
            raise SafeCodecError("Builder package blob descriptor is malformed")
        if (
            isinstance(value["bytes"], bool)
            or not isinstance(value["bytes"], int)
            or value["bytes"] < 0
            or value["bytes"] > MAX_BLOB_BYTES
            or not isinstance(value["media_type"], str)
            or value["media_type"] != "application/octet-stream"
        ):
            raise SafeCodecError("Builder package blob descriptor types are invalid")
        expected_path = f"blobs/sha256/{digest[:2]}/{digest}"
        if value["path"] != expected_path or expected_path not in entries:
            raise SafeCodecError("Builder package blob path is invalid")
        if entries[expected_path].role != "blob" or entries[expected_path].sha256 != digest or entries[expected_path].bytes != value["bytes"]:
            raise SafeCodecError("Builder package blob entry does not match its descriptor")
        referenced.add(expected_path)
        blobs[digest] = dict(value)
    # Optional extensions must explicitly and exclusively own every opaque entry.
    unreferenced = set(entries) - referenced
    if unreferenced != set(optional_entry_owners):
        raise SafeCodecError("Builder package optional entry ownership does not match its entries")
    if referenced & set(optional_entry_owners):
        raise SafeCodecError("Builder package optional feature claims a core entry")
    for entry_path, feature_id in optional_entry_owners.items():
        if entries[entry_path].role != f"extension:{feature_id}":
            raise SafeCodecError("Builder package optional entry role does not match its owner")

    index = ProjectIndex(
        target,
        _deep_freeze(manifest),
        MappingProxyType(dict(entries)),
        _deep_freeze(sections),
        _deep_freeze(blobs),
        fingerprint,
        hashlib.sha256(manifest_raw).hexdigest(),
    )
    if verify_entries:
        with index.open_reader() as reader:
            for entry_path in entries:
                reader.verify_entry(entry_path)
    index.assert_unchanged()
    return index


def _externalize_blobs(value: Any, blobs: dict[str, bytes]) -> Any:
    if isinstance(value, list):
        return [_externalize_blobs(item, blobs) for item in value]
    if not isinstance(value, dict):
        return value
    tag = value.get("$type")
    field = "value" if tag == "bytes" else ("data" if tag == "ndarray" else None)
    if field is not None and isinstance(value.get(field), str):
        try:
            raw = base64.b64decode(value[field].encode("ascii"), validate=True)
        except (UnicodeError, ValueError) as exc:
            raise SafeCodecError("Invalid base64 data in Builder codec payload") from exc
        if len(raw) > MAX_BLOB_BYTES:
            raise SafeCodecError("Builder codec blob exceeds its safe size limit")
        digest = hashlib.sha256(raw).hexdigest()
        blobs.setdefault(digest, raw)
        replaced = {key: _externalize_blobs(item, blobs) for key, item in value.items() if key != field}
        replaced["$blob"] = {"sha256": digest, "bytes": len(raw)}
        return replaced
    return {key: _externalize_blobs(item, blobs) for key, item in value.items()}


def _validate_tree_limits(value: Any, *, depth: int = 0, counter: list[int] | None = None) -> None:
    if counter is None:
        counter = [0]
    counter[0] += 1
    if counter[0] > 5_000_000:
        raise SafeCodecError("Builder package JSON tree exceeds its node limit")
    if depth > 100:
        raise SafeCodecError("Builder package JSON tree exceeds its depth limit")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise SafeCodecError("Builder package JSON object keys must be text")
            _validate_tree_limits(item, depth=depth + 1, counter=counter)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_tree_limits(item, depth=depth + 1, counter=counter)
    elif isinstance(value, float) and not math.isfinite(value):
        raise SafeCodecError("Builder package JSON values must be finite")


def _collect_blob_refs(value: Any, result: set[str], *, depth: int = 0) -> None:
    if depth > 100:
        raise SafeCodecError("Builder package payload nesting is too deep")
    if isinstance(value, list):
        for item in value:
            _collect_blob_refs(item, result, depth=depth + 1)
    elif isinstance(value, dict):
        ref = value.get("$blob")
        if isinstance(ref, dict) and isinstance(ref.get("sha256"), str):
            result.add(ref["sha256"])
        for item in value.values():
            _collect_blob_refs(item, result, depth=depth + 1)


def _restore_blob_refs(value: Any, reader: ProjectReader) -> Any:
    if isinstance(value, list):
        return [_restore_blob_refs(item, reader) for item in value]
    if not isinstance(value, dict):
        return value
    blob_ref = value.get("$blob")
    if blob_ref is not None:
        if not isinstance(blob_ref, dict) or set(blob_ref) != {"sha256", "bytes"}:
            raise SafeCodecError("Builder codec blob reference is malformed")
        digest, byte_count = blob_ref["sha256"], blob_ref["bytes"]
        if not isinstance(digest, str) or digest not in reader.index.blobs or reader.index.blobs[digest]["bytes"] != byte_count:
            raise SafeCodecError("Builder codec blob reference is invalid")
        raw = reader.read_blob(digest)
        if len(raw) != byte_count:
            raise SafeCodecError("Builder codec blob length is invalid")
        tag = value.get("$type")
        field = "value" if tag == "bytes" else ("data" if tag == "ndarray" else None)
        if field is None:
            raise SafeCodecError("Builder codec blob reference has an unsupported tag")
        restored = {key: _restore_blob_refs(item, reader) for key, item in value.items() if key != "$blob"}
        restored[field] = base64.b64encode(raw).decode("ascii")
        return restored
    return {key: _restore_blob_refs(item, reader) for key, item in value.items()}


def is_project_package(path: Path) -> bool:
    try:
        with Path(path).open("rb") as handle:
            if handle.read(4) != b"PK\x03\x04":
                return False
        with zipfile.ZipFile(Path(path), "r", allowZip64=True) as archive:
            infos = archive.infolist()
            if not infos or infos[0].filename != MIMETYPE_PATH:
                return False
            if infos[0].compress_type != zipfile.ZIP_STORED:
                return False
            expected = MIMETYPE.encode("ascii")
            if infos[0].file_size != len(expected):
                return False
            return _read_entry_bounded(
                archive, infos[0], limit=len(expected), expected=len(expected)
            ) == expected
    except (
        OSError,
        RuntimeError,
        SafeCodecError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
    ):
        return False


def load_project(path: Path) -> dict[str, Any]:
    if is_project_package(path):
        return inspect_project_package(path).materialize()
    payload = read_json_file(path)
    if not isinstance(payload, dict):
        raise SafeCodecError("Builder project root must be an object")
    return payload


def load_project_table_projection(
    path: Path,
    *,
    section_keys: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Load safe Builder table state without materializing embedded payloads."""

    target = Path(path)
    supported_formats = f"Builder package v{PACKAGE_VERSION} or legacy UTF-8 Builder JSON"
    try:
        packaged = is_project_package(target)
        if packaged:
            index = inspect_project_package(target)
            requested = (
                None if section_keys is None else {str(key) for key in section_keys}
            )
            selected = [
                key
                for key in index.sections
                if requested is None or key in requested
            ]
            payload = index.project_header()
            with index.open_reader() as reader:
                payload["sections"] = {
                    key: reader.read_section(key, load_payloads=False)
                    for key in selected
                }
        else:
            payload = load_project(target)
    except SafeCodecError as exc:
        raise SafeCodecError(
            f"Unsupported or corrupt Builder project; expected {supported_formats}. {exc}"
        ) from exc

    kind = payload.get("kind")
    if isinstance(kind, str) and kind and kind != PROJECT_KIND:
        raise SafeCodecError(
            f"Unsupported Builder project kind {kind!r}; expected {PROJECT_KIND!r}."
        )
    supported_json_versions = frozenset({1, 2, PACKAGE_VERSION})
    version = payload.get("version")
    if version is not None and (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version not in supported_json_versions
    ):
        supported_versions = ", ".join(
            str(item) for item in sorted(supported_json_versions)
        )
        raise SafeCodecError(
            f"Unsupported legacy Builder JSON version {version!r}; "
            f"supported versions are {supported_versions}."
        )

    if not packaged:
        sections = payload.get("sections")
        if isinstance(sections, Mapping):
            requested = (
                None if section_keys is None else {str(key) for key in section_keys}
            )
            selected = [
                str(key)
                for key in sections
                if requested is None or str(key) in requested
            ]
            payload = dict(payload)
            payload["sections"] = {
                key: {
                    field: value
                    for field, value in section.items()
                    if field != "payloads"
                }
                for key in selected
                if isinstance((section := sections.get(key)), Mapping)
            }
    return payload


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _entry_json_descriptor(path: str, role: str, raw: bytes) -> EntryDescriptor:
    return EntryDescriptor(path, role, "application/json", hashlib.sha256(raw).hexdigest(), len(raw))


def write_project_package(
    path: Path,
    payload: Mapping[str, Any],
    *,
    replace_existing: bool = True,
    source_index: ProjectIndex | None = None,
    loaded_sections: set[str] | None = None,
    trusted_migration: Mapping[str, Any] | None = None,
) -> ProjectIndex:
    """Write a fresh package, preserving logically unloaded source entries by SHA."""

    target = Path(path)
    if payload.get("kind") != PROJECT_KIND:
        raise SafeCodecError("Builder project kind is invalid")
    raw_sections = payload.get("sections")
    if not isinstance(raw_sections, Mapping):
        raise SafeCodecError("Builder project sections must be an object")
    if not set(raw_sections).issubset(ALLOWED_SECTION_KEYS):
        raise SafeCodecError("Builder project contains an unknown section")
    if source_index is not None:
        source_index.assert_unchanged()
    loaded = set(raw_sections) if loaded_sections is None else set(loaded_sections)
    if not loaded.issubset(raw_sections):
        raise SafeCodecError("Loaded Builder sections must be present in the save payload")

    entry_data: dict[str, bytes] = {}
    entry_files: dict[str, Path] = {}
    entry_descriptors: dict[str, EntryDescriptor] = {}
    source_copy_paths: set[str] = set()
    section_descriptors: dict[str, dict[str, Any]] = {}
    blobs: dict[str, bytes] = {}
    blob_descriptors: dict[str, dict[str, Any]] = {}

    def _descriptor_for_file(
        entry_path: str, source_path: Path, role: str,
        media_type: str = "application/json",
    ) -> EntryDescriptor:
        digest = hashlib.sha256()
        size = 0
        with Path(source_path).open("rb") as handle:
            while True:
                chunk = handle.read(STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > _entry_limit(role):
                    raise SafeCodecError(
                        f"Builder staged entry exceeds its limit: {entry_path}"
                    )
                digest.update(chunk)
        return EntryDescriptor(entry_path, role, media_type, digest.hexdigest(), size)

    if source_index is not None:
        reachable_source_blobs: set[str] = set()
        preserved_source_payloads: dict[str, dict[str, str]] = {}
        with source_index.open_reader() as source_reader:
            for key, descriptor in source_index.sections.items():
                source_payloads = dict(descriptor["payloads"])
                if key in loaded:
                    incoming_section = raw_sections.get(key, {})
                    incoming_payloads = (
                        incoming_section.get("payloads", {})
                        if isinstance(incoming_section, Mapping)
                        else {}
                    )
                    incoming_ids = (
                        set(incoming_payloads)
                        if isinstance(incoming_payloads, Mapping)
                        else set()
                    )
                    deleted_ids_raw = (
                        incoming_section.get(DELETED_PAYLOADS_KEY, ())
                        if isinstance(incoming_section, Mapping)
                        else ()
                    )
                    if not isinstance(deleted_ids_raw, (list, tuple, set)) or not all(
                        isinstance(item, str) and _ID_RE.fullmatch(item)
                        for item in deleted_ids_raw
                    ):
                        raise SafeCodecError(
                            f"Builder project deleted payload ids are invalid: {key}"
                        )
                    deleted_ids = set(deleted_ids_raw)
                    source_payloads = {
                        payload_id: entry_path
                        for payload_id, entry_path in source_payloads.items()
                        if payload_id not in incoming_ids and payload_id not in deleted_ids
                    }
                preserved_source_payloads[key] = source_payloads
                for payload_path in source_payloads.values():
                    encoded = _read_json_bytes(
                        source_reader.read_entry(payload_path), payload_path
                    )
                    _collect_blob_refs(encoded, reachable_source_blobs)
        for key, descriptor in source_index.sections.items():
            if key in loaded:
                for entry_path in preserved_source_payloads[key].values():
                    source_copy_paths.add(entry_path)
                    entry_descriptors[entry_path] = source_index.entries[entry_path]
                continue
            section_descriptors[key] = {
                "state": descriptor["state"], "table": descriptor["table"],
                "payloads": dict(preserved_source_payloads[key]),
            }
            for entry_path in {
                descriptor["state"],
                descriptor["table"],
                *preserved_source_payloads[key].values(),
            }:
                source_copy_paths.add(entry_path)
                entry_descriptors[entry_path] = source_index.entries[entry_path]
        for digest in reachable_source_blobs:
            descriptor = source_index.blobs.get(digest)
            if descriptor is None:
                raise SafeCodecError("Unloaded Builder section references a missing source blob")
            blob_path = descriptor["path"]
            blob_descriptors[digest] = dict(descriptor)
            source_copy_paths.add(blob_path)
            entry_descriptors[blob_path] = source_index.entries[blob_path]

    for key in loaded:
        raw_section = raw_sections[key]
        if not isinstance(raw_section, Mapping):
            raise SafeCodecError(f"Builder project section must be an object: {key}")
        section = dict(raw_section)
        _validate_tree_limits(section)
        section.pop(DELETED_PAYLOADS_KEY, None)
        table = {
            "columns": section.pop("columns", []),
            "rows": section.pop("rows", []),
            "index": section.pop("index", []),
        }
        raw_payloads = section.pop("payloads", {})
        if not isinstance(raw_payloads, Mapping):
            raise SafeCodecError(f"Builder project payload map must be an object: {key}")
        state_path = f"sections/{key}/state.json"
        table_path = f"sections/{key}/table.json"
        state_raw = _json_bytes(section, f"{key} state")
        table_raw = _json_bytes(table, f"{key} table")
        for entry_path, role, raw in ((state_path, "state", state_raw), (table_path, "table", table_raw)):
            if len(raw) > MAX_JSON_ENTRY_BYTES:
                raise SafeCodecError(f"Builder package JSON entry exceeds its limit: {entry_path}")
            entry_data[entry_path] = raw
            entry_descriptors[entry_path] = _entry_json_descriptor(entry_path, role, raw)
        payload_paths: dict[str, str] = (
            dict(preserved_source_payloads.get(key, {}))
            if source_index is not None
            else {}
        )
        for payload_id, encoded in raw_payloads.items():
            if not isinstance(payload_id, str) or not _ID_RE.fullmatch(payload_id):
                raise SafeCodecError(f"Builder project payload id is invalid: {key}")
            entry_path = f"sections/{key}/payloads/{payload_id}.json"
            if isinstance(encoded, StagedEncodedPayload):
                entry_files[entry_path] = Path(encoded.json_path)
                entry_descriptors[entry_path] = _descriptor_for_file(
                    entry_path, encoded.json_path, "payload"
                )
                for digest, blob_file in encoded.blobs.items():
                    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
                        raise SafeCodecError("Staged Builder blob digest is invalid")
                    blob_path = f"blobs/sha256/{digest[:2]}/{digest}"
                    blob_entry = _descriptor_for_file(
                        blob_path, blob_file, "blob", "application/octet-stream"
                    )
                    if blob_entry.sha256 != digest:
                        raise SafeCodecError("Staged Builder blob checksum is invalid")
                    entry_files[blob_path] = Path(blob_file)
                    entry_descriptors[blob_path] = blob_entry
                    source_copy_paths.discard(blob_path)
                    blob_descriptors[digest] = {
                        "path": blob_path,
                        "bytes": blob_entry.bytes,
                        "media_type": "application/octet-stream",
                    }
            else:
                encoded_external = _externalize_blobs(encoded, blobs)
                raw = _json_bytes(encoded_external, f"{key}.{payload_id}")
                if len(raw) > MAX_PAYLOAD_JSON_BYTES:
                    raise SafeCodecError(f"Builder package payload JSON exceeds its limit: {key}.{payload_id}")
                entry_data[entry_path] = raw
                entry_descriptors[entry_path] = _entry_json_descriptor(entry_path, "payload", raw)
            payload_paths[payload_id] = entry_path
        section_descriptors[key] = {"state": state_path, "table": table_path, "payloads": payload_paths}

    for digest, raw in blobs.items():
        blob_path = f"blobs/sha256/{digest[:2]}/{digest}"
        source_copy_paths.discard(blob_path)
        blob_descriptors[digest] = {
            "path": blob_path, "bytes": len(raw), "media_type": "application/octet-stream"
        }
        entry_data[blob_path] = raw
        entry_descriptors[blob_path] = EntryDescriptor(
            blob_path, "blob", "application/octet-stream", digest, len(raw)
        )

    # Preserve unknown optional entries opaquely and by logical digest.
    optional_features: list[dict[str, Any]] = []
    if source_index is not None:
        optional_features = [
            {"id": feature["id"], "entries": list(feature["entries"])}
            for feature in source_index.manifest["optional_features"]
        ]
        known_source_paths = {
            path for section in source_index.sections.values()
            for path in {section["state"], section["table"], *section["payloads"].values()}
        } | {blob["path"] for blob in source_index.blobs.values()}
        for entry_path in set(source_index.entries) - known_source_paths:
            source_copy_paths.add(entry_path)
            entry_descriptors[entry_path] = source_index.entries[entry_path]

    now = datetime.now(UTC).isoformat(timespec="seconds")
    project_id = source_index.manifest["project_id"] if source_index else uuid4().hex
    revision = source_index.manifest["revision"] + 1 if source_index else 1
    created_at = source_index.manifest["created_at"] if source_index else now
    writer = str(payload.get("writer") or "PyPlot Microwire Data Builder")
    manifest = {
        "format": PACKAGE_FORMAT, "version": PACKAGE_VERSION, "kind": PROJECT_KIND,
        "project_id": project_id, "revision": revision, "created_at": created_at,
        "saved_at": now, "writer": writer, "codec": CODEC_ID,
        "required_features": list(DEFAULT_REQUIRED_FEATURES),
        "optional_features": optional_features,
        "entries": [entry.__dict__ for _, entry in sorted(entry_descriptors.items())],
        "sections": {key: section_descriptors[key] for key in sorted(section_descriptors)},
        "blobs": {digest: blob_descriptors[digest] for digest in sorted(blob_descriptors)},
        "migration": _validated_migration_provenance(
            trusted_migration
            if trusted_migration is not None
            else (source_index.manifest["migration"] if source_index is not None else None)
        ),
    }
    if trusted_migration is not None:
        manifest["writer"] = f"{writer}; trusted migration"
    manifest_raw = _json_bytes(manifest, "manifest")
    if len(manifest_raw) > MAX_MANIFEST_BYTES:
        raise SafeCodecError("Builder package manifest exceeds its safe size limit")

    target.parent.mkdir(parents=True, exist_ok=True)
    expected_target = FileFingerprint.capture(target) if target.exists() else None
    lock_path = target.parent / f".{target.name}.save.lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
    except FileExistsError as exc:
        raise SafeCodecError(f"Another process is saving Builder project {target}") from exc
    temp_path: Path | None = None
    placeholder_created = False
    placeholder_fingerprint: FileFingerprint | None = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        os.close(fd)
        temp_path = Path(temp_name)
        with zipfile.ZipFile(
            temp_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
            allowZip64=True,
        ) as archive:
            archive.writestr(MIMETYPE_PATH, MIMETYPE, compress_type=zipfile.ZIP_STORED)
            archive.writestr(MANIFEST_PATH, manifest_raw, compress_type=zipfile.ZIP_STORED)
            for entry_path in sorted(entry_data):
                compression = _safe_zip_compression_for_bytes(entry_data[entry_path])
                archive.writestr(
                    entry_path,
                    entry_data[entry_path],
                    compress_type=compression,
                    compresslevel=None if compression == zipfile.ZIP_STORED else 6,
                )
            for entry_path in sorted(entry_files):
                compression = _safe_zip_compression_for_file(entry_files[entry_path])
                zip_info = zipfile.ZipInfo(entry_path)
                zip_info.compress_type = compression
                with entry_files[entry_path].open("rb") as source_handle, archive.open(
                    zip_info, "w", force_zip64=True
                ) as destination_handle:
                    shutil.copyfileobj(source_handle, destination_handle, STREAM_CHUNK_BYTES)
            if source_copy_paths:
                if source_index is None:
                    raise SafeCodecError("Builder package source entries have no source index")
                with source_index.open_reader() as source_reader:
                    source_archive = source_reader._require_open()
                    for entry_path in sorted(source_copy_paths):
                        descriptor = entry_descriptors[entry_path]
                        info = source_reader.info_map[entry_path]
                        zip_info = zipfile.ZipInfo(entry_path)
                        zip_info.compress_type = _safe_zip_compression_for_archive_entry(
                            source_archive, info
                        )
                        digest = hashlib.sha256()
                        copied = 0
                        with source_archive.open(info, "r") as source_handle, archive.open(
                            zip_info, "w", force_zip64=True
                        ) as destination_handle:
                            while True:
                                chunk = source_handle.read(STREAM_CHUNK_BYTES)
                                if not chunk:
                                    break
                                copied += len(chunk)
                                if copied > _entry_limit(descriptor.role):
                                    raise SafeCodecError(
                                        f"Builder source entry exceeds its limit: {entry_path}"
                                    )
                                source_reader.budget.consume(len(chunk))
                                digest.update(chunk)
                                destination_handle.write(chunk)
                        if copied != descriptor.bytes or digest.hexdigest() != descriptor.sha256:
                            raise SafeCodecError(
                                f"Builder source entry changed during streaming copy: {entry_path}"
                            )
        if temp_path.stat().st_size > MAX_PACKAGE_BYTES:
            raise SafeCodecError("Builder package exceeds the 2 GiB package limit")
        with temp_path.open("r+b") as handle:
            os.fsync(handle.fileno())
        verified = inspect_project_package(temp_path, verify_entries=True)
        if source_index is not None:
            source_index.assert_unchanged()
        current_target = FileFingerprint.capture(target) if target.exists() else None
        if current_target != expected_target:
            raise SafeCodecError("Builder project target changed during save; publish was cancelled")
        if replace_existing:
            os.replace(temp_path, target)
        else:
            placeholder_fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.close(placeholder_fd)
            placeholder_created = True
            placeholder_fingerprint = FileFingerprint.capture(target)
            os.replace(temp_path, target)
            placeholder_created = False
            placeholder_fingerprint = None
        temp_path = None
        _fsync_directory(target.parent)
        return inspect_project_package(target)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
        if placeholder_created and placeholder_fingerprint is not None:
            try:
                if FileFingerprint.capture(target) == placeholder_fingerprint:
                    target.unlink()
            except (FileNotFoundError, SafeCodecError):
                pass
        os.close(lock_fd)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def write_project_package_streaming(
    path: Path,
    sections: Iterable[tuple[str, Mapping[str, Any] | StagedProjectSection]],
    *,
    source_saved_at: str | None = None,
    replace_existing: bool = False,
    progress: Callable[[str], None] | None = None,
    migration_provenance: Mapping[str, Any] | None = None,
) -> ProjectIndex:
    """Stage a section iterator on disk, then publish a strict v3 package.

    This entrypoint is used by trusted legacy migration so neither all decoded
    sections nor all encoded ZIP entries coexist in memory.
    """

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not replace_existing:
        raise FileExistsError(target)
    expected_target = FileFingerprint.capture(target) if target.exists() else None
    lock_path = target.parent / f".{target.name}.save.lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
    except FileExistsError as exc:
        raise SafeCodecError(f"Another process is saving Builder project {target}") from exc
    temp_path: Path | None = None
    placeholder_created = False
    placeholder_fingerprint: FileFingerprint | None = None
    staging_dir: Path | None = None
    try:
        staging_dir = Path(
            tempfile.mkdtemp(prefix=f".{target.name}.stage.", dir=target.parent)
        )
        entry_descriptors: dict[str, EntryDescriptor] = {}
        section_descriptors: dict[str, dict[str, Any]] = {}
        blob_descriptors: dict[str, dict[str, Any]] = {}

        def _stage(entry_path: str, raw: bytes, role: str, media: str = "application/json") -> None:
            if len(raw) > _entry_limit(role):
                raise SafeCodecError(f"Builder staged entry exceeds its limit: {entry_path}")
            disk_path = staging_dir.joinpath(*PurePosixPath(entry_path).parts)
            disk_path.parent.mkdir(parents=True, exist_ok=True)
            disk_path.write_bytes(raw)
            entry_descriptors[entry_path] = EntryDescriptor(
                entry_path, role, media, hashlib.sha256(raw).hexdigest(), len(raw)
            )

        def _stage_file(
            entry_path: str,
            source_path: Path,
            role: str,
            media: str = "application/json",
        ) -> EntryDescriptor:
            disk_path = staging_dir.joinpath(*PurePosixPath(entry_path).parts)
            disk_path.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            size = 0
            with Path(source_path).open("rb") as source_handle, disk_path.open("wb") as output:
                while True:
                    chunk = source_handle.read(STREAM_CHUNK_BYTES)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > _entry_limit(role):
                        raise SafeCodecError(
                            f"Builder staged entry exceeds its limit: {entry_path}"
                        )
                    digest.update(chunk)
                    output.write(chunk)
            descriptor = EntryDescriptor(entry_path, role, media, digest.hexdigest(), size)
            entry_descriptors[entry_path] = descriptor
            return descriptor

        def _stage_encoded_payload(
            key: str,
            payload_id: str,
            encoded: Any,
        ) -> str:
            if not isinstance(payload_id, str) or not _ID_RE.fullmatch(payload_id):
                raise SafeCodecError(f"Legacy migration payload id is invalid: {key}")
            payload_path = f"sections/{key}/payloads/{payload_id}.json"
            if isinstance(encoded, StagedEncodedPayload):
                _stage_file(payload_path, encoded.json_path, "payload")
                for digest, source_blob_path in encoded.blobs.items():
                    if not _SHA256_RE.fullmatch(digest):
                        raise SafeCodecError("Staged Builder blob digest is invalid")
                    if digest in blob_descriptors:
                        continue
                    blob_path = f"blobs/sha256/{digest[:2]}/{digest}"
                    blob_entry = _stage_file(
                        blob_path,
                        source_blob_path,
                        "blob",
                        "application/octet-stream",
                    )
                    if blob_entry.sha256 != digest:
                        raise SafeCodecError("Staged Builder blob checksum is invalid")
                    blob_descriptors[digest] = {
                        "path": blob_path,
                        "bytes": blob_entry.bytes,
                        "media_type": "application/octet-stream",
                    }
            else:
                new_blobs: dict[str, bytes] = {}
                external = _externalize_blobs(encoded, new_blobs)
                _stage(payload_path, _json_bytes(external, f"{key}.{payload_id}"), "payload")
                for digest, raw_blob in new_blobs.items():
                    if digest in blob_descriptors:
                        continue
                    blob_path = f"blobs/sha256/{digest[:2]}/{digest}"
                    _stage(blob_path, raw_blob, "blob", "application/octet-stream")
                    blob_descriptors[digest] = {
                        "path": blob_path,
                        "bytes": len(raw_blob),
                        "media_type": "application/octet-stream",
                    }
            return payload_path

        for key, raw_section in sections:
            if key in section_descriptors or key not in ALLOWED_SECTION_KEYS:
                raise SafeCodecError(f"Legacy migration section is duplicate or unknown: {key}")
            if isinstance(raw_section, StagedProjectSection):
                state_path = f"sections/{key}/state.json"
                table_path = f"sections/{key}/table.json"
                _stage_file(state_path, raw_section.state_path, "state")
                _stage_file(table_path, raw_section.table_path, "table")
                payload_paths = {
                    payload_id: _stage_encoded_payload(key, payload_id, encoded)
                    for payload_id, encoded in raw_section.payloads.items()
                }
                section_descriptors[key] = {
                    "state": state_path,
                    "table": table_path,
                    "payloads": payload_paths,
                }
                if progress is not None:
                    progress(key)
                continue
            if not isinstance(raw_section, Mapping):
                raise SafeCodecError(f"Legacy migration section must be an object: {key}")
            section = dict(raw_section)
            _validate_tree_limits(section)
            table = {
                "columns": section.pop("columns", []),
                "rows": section.pop("rows", []),
                "index": section.pop("index", []),
            }
            raw_payloads = section.pop("payloads", {})
            if not isinstance(raw_payloads, Mapping):
                raise SafeCodecError(f"Legacy migration payloads must be an object: {key}")
            state_path = f"sections/{key}/state.json"
            table_path = f"sections/{key}/table.json"
            _stage(state_path, _json_bytes(section, f"{key} state"), "state")
            _stage(table_path, _json_bytes(table, f"{key} table"), "table")
            payload_paths: dict[str, str] = {}
            for payload_id, encoded in raw_payloads.items():
                payload_paths[payload_id] = _stage_encoded_payload(
                    key, payload_id, encoded
                )
            section_descriptors[key] = {
                "state": state_path,
                "table": table_path,
                "payloads": payload_paths,
            }
            if progress is not None:
                progress(key)

        now = datetime.now(UTC).isoformat(timespec="seconds")
        writer = "PyPlot Microwire Data Builder; trusted legacy migration"
        manifest = {
            "format": PACKAGE_FORMAT,
            "version": PACKAGE_VERSION,
            "kind": PROJECT_KIND,
            "project_id": uuid4().hex,
            "revision": 1,
            "created_at": now,
            "saved_at": now,
            "writer": writer,
            "codec": CODEC_ID,
            "required_features": list(DEFAULT_REQUIRED_FEATURES),
            "optional_features": [],
            "entries": [entry.__dict__ for _, entry in sorted(entry_descriptors.items())],
            "sections": {key: section_descriptors[key] for key in sorted(section_descriptors)},
            "blobs": {digest: blob_descriptors[digest] for digest in sorted(blob_descriptors)},
            "migration": _validated_migration_provenance(migration_provenance),
        }
        _ = source_saved_at  # retained only in external migration result; saved_at is always fresh
        manifest_raw = _json_bytes(manifest, "manifest")
        if len(manifest_raw) > MAX_MANIFEST_BYTES:
            raise SafeCodecError("Builder package manifest exceeds its safe size limit")

        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        os.close(fd)
        temp_path = Path(temp_name)
        with zipfile.ZipFile(
            temp_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
            allowZip64=True,
        ) as archive:
            archive.writestr(MIMETYPE_PATH, MIMETYPE, compress_type=zipfile.ZIP_STORED)
            archive.writestr(MANIFEST_PATH, manifest_raw, compress_type=zipfile.ZIP_STORED)
            for entry_path in sorted(entry_descriptors):
                staged = staging_dir.joinpath(*PurePosixPath(entry_path).parts)
                compression = _safe_zip_compression_for_file(staged)
                zip_info = zipfile.ZipInfo(entry_path)
                zip_info.compress_type = compression
                with staged.open("rb") as source_handle, archive.open(
                    zip_info, "w", force_zip64=True
                ) as destination_handle:
                    shutil.copyfileobj(source_handle, destination_handle, STREAM_CHUNK_BYTES)
        with temp_path.open("r+b") as handle:
            os.fsync(handle.fileno())
        inspect_project_package(temp_path, verify_entries=True)
        current_target = FileFingerprint.capture(target) if target.exists() else None
        if current_target != expected_target:
            raise SafeCodecError("Builder migration target changed during publish")
        if replace_existing:
            os.replace(temp_path, target)
        else:
            placeholder_fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.close(placeholder_fd)
            placeholder_created = True
            placeholder_fingerprint = FileFingerprint.capture(target)
            os.replace(temp_path, target)
            placeholder_created = False
            placeholder_fingerprint = None
        temp_path = None
        _fsync_directory(target.parent)
        return inspect_project_package(target)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
        if placeholder_created and placeholder_fingerprint is not None:
            try:
                if FileFingerprint.capture(target) == placeholder_fingerprint:
                    target.unlink()
            except (FileNotFoundError, SafeCodecError):
                pass
        if staging_dir is not None:
            shutil.rmtree(staging_dir, ignore_errors=True)
        os.close(lock_fd)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


__all__ = [
    "ALLOWED_SECTION_KEYS", "MIMETYPE", "PACKAGE_FORMAT", "PACKAGE_VERSION",
    "EntryDescriptor", "FileFingerprint", "ProjectIndex", "ProjectPayloadResolver",
    "ReadBudget", "inspect_project_package",
    "is_project_package", "load_project", "load_project_table_projection",
    "write_project_package",
    "write_project_package_streaming",
]
