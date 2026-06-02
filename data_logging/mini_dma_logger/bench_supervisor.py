from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from data_logging.shared_power_supply.bench_guard import identify_hmp_with_blank_retry
from data_logging.shared_power_supply.driver import HmpSerialDriver

from .bench_automation import MiniDmaBenchAutomationError, load_mini_dma_bench_plan


DEFAULT_SAFE_CHANNEL = 4
FINISHED_METADATA_CHILD_GRACE_S = 5.0
SAFE_OFF_CONNECT_ATTEMPTS = 6
SAFE_OFF_CONNECT_RETRY_S = 0.5


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _normalize_windows_path_env(env: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in env.items():
        normalized = "Path" if key.lower() == "path" else str(key)
        if any(existing.lower() == normalized.lower() for existing in result):
            continue
        result[normalized] = str(value)
    return result


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)


def _read_json_if_exists(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return dict(payload) if isinstance(payload, Mapping) else None


def _read_text_if_exists(path: Path | None, *, max_chars: int = 4000) -> str:
    if path is None or not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return text if len(text) <= max_chars else text[-max_chars:]


def _release_child_lock_if_held(lock_path: Path | None, child_pid: int | None) -> None:
    if lock_path is None or child_pid is None:
        return
    payload = _read_json_if_exists(lock_path)
    if not payload:
        return
    try:
        lock_pid = int(payload.get("pid") or 0)
    except (TypeError, ValueError):
        return
    if lock_pid != int(child_pid):
        return
    try:
        Path(lock_path).unlink()
    except FileNotFoundError:
        pass


def _latest_run_dir(log_dir: Path | None) -> str | None:
    if log_dir is None or not log_dir.exists():
        return None
    candidates = [path for path in log_dir.iterdir() if path.is_dir() and path.name != "metadata"]
    if not candidates:
        return None
    return str(max(candidates, key=lambda path: path.stat().st_mtime))


def _latest_run_finished_metadata(
    log_dir: Path | None,
    *,
    not_before_s: float,
) -> dict[str, Any] | None:
    latest = _latest_run_dir(log_dir)
    if latest is None:
        return None
    metadata_path = Path(latest) / "metadata.json"
    metadata = _read_json_if_exists(metadata_path)
    if metadata is None or str(metadata.get("session_state") or "") != "finished":
        return None
    try:
        mtime_s = metadata_path.stat().st_mtime
        age_s = max(0.0, time.time() - mtime_s)
    except Exception:
        mtime_s = 0.0
        age_s = 0.0
    if mtime_s < not_before_s - 1.0:
        return None
    if age_s < FINISHED_METADATA_CHILD_GRACE_S:
        return None
    return {
        "run_dir": latest,
        "metadata_path": str(metadata_path),
        "stop": metadata.get("stop"),
        "age_s": age_s,
    }


def _safe_channel_off(
    *,
    channel: int,
    port_name: str,
    baudrate: int,
    driver_factory: Callable[..., HmpSerialDriver] = HmpSerialDriver,
    attempts: int = SAFE_OFF_CONNECT_ATTEMPTS,
    retry_s: float = SAFE_OFF_CONNECT_RETRY_S,
) -> dict[str, Any]:
    last_error: Exception | None = None
    attempts = max(1, int(attempts))
    for attempt in range(1, attempts + 1):
        driver = driver_factory(port_name=port_name, baudrate=baudrate, timeout_s=0.8)
        try:
            driver.connect()
            idn = identify_hmp_with_blank_retry(driver, attempts=6, delay_s=0.35)
            driver.set_output(channel=channel, output_on=False)
            states = {
                str(ch): {
                    "output_on": driver.output_state(channel=ch),
                    "readback": driver.measure(channel=ch),
                }
                for ch in (1, 3, channel)
            }
            return {
                "status": "ok",
                "channel": channel,
                "idn": idn,
                "states": states,
                "attempt": attempt,
            }
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            message = str(exc)
            transient_access_error = (
                "Access is denied" in message
                or "PermissionError" in message
                or "could not open port" in message
            )
            if not transient_access_error:
                break
            time.sleep(max(0.0, float(retry_s)))
        finally:
            try:
                driver.close()
            except Exception:
                pass
    assert last_error is not None
    return {
        "status": "error",
        "channel": channel,
        "error": f"{type(last_error).__name__}: {last_error}",
        "attempts": attempts,
    }


def _finished_metadata_is_normal(supervisor_recovery: Mapping[str, Any] | None) -> bool:
    if not supervisor_recovery:
        return False
    stop = supervisor_recovery.get("stop")
    if not isinstance(stop, Mapping):
        return False
    reason = str(stop.get("reason") or "")
    category = str(stop.get("category") or "")
    return reason == "recipe_completed" or category == "normal"


def _status_payload(
    *,
    state: str,
    started_utc: str,
    plan_path: Path,
    command: Sequence[str],
    child_pid: int | None,
    child_returncode: int | None,
    lock_path: Path | None,
    summary_path: Path | None,
    log_dir: Path | None,
    stdout_path: Path,
    stderr_path: Path,
    safe_off: Mapping[str, Any] | None = None,
    supervisor_recovery: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    lock_payload = _read_json_if_exists(lock_path)
    summary_payload = _read_json_if_exists(summary_path)
    return {
        "kind": "mini_dma_bench_supervisor_status",
        "state": state,
        "started_utc": started_utc,
        "updated_utc": _utc_timestamp(),
        "supervisor_pid": os.getpid(),
        "child_pid": child_pid,
        "child_returncode": child_returncode,
        "plan_path": str(plan_path),
        "command": list(command),
        "lock_path": None if lock_path is None else str(lock_path),
        "lock": lock_payload,
        "summary_path": None if summary_path is None else str(summary_path),
        "summary": summary_payload,
        "latest_run_dir": _latest_run_dir(log_dir),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "stdout_tail": _read_text_if_exists(stdout_path, max_chars=1200),
        "stderr_tail": _read_text_if_exists(stderr_path, max_chars=1200),
        "safe_off": None if safe_off is None else dict(safe_off),
        "supervisor_recovery": None if supervisor_recovery is None else dict(supervisor_recovery),
    }


def _terminate_child_if_running(child: Any, *, timeout_s: float = 5.0) -> int | None:
    try:
        returncode = child.poll()
    except Exception:
        return None
    if returncode is not None:
        return int(returncode)
    try:
        child.terminate()
    except Exception:
        pass
    try:
        returncode = child.wait(timeout=timeout_s)
    except Exception:
        try:
            child.kill()
            returncode = child.wait(timeout=timeout_s)
        except Exception:
            return None
    return None if returncode is None else int(returncode)


def run_supervised_mini_dma_bench(
    plan_path: str | Path,
    *,
    python_executable: str | Path | None = None,
    launcher_path: str | Path = "launcher.py",
    status_path: str | Path | None = None,
    stdout_path: str | Path | None = None,
    stderr_path: str | Path | None = None,
    poll_interval_s: float = 1.0,
    safe_off_channel: int = DEFAULT_SAFE_CHANNEL,
    safe_off_port: str = "COM3",
    safe_off_baud: int = 115200,
    env_overrides: Mapping[str, str] | None = None,
    popen_factory: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
    safe_off_fn: Callable[..., dict[str, Any]] = _safe_channel_off,
) -> dict[str, Any]:
    plan = load_mini_dma_bench_plan(plan_path)
    resolved_plan_path = Path(plan_path).expanduser().resolve()
    python_path = str(Path(python_executable).expanduser() if python_executable is not None else sys.executable)
    launcher = Path(launcher_path)
    command = [
        python_path,
        str(launcher),
        "--mini-dma-bench-plan",
        str(resolved_plan_path),
    ]
    base = resolved_plan_path.parent
    log_root = base / "logs"
    stdout_file = Path(stdout_path).expanduser() if stdout_path is not None else log_root / "mini-dma-bench-supervisor.stdout.log"
    stderr_file = Path(stderr_path).expanduser() if stderr_path is not None else log_root / "mini-dma-bench-supervisor.stderr.log"
    status_file = Path(status_path).expanduser() if status_path is not None else base / "mini-dma-bench-supervisor.status.json"
    stdout_file.parent.mkdir(parents=True, exist_ok=True)
    stderr_file.parent.mkdir(parents=True, exist_ok=True)

    env = _normalize_windows_path_env(os.environ)
    if env_overrides:
        env.update({str(key): str(value) for key, value in env_overrides.items()})

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    started_utc = _utc_timestamp()
    started_wall_s = time.time()
    safe_off: dict[str, Any] | None = None
    child_returncode: int | None = None
    supervisor_recovery: dict[str, Any] | None = None
    with stdout_file.open("ab", buffering=0) as stdout_handle, stderr_file.open("ab", buffering=0) as stderr_handle:
        child = popen_factory(
            command,
            cwd=str(Path.cwd()),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            creationflags=creationflags,
        )
        child_pid = int(getattr(child, "pid", 0) or 0)
        _atomic_write_json(
            status_file,
            _status_payload(
                state="running",
                started_utc=started_utc,
                plan_path=resolved_plan_path,
                command=command,
                child_pid=child_pid,
                child_returncode=None,
                lock_path=plan.bench_lock.lock_path,
                summary_path=plan.summary_path,
                log_dir=plan.log_dir,
                stdout_path=stdout_file,
                stderr_path=stderr_file,
                supervisor_recovery=supervisor_recovery,
            ),
        )
        interrupted = False
        try:
            while True:
                child_returncode = child.poll()
                if child_returncode is not None:
                    break
                time.sleep(max(0.1, float(poll_interval_s)))
                finished_metadata = _latest_run_finished_metadata(plan.log_dir, not_before_s=started_wall_s)
                if finished_metadata is not None:
                    child_returncode = _terminate_child_if_running(child)
                    supervisor_recovery = {
                        "reason": "finished_metadata_child_still_running",
                        **finished_metadata,
                    }
                    break
                _atomic_write_json(
                    status_file,
                    _status_payload(
                        state="running",
                        started_utc=started_utc,
                        plan_path=resolved_plan_path,
                        command=command,
                        child_pid=child_pid,
                        child_returncode=None,
                        lock_path=plan.bench_lock.lock_path,
                        summary_path=plan.summary_path,
                        log_dir=plan.log_dir,
                        stdout_path=stdout_file,
                        stderr_path=stderr_file,
                        supervisor_recovery=supervisor_recovery,
                    ),
                )
        except BaseException:
            interrupted = True
            child_returncode = _terminate_child_if_running(child)
            raise
        finally:
            if interrupted:
                child_returncode = _terminate_child_if_running(child)
            safe_off = safe_off_fn(
                channel=int(safe_off_channel),
                port_name=str(safe_off_port),
                baudrate=int(safe_off_baud),
            )

    state = "completed" if child_returncode == 0 else "failed"
    _release_child_lock_if_held(plan.bench_lock.lock_path, child_pid)
    final_payload = _status_payload(
        state="completed" if _finished_metadata_is_normal(supervisor_recovery) else state,
        started_utc=started_utc,
        plan_path=resolved_plan_path,
        command=command,
        child_pid=child_pid,
        child_returncode=0 if _finished_metadata_is_normal(supervisor_recovery) else child_returncode,
        lock_path=plan.bench_lock.lock_path,
        summary_path=plan.summary_path,
        log_dir=plan.log_dir,
        stdout_path=stdout_file,
        stderr_path=stderr_file,
        safe_off=safe_off,
        supervisor_recovery=supervisor_recovery,
    )
    _atomic_write_json(status_file, final_payload)
    return final_payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Supervise a Mini DMA bench plan and leave CH4 safe on exit.")
    parser.add_argument("plan_path", help="Mini DMA bench plan JSON path.")
    parser.add_argument("--python", dest="python_executable", default=None, help="Python executable for launcher.py.")
    parser.add_argument("--launcher", default="launcher.py", help="Launcher entrypoint path.")
    parser.add_argument("--status-path", default=None, help="Status JSON path.")
    parser.add_argument("--stdout-path", default=None, help="Child stdout log path.")
    parser.add_argument("--stderr-path", default=None, help="Child stderr log path.")
    parser.add_argument("--poll-seconds", type=float, default=1.0, help="Status refresh interval.")
    parser.add_argument("--safe-off-channel", type=int, default=DEFAULT_SAFE_CHANNEL, help="HMP channel to turn off on exit.")
    parser.add_argument("--safe-off-port", default="COM3", help="HMP serial port for safe-off cleanup.")
    parser.add_argument("--safe-off-baud", type=int, default=115200, help="HMP baud rate for safe-off cleanup.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        summary = run_supervised_mini_dma_bench(
            args.plan_path,
            python_executable=args.python_executable,
            launcher_path=args.launcher,
            status_path=args.status_path,
            stdout_path=args.stdout_path,
            stderr_path=args.stderr_path,
            poll_interval_s=args.poll_seconds,
            safe_off_channel=args.safe_off_channel,
            safe_off_port=args.safe_off_port,
            safe_off_baud=args.safe_off_baud,
        )
    except MiniDmaBenchAutomationError as exc:
        print(f"[mini-dma-supervisor] {exc}")
        return 2
    except Exception as exc:
        print(f"[mini-dma-supervisor] {type(exc).__name__}: {exc}")
        return 1
    print(json.dumps(summary, ensure_ascii=True))
    return int(summary.get("child_returncode") or 0)


if __name__ == "__main__":
    raise SystemExit(main())
