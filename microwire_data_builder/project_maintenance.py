"""Explicit, validated table corrections and backed-up database publication.

Normal imports remain strict. Recovery of stale manifests is a separate opt-in
operation; this module never repairs or accepts invalid candidate packages.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import project_package as pp
from .safe_codec import SafeCodecError


def update_project_tables(
    source: Path, destination: Path, tables: Mapping[str, Mapping[str, Any]],
) -> pp.ProjectIndex:
    """Save corrected tables to a NEW package using the normal verified writer.

    Preserve section state, deferred payloads, blobs and optional entries. Table
    replacements contain exactly columns/rows/index, never serialized ZIP edits.
    """
    source, destination = Path(source), Path(destination)
    if destination.exists() or source.resolve() == destination.resolve():
        raise FileExistsError(destination)
    index = pp.inspect_project_package(source, verify_entries=True)
    if not tables or not set(tables).issubset(index.sections):
        raise SafeCodecError('Table corrections must name existing sections')
    sections = {}
    for key, table in tables.items():
        if (not isinstance(table, Mapping) or set(table) != {'columns', 'rows', 'index'}
                or not all(isinstance(table[k], list) for k in table)
                or not all(isinstance(row, dict) for row in table['rows'])
                or len(table['rows']) != len(table['index'])):
            raise SafeCodecError(f'Invalid corrected table schema: {key}')
        section = index.read_section(key, load_payloads=False)
        section.update(table)
        sections[key] = section
    return pp.write_project_package(
        destination, {'kind': pp.PROJECT_KIND, 'sections': sections,
                      'writer': 'PyPlot verified table correction'},
        source_index=index, loaded_sections=set(sections), replace_existing=False,
    )


def _sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _copy_exclusive(source: Path, destination: Path) -> None:
    """Create a durable backup without ever replacing a pre-existing file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as src, destination.open('xb') as dst:
        shutil.copyfileobj(src, dst, pp.STREAM_CHUNK_BYTES)
        dst.flush()
        os.fsync(dst.fileno())


def publish_verified_project(
    candidate: Path, target: Path, *, backup: Path, expected_target_sha256: str,
) -> dict[str, Any]:
    """Publish a strictly valid candidate, preserving the exact previous file.

    Requires explicit knowledge of the target's old hash, an unused backup path,
    matching project identity and a newer revision. Shares the Builder save lock.
    Even when replacing a damaged target, only its bounded manifest header is
    read; candidate and staged bytes must pass every ordinary integrity check.
    """
    candidate, target, backup = Path(candidate), Path(target), Path(backup)
    if len({p.resolve() for p in (candidate, target, backup)}) != 3:
        raise SafeCodecError('Candidate, target and backup must be different paths')
    if backup.exists():
        raise FileExistsError(backup)
    if not pp._SHA256_RE.fullmatch(expected_target_sha256):
        raise SafeCodecError('Expected target SHA-256 must be a lowercase hex digest')
    candidate_index = pp.inspect_project_package(candidate, verify_entries=True)
    candidate_hash = _sha256(candidate)
    candidate_index.assert_unchanged()
    lock_path = target.parent / f'.{target.name}.save.lock'
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
    except FileExistsError as exc:
        raise SafeCodecError('Another process is saving this Builder project') from exc
    staged: Path | None = None
    try:
        target_fingerprint = pp.FileFingerprint.capture(target)
        if _sha256(target) != expected_target_sha256:
            raise SafeCodecError('Database changed; expected target hash does not match')
        with pp._open_archive(target) as old:
            infos = pp._validated_info_map(old)
            manifest = pp._read_json_bytes(pp._read_entry_bounded(
                old, infos[pp.MANIFEST_PATH], limit=pp.MAX_MANIFEST_BYTES), 'manifest')
        if (manifest.get('project_id') != candidate_index.manifest['project_id']
                or not isinstance(manifest.get('revision'), int)
                or candidate_index.manifest['revision'] <= manifest['revision']):
            raise SafeCodecError('Publication requires the same project and a newer revision')
        fd, name = tempfile.mkstemp(prefix='.project-publish-', suffix='.pydpj', dir=target.parent)
        os.close(fd)
        staged = Path(name)
        with candidate.open('rb') as src, staged.open('wb') as dst:
            shutil.copyfileobj(src, dst, pp.STREAM_CHUNK_BYTES)
            dst.flush()
            os.fsync(dst.fileno())
        candidate_index.assert_unchanged()
        if _sha256(staged) != candidate_hash:
            raise SafeCodecError('Candidate changed during staging')
        pp.inspect_project_package(staged, verify_entries=True)
        pp.load_project_table_projection(staged)
        _copy_exclusive(target, backup)
        if _sha256(backup) != expected_target_sha256:
            raise SafeCodecError('Backup verification failed; database was not replaced')
        if (pp.FileFingerprint.capture(target) != target_fingerprint
                or _sha256(target) != expected_target_sha256):
            raise SafeCodecError('Database changed during publication; replacement cancelled')
        os.replace(staged, target)
        staged = None
        pp._fsync_directory(target.parent)
        if _sha256(target) != candidate_hash:
            raise SafeCodecError(f'Published hash mismatch; previous database retained at {backup}')
        return {
            'published_at_utc': datetime.now(UTC).isoformat(timespec='seconds'),
            'target': str(target), 'backup': str(backup),
            'previous_sha256': expected_target_sha256, 'published_sha256': candidate_hash,
            'project_id': candidate_index.manifest['project_id'],
            'revision': candidate_index.manifest['revision'],
            'strict_verification_passed': True,
        }
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
        os.close(lock_fd)
        lock_path.unlink(missing_ok=True)
