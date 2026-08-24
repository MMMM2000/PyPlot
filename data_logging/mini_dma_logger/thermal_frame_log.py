from __future__ import annotations

import math
import queue
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock, RLock, Thread
from typing import Callable, Iterator, Sequence
from uuid import uuid4


THERMAL_FRAME_LOG_FILENAME = "ir_frames.bin"
THERMAL_FRAME_LOG_FORMAT = "tma-ir-frames-v1"
_FILE_MAGIC = b"TMAIRF1\0"
_FILE_HEADER = struct.Struct("<8sHHI")
_RECORD_MAGIC = b"FRM1"
_RECORD_HEADER = struct.Struct("<4sIQQddHHIfI")
_CRC = struct.Struct("<I")


@dataclass(frozen=True)
class ThermalFrameRecord:
    sequence: int
    device_elapsed_ms: int
    host_timestamp_s: float
    session_elapsed_s: float
    width: int
    height: int
    flags: int
    ambient_c: float
    raw_read_us: int
    values_c: tuple[float, ...]


def thermal_frame_record_from_object(
    frame: object,
    *,
    host_timestamp_s: float,
    session_start_wall_s: float,
) -> ThermalFrameRecord:
    width = int(getattr(frame, "width", 0) or 0)
    height = int(getattr(frame, "height", 0) or 0)
    values = tuple(float(value) for value in getattr(frame, "values", ()))
    if width <= 0 or height <= 0 or len(values) != width * height:
        raise ValueError(
            f"invalid thermal frame dimensions {width}x{height} for {len(values)} pixels"
        )
    if not all(math.isfinite(value) or math.isnan(value) for value in values):
        raise ValueError("thermal frame contains an infinite pixel value")
    ambient = getattr(frame, "ambient_c", math.nan)
    return ThermalFrameRecord(
        sequence=max(0, int(getattr(frame, "sequence", 0) or 0)),
        device_elapsed_ms=max(0, int(getattr(frame, "elapsed_ms", 0) or 0)),
        host_timestamp_s=float(host_timestamp_s),
        session_elapsed_s=max(0.0, float(host_timestamp_s) - float(session_start_wall_s)),
        width=width,
        height=height,
        flags=max(0, int(getattr(frame, "flags", 0) or 0)),
        ambient_c=math.nan if ambient is None else float(ambient),
        raw_read_us=max(0, int(getattr(frame, "raw_read_us", 0) or 0)),
        values_c=values,
    )


def _encode_record(record: ThermalFrameRecord) -> bytes:
    pixels = struct.pack(f"<{len(record.values_c)}f", *record.values_c)
    record_size = _RECORD_HEADER.size + len(pixels) + _CRC.size
    header = _RECORD_HEADER.pack(
        _RECORD_MAGIC,
        record_size,
        record.sequence,
        record.device_elapsed_ms,
        record.host_timestamp_s,
        record.session_elapsed_s,
        record.width,
        record.height,
        record.flags,
        record.ambient_c,
        record.raw_read_us,
    )
    payload = header + pixels
    return payload + _CRC.pack(zlib.crc32(payload) & 0xFFFFFFFF)


