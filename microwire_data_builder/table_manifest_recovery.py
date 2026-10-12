"""Explicit copy-only recovery of externally edited table descriptors.

This never runs during ordinary imports. Accepting edited tables is an explicit
recovery operation, not evidence that the original checksum was valid.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from . import project_package as pp
from .safe_codec import SafeCodecError


def recover_table_manifest(source: Path, destination: Path) -> list[str]:
    """Retain table bytes, rebuild their descriptors and verify a new package.

    Refuses to overwrite, repair non-table entries or publish any package that
    fails the normal strict reader. The source remains unchanged.
    """
    source, destination = Path(source), Path(destination)
    if destination.exists() or source.resolve() == destination.resolve():
        raise FileExistsError(destination)
    fingerprint = pp.FileFingerprint.capture(source)
    if fingerprint.size > pp.MAX_PACKAGE_BYTES:
        raise SafeCodecError('Builder package exceeds the package limit')
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.table-recovery-', suffix='.pydpj', dir=destination.parent)
    os.close(fd)
    temporary = Path(temporary)
    changed: list[str] = []
    try:
        with pp._open_archive(source) as original:
            pp._validate_archive_boundaries(source, original.infolist())
            infos = pp._validated_info_map(original)
            manifest = pp._read_json_bytes(pp._read_entry_bounded(
                original, infos[pp.MANIFEST_PATH], limit=pp.MAX_MANIFEST_BYTES), 'manifest')
            if not isinstance(manifest, dict) or manifest.get('version') != pp.PACKAGE_VERSION:
                raise SafeCodecError('Recovery requires a Builder v3 package')
            descriptors = [pp._descriptor_from_json(e) for e in manifest['entries']]
            by_path = {e.path: e for e in descriptors}
            if len(by_path) != len(descriptors) or set(infos) != set(by_path) | {pp.MIMETYPE_PATH, pp.MANIFEST_PATH}:
                raise SafeCodecError('Recovery refuses duplicate, missing or unlisted entries')
            accepted_tables: dict[str, tuple[int, str]] = {}
            table_budget = pp.ReadBudget()
            for value in manifest['entries']:
                e = by_path[value['path']]
                if e.role != 'table':
                    continue
                if e.path not in {f'sections/{k}/table.json' for k in pp.ALLOWED_SECTION_KEYS}:
                    raise SafeCodecError('Noncanonical table path')
                raw = pp._read_entry_bounded(original, infos[e.path], limit=pp.MAX_JSON_ENTRY_BYTES, budget=table_budget)
                digest = hashlib.sha256(raw).hexdigest()
                if len(raw) == e.bytes and digest == e.sha256:
                    continue
                table = pp._read_json_bytes(raw, e.path)
                pp._validate_tree_limits(table)
                if (not isinstance(table, dict) or set(table) != {'columns', 'rows', 'index'}
                        or not all(isinstance(table[k], list) for k in table)
                        or not all(isinstance(r, dict) for r in table['rows'])
                        or len(table['rows']) != len(table['index'])):
                    raise SafeCodecError('Edited table has invalid schema')
                value.update(bytes=len(raw), sha256=digest)
                accepted_tables[e.path] = (len(raw), digest)
                changed.append(e.path)
            if not changed:
                raise SafeCodecError('No table descriptor mismatch to recover')
            manifest['revision'] += 1
            manifest['saved_at'] = datetime.now(UTC).isoformat(timespec='seconds')
            manifest['writer'] = 'PyPlot explicit table-manifest recovery (edited table bytes accepted)'
            raw_manifest = pp._json_bytes(manifest, 'manifest')
            if len(raw_manifest) > pp.MAX_MANIFEST_BYTES:
                raise SafeCodecError('Recovery manifest exceeds limit')
            with zipfile.ZipFile(temporary, 'w', allowZip64=True) as output:
                output.writestr(pp.MIMETYPE_PATH, pp.MIMETYPE, compress_type=zipfile.ZIP_STORED)
                output.writestr(pp.MANIFEST_PATH, raw_manifest, compress_type=zipfile.ZIP_STORED)
                budget = pp.ReadBudget()
                for e in descriptors:
                    info = infos[e.path]
                    clone = zipfile.ZipInfo(e.path, info.date_time)
                    clone.compress_type = info.compress_type
                    size, digest = 0, hashlib.sha256()
                    with original.open(info) as handle, output.open(clone, 'w', force_zip64=True) as sink:
                        while chunk := handle.read(pp.STREAM_CHUNK_BYTES):
                            size += len(chunk)
                            budget.consume(len(chunk))
                            if size > pp._entry_limit(e.role):
                                raise SafeCodecError('Recovery entry exceeds limit')
                            digest.update(chunk)
                            sink.write(chunk)
                    expected = accepted_tables.get(e.path)
                    expected_size = expected[0] if expected is not None else e.bytes
                    expected_hash = expected[1] if expected is not None else e.sha256
                    if size != expected_size or digest.hexdigest() != expected_hash:
                        raise SafeCodecError(f'Recovery refuses changed non-table entry: {e.path}')
            pp.inspect_project_package(temporary, verify_entries=True)
            if pp.FileFingerprint.capture(source) != fingerprint:
                raise SafeCodecError('Recovery source changed during copy')
            # Windows rename fails if destination appeared; never replace user data.
            if os.name == 'nt':
                temporary.rename(destination)
            else:
                os.link(temporary, destination)
                temporary.unlink()
            return changed
    finally:
        temporary.unlink(missing_ok=True)
