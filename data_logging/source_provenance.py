from __future__ import annotations

import json
import os
import subprocess
from collections import OrderedDict
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread
from types import MappingProxyType
from typing import Any
from uuid import uuid4


CAPTURE_PENDING = "pending"
CAPTURE_COMPLETE = "complete"
CAPTURE_UNAVAILABLE = "unavailable"
DIRTY_CLEAN = "clean"
DIRTY_DIRTY = "dirty"
DIRTY_UNKNOWN = "unknown"
DEFAULT_MAX_ACTIVE_CAPTURES = 2
DEFAULT_MAX_RETAINED_SNAPSHOTS = 8

GitRunner = Callable[..., Any]
Collector = Callable[..., Mapping[str, Any]]


@dataclass(frozen=True)
class _CaptureJob:
    token: Hashable
    repo_root: Path
    requested_utc: str


def _utc_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _hidden_subprocess_kwargs() -> dict[str, object]:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "startupinfo": startupinfo,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }


def pending_source_provenance(
    repo_root: str | Path,
    *,
    requested_utc: str | None = None,
) -> dict[str, Any]:
    """Return a truthful, consumer-compatible snapshot before Git finishes."""

    return {
        "repo_root": str(Path(repo_root)),
        "branch": None,
        "commit": None,
        "is_dirty": False,
        "status_short": "",
        "remote_url": None,
        "capture_state": CAPTURE_PENDING,
        "capture_requested_utc": requested_utc or _utc_timestamp(),
        "capture_completed_utc": None,
        "dirty_state": DIRTY_UNKNOWN,
        "head_state": "unknown",
        "remote_state": "unknown",
        "capture_error": None,
    }


def unavailable_source_provenance(
    repo_root: str | Path,
    *,
    requested_utc: str | None = None,
    completed_utc: str | None = None,
    error: str,
) -> dict[str, Any]:
    snapshot = pending_source_provenance(repo_root, requested_utc=requested_utc)
    snapshot.update(
        capture_state=CAPTURE_UNAVAILABLE,
        capture_completed_utc=completed_utc or _utc_timestamp(),
        capture_error=str(error),
    )
    return snapshot


def parse_porcelain_v2_status(text: str) -> dict[str, Any]:
    """Parse one coherent ``git status --porcelain=v2 --branch`` response."""

    branch_oid: str | None = None
    branch_head: str | None = None
    worktree_lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.rstrip("\r\n")
        if line.startswith("# branch.oid "):
            branch_oid = line.removeprefix("# branch.oid ").strip() or None
        elif line.startswith("# branch.head "):
            branch_head = line.removeprefix("# branch.head ").strip() or None
        elif line and not line.startswith("# "):
            worktree_lines.append(line)

    errors: list[str] = []
    commit: str | None = branch_oid
    if branch_oid is None:
        errors.append("missing_branch_oid")
    elif branch_oid == "(initial)":
        commit = None
        errors.append("unborn_head")

    branch: str | None = None
    head_state = "unknown"
    if branch_head is None:
        errors.append("missing_branch_head")
    elif branch_head == "(detached)":
        head_state = "detached"
    elif branch_head.startswith("(") and branch_head.endswith(")"):
        errors.append(f"unknown_branch_head:{branch_head}")
    else:
        branch = branch_head
        head_state = "attached"

    return {
        "branch": branch,
        "commit": commit,
        "status_short": "\n".join(worktree_lines),
        "dirty_state": DIRTY_DIRTY if worktree_lines else DIRTY_CLEAN,
        "head_state": head_state,
        "error": "; ".join(errors) or None,
    }


