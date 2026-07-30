from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_logging.mini_dma_logger import metadata_checkpoints


class _Clock:
    def __init__(self) -> None:
        self.value = 1.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _payload(session_identity: str, point_count: int, *, state: str = "running") -> dict[str, object]:
    return {
        "session_identity": session_identity,
        "session_state": state,
        "point_count": point_count,
        "source_control": {"capture_state": "complete", "commit": "abc123"},
        "logging": {"sensor_sidecars": {}},
    }


def test_repeated_updates_keep_fast_local_checkpoint_and_reduce_destination_replaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    canonical = tmp_path / "drivefs-like" / "run" / "metadata.json"
    checkpoint_root = tmp_path / "local-checkpoints"
    store = metadata_checkpoints.SessionMetadataCheckpointStore(
        canonical,
        session_identity="session-a",
        checkpoint_root=checkpoint_root,
        canonical_write_interval_s=60.0,
        monotonic=clock,
    )
    writes: list[Path] = []
    original_replace = metadata_checkpoints._atomic_replace_text

    def _recording_replace(path: Path, text: str) -> None:
        writes.append(path)
        original_replace(path, text)

    monkeypatch.setattr(metadata_checkpoints, "_atomic_replace_text", _recording_replace)

    for point_count in range(121):
        store.write(_payload("session-a", point_count))
        clock.advance(5.0)

    destination_writes = [path for path in writes if path == canonical]
    checkpoint_writes = [path for path in writes if path == store.checkpoint_path]
    assert len(checkpoint_writes) == 121
    assert len(destination_writes) == 11
    assert len(destination_writes) < 121 / 10
    assert json.loads(canonical.read_text(encoding="utf-8"))["point_count"] == 120
    _, recovered = metadata_checkpoints.load_metadata_checkpoint(store.checkpoint_path)
    assert recovered["point_count"] == 120


def test_restart_can_load_newer_local_checkpoint_after_interrupted_run(tmp_path: Path) -> None:
    clock = _Clock()
    canonical = tmp_path / "remote" / "metadata.json"
    store = metadata_checkpoints.SessionMetadataCheckpointStore(
        canonical,
        session_identity="session-crash",
        checkpoint_root=tmp_path / "local",
        canonical_write_interval_s=60.0,
        monotonic=clock,
    )
    store.write(_payload("session-crash", 0))
    clock.advance(5.0)
    store.write(_payload("session-crash", 17))

    assert json.loads(canonical.read_text(encoding="utf-8"))["point_count"] == 0
    recovered_path, recovered = metadata_checkpoints.load_metadata_checkpoint(
        store.checkpoint_path
    )
    assert recovered_path == canonical
    assert recovered["session_state"] == "running"
    assert recovered["point_count"] == 17


def test_finalization_publishes_current_canonical_metadata_and_cleans_checkpoint(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    canonical = tmp_path / "remote" / "metadata.json"
    store = metadata_checkpoints.SessionMetadataCheckpointStore(
        canonical,
        session_identity="session-final",
        checkpoint_root=tmp_path / "local",
        canonical_write_interval_s=60.0,
        monotonic=clock,
    )
    store.write(_payload("session-final", 1))
    clock.advance(5.0)
    store.write(_payload("session-final", 12))
    result = store.write(_payload("session-final", 13, state="finished"), final=True)

    assert result.canonical_written is True
    assert result.checkpoint_cleanup_error is None
    assert not store.checkpoint_path.exists()
    assert json.loads(canonical.read_text(encoding="utf-8")) == _payload(
        "session-final",
        13,
        state="finished",
    )


def test_checkpoint_failure_falls_back_to_destination_and_remains_observable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = tmp_path / "remote" / "metadata.json"
    store = metadata_checkpoints.SessionMetadataCheckpointStore(
        canonical,
        session_identity="session-checkpoint-failure",
        checkpoint_root=tmp_path / "local",
    )
    original_replace = metadata_checkpoints._atomic_replace_text

    def _fail_checkpoint(path: Path, text: str) -> None:
        if path == store.checkpoint_path:
            raise OSError("synthetic local checkpoint failure")
        original_replace(path, text)

    monkeypatch.setattr(metadata_checkpoints, "_atomic_replace_text", _fail_checkpoint)

    with pytest.raises(OSError, match="synthetic local checkpoint failure"):
        store.write(_payload("session-checkpoint-failure", 4))

    assert json.loads(canonical.read_text(encoding="utf-8"))["point_count"] == 4


def test_destination_failure_keeps_current_local_recovery_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = tmp_path / "remote" / "metadata.json"
    store = metadata_checkpoints.SessionMetadataCheckpointStore(
        canonical,
        session_identity="session-destination-failure",
        checkpoint_root=tmp_path / "local",
    )
    original_replace = metadata_checkpoints._atomic_replace_text

    def _fail_destination(path: Path, text: str) -> None:
        if path == canonical:
            raise OSError("synthetic DriveFS replace failure")
        original_replace(path, text)

    monkeypatch.setattr(metadata_checkpoints, "_atomic_replace_text", _fail_destination)

    with pytest.raises(OSError, match="synthetic DriveFS replace failure"):
        store.write(_payload("session-destination-failure", 9))

    _, recovered = metadata_checkpoints.load_metadata_checkpoint(store.checkpoint_path)
    assert recovered["point_count"] == 9
    assert not canonical.exists()


def test_atomic_replace_failure_preserves_previous_valid_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    metadata_checkpoints._atomic_replace_text(checkpoint, '{"point_count": 1}')
    original_replace = metadata_checkpoints.os.replace

    def _fail_replace(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == checkpoint:
            raise OSError("synthetic replace failure")
        original_replace(source, destination)

    monkeypatch.setattr(metadata_checkpoints.os, "replace", _fail_replace)

    with pytest.raises(OSError, match="synthetic replace failure"):
        metadata_checkpoints._atomic_replace_text(checkpoint, '{"point_count": 2}')

    assert json.loads(checkpoint.read_text(encoding="utf-8"))["point_count"] == 1
    assert not checkpoint.with_name(f".{checkpoint.name}.pending").exists()


def test_checkpoint_rejects_mismatched_session_identity(tmp_path: Path) -> None:
    store = metadata_checkpoints.SessionMetadataCheckpointStore(
        tmp_path / "metadata.json",
        session_identity="expected",
        checkpoint_root=tmp_path / "local",
    )

    with pytest.raises(ValueError, match="session identity"):
        store.write(_payload("different", 1))
