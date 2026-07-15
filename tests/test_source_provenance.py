from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_logging import source_provenance


def _runner_for(
    replies: dict[tuple[str, ...], tuple[int, str] | BaseException],
):
    def _run(args: list[str], **_kwargs: object) -> object:
        reply = replies[tuple(args[3:])]
        if isinstance(reply, BaseException):
            raise reply
        returncode, stdout = reply
        return SimpleNamespace(returncode=returncode, stdout=stdout)

    return _run


def _base_replies() -> dict[tuple[str, ...], tuple[int, str] | BaseException]:
    return {
        ("branch", "--show-current"): (0, "main\n"),
        ("rev-parse", "HEAD"): (0, "abc123\n"),
        ("status", "--short"): (0, ""),
        ("config", "--get", "remote.origin.url"): (0, "https://example.test/repo.git\n"),
    }


def test_collect_source_provenance_clean_preserves_legacy_keys(tmp_path: Path) -> None:
    snapshot = source_provenance.collect_source_provenance(
        tmp_path,
        requested_utc="2026-07-15T10:00:00.000Z",
        runner=_runner_for(_base_replies()),
    )

    assert snapshot["capture_state"] == "complete"
    assert snapshot["capture_requested_utc"] == "2026-07-15T10:00:00.000Z"
    assert snapshot["capture_completed_utc"]
    assert snapshot["dirty_state"] == "clean"
    assert snapshot["head_state"] == "attached"
    assert snapshot["remote_state"] == "configured"
    assert snapshot["capture_error"] is None
    assert snapshot["branch"] == "main"
    assert snapshot["commit"] == "abc123"
    assert snapshot["is_dirty"] is False
    assert snapshot["status_short"] == ""
    assert snapshot["remote_url"] == "https://example.test/repo.git"
    assert set(
        ("repo_root", "branch", "commit", "is_dirty", "status_short", "remote_url")
    ).issubset(snapshot)


def test_collect_source_provenance_dirty(tmp_path: Path) -> None:
    replies = _base_replies()
    replies[("status", "--short")] = (0, " M changed.py\n")

    snapshot = source_provenance.collect_source_provenance(
        tmp_path,
        runner=_runner_for(replies),
    )

    assert snapshot["capture_state"] == "complete"
    assert snapshot["dirty_state"] == "dirty"
    assert snapshot["is_dirty"] is True
    assert snapshot["status_short"] == "M changed.py"


@pytest.mark.parametrize(
    ("reply_key", "reply", "error_fragment"),
    [
        (("status", "--short"), (128, ""), "status:exit:128"),
        (
            ("status", "--short"),
            subprocess.TimeoutExpired(cmd="git status", timeout=0.01),
            "status:timeout",
        ),
    ],
)
def test_collect_source_provenance_unavailable_or_timeout_is_not_falsely_clean(
    tmp_path: Path,
    reply_key: tuple[str, ...],
    reply: tuple[int, str] | BaseException,
    error_fragment: str,
) -> None:
    replies = _base_replies()
    replies[reply_key] = reply

    snapshot = source_provenance.collect_source_provenance(
        tmp_path,
        runner=_runner_for(replies),
    )

    assert snapshot["capture_state"] == "unavailable"
    assert snapshot["dirty_state"] == "unknown"
    assert snapshot["is_dirty"] is False
    assert error_fragment in snapshot["capture_error"]


def test_collect_source_provenance_detached_head_is_complete(tmp_path: Path) -> None:
    replies = _base_replies()
    replies[("branch", "--show-current")] = (0, "")

    snapshot = source_provenance.collect_source_provenance(
        tmp_path,
        runner=_runner_for(replies),
    )

    assert snapshot["capture_state"] == "complete"
    assert snapshot["head_state"] == "detached"
    assert snapshot["branch"] is None
    assert snapshot["commit"] == "abc123"


def test_collect_source_provenance_missing_remote_is_complete(tmp_path: Path) -> None:
    replies = _base_replies()
    replies[("config", "--get", "remote.origin.url")] = (1, "")

    snapshot = source_provenance.collect_source_provenance(
        tmp_path,
        runner=_runner_for(replies),
    )

    assert snapshot["capture_state"] == "complete"
    assert snapshot["remote_state"] == "missing"
    assert snapshot["remote_url"] is None
    assert snapshot["capture_error"] is None


def test_source_provenance_cache_runs_collector_once_per_token_and_reuses_snapshot(
    tmp_path: Path,
) -> None:
    release = threading.Event()
    calls: list[str] = []

    def _collector(repo_root: Path, *, requested_utc: str) -> dict[str, object]:
        calls.append(requested_utc)
        assert release.wait(timeout=2.0)
        snapshot = source_provenance.pending_source_provenance(
            repo_root,
            requested_utc=requested_utc,
        )
        snapshot.update(
            capture_state="complete",
            capture_completed_utc="2026-07-15T10:00:01.000Z",
            branch="codex/test",
            commit="abc123",
            dirty_state="clean",
            head_state="attached",
            remote_state="missing",
        )
        return snapshot

    cache = source_provenance.SourceProvenanceCache(_collector)
    token = cache.request(tmp_path, token="run-1")
    assert cache.request(tmp_path, token="run-1") == token
    assert cache.snapshot(token)["capture_state"] == "pending"  # type: ignore[index]
    assert len(calls) == 1

    release.set()
    deadline = time.monotonic() + 2.0
    while cache.snapshot(token)["capture_state"] == "pending":  # type: ignore[index]
        assert time.monotonic() < deadline
        time.sleep(0.005)

    first = cache.snapshot(token)
    assert first is not None
    first["commit"] = "operator-mutated-copy"
    assert cache.snapshot(token)["commit"] == "abc123"  # type: ignore[index]
    assert len(calls) == 1


def test_permanently_blocked_capture_uses_only_a_daemon_thread(tmp_path: Path) -> None:
    entered = threading.Event()
    never_release = threading.Event()

    def _blocked_collector(_repo_root: Path, *, requested_utc: str) -> dict[str, object]:
        del requested_utc
        entered.set()
        never_release.wait()
        raise AssertionError("unreachable")

    cache = source_provenance.SourceProvenanceCache(_blocked_collector)
    cache.request(tmp_path, token="blocked")
    assert entered.wait(timeout=1.0)

    capture_threads = [
        thread
        for thread in threading.enumerate()
        if thread.name == "SourceProvenanceCapture" and thread.is_alive()
    ]
    assert capture_threads
    assert all(thread.daemon for thread in capture_threads)