def patch_source_control_metadata(
    metadata_path: str | Path,
    source_control: Mapping[str, Any],
    *,
    ensure_ascii: bool = True,
) -> None:
    """Atomically replace only ``source_control`` in an existing valid sidecar."""

    path = Path(metadata_path)
    raw_text = path.read_text(encoding="utf-8")
    payload = json.loads(raw_text)
    if not isinstance(payload, dict):
        raise ValueError("metadata payload is not an object")
    if "source_control" not in payload:
        raise ValueError("metadata payload has no source_control field")
    payload["source_control"] = dict(source_control)
    replacement = json.dumps(payload, indent=2, ensure_ascii=ensure_ascii)
    if raw_text.endswith("\n"):
        replacement += "\n"
    temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary_path.write_text(replacement, encoding="utf-8")
        os.replace(temporary_path, path)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def collect_source_provenance(
    repo_root: str | Path,
    *,
    requested_utc: str | None = None,
    git_timeout_s: float = 1.5,
    runner: GitRunner | None = None,
) -> dict[str, Any]:
    """Collect one immutable Git snapshot without any Qt or logger dependency."""

    root = Path(repo_root)
    requested = requested_utc or _utc_timestamp()
    run = subprocess.run if runner is None else runner

    def _git_text(
        args: Sequence[str],
        *,
        missing_exit_code: int | None = None,
    ) -> tuple[str | None, str]:
        try:
            completed = run(
                ["git", "-C", str(root), *args],
                capture_output=True,
                text=True,
                timeout=git_timeout_s,
                check=False,
                **_hidden_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired:
            return None, "timeout"
        except Exception as exc:
            return None, f"error:{exc.__class__.__name__}"
        returncode = int(getattr(completed, "returncode", 1))
        if missing_exit_code is not None and returncode == missing_exit_code:
            return None, "missing"
        if returncode != 0:
            return None, f"exit:{returncode}"
        value = str(getattr(completed, "stdout", "") or "").strip()
        return value or None, "ok"

    status_text, status_result = _git_text(("status", "--porcelain=v2", "--branch"))
    parsed_status = (
        parse_porcelain_v2_status(status_text or "")
        if status_result == "ok"
        else {
            "branch": None,
            "commit": None,
            "status_short": "",
            "dirty_state": DIRTY_UNKNOWN,
            "head_state": "unknown",
            "error": None,
        }
    )
    remote_url, remote_result = _git_text(
        ("config", "--get", "remote.origin.url"),
        missing_exit_code=1,
    )

    if remote_result == "ok":
        remote_state = "configured"
    elif remote_result == "missing":
        remote_state = "missing"
    else:
        remote_state = "unknown"

    failures = {
        "status": status_result,
        "remote": remote_result,
    }
    errors = [
        f"{name}:{result}"
        for name, result in failures.items()
        if result not in {"ok", "missing"}
    ]
    if parsed_status["error"]:
        errors.append(f"status:{parsed_status['error']}")
    capture_state = CAPTURE_COMPLETE if not errors else CAPTURE_UNAVAILABLE
    return {
        "repo_root": str(root),
        "branch": parsed_status["branch"],
        "commit": parsed_status["commit"],
        "is_dirty": parsed_status["dirty_state"] == DIRTY_DIRTY,
        "status_short": parsed_status["status_short"],
        "remote_url": remote_url,
        "capture_state": capture_state,
        "capture_requested_utc": requested,
        "capture_completed_utc": _utc_timestamp(),
        "dirty_state": parsed_status["dirty_state"],
        "head_state": parsed_status["head_state"],
        "remote_state": remote_state,
        "capture_error": "; ".join(errors) or None,
    }


class SourceProvenanceCache:
    """Bounded daemon capture scheduler with one retained snapshot per token.

    At most ``max_active_captures`` collectors run concurrently. When capacity
    is full, only the latest queued request is retained; older queued requests
    are retired. Completed snapshots are kept in LRU order up to
    ``max_retained_snapshots`` and can be explicitly released by their owner.
    """

    def __init__(
        self,
        collector: Collector | None = None,
        *,
        max_active_captures: int = DEFAULT_MAX_ACTIVE_CAPTURES,
        max_retained_snapshots: int = DEFAULT_MAX_RETAINED_SNAPSHOTS,
    ) -> None:
        if max_active_captures < 1:
            raise ValueError("max_active_captures must be at least 1")
        if max_retained_snapshots < max_active_captures + 1:
            raise ValueError(
                "max_retained_snapshots must allow active captures plus one queued request"
            )
        self._collector = collector
        self._max_active_captures = int(max_active_captures)
        self._max_retained_snapshots = int(max_retained_snapshots)
        self._lock = Lock()
        self._snapshots: OrderedDict[Hashable, Mapping[str, Any]] = OrderedDict()
        self._active_jobs: dict[Hashable, _CaptureJob] = {}
        self._queued_job: _CaptureJob | None = None

    def request(
        self,
        repo_root: str | Path,
        *,
        token: Hashable | None = None,
    ) -> Hashable:
        capture_token: Hashable = uuid4().hex if token is None else token
        requested_utc = _utc_timestamp()
        pending = pending_source_provenance(repo_root, requested_utc=requested_utc)
        job_to_start: _CaptureJob | None = None
        with self._lock:
            if (
                capture_token in self._snapshots
                or capture_token in self._active_jobs
                or (
                    self._queued_job is not None
                    and self._queued_job.token == capture_token
                )
            ):
                return capture_token
            self._snapshots[capture_token] = MappingProxyType(pending)
            job = _CaptureJob(capture_token, Path(repo_root), requested_utc)
            if len(self._active_jobs) < self._max_active_captures:
                self._active_jobs[capture_token] = job
                job_to_start = job
            else:
                if self._queued_job is not None:
                    self._snapshots.pop(self._queued_job.token, None)
                self._queued_job = job
            self._evict_completed_locked()
        if job_to_start is not None:
            self._start_job(job_to_start)
        return capture_token

    def snapshot(self, token: Hashable | None) -> dict[str, Any] | None:
        if token is None:
            return None
        with self._lock:
            snapshot = self._snapshots.get(token)
            if snapshot is None:
                return None
            self._snapshots.move_to_end(token)
            return dict(snapshot)

    def release(self, token: Hashable | None) -> None:
        if token is None:
            return
        with self._lock:
            self._snapshots.pop(token, None)
            if self._queued_job is not None and self._queued_job.token == token:
                self._queued_job = None

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "entries": len(self._snapshots),
                "active": len(self._active_jobs),
                "queued": 0 if self._queued_job is None else 1,
            }

    def _start_job(self, job: _CaptureJob) -> None:
        Thread(
            target=self._collect,
            args=(job,),
            name="SourceProvenanceCapture",
            daemon=True,
        ).start()

    def _collect(self, job: _CaptureJob) -> None:
        collector = collect_source_provenance if self._collector is None else self._collector
        try:
            result = dict(collector(job.repo_root, requested_utc=job.requested_utc))
        except Exception as exc:
            result = unavailable_source_provenance(
                job.repo_root,
                requested_utc=job.requested_utc,
                error=f"collector:{exc.__class__.__name__}",
            )
        next_job: _CaptureJob | None = None
        with self._lock:
            if self._active_jobs.get(job.token) != job:
                return
            self._active_jobs.pop(job.token, None)
            if job.token in self._snapshots:
                self._snapshots[job.token] = MappingProxyType(result)
                self._snapshots.move_to_end(job.token)
            if self._queued_job is not None:
                next_job = self._queued_job
                self._queued_job = None
                self._active_jobs[next_job.token] = next_job
            self._evict_completed_locked()
        if next_job is not None:
            self._start_job(next_job)

    def _evict_completed_locked(self) -> None:
        protected = set(self._active_jobs)
        if self._queued_job is not None:
            protected.add(self._queued_job.token)
        while len(self._snapshots) > self._max_retained_snapshots:
            evicted = False
            for token in tuple(self._snapshots):
                if token not in protected:
                    self._snapshots.pop(token, None)
                    evicted = True
                    break
            if not evicted:
                break