def iter_thermal_frame_records(path: str | Path) -> Iterator[ThermalFrameRecord]:
    with Path(path).open("rb") as handle:
        file_header = handle.read(_FILE_HEADER.size)
        if len(file_header) != _FILE_HEADER.size:
            raise ValueError("thermal frame log has a truncated file header")
        magic, version, header_size, _reserved = _FILE_HEADER.unpack(file_header)
        if magic != _FILE_MAGIC or version != 1 or header_size != _FILE_HEADER.size:
            raise ValueError("unsupported thermal frame log format")
        while True:
            header = handle.read(_RECORD_HEADER.size)
            if not header:
                return
            if len(header) != _RECORD_HEADER.size:
                raise ValueError("thermal frame log ends in a truncated record header")
            (
                record_magic,
                record_size,
                sequence,
                device_elapsed_ms,
                host_timestamp_s,
                session_elapsed_s,
                width,
                height,
                flags,
                ambient_c,
                raw_read_us,
            ) = _RECORD_HEADER.unpack(header)
            pixel_count = int(width) * int(height)
            expected_size = _RECORD_HEADER.size + pixel_count * 4 + _CRC.size
            if record_magic != _RECORD_MAGIC or record_size != expected_size:
                raise ValueError("thermal frame log contains an invalid record header")
            pixels_and_crc = handle.read(pixel_count * 4 + _CRC.size)
            if len(pixels_and_crc) != pixel_count * 4 + _CRC.size:
                raise ValueError("thermal frame log ends in a truncated frame")
            pixel_bytes = pixels_and_crc[:-_CRC.size]
            (stored_crc,) = _CRC.unpack(pixels_and_crc[-_CRC.size:])
            if zlib.crc32(header + pixel_bytes) & 0xFFFFFFFF != stored_crc:
                raise ValueError("thermal frame log frame checksum mismatch")
            yield ThermalFrameRecord(
                sequence=sequence,
                device_elapsed_ms=device_elapsed_ms,
                host_timestamp_s=host_timestamp_s,
                session_elapsed_s=session_elapsed_s,
                width=width,
                height=height,
                flags=flags,
                ambient_c=ambient_c,
                raw_read_us=raw_read_us,
                values_c=struct.unpack(f"<{pixel_count}f", pixel_bytes),
            )


