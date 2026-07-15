from __future__ import annotations

import subprocess
import threading
import time
import json
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
        ("status", "--porcelain=v2", "--branch"): (
            0,
            "# branch.oid abc123\n# branch.head main\n",
        ),
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
    replies[("status", "--porcelain=v2", "--branch")] = (
        0,
        "# branch.oid abc123\n# branch.head main\n1 .M N... 100644 100644 100644 abc abc changed.py\n",
    )

    snapshot = source_provenance.collect_source_provenance(
        tmp_path,
        runner=_runner_for(replies),
    )

    assert snapshot["capture_state"] == "complete"
    assert snapshot["dirty_state"] == "dirty"
    assert snapshot["is_dirty"] is True
    assert snapshot["status_short"].startswith("1 .M")


@pytest.mark.parametrize(
    ("reply_key", "reply", "error_fragment"),
    [
        (("status", "--porcelain=v2", "--branch"), (128, ""), "status:exit:128"),
        (
            ("status", "--porcelain=v2", "--branch"),
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
    replies[("status", "--porcelain=v2", "--branch")] = (
        0,
        "# branch.oid abc123\n# branch.head (detached)\n",
    )

    snapshot = source_provenance.collect_source_provenance(
        tmp_path,
        runner=_runner_for(replies),
    )

    assert snapshot["capture_state"] == "complete"
    assert snapshot["head_state"] == "detached"
    assert snapshot["branch"] is None
    assert snapshot["commit"] == "abc123"


@pytest.mark.parametrize(
    ("text", "branch", "commit", "dirty_state", "head_state", "error"),
    [
        (
            "# branch.oid abc123\n# branch.head main\n",
            "main",
            "abc123",
            "clean",
            "attached",
            None,
        ),
        (
            "# branch.oid def456\n# branch.head feature\n? new.txt\n",
            "feature",
            "def456",
            "dirty",
            "attached",
            None,
        ),
        (
            "# branch.oid abc123\n# branch.head (detached)\n",
            None,
            "abc123",
            "clean",
            "detached",
            None,
        ),
        (
            "# branch.oid (initial)\n# branch.head main\n",
            "main",
            None,
            "clean",
            "attached",
            "unborn_head",
        ),
    ],
)
def test_parse_porcelain_v2_status_semantics(
    text: str,
    branch: str | None,
    commit: str | None,
    dirty_state: str,
    head_state: str,
    error: str | None,
) -> None:
    parsed = source_provenance.parse_porcelain_v2_status(text)

    assert parsed["branch"] == branch
    assert parsed["commit"] == commit
    assert parsed["dirty_state"] == dirty_state
    assert parsed["head_state"] == head_state
    assert parsed["error"] == error


def test_collect_source_provenance_unborn_head_is_unavailable(tmp_path: Path) -> None:
    replies = _base_replies()
    replies[("status", "--porcelain=v2", "--branch")] = (
        0,
        "# branch.oid (initial)\n# branch.head main\n",
    )

    snapshot = source_provenance.collect_source_provenance(
        tmp_path,
        runner=_runner_for(replies),
    )

    assert snapshot["capture_state"] == "unavailable"
    assert snapshot["branch"] == "main"
    assert snapshot["commit"] is None
    assert "unborn_head" in snapshot["capture_error"]


def test_collect_source_provenance_cannot_mix_branch_and_commit_when_repo_changes(
    tmp_path: Path,
) -> None:
    state = {"branch": "main", "commit": "old123"}
    calls: list[tuple[str, ...]] = []

    def _changing_runner(args: list[str], **_kwargs: object) -> object:
        command = tuple(args[3:])
        calls.append(command)
        if command == ("status", "--porcelain=v2", "--branch"):
            output = (
                f"# branch.oid {state['commit']}\n"
                f"# branch.head {state['branch']}\n"
            )
            state.update(branch="new-branch", commit="new456")
            return SimpleNamespace(returncode=0, stdout=output)
        assert command == ("config", "--get", "remote.origin.url")
        return SimpleNamespace(returncode=0, stdout="https://example.test/repo.git\n")

    snapshot = source_provenance.collect_source_provenance(
        tmp_path,
        runner=_changing_runner,
    )

    assert snapshot["branch"] == "main"
    assert snapshot["commit"] == "old123"
    assert calls == [
        ("status", "--porcelain=v2", "--branch"),
        ("config", "--get", "remote.origin.url"),
    ]


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


def test_patch_source_control_metadata_preserves_other_values_atomically(tmp_path: Path) -> None:
    metadata_path = tmp_path / "metadata.json"
    original = {
        "finished_utc": "2026-07-15T10:00:00Z",
        "stop": {"reason": "manual_session_stop"},
        "logging": {
            "run_log_txt": "run_log.txt",
            "run_log_complete": False,
            "run_log_incomplete_lines": 3,
        },
        "source_control": {"capture_state": "pending"},
    }
    metadata_path.write_text(json.dumps(original, indent=2), encoding="utf-8")
    completed = {"capture_state": "complete", "commit": "abc123"}

    source_provenance.patch_source_control_metadata(metadata_path, completed)
    patched = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert patched["source_control"] == completed
    patched.pop("source_control")
    original.pop("source_control")
    assert patched == original
    assert not list(tmp_path.glob(".metadata.json.*.tmp"))


@pytest.mark.parametrize("contents", ["{malformed", '{"other": true}'])
def test_patch_source_control_metadata_never_replaces_malformed_or_incompatible_file(
    tmp_path: Path,
    contents: str,
) -> None:
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(contents, encoding="utf-8")

    with pytest.raises((ValueError, json.JSONDecodeError)):
        source_provenance.patch_source_control_metadata(
            metadata_path,
            {"capture_state": "complete"},
        )

    assert metadata_path.read_text(encoding="utf-8") == contents
    assert not list(tmp_path.glob(".metadata.json.*.tmp"))


def test_patch_source_control_metadata_never_creates_missing_file(tmp_path: Path) -> None:
    metadata_path = tmp_path / "metadata.json"

    with pytest.raises(FileNotFoundError):
        source_provenance.patch_source_control_metadata(
            metadata_path,
            {"capture_state": "complete"},
        )

    assert not metadata_path.exists()


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


def test_source_provenance_cache_bounds_blocked_workers_and_retains_only_latest_queue(
    tmp_path: Path,
) -> None:
    entered: list[int] = []
    releases = [threading.Event() for _ in range(3)]

    def _collector(repo_root: Path, *, requested_utc: str) -> dict[str, object]:
        index = len(entered)
        entered.append(index)
        assert releases[index].wait(timeout=3.0)
        snapshot = source_provenance.pending_source_provenance(
            repo_root,
            requested_utc=requested_utc,
        )
        snapshot.update(
            capture_state="complete",
            capture_completed_utc=f"2026-07-15T10:00:0{index}.000Z",
            commit=f"commit-{index}",
            dirty_state="clean",
            head_state="attached",
            remote_state="missing",
        )
        return snapshot

    cache = source_provenance.SourceProvenanceCache(
        _collector,
        max_active_captures=2,
        max_retained_snapshots=4,
    )
    for index in range(10):
        cache.request(tmp_path, token=f"token-{index}")

    deadline = time.monotonic() + 1.0
    while len(entered) < 2:
        assert time.monotonic() < deadline
        time.sleep(0.005)
    assert cache.stats() == {"entries": 3, "active": 2, "queued": 1}
    assert cache.snapshot("token-9")["capture_state"] == "pending"  # type: ignore[index]
    for index in range(2, 9):
        assert cache.snapshot(f"token-{index}") is None

    releases[0].set()
    deadline = time.monotonic() + 1.0
    while len(entered) < 3:
        assert time.monotonic() < deadline
        time.sleep(0.005)
    assert cache.stats()["active"] == 2

    releases[2].set()
    deadline = time.monotonic() + 1.0
    while cache.snapshot("token-9")["capture_state"] == "pending":  # type: ignore[index]
        assert time.monotonic() < deadline
        time.sleep(0.005)
    assert cache.snapshot("token-9")["commit"] == "commit-2"  # type: ignore[index]
    releases[1].set()


def test_source_provenance_cache_release_evicts_and_discards_late_completion(
    tmp_path: Path,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def _collector(repo_root: Path, *, requested_utc: str) -> dict[str, object]:
        entered.set()
        assert release.wait(timeout=2.0)
        snapshot = source_provenance.pending_source_provenance(
            repo_root,
            requested_utc=requested_utc,
        )
        snapshot.update(capture_state="complete", commit="late")
        return snapshot

    cache = source_provenance.SourceProvenanceCache(_collector)
    token = cache.request(tmp_path, token="released")
    assert entered.wait(timeout=1.0)
    cache.release(token)
    assert cache.snapshot(token) is None

    release.set()
    deadline = time.monotonic() + 1.0
    while cache.stats()["active"]:
        assert time.monotonic() < deadline
        time.sleep(0.005)
    assert cache.snapshot(token) is None
    assert cache.stats()["entries"] == 0


def test_source_provenance_cache_bounds_completed_snapshots_and_release_evicts(
    tmp_path: Path,
) -> None:
    def _collector(repo_root: Path, *, requested_utc: str) -> dict[str, object]:
        snapshot = source_provenance.pending_source_provenance(
            repo_root,
            requested_utc=requested_utc,
        )
        snapshot.update(capture_state="complete", commit=requested_utc)
        return snapshot

    cache = source_provenance.SourceProvenanceCache(
        _collector,
        max_active_captures=1,
        max_retained_snapshots=3,
    )
    for index in range(6):
        token = cache.request(tmp_path, token=f"completed-{index}")
        deadline = time.monotonic() + 1.0
        while cache.snapshot(token)["capture_state"] == "pending":  # type: ignore[index]
            assert time.monotonic() < deadline
            time.sleep(0.005)

    assert cache.stats() == {"entries": 3, "active": 0, "queued": 0}
    assert cache.snapshot("completed-0") is None
    assert cache.snapshot("completed-5") is not None
    cache.release("completed-5")
    assert cache.snapshot("completed-5") is None
    assert cache.stats()["entries"] == 2


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
