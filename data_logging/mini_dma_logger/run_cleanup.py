"""Safe TMA sibling-run cleanup helpers."""

from __future__ import annotations

import csv
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SKIPPED_SUBTREE_NAMES = {
    "archive",
    "automated",
    "automated_control_tests",
    "automation_history",
}
WIRE_BREAK_STOP_REASONS = {"wire_break_or_contact_loss"}


@dataclass(frozen=True)
class MiniDmaRunIdentity:
    sample_key: tuple[str, str, str, str] | tuple[str]
    recipe_mode: str


@dataclass(frozen=True)
class MiniDmaRunCleanupCandidate:
    path: Path
    name: str
    created_utc: str
    last_write_utc: str
    recipe_mode: str
    recipe_summary: str
    stop_reason: str
    stop_label: str
    stop_detail: str
    measurement_rows: int | None
    duration_s: float | None
    is_current_run: bool
    is_preconditioning: bool
    suggested_action: str


@dataclass(frozen=True)
class MiniDmaArchiveMove:
    source: Path
    destination: Path


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _clean_key(value: Any) -> str:
    return str(value or "").strip().casefold()


def _recipe_mode(metadata: Mapping[str, Any]) -> str:
    direct = str(metadata.get("recipe_mode") or "").strip()
    if direct:
        return direct
    controlled = _mapping(metadata.get("controlled_current_sweep"))
    nested = str(controlled.get("mode") or "").strip()
    if nested:
        return nested
    return str(metadata.get("recipe_summary") or "").strip()


def run_identity_from_metadata(metadata: Mapping[str, Any]) -> MiniDmaRunIdentity | None:
    name_fields = _mapping(metadata.get("name_fields"))
    sample_parts = (
        _clean_key(name_fields.get("composition")),
        _clean_key(name_fields.get("microwire")),
        _clean_key(name_fields.get("specimen")),
        _clean_key(name_fields.get("condition")),
    )
    if any(sample_parts):
        sample_key: tuple[str, str, str, str] | tuple[str] = sample_parts
    else:
        sample_name = _clean_key(metadata.get("sample_name"))
        if not sample_name:
            return None
        sample_key = (sample_name,)
    mode = _clean_key(_recipe_mode(metadata))
    if not mode:
        return None
    return MiniDmaRunIdentity(sample_key=sample_key, recipe_mode=mode)


def _csv_summary(path: Path) -> tuple[int | None, float | None]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = 0
            first_elapsed: float | None = None
            last_elapsed: float | None = None
            for row in reader:
                rows += 1
                try:
                    elapsed = float(str(row.get("elapsed_s") or ""))
                except ValueError:
                    elapsed = None
                if elapsed is not None:
                    if first_elapsed is None:
                        first_elapsed = elapsed
                    last_elapsed = elapsed
    except OSError:
        return None, None
    if first_elapsed is not None and last_elapsed is not None and last_elapsed >= first_elapsed:
        return rows, last_elapsed - first_elapsed
    return rows, None


def _last_write_utc(path: Path) -> str:
    try:
        timestamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return ""
    return timestamp.isoformat(timespec="seconds")


def _is_preconditioning_run(metadata: Mapping[str, Any], run_name: str) -> bool:
    controlled = _mapping(metadata.get("controlled_current_sweep"))
    if bool(controlled.get("first_overheating")):
        return True
    text = " ".join(
        str(value or "")
        for value in (
            run_name,
            metadata.get("sample_name"),
            metadata.get("recipe_summary"),
            controlled.get("mode"),
        )
    ).casefold()
    return any(token in text for token in ("first overheating", "first-overheating", "preconditioning", "preheat"))


def _candidate_from_run_dir(
    run_dir: Path,
    *,
    metadata: Mapping[str, Any],
    current_run: Path,
) -> MiniDmaRunCleanupCandidate:
    stop = _mapping(metadata.get("stop"))
    rows, duration_s = _csv_summary(run_dir / "measurement.csv")
    is_current = _same_path(run_dir, current_run)
    is_preconditioning = _is_preconditioning_run(metadata, run_dir.name)
    suggested_action = "keep" if is_current or is_preconditioning else "archive"
    return MiniDmaRunCleanupCandidate(
        path=run_dir,
        name=run_dir.name,
        created_utc=str(metadata.get("created_utc") or ""),
        last_write_utc=_last_write_utc(run_dir),
        recipe_mode=str(_recipe_mode(metadata) or ""),
        recipe_summary=str(metadata.get("recipe_summary") or ""),
        stop_reason=str(stop.get("reason") or ""),
        stop_label=str(stop.get("label") or ""),
        stop_detail=str(stop.get("detail") or ""),
        measurement_rows=rows,
        duration_s=duration_s,
        is_current_run=is_current,
        is_preconditioning=is_preconditioning,
        suggested_action=suggested_action,
    )


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left.absolute() == right.absolute()


