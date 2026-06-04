"""Detached AC sweep PSU watchdog.

The GUI normally turns the PSU off in the sweep worker's ``finally`` block. This
watchdog covers harder exits such as a parent process disappearing during an
app update, where Python cleanup in the GUI process may never run.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import time
from typing import Sequence

from . import sweep


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_WAIT_TIMEOUT = 0x00000102


def _parent_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(_SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def _write_log(log_path: Path | None, event: str, **fields: object) -> None:
    if log_path is None:
        return
    payload = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, **fields}
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")
    except Exception:
        pass


def _heartbeat_stale(path: Path, *, timeout_s: float) -> bool:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return True
    return (time.time() - mtime) > max(1.0, float(timeout_s))


def _turn_output_off(*, backend: str, resource: str, baudrate: int, log_path: Path | None, reason: str) -> int:
    _write_log(log_path, "triggered", reason=reason, backend=backend, resource=resource)
    psu = sweep.SerialScpiCurrentSource(
        backend_id=backend,
        resource=resource,
        baudrate=baudrate,
    )
    try:
        psu.output_off()
    except Exception as exc:
        _write_log(log_path, "output_off_failed", error=str(exc))
        return 2
    finally:
        psu.close()
    _write_log(log_path, "output_off_sent")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Watch an AC sweep heartbeat and turn the PSU off if it dies.")
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--heartbeat", type=Path, required=True)
    parser.add_argument("--disarm", type=Path, required=True)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--resource", required=True)
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--timeout-s", type=float, default=8.0)
    parser.add_argument("--poll-s", type=float, default=1.0)
    parser.add_argument("--log", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    log_path = args.log
    _write_log(
        log_path,
        "armed",
        parent_pid=args.parent_pid,
        heartbeat=str(args.heartbeat),
        disarm=str(args.disarm),
        backend=args.backend,
        resource=args.resource,
    )
    while True:
        if args.disarm.exists():
            _write_log(log_path, "disarmed")
            return 0
        if not _parent_process_alive(args.parent_pid):
            return _turn_output_off(
                backend=args.backend,
                resource=args.resource,
                baudrate=args.baudrate,
                log_path=log_path,
                reason="parent process exited",
            )
        if _heartbeat_stale(args.heartbeat, timeout_s=args.timeout_s):
            return _turn_output_off(
                backend=args.backend,
                resource=args.resource,
                baudrate=args.baudrate,
                log_path=log_path,
                reason="heartbeat stale",
            )
        time.sleep(max(0.2, float(args.poll_s)))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
