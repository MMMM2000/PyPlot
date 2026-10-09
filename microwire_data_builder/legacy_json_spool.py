"""Strict bounded spooling for explicitly trusted legacy Builder JSON projects.

This module is deliberately lexical: it validates and splits legacy JSON without
constructing a project-sized Python object and it never imports or executes
``pickle``.  Trusted pickle execution remains isolated in the migration child.
"""

from __future__ import annotations

import base64
import binascii
import codecs
import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

from .safe_codec import SafeCodecError


CHUNK_BYTES = 64 * 1024
MAX_KEY_BYTES = 4096
_NUMBER_RE = re.compile(rb"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
_HEX = frozenset(b"0123456789abcdefABCDEF")
_SIMPLE_ESCAPES = {
    ord('"'): ord('"'), ord("\\"): ord("\\"), ord("/"): ord("/"),
    ord("b"): 0x08, ord("f"): 0x0C, ord("n"): 0x0A,
    ord("r"): 0x0D, ord("t"): 0x09,
}


@dataclass(frozen=True)
class SpoolLimits:
    source_bytes: int = 2 * 1024 * 1024 * 1024
    root_scalar_bytes: int = 8 * 1024 * 1024
    state_bytes: int = 256 * 1024 * 1024
    table_member_bytes: int = 256 * 1024 * 1024
    payload_bytes: int = 768 * 1024 * 1024
    encoded_pickle_bytes: int = 512 * 1024 * 1024
    decoded_pickle_bytes: int = 384 * 1024 * 1024
    depth: int = 128
    nodes: int = 5_000_000


@dataclass(frozen=True)
class SpooledPayload:
    name: str
    envelope_path: Path
    encoding: str | None
    pickle_path: Path | None
    encoded_bytes: int
    decoded_bytes: int
    sha256: str | None


@dataclass(frozen=True)
class SpooledSection:
    name: str
    state_path: Path
    table_path: Path
    payloads: dict[str, SpooledPayload]


@dataclass(frozen=True)
class SpooledProject:
    metadata: dict[str, object]
    sections: tuple[SpooledSection, ...]
    source_sha256: str
    source_bytes: int
    max_internal_buffer: int


class _LimitedDigestWriter:
    def __init__(self, handle: BinaryIO, limit: int, label: str) -> None:
        self.handle = handle
        self.limit = limit
        self.label = label
        self.size = 0
        self.digest = hashlib.sha256()

    def write(self, data: bytes | bytearray) -> int:
        raw = bytes(data)
        self.size += len(raw)
        if self.size > self.limit:
            raise SafeCodecError(f"Legacy Builder {self.label} exceeds its safe limit")
        self.digest.update(raw)
        return self.handle.write(raw)


class _DiscardWriter:
    def write(self, data: bytes | bytearray) -> int:
        return len(data)


class JsonByteCursor:
    """Fixed-buffer strict UTF-8 JSON parser with streaming value copying."""

    def __init__(
        self,
        handle: BinaryIO,
        *,
        limits: SpoolLimits,
        progress: Callable[[int], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self.handle = handle
        self.limits = limits
        self.progress = progress
        self.cancelled = cancelled
        self.buffer = b""
        self.offset = 0
        self.position = 0
        self.source_bytes = 0
        self.source_digest = hashlib.sha256()
        self.max_internal_buffer = 0
        self.max_string_buffer = 0
        self.nodes = 0
        self._next_callback = CHUNK_BYTES

    def _fill(self) -> bool:
        raw = self.handle.read(CHUNK_BYTES)
        if not raw:
            self.buffer = b""
            self.offset = 0
            return False
        self.source_bytes += len(raw)
        if self.source_bytes > self.limits.source_bytes:
            raise SafeCodecError("Legacy Builder project exceeds its migration size limit")
        self.source_digest.update(raw)
        self.buffer = raw
        self.offset = 0
        self.max_internal_buffer = max(self.max_internal_buffer, len(raw))
        if self.cancelled is not None and self.cancelled():
            raise SafeCodecError("Trusted Builder migration was cancelled")
        if self.progress is not None and self.source_bytes >= self._next_callback:
            self.progress(self.source_bytes)
            self._next_callback = self.source_bytes + CHUNK_BYTES
        return True

    def peek(self) -> int | None:
        if self.offset >= len(self.buffer) and not self._fill():
            return None
        return self.buffer[self.offset]

    def read(self) -> int:
        value = self.peek()
        if value is None:
            raise SafeCodecError("Unexpected end of legacy Builder JSON")
        self.offset += 1
        self.position += 1
        return value

    def skip_ws(self) -> None:
        while (value := self.peek()) is not None and value in b" \t\r\n":
            self.read()

    def take(self, expected: int) -> None:
        self.skip_ws()
        if self.read() != expected:
            raise SafeCodecError(
                f"Malformed legacy Builder JSON near byte {self.position}: expected {chr(expected)!r}"
            )

    def _node(self, depth: int) -> None:
        if depth > self.limits.depth:
            raise SafeCodecError("Legacy Builder JSON exceeds its nesting-depth limit")
        self.nodes += 1
        if self.nodes > self.limits.nodes:
            raise SafeCodecError("Legacy Builder JSON exceeds its node-count limit")

    def read_string_bytes(self, destination: BinaryIO | None = None, *, key: bool = False) -> bytes:
        self.skip_ws()
        if self.read() != ord('"'):
            raise SafeCodecError("Legacy Builder JSON object key must be a string")
        if destination is not None:
            destination.write(b'"')
        captured = bytearray()
        run = bytearray()
        decoder = codecs.getincrementaldecoder("utf-8")("strict")

        def flush_run(*, final: bool) -> None:
            try:
                decoder.decode(bytes(run), final=final)
            except UnicodeDecodeError as exc:
                raise SafeCodecError("Legacy Builder JSON contains invalid UTF-8") from exc
            if destination is not None and run:
                destination.write(run)
            run.clear()

        while True:
            value = self.read()
            if value == ord('"'):
                flush_run(final=True)
                if destination is not None:
                    destination.write(b'"')
                break
            if value < 0x20:
                raise SafeCodecError("Legacy Builder JSON string contains a control byte")
            if value == ord("\\"):
                flush_run(final=True)
                decoder = codecs.getincrementaldecoder("utf-8")("strict")
                escape = self.read()
                raw = bytearray((value, escape))
                if escape == ord("u"):
                    digits = bytes(self.read() for _ in range(4))
                    if any(item not in _HEX for item in digits):
                        raise SafeCodecError("Legacy Builder JSON contains an invalid Unicode escape")
                    raw.extend(digits)
                elif escape not in _SIMPLE_ESCAPES:
                    raise SafeCodecError("Legacy Builder JSON contains an invalid string escape")
                if destination is not None:
                    destination.write(raw)
                if key:
                    captured.extend(raw)
            else:
                run.append(value)
                self.max_string_buffer = max(self.max_string_buffer, len(run))
                if key:
                    captured.append(value)
                if len(run) >= CHUNK_BYTES:
                    flush_run(final=False)
            if key and len(captured) > MAX_KEY_BYTES:
                raise SafeCodecError("Legacy Builder JSON key exceeds its safe limit")
        return bytes(captured)

    def read_key(self) -> str:
        raw = self.read_string_bytes(key=True)
        try:
            value = json.loads(b'"' + raw + b'"')
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise SafeCodecError("Legacy Builder JSON key is malformed") from exc
        if not isinstance(value, str):
            raise SafeCodecError("Legacy Builder JSON key is not text")
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise SafeCodecError("Legacy Builder JSON key contains an invalid surrogate")
        return value

    @staticmethod
    def _collision_key(value: str) -> str:
        return unicodedata.normalize("NFC", value).casefold()

    def object_members(
        self, *, depth: int, reject_case_collisions: bool = True
    ) -> Iterator[str]:
        self._node(depth)
        self.take(ord("{"))
        self.skip_ws()
        if self.peek() == ord("}"):
            self.read()
            return
        exact: set[str] = set()
        folded: set[str] = set()
        while True:
            key = self.read_key()
            collision = self._collision_key(key)
            if key in exact:
                raise SafeCodecError(f"Duplicate key in legacy Builder JSON: {key}")
            if reject_case_collisions and collision in folded:
                raise SafeCodecError(f"Case-colliding key in legacy Builder JSON: {key}")
            exact.add(key)
            folded.add(collision)
            self.take(ord(":"))
            yield key
            self.skip_ws()
            delimiter = self.read()
            if delimiter == ord("}"):
                return
            if delimiter != ord(","):
                raise SafeCodecError("Malformed legacy Builder JSON object delimiter")

    def copy_value(self, destination: BinaryIO, *, limit: int, depth: int = 0) -> int:
        writer = _LimitedDigestWriter(destination, limit, "JSON fragment")
        self._copy_value(writer, depth)
        return writer.size

    def _copy_value(self, destination: BinaryIO, depth: int) -> None:
        self.skip_ws()
        self._node(depth)
        first = self.peek()
        if first == ord('"'):
            self.read_string_bytes(destination)
            return
        if first == ord("{"):
            self.read()
            destination.write(b"{")
            self.skip_ws()
            if self.peek() == ord("}"):
                self.read(); destination.write(b"}"); return
            # Arbitrary state/table row keys are data, not identifiers. Their
            # case can be semantically meaningful (for example ``d (µm)`` and
            # ``D (µm)`` diameter columns), so only exact JSON duplicates are
            # rejected here. Structural objects parsed through object_members
            # retain NFC/casefold collision checks for root/section/payload IDs.
            exact: set[str] = set(); wrote = False
            while True:
                key = self.read_key()
                if key in exact:
                    raise SafeCodecError(f"Duplicate key in legacy Builder JSON: {key}")
                exact.add(key)
                if wrote: destination.write(b",")
                destination.write(json.dumps(key, ensure_ascii=False).encode("utf-8")); destination.write(b":")
                self.take(ord(":")); self._copy_value(destination, depth + 1); wrote = True
                self.skip_ws(); delimiter = self.read()
                if delimiter == ord("}"): destination.write(b"}"); return
                if delimiter != ord(","):
                    raise SafeCodecError("Malformed legacy Builder JSON object delimiter")
        if first == ord("["):
            self.read(); destination.write(b"["); self.skip_ws()
            if self.peek() == ord("]"):
                self.read(); destination.write(b"]"); return
            wrote = False
            while True:
                if wrote: destination.write(b",")
                self._copy_value(destination, depth + 1); wrote = True
                self.skip_ws(); delimiter = self.read()
                if delimiter == ord("]"): destination.write(b"]"); return
                if delimiter != ord(","):
                    raise SafeCodecError("Malformed legacy Builder JSON array delimiter")
        if first in (ord("t"), ord("f"), ord("n")):
            literal = {ord("t"): b"true", ord("f"): b"false", ord("n"): b"null"}[first]
            actual = bytes(self.read() for _ in literal)
            if actual != literal:
                raise SafeCodecError("Malformed literal in legacy Builder JSON")
            destination.write(literal); return
        token = bytearray()
        while (value := self.peek()) is not None and value not in b" \t\r\n,]}:":
            token.append(self.read())
            if len(token) > 128:
                raise SafeCodecError("Legacy Builder JSON number exceeds its lexical limit")
        if not token or _NUMBER_RE.fullmatch(token) is None:
            raise SafeCodecError("Malformed number or value in legacy Builder JSON")
        destination.write(token)

    def finish_document(self) -> None:
        self.skip_ws()
        if self.peek() is not None:
            raise SafeCodecError("Legacy Builder JSON contains trailing data")


def _load_fragment(path: Path, limit: int, label: str) -> object:
    if path.stat().st_size > limit:
        raise SafeCodecError(f"Legacy Builder {label} exceeds its safe limit")
    try:
        return json.loads(path.read_bytes())
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SafeCodecError(f"Legacy Builder {label} is invalid") from exc


def _read_scalar_member(
    path: Path, member: str, limits: SpoolLimits, *, stop_after_found: bool = False
) -> object:
    found: object = _MISSING
    with path.open("rb") as handle:
        cursor = JsonByteCursor(handle, limits=limits)
        for key in cursor.object_members(depth=0, reject_case_collisions=False):
            if key == member:
                if found is not _MISSING:
                    raise SafeCodecError(f"Duplicate legacy payload field: {member}")
                from io import BytesIO
                raw = BytesIO(); cursor.copy_value(raw, limit=limits.root_scalar_bytes, depth=1)
                try: found = json.loads(raw.getvalue())
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise SafeCodecError(f"Legacy payload field is invalid: {member}") from exc
                if stop_after_found:
                    return found
            else:
                cursor.copy_value(_DiscardWriter(), limit=limits.payload_bytes, depth=1)
        cursor.finish_document()
    return None if found is _MISSING else found


_MISSING = object()


def _iter_decoded_json_string(cursor: JsonByteCursor) -> Iterator[int]:
    cursor.skip_ws()
    if cursor.read() != ord('"'):
        raise SafeCodecError("Legacy pickle value must be a JSON string")
    while True:
        value = cursor.read()
        if value == ord('"'):
            return
        if value < 0x20:
            raise SafeCodecError("Legacy pickle string contains a control byte")
        if value != ord("\\"):
            yield value
            continue
        escape = cursor.read()
        if escape in _SIMPLE_ESCAPES:
            yield _SIMPLE_ESCAPES[escape]
            continue
        if escape != ord("u"):
            raise SafeCodecError("Legacy pickle string contains an invalid escape")
        digits = bytes(cursor.read() for _ in range(4))
        if any(item not in _HEX for item in digits):
            raise SafeCodecError("Legacy pickle string contains an invalid Unicode escape")
        codepoint = int(digits, 16)
        if 0xD800 <= codepoint <= 0xDFFF:
            raise SafeCodecError("Legacy pickle base64 string contains a surrogate escape")
        encoded = chr(codepoint).encode("utf-8")
        yield from encoded


def decode_base64_json_member_to_file(
    path: Path,
    member: str,
    output: Path,
    *,
    max_encoded: int,
    max_decoded: int,
    limits: SpoolLimits | None = None,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[int, int, str]:
    """Incrementally JSON-unescape and strict-base64-decode one object member."""

    active = limits or SpoolLimits()
    encoded_count = decoded_count = 0
    digest = hashlib.sha256()
    found = False
    padding_count = 0
    try:
        envelope_bytes = Path(path).stat().st_size
        with Path(path).open("rb") as handle, Path(output).open("xb") as destination:
            cursor = JsonByteCursor(
                handle,
                limits=active,
                cancelled=cancelled,
                progress=(
                    (lambda done: progress(min(done, envelope_bytes), envelope_bytes))
                    if progress is not None
                    else None
                ),
            )
            for key in cursor.object_members(depth=0, reject_case_collisions=False):
                if key != member:
                    cursor.copy_value(_DiscardWriter(), limit=active.payload_bytes, depth=1)
                    continue
                found = True
                pending = bytearray()
                for value in _iter_decoded_json_string(cursor):
                    if value > 0x7F:
                        raise SafeCodecError("Legacy pickle base64 string is not ASCII")
                    if padding_count and value != ord("="):
                        raise SafeCodecError("Legacy project payload contains data after base64 padding")
                    pending.append(value); encoded_count += 1
                    if value == ord("="):
                        padding_count += 1
                        if padding_count > 2:
                            raise SafeCodecError("Legacy project payload contains invalid base64 padding")
                    if encoded_count > max_encoded:
                        raise SafeCodecError("Legacy project payload exceeds its encoded migration limit")
                    if len(pending) >= CHUNK_BYTES:
                        usable = len(pending) - len(pending) % 4
                        try: decoded = base64.b64decode(pending[:usable], validate=True)
                        except (ValueError, binascii.Error) as exc:
                            raise SafeCodecError("Legacy project payload contains invalid base64") from exc
                        decoded_count += len(decoded)
                        if decoded_count > max_decoded:
                            raise SafeCodecError("Legacy project payload exceeds its decoded migration limit")
                        destination.write(decoded); digest.update(decoded); del pending[:usable]
                if pending:
                    try: decoded = base64.b64decode(pending, validate=True)
                    except (ValueError, binascii.Error) as exc:
                        raise SafeCodecError("Legacy project payload contains invalid base64") from exc
                    decoded_count += len(decoded)
                    if decoded_count > max_decoded:
                        raise SafeCodecError("Legacy project payload exceeds its decoded migration limit")
                    destination.write(decoded); digest.update(decoded)
            cursor.finish_document()
            if not found:
                raise SafeCodecError(f"Legacy payload is missing {member}")
            destination.flush(); os.fsync(destination.fileno())
            if progress is not None:
                progress(envelope_bytes, envelope_bytes)
    except Exception:
        try: Path(output).unlink()
        except FileNotFoundError: pass
        raise
    return encoded_count, decoded_count, digest.hexdigest()


def _spool_payload(
    name: str,
    envelope_path: Path,
    section_dir: Path,
    limits: SpoolLimits,
    cancelled: Callable[[], bool] | None,
    progress: Callable[[str, int, int], None] | None,
) -> SpooledPayload:
    # json.dumps writes ``encoding`` before ``value``.  The source envelope was
    # already fully grammar-validated while spooling, so stop as soon as the
    # small discriminator is found and avoid a redundant scan of a huge value.
    encoding = _read_scalar_member(
        envelope_path, "encoding", limits, stop_after_found=True
    )
    if encoding is not None and not isinstance(encoding, str):
        raise SafeCodecError(f"Legacy payload encoding must be text: {name}")
    if encoding != "pickle-base64":
        return SpooledPayload(name, envelope_path, encoding, None, 0, 0, None)
    pickle_path = section_dir / f"payload-{len(list(section_dir.glob('*.pkl'))):04d}.pkl"
    encoded, decoded, digest = decode_base64_json_member_to_file(
        envelope_path, "value", pickle_path,
        max_encoded=limits.encoded_pickle_bytes,
        max_decoded=limits.decoded_pickle_bytes,
        limits=limits, cancelled=cancelled,
        progress=(
            (lambda done, total: progress(f"decode_pickle:{name}", done, total))
            if progress is not None
            else None
        ),
    )
    return SpooledPayload(name, envelope_path, encoding, pickle_path, encoded, decoded, digest)


def _spool_section(
    cursor: JsonByteCursor,
    name: str,
    section_dir: Path,
    limits: SpoolLimits,
    cancelled: Callable[[], bool] | None,
    progress: Callable[[str, int, int], None] | None,
) -> SpooledSection:
    section_dir.mkdir()
    state_path = section_dir / "state.json"
    table_path = section_dir / "table.json"
    payloads: dict[str, SpooledPayload] = {}
    table_paths: dict[str, Path] = {}
    with state_path.open("xb") as state_handle:
        state = _LimitedDigestWriter(state_handle, limits.state_bytes, f"section state {name}")
        state.write(b"{"); state_wrote = False
        for key in cursor.object_members(depth=2, reject_case_collisions=False):
            if key == "payloads":
                for payload_name in cursor.object_members(depth=3):
                    payload_path = section_dir / f"payload-{len(payloads):04d}.json"
                    with payload_path.open("xb") as out:
                        cursor.copy_value(out, limit=limits.payload_bytes, depth=4)
                        out.flush(); os.fsync(out.fileno())
                    payloads[payload_name] = _spool_payload(
                        payload_name, payload_path, section_dir, limits, cancelled,
                        progress,
                    )
            elif key in {"columns", "rows", "index"}:
                fragment = section_dir / f"table-{key}.json"
                with fragment.open("xb") as out:
                    cursor.copy_value(out, limit=limits.table_member_bytes, depth=3)
                table_paths[key] = fragment
            else:
                if state_wrote: state.write(b",")
                state.write(json.dumps(key, ensure_ascii=False).encode("utf-8")); state.write(b":")
                cursor.copy_value(state, limit=limits.state_bytes - state.size, depth=3)
                state_wrote = True
        state.write(b"}"); state_handle.flush(); os.fsync(state_handle.fileno())
    with table_path.open("xb") as table_handle:
        table = _LimitedDigestWriter(table_handle, limits.state_bytes, f"section table {name}")
        table.write(b"{")
        for index, key in enumerate(("columns", "rows", "index")):
            if index: table.write(b",")
            table.write(json.dumps(key).encode("ascii")); table.write(b":")
            fragment = table_paths.get(key)
            if fragment is None: table.write(b"[]")
            else:
                with fragment.open("rb") as source:
                    while chunk := source.read(CHUNK_BYTES): table.write(chunk)
        table.write(b"}"); table_handle.flush(); os.fsync(table_handle.fileno())
    for fragment in table_paths.values(): fragment.unlink()
    return SpooledSection(name, state_path, table_path, payloads)


def spool_legacy_project(
    source: Path,
    staging_dir: Path,
    *,
    progress: Callable[[str, int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    limits: SpoolLimits | None = None,
) -> SpooledProject:
    """Validate and split a v1/v2 Builder project using bounded memory."""

    source = Path(source); staging_dir = Path(staging_dir); active = limits or SpoolLimits()
    initial_stat = source.stat()
    total = initial_stat.st_size
    if total > active.source_bytes:
        raise SafeCodecError("Legacy Builder project exceeds its migration size limit")
    staging_dir.mkdir(parents=True, exist_ok=False)
    metadata: dict[str, object] = {}; sections: list[SpooledSection] = []
    saw_sections = False

    def report(position: int) -> None:
        if progress is not None: progress("spool", min(position, total), total)

    try:
        with source.open("rb") as handle:
            cursor = JsonByteCursor(handle, limits=active, progress=report, cancelled=cancelled)
            for key in cursor.object_members(depth=0):
                if key == "sections":
                    saw_sections = True
                    for section_name in cursor.object_members(depth=1):
                        section_dir = staging_dir / f"section-{len(sections):03d}"
                        sections.append(_spool_section(
                            cursor, section_name, section_dir, active, cancelled,
                            progress,
                        ))
                    continue
                from io import BytesIO
                raw = BytesIO(); cursor.copy_value(raw, limit=active.root_scalar_bytes, depth=1)
                try: metadata[key] = json.loads(raw.getvalue())
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise SafeCodecError(f"Legacy Builder root field is invalid: {key}") from exc
            cursor.finish_document()
            version = metadata.get("version")
            if (
                metadata.get("kind") != "MicrowireDataBuilder"
                or type(version) is not int
                or version not in {1, 2}
            ):
                raise SafeCodecError("Trusted migration source is not a legacy Builder v1/v2 project")
            if not saw_sections:
                raise SafeCodecError("Legacy Builder project is missing sections")
            if not isinstance(metadata.get("saved_at", ""), str):
                raise SafeCodecError("Legacy Builder saved_at must be text")
            final_stat = source.stat()
            if (
                final_stat.st_size != initial_stat.st_size
                or final_stat.st_mtime_ns != initial_stat.st_mtime_ns
                or final_stat.st_dev != initial_stat.st_dev
                or final_stat.st_ino != initial_stat.st_ino
            ):
                raise SafeCodecError("Legacy Builder project changed while it was being spooled")
            result = SpooledProject(
                metadata, tuple(sections), cursor.source_digest.hexdigest(),
                cursor.source_bytes, max(cursor.max_internal_buffer, cursor.max_string_buffer),
            )
        if progress is not None: progress("spool", total, total)
        return result
    except Exception:
        # The caller owns staging_dir; leave no partial payload that could be mistaken for success.
        import shutil
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


def read_small_json(path: Path, *, limit: int) -> object:
    return _load_fragment(Path(path), limit, "JSON fragment")


def json_object_scalar(path: Path, member: str, *, limit: int = CHUNK_BYTES) -> object:
    limits = SpoolLimits(root_scalar_bytes=limit)
    value = _read_scalar_member(Path(path), member, limits)
    if value is None:
        raise SafeCodecError(f"Legacy payload is missing {member}")
    return value


__all__ = [
    "CHUNK_BYTES", "SpoolLimits", "SpooledPayload", "SpooledProject", "SpooledSection",
    "decode_base64_json_member_to_file", "json_object_scalar", "read_small_json",
    "spool_legacy_project",
]
