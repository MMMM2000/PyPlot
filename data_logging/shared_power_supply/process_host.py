"""Explicit subprocess entry point for the process-owned HMP broker.

Windows ``multiprocessing`` must reconstruct the launching GUI's ``__main__``
module before it can call a child target.  The TMA launcher can therefore fail
before broker diagnostics or hardware code run.  This guarded module is a
stable, importable child boundary independent of the visible Qt application.
"""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sys
import time
from typing import Any

from .process import (
    BrokerChannelConfig,
    BrokerProcessConfig,
    BrokerProcessReady,
    _run_broker_process,
)


class _FileReadySender:
    def __init__(self, path: Path) -> None:
        self._path = path

    def send(self, payload: object) -> None:
        if isinstance(payload, BrokerProcessReady):
            serializable: dict[str, object] = {"kind": "ready", **asdict(payload)}
        elif isinstance(payload, dict):
            serializable = dict(payload)
            serializable.setdefault("kind", "error")
        else:
            serializable = {"kind": "error", "error": str(payload)}
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(serializable, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(self._path)

    def close(self) -> None:
        return None


class _FileStopEvent:
    def __init__(self, path: Path) -> None:
        self._path = path

    def wait(self, timeout_s: float) -> bool:
        deadline_s = time.monotonic() + max(0.0, float(timeout_s))
        while not self._path.exists():
            remaining_s = deadline_s - time.monotonic()
            if remaining_s <= 0.0:
                return False
            time.sleep(min(0.01, remaining_s))
        return True


def _load_config(path: Path) -> BrokerProcessConfig:
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("broker configuration must be a JSON object")
    channels_raw = raw.pop("channels", [])
    channels = tuple(BrokerChannelConfig(**channel) for channel in channels_raw)
    return BrokerProcessConfig(channels=channels, **raw)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 3:
        return 2
    config_path, status_path, stop_path = map(Path, arguments)
    try:
        config = _load_config(config_path)
    except BaseException as exc:
        _FileReadySender(status_path).send(
            {"error": f"{exc.__class__.__name__}: {exc}"}
        )
        return 1
    _run_broker_process(
        config,
        _FileReadySender(status_path),
        _FileStopEvent(stop_path),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
