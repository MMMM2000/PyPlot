"""Shared helpers for rotating workspace log files."""

from __future__ import annotations

from pathlib import Path
from typing import TextIO

DEFAULT_LOG_MAX_BYTES = 1_048_576  # 1 MiB
DEFAULT_LOG_BACKUP_COUNT = 5


def rotate_text_log(
    path: Path | str,
    *,
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
    backup_count: int = DEFAULT_LOG_BACKUP_COUNT,
) -> None:
    """Rotate ``path`` in place when it exceeds ``max_bytes``.

    Rotation scheme:
    ``file`` -> ``file.1`` -> ``file.2`` ... up to ``backup_count``.
    """

    if max_bytes <= 0 or backup_count <= 0:
        return

    candidate = Path(path)
    try:
        size = candidate.stat().st_size
    except FileNotFoundError:
        return
    except OSError:
        return
    if size < max_bytes:
        return

    # Prune stale backups above the configured window so retention is bounded.
    try:
        for backup in candidate.parent.glob(f"{candidate.name}.*"):
            suffix = backup.name.rsplit(".", 1)[-1]
            if not suffix.isdigit():
                continue
            if int(suffix) > backup_count:
                backup.unlink()
    except OSError:
        return

    try:
        oldest = candidate.with_name(f"{candidate.name}.{backup_count}")
        if oldest.exists():
            oldest.unlink()
    except OSError:
        return

    for index in range(backup_count - 1, 0, -1):
        src = candidate.with_name(f"{candidate.name}.{index}")
        if not src.exists():
            continue
        dst = candidate.with_name(f"{candidate.name}.{index + 1}")
        try:
            if dst.exists():
                dst.unlink()
            src.replace(dst)
        except OSError:
            return

    first_backup = candidate.with_name(f"{candidate.name}.1")
    try:
        if first_backup.exists():
            first_backup.unlink()
        candidate.replace(first_backup)
    except OSError:
        return


def append_text_with_rotation(
    path: Path | str,
    text: str,
    *,
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
    backup_count: int = DEFAULT_LOG_BACKUP_COUNT,
    encoding: str = "utf-8",
) -> None:
    """Append ``text`` to ``path`` with pre-append size rotation."""

    candidate = Path(path)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    rotate_text_log(candidate, max_bytes=max_bytes, backup_count=backup_count)
    with candidate.open("a", encoding=encoding) as handle:
        handle.write(text)


def open_rotating_text_log(
    path: Path | str,
    *,
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
    backup_count: int = DEFAULT_LOG_BACKUP_COUNT,
    encoding: str = "utf-8",
) -> TextIO:
    """Open ``path`` for append after applying the same rotation policy."""

    candidate = Path(path)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    rotate_text_log(candidate, max_bytes=max_bytes, backup_count=backup_count)
    return candidate.open("a", encoding=encoding)
