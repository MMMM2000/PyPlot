from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from data_logging.shared_power_supply.bench_guard import (
    BenchLockBusy,
    acquire_bench_lock,
    default_lock_path,
    probe_hmp_bench,
    read_lock_info,
    wait_for_bench_lock,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Coordinate access to the shared HMP bench.")
    parser.add_argument("--lock-path", default=str(default_lock_path()))
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Report lock and optional HMP serial status.")
    status.add_argument("--lock-path", dest="command_lock_path", default=None)
    status.add_argument("--port", default="COM3")
    status.add_argument("--baud", type=int, default=115200)
    status.add_argument("--probe", action="store_true")

    acquire = subparsers.add_parser("acquire", help="Acquire the bench lock and hold it briefly.")
    acquire.add_argument("--lock-path", dest="command_lock_path", default=None)
    acquire.add_argument("--owner", default="codex")
    acquire.add_argument("--purpose", default="")
    acquire.add_argument("--timeout", type=float, default=0.0)
    acquire.add_argument("--hold-seconds", type=float, default=0.0)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    lock_path = Path(args.command_lock_path or args.lock_path or default_lock_path())
    if args.command == "status":
        payload: dict[str, object] = {
            "lock_path": str(lock_path),
            "lock": None if read_lock_info(lock_path) is None else read_lock_info(lock_path).to_dict(),
        }
        if args.probe:
            probe = probe_hmp_bench(port_name=args.port, baudrate=args.baud)
            payload["probe"] = {
                "available": probe.available,
                "message": probe.message,
                "idn": probe.idn,
                "channel_readbacks": probe.channel_readbacks or {},
            }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "acquire":
        try:
            if args.timeout > 0:
                lock = wait_for_bench_lock(
                    owner=args.owner,
                    purpose=args.purpose,
                    timeout_s=args.timeout,
                    lock_path=lock_path,
                )
            else:
                lock = acquire_bench_lock(owner=args.owner, purpose=args.purpose, lock_path=lock_path)
        except BenchLockBusy as exc:
            print(str(exc), file=sys.stderr)
            return 2
        with lock:
            print(json.dumps({"lock_path": str(lock.lock_path), "lock": lock.info.to_dict()}, indent=2))
            if args.hold_seconds > 0:
                time.sleep(args.hold_seconds)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
