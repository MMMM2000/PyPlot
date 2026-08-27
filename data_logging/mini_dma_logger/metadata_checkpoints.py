from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4


CHECKPOINT_FORMAT_VERSION = 1
DEFAULT_CANONICAL_WRITE_INTERVAL_S = 60.0
ATOMIC_REPLACE_RETRY_DELAYS_S = (0.01, 0.02, 0.05, 0.1)


def default_checkpoint_root() -> Path:
    override = os.environ.get("MINI_DMA_METADATA_CHECKPOINT_DIR", "").strip()
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "PyPlot" / "MiniDMA" / "metadata_checkpoints"
    state_home = os.environ.get("XDG_STATE_HOME", "").strip()
    if state_home:
        return Path(state_home) / "pyplot" / "mini_dma" / "metadata_checkpoints"
    return Path.home() / ".local" / "state" / "pyplot" / "mini_dma" / "metadata_checkpoints"


def _atomic_replace_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.pending")
    try:
        temporary.write_text(text, encoding="utf-8")
        for retry_delay_s in (*ATOMIC_REPLACE_RETRY_DELAYS_S, None):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if retry_delay_s is None:
                    raise
                time.sleep(retry_delay_s)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


@dataclass(frozen=True)
class MetadataWriteResult:
    checkpoint_path: Path
    checkpoint_written: bool
    canonical_written: bool
    checkpoint_cleanup_error: str | None = None


class SessionMetadataCheckpointStore:
    """Keep frequent metadata checkpoints local and publish canonical JSON less often."""

    def __init__(
        self,
        canonical_path: str | Path,
        *,
        session_identity: str,
        checkpoint_root: str | Path | None = None,
        canonical_write_interval_s: float = DEFAULT_CANONICAL_WRITE_INTERVAL_S,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        identity = str(session_identity).strip()
        if not identity:
            raise ValueError("session_identity must not be empty")
        self.canonical_path = Path(canonical_path)
        self.session_identity = identity
        self.checkpoint_root = (
            default_checkpoint_root() if checkpoint_root is None else Path(checkpoint_root)
        )
        self.checkpoint_path = self.checkpoint_root / f"{identity}.json"
        self.canonical_write_interval_s = max(0.0, float(canonical_write_interval_s))
        self._monotonic = monotonic
        self._last_canonical_write_s = 0.0
        self._canonical_parent_established = self.canonical_path.parent.is_dir()

    def _checkpoint_text(self, payload: Mapping[str, Any]) -> str:
        envelope = {
            "checkpoint_format_version": CHECKPOINT_FORMAT_VERSION,
            "session_identity": self.session_identity,
            "canonical_metadata_path": str(self.canonical_path),
            "metadata": dict(payload),
        }
        return json.dumps(envelope, indent=2)

    def _canonical_write_due(self, now_s: float) -> bool:
        if self._last_canonical_write_s <= 0.0:
            return True
        return now_s - self._last_canonical_write_s >= self.canonical_write_interval_s

    def write(
        self,
        payload: Mapping[str, Any],
        *,
        final: bool = False,
        force_canonical: bool = False,
    ) -> MetadataWriteResult:
        if payload.get("session_identity") != self.session_identity:
            raise ValueError("metadata session identity does not match checkpoint store")
        canonical_text = json.dumps(dict(payload), indent=2)
        checkpoint_written = False
        checkpoint_error: OSError | None = None
        try:
            _atomic_replace_text(self.checkpoint_path, self._checkpoint_text(payload))
            checkpoint_written = True
        except OSError as exc:
            checkpoint_error = exc

        now_s = self._monotonic()
        write_canonical = (
            final
            or force_canonical
            or checkpoint_error is not None
            or self._canonical_write_due(now_s)
        )
        canonical_written = False
        if write_canonical:
            if self._canonical_parent_established and not self.canonical_path.parent.is_dir():
                raise FileNotFoundError(
                    f"established canonical metadata directory disappeared: "
                    f"{self.canonical_path.parent}"
                )
            _atomic_replace_text(self.canonical_path, canonical_text)
            self._last_canonical_write_s = now_s
            self._canonical_parent_established = True
            canonical_written = True

        if checkpoint_error is not None:
            raise checkpoint_error

        cleanup_error = None
        if final and canonical_written:
            try:
                self.checkpoint_path.unlink(missing_ok=True)
            except OSError as exc:
                cleanup_error = str(exc)
        return MetadataWriteResult(
            checkpoint_path=self.checkpoint_path,
            checkpoint_written=checkpoint_written,
            canonical_written=canonical_written,
            checkpoint_cleanup_error=cleanup_error,
        )


def load_metadata_checkpoint(path: str | Path) -> tuple[Path, dict[str, Any]]:
    checkpoint_path = Path(path)
    envelope = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if not isinstance(envelope, dict):
        raise ValueError("metadata checkpoint is not an object")
    if envelope.get("checkpoint_format_version") != CHECKPOINT_FORMAT_VERSION:
        raise ValueError("unsupported metadata checkpoint format")
    session_identity = envelope.get("session_identity")
    canonical_path = envelope.get("canonical_metadata_path")
    payload = envelope.get("metadata")
    if not isinstance(session_identity, str) or not session_identity:
        raise ValueError("metadata checkpoint has no session identity")
    if not isinstance(canonical_path, str) or not canonical_path:
        raise ValueError("metadata checkpoint has no canonical path")
    if not isinstance(payload, dict):
        raise ValueError("metadata checkpoint payload is not an object")
    if payload.get("session_identity") != session_identity:
        raise ValueError("metadata checkpoint session identity changed")
    return Path(canonical_path), payload
