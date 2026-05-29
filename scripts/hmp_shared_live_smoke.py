from __future__ import annotations

import argparse
import json
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from data_logging.shared_power_supply.bench_guard import (
    probe_hmp_bench,
    wait_for_bench_lock,
)
from data_logging.shared_power_supply.broker import (
    ROLE_CURRENT_ANNEALING,
    ROLE_MINI_DMA_CURRENT,
    ROLE_MINI_DMA_MOTOR,
    SharedPowerSupplyBroker,
)
from data_logging.shared_power_supply.driver import HmpSerialDriver
from data_logging.shared_power_supply.profiles import HMP4040_PROFILE
from data_logging.shared_power_supply.protocol import BrokerJsonClient, start_broker_server


DEFAULT_OWNER = "codex-shared-hmp-live-smoke"
DEFAULT_OUTPUT_DIR = Path("artifacts") / "hmp-live-validation"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _broker_alive(*, host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def _assign_roles(client: BrokerJsonClient) -> None:
    assignments = [
        (1, ROLE_CURRENT_ANNEALING, 1.0, 0.01),
        (3, ROLE_MINI_DMA_MOTOR, 12.0, 0.4),
        (4, ROLE_MINI_DMA_CURRENT, 1.0, 0.01),
    ]
    for channel, role, voltage_limit_v, current_limit_a in assignments:
        client.request(
            "assign_role",
            channel=channel,
            role=role,
            confirmed=True,
            voltage_limit_v=voltage_limit_v,
            current_limit_a=current_limit_a,
        )
    client.request("save_profile", name="Codex guarded shared HMP smoke")


def _measure(client: BrokerJsonClient, channel: int) -> dict[str, object]:
    payload = client.measure_channel(channel=channel)
    keys = {"voltage_V", "current_mA", "timestamp_s", "cached", "age_s"}
    return {key: value for key, value in payload.items() if key in keys}


def _output_states(client: BrokerJsonClient) -> dict[str, bool | None]:
    return {str(channel): client.output_state(channel=channel) for channel in (1, 3, 4)}


def _all_outputs_off(states: dict[str, object]) -> bool:
    return all(states.get(str(channel)) is False for channel in (1, 3, 4))


def _wait_until_idle(
    *,
    port_name: str,
    baudrate: int,
    timeout_s: float,
    poll_s: float,
) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        probe = probe_hmp_bench(port_name=port_name, baudrate=baudrate)
        entry: dict[str, object] = {
            "checked_at_utc": _utc_text(),
            "available": probe.available,
            "electrically_idle": probe.electrically_idle,
            "message": probe.message,
            "idn": probe.idn,
            "busy_channels": list(probe.busy_channels),
            "unknown_output_channels": list(probe.unknown_output_channels),
            "channel_readbacks": probe.channel_readbacks or {},
        }
        checks.append(entry)
        print(json.dumps(entry, sort_keys=True), flush=True)
        if probe.electrically_idle or time.monotonic() >= deadline:
            return checks
        time.sleep(max(0.2, min(float(poll_s), deadline - time.monotonic())))


def run_smoke(
    *,
    host: str,
    preferred_port: int,
    port_name: str,
    baudrate: int,
    owner: str,
    wait_s: float,
    poll_s: float,
    output_dir: Path,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / f"{_utc_stamp()}-shared-hmp-smoke.json"
    result: dict[str, object] = {
        "started_at_utc": _utc_text(),
        "owner": owner,
        "artifact": str(artifact_path),
        "passed": False,
    }

    checks = _wait_until_idle(
        port_name=port_name,
        baudrate=baudrate,
        timeout_s=wait_s,
        poll_s=poll_s,
    )
    result["availability_checks"] = checks
    if not checks or not bool(checks[-1].get("electrically_idle")):
        result["skipped_reason"] = "hardware_not_electrically_idle_before_timeout"
        result["finished_at_utc"] = _utc_text()
        artifact_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        return result

    server = None
    client: BrokerJsonClient | None = None
    leases: list[tuple[int, str]] = []
    try:
        with wait_for_bench_lock(owner=owner, purpose="shared HMP live smoke", timeout_s=5.0):
            guarded = probe_hmp_bench(port_name=port_name, baudrate=baudrate)
            result["guarded_precheck"] = {
                "checked_at_utc": _utc_text(),
                "available": guarded.available,
                "electrically_idle": guarded.electrically_idle,
                "busy_channels": list(guarded.busy_channels),
                "unknown_output_channels": list(guarded.unknown_output_channels),
                "channel_readbacks": guarded.channel_readbacks or {},
            }
            if not guarded.electrically_idle:
                raise RuntimeError("Hardware became non-idle after acquiring the bench lock.")

            if _broker_alive(host=host, port=preferred_port):
                client = BrokerJsonClient(host=host, port=preferred_port)
                result["using_existing_broker"] = True
                result["broker_port"] = preferred_port
            else:
                driver = HmpSerialDriver(
                    port_name=port_name,
                    baudrate=baudrate,
                    profile=HMP4040_PROFILE,
                    timeout_s=0.8,
                )
                driver.connect()
                result["idn"] = driver.identify()
                broker = SharedPowerSupplyBroker(driver, HMP4040_PROFILE)
                server, _thread = start_broker_server(broker, host=host, port=0)
                result["using_existing_broker"] = False
                result["broker_port"] = int(server.server_address[1])
                client = BrokerJsonClient(host=host, port=int(server.server_address[1]))

            _assign_roles(client)
            initial_snapshot = client.snapshot()
            result["initial_snapshot"] = initial_snapshot
            if initial_snapshot.get("leases"):
                raise RuntimeError(f"Broker has active leases: {initial_snapshot.get('leases')}")

            initial_outputs = _output_states(client)
            result["initial_outputs"] = initial_outputs
            if not _all_outputs_off(initial_outputs):
                raise RuntimeError(f"One or more HMP outputs are already on: {initial_outputs}")

            wrong_role_rejected = False
            try:
                client.lease(channel=1, owner=owner, role=ROLE_MINI_DMA_CURRENT)
            except Exception as exc:
                wrong_role_rejected = True
                result["wrong_role_rejection"] = str(exc)
            if not wrong_role_rejected:
                raise RuntimeError("Wrong-role lease unexpectedly succeeded.")

            for channel, role in (
                (1, ROLE_CURRENT_ANNEALING),
                (3, ROLE_MINI_DMA_MOTOR),
                (4, ROLE_MINI_DMA_CURRENT),
            ):
                lease = client.lease(channel=channel, owner=owner, role=role)
                leases.append((channel, str(lease["lease_id"])))
            lease_map = {channel: lease_id for channel, lease_id in leases}

            ch1_rows = []
            client.configure_channel(channel=1, lease_id=lease_map[1], voltage_v=1.0, current_a=0.001, output_on=False)
            client.set_output(channel=1, lease_id=lease_map[1], output_on=True)
            for setpoint_mA in (1.0, 2.0, 1.0):
                client.set_current(channel=1, lease_id=lease_map[1], current_mA=setpoint_mA)
                time.sleep(0.45)
                ch1_rows.append({"setpoint_mA": setpoint_mA, **_measure(client, 1)})
            client.set_output(channel=1, lease_id=lease_map[1], output_on=False)
            result["ch1_low_current_rows"] = ch1_rows

            client.configure_channel(channel=4, lease_id=lease_map[4], voltage_v=1.0, current_a=0.001, output_on=False)
            client.set_output(channel=4, lease_id=lease_map[4], output_on=True)
            time.sleep(0.45)
            result["ch4_low_current_rows"] = [{"setpoint_mA": 1.0, **_measure(client, 4)}]
            client.set_output(channel=4, lease_id=lease_map[4], output_on=False)

            client.configure_channel(channel=3, lease_id=lease_map[3], voltage_v=12.0, current_a=0.4, output_on=False)
            client.set_output(channel=3, lease_id=lease_map[3], output_on=True)
            time.sleep(0.6)
            result["ch3_motor_rail_on_readback"] = _measure(client, 3)
            client.set_output(channel=3, lease_id=lease_map[3], output_on=False)
            time.sleep(0.2)
            result["ch3_motor_rail_off_readback"] = _measure(client, 3)

            final_outputs = _output_states(client)
            result["final_outputs_before_release"] = final_outputs
            result["passed"] = _all_outputs_off(final_outputs)
            result["final_snapshot"] = client.snapshot()
    finally:
        if client is not None:
            for channel, lease_id in reversed(leases):
                try:
                    client.set_output(channel=channel, lease_id=lease_id, output_on=False)
                except Exception:
                    pass
                try:
                    client.release(channel=channel, lease_id=lease_id)
                except Exception:
                    pass
        if server is not None:
            server.shutdown()
            server.server_close()
        result["finished_at_utc"] = _utc_text()
        artifact_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")

    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a guarded shared HMP live smoke test.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--broker-port", type=int, default=8765)
    parser.add_argument("--hmp-port", default="COM3")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--owner", default=DEFAULT_OWNER)
    parser.add_argument("--wait-seconds", type=float, default=0.0)
    parser.add_argument("--poll-seconds", type=float, default=20.0)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = run_smoke(
        host=args.host,
        preferred_port=args.broker_port,
        port_name=args.hmp_port,
        baudrate=args.baud,
        owner=args.owner,
        wait_s=args.wait_seconds,
        poll_s=args.poll_seconds,
        output_dir=Path(args.output_dir),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("passed") else 3


if __name__ == "__main__":
    raise SystemExit(main())