class SessionThermalFrameTarget:
    """Write calibrated thermal frames without blocking the camera worker."""

    def __init__(
        self,
        path: str | Path,
        *,
        name: str = "ir_frames",
        metadata_path: Path | None = None,
        session_identity: str | None = None,
        metadata_lock: RLock | None = None,
        queue_capacity: int = 512,
        flush_frames: int = 64,
    ) -> None:
        self.path = Path(path)
        self.name = str(name)
        self.metadata_path = None if metadata_path is None else Path(metadata_path)
        self.session_identity = str(session_identity or uuid4().hex)
        self.target_identity = uuid4().hex
        self.metadata_lock = metadata_lock or RLock()
        self._queue: queue.Queue[ThermalFrameRecord | None] = queue.Queue(
            maxsize=max(1, int(queue_capacity))
        )
        self._flush_frames = max(1, int(flush_frames))
        self._state_lock = Lock()
        self.closed_event = Event()
        self.accepting = True
        self.accepted_rows = 0
        self.written_rows = 0
        self.failed_rows = 0
        self.failure_reason: str | None = None
        self._failure_notice_taken = False
        self._reconciliation_requested = False
        self.reconciliation_attempts = 0
        self.reconciliation_result: str | None = None
        self._thread = Thread(
            target=self._write_loop,
            name="ir-frames-writer",
            daemon=True,
        )
        self._thread.start()

    def _latch_failure(self, stage: str, exc: BaseException) -> str | None:
        reason = f"{stage}: {type(exc).__name__}: {exc}"
        with self._state_lock:
            if self.failure_reason is not None:
                return None
            self.failure_reason = reason
            self.accepting = False
        return reason

    def submit(self, record: ThermalFrameRecord) -> str | None:
        with self._state_lock:
            if not self.accepting:
                return self.failure_reason
            self.accepted_rows += 1
        try:
            self._queue.put_nowait(record)
        except queue.Full as exc:
            with self._state_lock:
                self.failed_rows += 1
            return self._latch_failure("queue_overflow", exc)
        return None

    def reject(self, stage: str, exc: BaseException) -> str | None:
        """Mark the sidecar incomplete when an incoming frame is invalid."""
        return self._latch_failure(stage, exc)

    def detach_and_close(self) -> None:
        with self._state_lock:
            if not self.accepting and self.closed_event.is_set():
                return
            self.accepting = False
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            # A daemon helper may block here only during shutdown, allowing all
            # already accepted frames to reach disk before the sentinel.
            Thread(target=self._queue.put, args=(None,), daemon=True).start()

    def wait_closed(self, timeout_s: float) -> bool:
        return self.closed_event.wait(timeout=max(0.0, float(timeout_s)))

    def take_failure_notice(self) -> str | None:
        with self._state_lock:
            if self.failure_reason is None or self._failure_notice_taken:
                return None
            self._failure_notice_taken = True
            return self.failure_reason

    def outcome(self, *, close_timed_out: bool = False) -> dict[str, object]:
        with self._state_lock:
            accepted = self.accepted_rows
            written = self.written_rows
            failed = self.failed_rows
            failure_reason = self.failure_reason
        pending = max(0, accepted - written - failed)
        if close_timed_out:
            status, complete, reason = "incomplete", False, failure_reason or "close_timeout"
        elif failure_reason is not None:
            status, complete, reason = "incomplete", False, failure_reason
        elif self.closed_event.is_set():
            status, complete, reason = "complete", True, None
        else:
            status, complete, reason = "active", None, None
        return {
            "session_identity": self.session_identity,
            "target_identity": self.target_identity,
            "status": status,
            "complete": complete,
            "format": THERMAL_FRAME_LOG_FORMAT,
            "filename": self.path.name,
            "accepted_rows": accepted,
            "written_rows": written,
            "lost_rows": failed,
            "pending_rows": pending,
            "reason": reason,
        }

    def request_reconciliation(
        self,
        observer: Callable[["SessionThermalFrameTarget"], None] | None = None,
    ) -> bool:
        # The GUI's existing sensor-sidecar reconciliation path is deliberately
        # duck typed, so this target can use the same metadata patching helper.
        with self._state_lock:
            if self._reconciliation_requested:
                return False
            self._reconciliation_requested = True
        Thread(
            target=self._reconcile_when_closed,
            args=(observer,),
            name="ir-frames-metadata-reconcile",
            daemon=True,
        ).start()
        return True

    def _reconcile_when_closed(
        self,
        observer: Callable[["SessionThermalFrameTarget"], None] | None,
    ) -> None:
        self.closed_event.wait()
        try:
            if self.metadata_path is None:
                raise ValueError("sensor sidecar metadata path is unavailable")
            # Imported lazily to avoid a circular dependency with the logger.
            from data_logging.mini_dma_logger.mini_dma_logger import (
                patch_sensor_sidecar_metadata,
            )

            with self.metadata_lock:
                patch_sensor_sidecar_metadata(
                    self.metadata_path,
                    session_identity=self.session_identity,
                    target_identity=self.target_identity,
                    sidecar_name=self.name,
                    outcome=self.outcome(),
                )
        except (OSError, ValueError) as exc:
            result = f"reconcile_failed: {type(exc).__name__}: {exc}"
        else:
            result = "reconciled"
        with self._state_lock:
            self.reconciliation_attempts += 1
            self.reconciliation_result = result
        if observer is not None:
            observer(self)

    def _write_loop(self) -> None:
        frames_since_flush = 0
        last_flush_s = time.monotonic()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("wb", buffering=1024 * 1024) as handle:
                handle.write(_FILE_HEADER.pack(_FILE_MAGIC, 1, _FILE_HEADER.size, 0))
                while True:
                    record = self._queue.get()
                    if record is None:
                        break
                    try:
                        handle.write(_encode_record(record))
                    except BaseException as exc:
                        with self._state_lock:
                            self.failed_rows += 1
                        self._latch_failure("write_failed", exc)
                        break
                    with self._state_lock:
                        self.written_rows += 1
                    frames_since_flush += 1
                    now_s = time.monotonic()
                    if frames_since_flush >= self._flush_frames or now_s - last_flush_s >= 1.0:
                        handle.flush()
                        frames_since_flush = 0
                        last_flush_s = now_s
                handle.flush()
        except BaseException as exc:
            self._latch_failure("open_or_flush_failed", exc)
        finally:
            with self._state_lock:
                unresolved = max(0, self.accepted_rows - self.written_rows - self.failed_rows)
                self.failed_rows += unresolved
            self.closed_event.set()
