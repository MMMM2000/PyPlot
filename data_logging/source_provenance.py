from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Hashable, Mapping, Sequence
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

GitRunner = Callable[..., Any]
Collector = Callable[..., Mapping[str, Any]]


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

    branch, branch_result = _git_text(("branch", "--show-current"))
    commit, commit_result = _git_text(("rev-parse", "HEAD"))
    status, status_result = _git_text(("status", "--short"))
    remote_url, remote_result = _git_text(
        ("config", "--get", "remote.origin.url"),
        missing_exit_code=1,
    )

    dirty_state = DIRTY_UNKNOWN
    if status_result == "ok":
        dirty_state = DIRTY_DIRTY if status else DIRTY_CLEAN

    if branch_result == "ok":
        head_state = "attached" if branch else "detached"
    else:
        head_state = "unknown"
    if remote_result == "ok":
        remote_state = "configured"
    elif remote_result == "missing":
        remote_state = "missing"
    else:
        remote_state = "unknown"

    failures = {
        "branch": branch_result,
        "commit": commit_result,
        "status": status_result,
        "remote": remote_result,
    }
    errors = [
        f"{name}:{result}"
        for name, result in failures.items()
        if result not in {"ok", "missing"}
    ]
    capture_state = CAPTURE_COMPLETE if not errors else CAPTURE_UNAVAILABLE
    return {
        "repo_root": str(root),
        "branch": branch,
        "commit": commit,
        "is_dirty": dirty_state == DIRTY_DIRTY,
        "status_short": status or "",
        "remote_url": remote_url,
        "capture_state": capture_state,
        "capture_requested_utc": requested,
        "capture_completed_utc": _utc_timestamp(),
        "dirty_state": dirty_state,
        "head_state": head_state,
        "remote_state": remote_state,
        "capture_error": "; ".join(errors) or None,
    }


class SourceProvenanceCache:
    """Start exactly one daemon capture per token and cache immutable results."""

    def __init__(self, collector: Collector | None = None) -> None:
        self._collector = collector
        self._lock = Lock()
        self._snapshots: dict[Hashable, Mapping[str, Any]] = {}

    def request(
        self,
        repo_root: str | Path,
        *,
        token: Hashable | None = None,
    ) -> Hashable:
        capture_token: Hashable = uuid4().hex if token is None else token
        requested_utc = _utc_timestamp()
        pending = pending_source_provenance(repo_root, requested_utc=requested_utc)
        with self._lock:
            if capture_token in self._snapshots:
                return capture_token
            self._snapshots[capture_token] = MappingProxyType(pending)
        Thread(
            target=self._collect,
            args=(capture_token, Path(repo_root), requested_utc),
            name="SourceProvenanceCapture",
            daemon=True,
        ).start()
        return capture_token

    def snapshot(self, token: Hashable | None) -> dict[str, Any] | None:
        if token is None:
            return None
        with self._lock:
            snapshot = self._snapshots.get(token)
            return None if snapshot is None else dict(snapshot)

    def _collect(self, token: Hashable, repo_root: Path, requested_utc: str) -> None:
        collector = collect_source_provenance if self._collector is None else self._collector
        try:
            result = dict(collector(repo_root, requested_utc=requested_utc))
        except Exception as exc:
            result = unavailable_source_provenance(
                repo_root,
                requested_utc=requested_utc,
                error=f"collector:{exc.__class__.__name__}",
            )
        with self._lock:
            self._snapshots[token] = MappingProxyType(result)