def _is_direct_child(parent: Path, child: Path) -> bool:
    try:
        return child.resolve().parent == parent.resolve()
    except OSError:
        return child.absolute().parent == parent.absolute()


def _iter_sibling_run_dirs(root: Path) -> Iterable[Path]:
    try:
        children = sorted(root.iterdir(), key=lambda path: path.name.casefold())
    except OSError:
        return []
    return (
        child
        for child in children
        if child.is_dir()
        and child.name.casefold() not in SKIPPED_SUBTREE_NAMES
        and (child / "metadata.json").exists()
    )


def discover_cleanup_candidates_for_run(current_run: Path | str) -> list[MiniDmaRunCleanupCandidate]:
    current_path = Path(current_run)
    if not current_path.exists() or not current_path.is_dir():
        return []
    root = current_path.parent
    if current_path.name.casefold() in SKIPPED_SUBTREE_NAMES or not _is_direct_child(root, current_path):
        return []
    current_metadata = _read_json(current_path / "metadata.json")
    current_stop = _mapping(current_metadata.get("stop"))
    if str(current_stop.get("reason") or "") not in WIRE_BREAK_STOP_REASONS:
        return []
    identity = run_identity_from_metadata(current_metadata)
    if identity is None:
        return []

    candidates: list[MiniDmaRunCleanupCandidate] = []
    for run_dir in _iter_sibling_run_dirs(root):
        metadata = _read_json(run_dir / "metadata.json")
        if run_identity_from_metadata(metadata) != identity:
            continue
        candidates.append(_candidate_from_run_dir(run_dir, metadata=metadata, current_run=current_path))

    if len(candidates) < 2 or not any(candidate.is_current_run for candidate in candidates):
        return []
    candidates.sort(key=lambda candidate: (candidate.created_utc or candidate.last_write_utc, candidate.name))
    return candidates


def _archive_batch_name(now: datetime | None = None) -> str:
    timestamp = now or datetime.now(timezone.utc)
    return timestamp.strftime("%Y-%m-%d_cleanup_%H%M%S")


def _safe_destination(base: Path, name: str) -> Path:
    candidate = base / name
    if not candidate.exists():
        return candidate
    stem = name
    for index in range(2, 1000):
        candidate = base / f"{stem}_{index:02d}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not find an unused archive destination for {name!r}")


def archive_cleanup_candidates(
    candidates: Sequence[MiniDmaRunCleanupCandidate],
    selected_paths: Iterable[Path | str],
    *,
    archive_name: str | None = None,
) -> list[MiniDmaArchiveMove]:
    selected = {Path(path).resolve() for path in selected_paths}
    by_path = {candidate.path.resolve(): candidate for candidate in candidates}
    moves: list[MiniDmaArchiveMove] = []
    if not selected:
        return moves
    roots = {candidate.path.parent.resolve() for candidate in candidates}
    if len(roots) != 1:
        raise ValueError("Cleanup candidates must share one TMA data root.")
    root = next(iter(roots))
    archive_root = root / "archive" / (archive_name or _archive_batch_name())

    for path in sorted(selected, key=lambda item: item.name.casefold()):
        candidate = by_path.get(path)
        if candidate is None:
            raise ValueError(f"Selected path is not a cleanup candidate: {path}")
        if candidate.is_current_run:
            raise ValueError("The current run cannot be archived from the cleanup dialog.")
        if candidate.path.name.casefold() in SKIPPED_SUBTREE_NAMES:
            raise ValueError(f"Refusing to archive skipped subtree: {candidate.path}")
        if not _is_direct_child(root, candidate.path):
            raise ValueError(f"Refusing to archive non-sibling run folder: {candidate.path}")
        archive_root.mkdir(parents=True, exist_ok=True)
        destination = _safe_destination(archive_root, candidate.path.name)
        shutil.move(str(candidate.path), str(destination))
        moves.append(MiniDmaArchiveMove(source=candidate.path, destination=destination))
    return moves


def cleanup_summary_text(candidates: Sequence[MiniDmaRunCleanupCandidate]) -> str:
    archive_count = sum(
        1 for candidate in candidates if not candidate.is_current_run and candidate.suggested_action == "archive"
    )
    keep_count = len(candidates) - archive_count
    sample_modes = sorted({candidate.recipe_mode or "unknown" for candidate in candidates})
    mode_text = ", ".join(sample_modes)
    return f"{len(candidates)} related run folder(s), {archive_count} suggested archive, {keep_count} suggested keep. Mode: {mode_text}."


def format_duration_s(duration_s: float | None) -> str:
    if duration_s is None:
        return ""
    if duration_s < 90:
        return f"{duration_s:.0f} s"
    minutes = duration_s / 60.0
    if minutes < 90:
        return f"{minutes:.1f} min"
    return f"{minutes / 60.0:.1f} h"


def sanitize_archive_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("._") or _archive_batch_name()
