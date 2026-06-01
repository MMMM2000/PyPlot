from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable

from .driver import HmpSerialDriver


def default_lock_path() -> Path:
    configured = os.environ.get("PYPLOT_HMP_BENCH_LOCK")
    if configured:
        return Path(configured)
    if os.name == "nt":
        return Path(r"C:\tmp\pyplot_hmp_bench.lock")
    return Path(tempfile.gettempdir()) / "pyplot_hmp_bench.lock"


@dataclass(frozen=True)
class BenchLockInfo:
    owner: str
    purpose: str
    pid: int
    cwd: str
    created_at_utc: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "purpose": self.purpose,
            "pid": self.pid,
            "cwd": self.cwd,
            "created_at_utc": self.created_at_utc,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BenchLockInfo":
        return cls(
            owner=str(payload.get("owner") or "unknown"),
            purpose=str(payload.get("purpose") or ""),
            pid=int(payload.get("pid") or 0),
            cwd=str(payload.get("cwd") or ""),
            created_at_utc=str(payload.get("created_at_utc") or ""),
        )


class BenchLockBusy(RuntimeError):
    def __init__(self, lock_path: Path, info: BenchLockInfo | None) -> None:
        self.lock_path = lock_path
        self.info = info
        if info is None:
            message = f"HMP bench lock is held at {lock_path}."
        else:
            message = (
                f"HMP bench lock is held by {info.owner or 'unknown'}"
                f" (pid {info.pid}, purpose: {info.purpose or 'unspecified'})."
            )
        super().__init__(message)


class BenchLock:
    def __init__(self, *, lock_path: Path, info: BenchLockInfo) -> None:
        self.lock_path = lock_path
        self.info = info
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            existing = read_lock_info(self.lock_path)
            if existing is not None and existing.pid != self.info.pid:
                return
        except Exception:
            pass
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self) -> "BenchLock":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.release()


def read_lock_info(lock_path: Path | None = None) -> BenchLockInfo | None:
    path = default_lock_path() if lock_path is None else Path(lock_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return BenchLockInfo.from_dict(payload)


def acquire_bench_lock(
    *,
    owner: str,
    purpose: str = "",
    lock_path: Path | None = None,
) -> BenchLock:
    path = default_lock_path() if lock_path is None else Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    info = BenchLockInfo(
        owner=str(owner or "unknown"),
        purpose=str(purpose or ""),
        pid=os.getpid(),
        cwd=str(Path.cwd()),
        created_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(path), flags)
    except FileExistsError as exc:
        raise BenchLockBusy(path, read_lock_info(path)) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(info.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
    return BenchLock(lock_path=path, info=info)


def wait_for_bench_lock(
    *,
    owner: str,
    purpose: str = "",
    timeout_s: float = 0.0,
    poll_s: float = 1.0,
    lock_path: Path | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.monotonic,
) -> BenchLock:
    deadline = now_fn() + max(0.0, float(timeout_s))
    last_error: BenchLockBusy | None = None
    while True:
        try:
            return acquire_bench_lock(owner=owner, purpose=purpose, lock_path=lock_path)
        except BenchLockBusy as exc:
            last_error = exc
            if now_fn() >= deadline:
                raise exc
            sleep_fn(max(0.05, min(float(poll_s), deadline - now_fn())))
        if now_fn() >= deadline and last_error is not None:
            raise last_error


def identify_hmp_with_blank_retry(
    driver: HmpSerialDriver,
    *,
    attempts: int = 6,
    delay_s: float = 0.35,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> str:
    tries = max(1, int(attempts))
    last_idn = ""
    for index in range(tries):
        reset = getattr(driver, "reset_io_buffers", None)
        if callable(reset):
            reset()
        last_idn = str(driver.identify() or "")
        if getattr(driver, "profile", None) is not None or last_idn.strip():
            return last_idn
        if index + 1 < tries:
            sleep_fn(max(0.0, float(delay_s)))
    return last_idn


@dataclass(frozen=True)
class BenchProbeResult:
    available: bool
    message: str
    idn: str = ""
    channel_readbacks: dict[int, dict[str, Any]] | None = None
    busy_channels: tuple[int, ...] = ()
    unknown_output_channels: tuple[int, ...] = ()

    @property
    def electrically_idle(self) -> bool:
        return self.available and not self.busy_channels and not self.unknown_output_channels


def probe_hmp_bench(
    *,
    port_name: str = "COM3",
    baudrate: int = 115200,
    timeout_s: float = 0.8,
    channels: tuple[int, ...] = (1, 3, 4),
    driver_factory: Callable[..., HmpSerialDriver] = HmpSerialDriver,
) -> BenchProbeResult:
    driver = driver_factory(port_name=port_name, baudrate=baudrate, timeout_s=timeout_s)
    try:
        driver.connect()
        idn = identify_hmp_with_blank_retry(driver)
        readbacks: dict[int, dict[str, Any]] = {}
        busy_channels: list[int] = []
        unknown_output_channels: list[int] = []
        for channel in channels:
            output_on = driver.output_state(channel=int(channel))
            if output_on is True:
                busy_channels.append(int(channel))
            elif output_on is None:
                unknown_output_channels.append(int(channel))
            readbacks[int(channel)] = {
                "output_on": output_on,
                "readback": driver.measure(channel=int(channel)),
            }
        return BenchProbeResult(
            available=True,
            message=f"HMP bench available on {port_name} at {baudrate} baud.",
            idn=idn,
            channel_readbacks=readbacks,
            busy_channels=tuple(busy_channels),
            unknown_output_channels=tuple(unknown_output_channels),
        )
    except Exception as exc:
        return BenchProbeResult(
            available=False,
            message=f"HMP bench unavailable on {port_name}: {exc}",
        )
    finally:
        try:
            driver.close()
        except Exception:
            pass
