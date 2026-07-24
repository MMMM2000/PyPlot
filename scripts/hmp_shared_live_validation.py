from __future__ import annotations

import argparse
import csv
import json
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from data_logging.shared_power_supply.bench_guard import acquire_bench_lock, default_lock_path
from data_logging.shared_power_supply.broker import (
    ROLE_CURRENT_ANNEALING,
    ROLE_MINI_DMA_CURRENT,
    ROLE_MINI_DMA_MOTOR,
    SharedPowerSupplyBroker,
)
from data_logging.shared_power_supply.driver import HmpSerialDriver
from data_logging.shared_power_supply.profiles import HMP4040_PROFILE, detect_hmp_profile
from data_logging.shared_power_supply.protocol import BrokerJsonClient, start_broker_server


def _utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_ready(payload), indent=2, sort_keys=True), encoding="utf-8")


def _readbacks(client: BrokerJsonClient, channels: tuple[int, ...]) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    for channel in channels:
        states[str(channel)] = {
            "output_on": client.output_state(channel=channel),
            "readback": client.measure_channel(channel=channel),
        }
    return states


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "elapsed_s",
        "stage",
        "setpoint_mA",
        "measured_current_mA",
        "measured_voltage_V",
        "output_on",
        "note",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _classify_current_path(
    rows: list[dict[str, Any]],
    *,
    voltage_limit_v: float,
    target_current_mA: float,
) -> dict[str, Any]:
    measured_currents = [float(row["measured_current_mA"] or 0.0) for row in rows]
    measured_voltages = [float(row["measured_voltage_V"] or 0.0) for row in rows]
    max_current_mA = max(measured_currents, default=0.0)
    max_voltage_v = max(measured_voltages, default=0.0)
    voltage_limited = bool(rows) and max_voltage_v >= max(0.0, float(voltage_limit_v)) - 0.25
    near_open_current_mA = max(0.5, min(2.0, float(target_current_mA) * 0.025))
    open_circuit = voltage_limited and max_current_mA <= near_open_current_mA
    current_reached = max_current_mA >= max(0.0, float(target_current_mA)) * 0.95
    if current_reached:
        status = "current_reached"
    elif open_circuit:
        status = "open_circuit_or_broken_wire"
    elif voltage_limited:
        status = "voltage_limited"
    else:
        status = "current_below_target"
    return {
        "status": status,
        "max_measured_current_mA": max_current_mA,
        "max_measured_voltage_V": max_voltage_v,
        "target_current_mA": float(target_current_mA),
        "voltage_limit_v": float(voltage_limit_v),
        "voltage_limited": voltage_limited,
        "open_circuit": open_circuit,
        "current_reached": current_reached,
    }


def _current_profile(start_mA: float, stop_mA: float, *, step_mA: float) -> list[float]:
    values: list[float] = []
    value = float(start_mA)
    while value < float(stop_mA):
        values.append(round(value, 3))
        value += float(step_mA)
    values.append(round(float(stop_mA), 3))
    return values


def _run_current_annealing_client(
    *,
    host: str,
    port: int,
    channel: int,
    voltage_limit_v: float,
    current_limit_mA: float,
    max_current_mA: float,
    step_mA: float,
    dwell_s: float,
    stop_event: threading.Event,
    rows: list[dict[str, Any]],
    result: dict[str, Any],
) -> None:
    client = BrokerJsonClient(host=host, port=port)
    lease_id: str | None = None
    started_s = time.monotonic()
    try:
        lease = client.lease(channel=channel, owner="current_annealing_logger_live_validation", role=ROLE_CURRENT_ANNEALING)
        lease_id = str(lease["lease_id"])
        result["lease"] = lease
        client.configure_channel(
            channel=channel,
            lease_id=lease_id,
            voltage_v=voltage_limit_v,
            current_a=current_limit_mA / 1000.0,
            output_on=True,
        )
        for setpoint_mA in _current_profile(1.0, max_current_mA, step_mA=step_mA):
            client.set_current(channel=channel, lease_id=lease_id, current_mA=setpoint_mA)
            time.sleep(dwell_s)
            readback = client.measure_channel(channel=channel)
            rows.append(
                {
                    "elapsed_s": round(time.monotonic() - started_s, 3),
                    "stage": "current_annealing_ramp",
                    "setpoint_mA": setpoint_mA,
                    "measured_current_mA": readback.get("current_mA"),
                    "measured_voltage_V": readback.get("voltage_V"),
                    "output_on": client.output_state(channel=channel),
                    "note": "",
                }
            )
            if stop_event.is_set():
                break
        result["max_measured_current_mA"] = max(
            (float(row["measured_current_mA"] or 0.0) for row in rows),
            default=0.0,
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        try:
            if lease_id:
                client.set_output(channel=channel, lease_id=lease_id, output_on=False)
                client.release(channel=channel, lease_id=lease_id)
        finally:
            result["stopped_at_s"] = round(time.monotonic() - started_s, 3)


def _run_mini_dma_client(
    *,
    host: str,
    port: int,
    current_channel: int,
    motor_channel: int,
    voltage_limit_v: float,
    current_limit_mA: float,
    max_current_mA: float,
    ramp_rate_mA_s: float,
    rows: list[dict[str, Any]],
    result: dict[str, Any],
) -> None:
    client = BrokerJsonClient(host=host, port=port)
    current_lease_id: str | None = None
    motor_lease_id: str | None = None
    started_s = time.monotonic()
    dwell_s = 1.0
    step_mA = max(0.2, float(ramp_rate_mA_s) * dwell_s)
    try:
        current_lease = client.lease(
            channel=current_channel,
            owner="mini_dma_iso_stress_live_validation",
            role=ROLE_MINI_DMA_CURRENT,
        )
        motor_lease = client.lease(
            channel=motor_channel,
            owner="mini_dma_iso_stress_live_validation",
            role=ROLE_MINI_DMA_MOTOR,
        )
        current_lease_id = str(current_lease["lease_id"])
        motor_lease_id = str(motor_lease["lease_id"])
        result["current_lease"] = current_lease
        result["motor_lease"] = motor_lease
        motor_before = {
            "output_on": client.output_state(channel=motor_channel),
            "readback": client.measure_channel(channel=motor_channel),
        }
        result["motor_before"] = motor_before
        if not bool(motor_before.get("output_on")):
            client.configure_channel(
                channel=motor_channel,
                lease_id=motor_lease_id,
                voltage_v=12.0,
                current_a=0.4,
                output_on=True,
            )
        client.configure_channel(
            channel=current_channel,
            lease_id=current_lease_id,
            voltage_v=voltage_limit_v,
            current_a=current_limit_mA / 1000.0,
            output_on=True,
        )
        for stage, setpoints in (
            ("mini_dma_iso_stress_current_ramp_up", _current_profile(1.0, max_current_mA, step_mA=step_mA)),
            ("mini_dma_iso_stress_current_hold", [max_current_mA] * 5),
            ("mini_dma_iso_stress_current_ramp_down", list(reversed(_current_profile(1.0, max_current_mA, step_mA=step_mA)))),
        ):
            for setpoint_mA in setpoints:
                client.set_current(channel=current_channel, lease_id=current_lease_id, current_mA=setpoint_mA)
                time.sleep(dwell_s)
                readback = client.measure_channel(channel=current_channel)
                rows.append(
                    {
                        "elapsed_s": round(time.monotonic() - started_s, 3),
                        "stage": stage,
                        "setpoint_mA": setpoint_mA,
                        "measured_current_mA": readback.get("current_mA"),
                        "measured_voltage_V": readback.get("voltage_V"),
                        "output_on": client.output_state(channel=current_channel),
                        "note": "target_stress_mpa=20.0",
                    }
                )
        result["max_measured_current_mA"] = max(
            (float(row["measured_current_mA"] or 0.0) for row in rows),
            default=0.0,
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        try:
            if current_lease_id:
                client.set_output(channel=current_channel, lease_id=current_lease_id, output_on=False)
                client.release(channel=current_channel, lease_id=current_lease_id)
            if motor_lease_id:
                motor_after = {
                    "output_on": client.output_state(channel=motor_channel),
                    "readback": client.measure_channel(channel=motor_channel),
                }
                result["motor_after"] = motor_after
                client.release(channel=motor_channel, lease_id=motor_lease_id)
        finally:
            result["stopped_at_s"] = round(time.monotonic() - started_s, 3)


def run_validation(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir).expanduser().resolve() / f"shared-hmp-live-{_utc_stamp()}"
    output_dir.mkdir(parents=True, exist_ok=True)
    host = "127.0.0.1"
    ca_rows: list[dict[str, Any]] = []
    mini_rows: list[dict[str, Any]] = []
    ca_result: dict[str, Any] = {}
    mini_result: dict[str, Any] = {}
    edge_results: dict[str, Any] = {}
    stop_ca = threading.Event()
    errors: list[str] = []

    with ExitStack() as stack:
        lock = acquire_bench_lock(
            owner=args.owner,
            purpose="shared HMP live validation",
            lock_path=Path(args.lock_path),
        )
        stack.enter_context(lock)

        driver = HmpSerialDriver(port_name=args.port, baudrate=args.baud, timeout_s=0.8)
        driver.connect()
        stack.callback(driver.close)
        idn = driver.identify()
        profile = detect_hmp_profile(idn) or HMP4040_PROFILE
        if profile.channel_count < 4:
            raise RuntimeError(f"Live validation requires CH4 but detected {profile.label}.")
        broker = SharedPowerSupplyBroker(driver, profile)
        for channel, role, voltage_limit_v, current_limit_a in (
            (args.current_annealing_channel, ROLE_CURRENT_ANNEALING, args.ca_voltage_limit_v, args.max_current_mA / 1000.0),
            (args.motor_channel, ROLE_MINI_DMA_MOTOR, 12.0, 0.5),
            (args.mini_dma_current_channel, ROLE_MINI_DMA_CURRENT, args.mini_voltage_limit_v, args.max_current_mA / 1000.0),
        ):
            broker.assign_role(
                channel=channel,
                role=role,
                confirmed=True,
                voltage_limit_v=voltage_limit_v,
                current_limit_a=current_limit_a,
            )
        broker.confirm_profile(name="Codex shared HMP live validation")
        server, thread = start_broker_server(broker, host=host, port=0)
        stack.callback(server.server_close)
        stack.callback(server.shutdown)
        port = int(server.server_address[1])
        client = BrokerJsonClient(host=host, port=port)
        initial_state = _readbacks(
            client,
            (args.current_annealing_channel, args.motor_channel, args.mini_dma_current_channel),
        )

        try:
            client.lease(
                channel=args.current_annealing_channel,
                owner="wrong-role-validation",
                role=ROLE_MINI_DMA_CURRENT,
            )
            edge_results["wrong_role_rejected"] = False
        except Exception as exc:
            edge_results["wrong_role_rejected"] = True
            edge_results["wrong_role_error"] = str(exc)

        ca_thread = threading.Thread(
            target=_run_current_annealing_client,
            kwargs={
                "host": host,
                "port": port,
                "channel": args.current_annealing_channel,
                "voltage_limit_v": args.ca_voltage_limit_v,
                "current_limit_mA": args.max_current_mA,
                "max_current_mA": args.max_current_mA,
                "step_mA": args.ca_step_mA,
                "dwell_s": args.ca_dwell_s,
                "stop_event": stop_ca,
                "rows": ca_rows,
                "result": ca_result,
            },
            name="current-annealing-live-validation",
        )
        mini_thread = threading.Thread(
            target=_run_mini_dma_client,
            kwargs={
                "host": host,
                "port": port,
                "current_channel": args.mini_dma_current_channel,
                "motor_channel": args.motor_channel,
                "voltage_limit_v": args.mini_voltage_limit_v,
                "current_limit_mA": args.max_current_mA,
                "max_current_mA": args.max_current_mA,
                "ramp_rate_mA_s": args.mini_ramp_rate_mA_s,
                "rows": mini_rows,
                "result": mini_result,
            },
            name="mini-dma-live-validation",
        )
        started_s = time.monotonic()
        ca_thread.start()
        time.sleep(args.client_start_stagger_s)
        mini_thread.start()

        while ca_thread.is_alive() or mini_thread.is_alive():
            if ca_thread.is_alive() and time.monotonic() - started_s >= args.ca_stop_after_s:
                stop_ca.set()
            ca_thread.join(timeout=0.2)
            mini_thread.join(timeout=0.2)
            if ca_result.get("error"):
                errors.append(str(ca_result["error"]))
            if mini_result.get("error"):
                errors.append(str(mini_result["error"]))
            if errors:
                break
        ca_thread.join(timeout=5.0)
        mini_thread.join(timeout=5.0)

        if ca_thread.is_alive() or mini_thread.is_alive():
            raise RuntimeError("Validation clients did not stop before timeout.")

        try:
            conflict = client.lease(
                channel=args.mini_dma_current_channel,
                owner="post-run-conflict-check-a",
                role=ROLE_MINI_DMA_CURRENT,
            )
            try:
                client.lease(
                    channel=args.mini_dma_current_channel,
                    owner="post-run-conflict-check-b",
                    role=ROLE_MINI_DMA_CURRENT,
                )
                edge_results["same_channel_conflict_rejected"] = False
            except Exception as exc:
                edge_results["same_channel_conflict_rejected"] = True
                edge_results["same_channel_conflict_error"] = str(exc)
            finally:
                client.release(channel=args.mini_dma_current_channel, lease_id=str(conflict["lease_id"]))
        except Exception as exc:
            edge_results["same_channel_conflict_setup_error"] = str(exc)

        try:
            over = client.lease(channel=args.mini_dma_current_channel, owner="overlimit-check", role=ROLE_MINI_DMA_CURRENT)
            try:
                client.set_current(
                    channel=args.mini_dma_current_channel,
                    lease_id=str(over["lease_id"]),
                    current_mA=args.max_current_mA + 20.0,
                )
                edge_results["over_current_rejected"] = False
            except Exception as exc:
                edge_results["over_current_rejected"] = True
                edge_results["over_current_error"] = str(exc)
            finally:
                client.set_output(channel=args.mini_dma_current_channel, lease_id=str(over["lease_id"]), output_on=False)
                client.release(channel=args.mini_dma_current_channel, lease_id=str(over["lease_id"]))
        except Exception as exc:
            edge_results["over_current_setup_error"] = str(exc)

        final_state = _readbacks(
            client,
            (args.current_annealing_channel, args.motor_channel, args.mini_dma_current_channel),
        )
        snapshot = client.request("snapshot")["snapshot"]

    ca_path = output_dir / "current_annealing_rows.csv"
    mini_path = output_dir / "mini_dma_iso_stress_rows.csv"
    _write_rows(ca_path, ca_rows)
    _write_rows(mini_path, mini_rows)
    metadata = {
        "kind": "shared_hmp_live_validation",
        "validation_scope": (
            "electrical_shared_broker_validation; this harness does not run a full TMA saved recipe "
            "or prove mechanical iso-stress/iso-strain control"
        ),
        "idn": idn,
        "broker_port": port,
        "broker_thread_alive_at_snapshot": thread.is_alive(),
        "channels": {
            "current_annealing": args.current_annealing_channel,
            "mini_dma_motor_supply": args.motor_channel,
            "mini_dma_current_sweep": args.mini_dma_current_channel,
        },
        "limits": {
            "max_current_mA": args.max_current_mA,
            "ca_voltage_limit_v": args.ca_voltage_limit_v,
            "mini_voltage_limit_v": args.mini_voltage_limit_v,
            "mini_ramp_rate_mA_s": args.mini_ramp_rate_mA_s,
        },
        "initial_state": initial_state,
        "current_annealing": {
            "rows": len(ca_rows),
            **ca_result,
            "csv_path": ca_path,
            "current_path": _classify_current_path(
                ca_rows,
                voltage_limit_v=args.ca_voltage_limit_v,
                target_current_mA=args.max_current_mA,
            ),
        },
        "mini_dma_current_sweep": {
            "rows": len(mini_rows),
            **mini_result,
            "csv_path": mini_path,
            "current_path": _classify_current_path(
                mini_rows,
                voltage_limit_v=args.mini_voltage_limit_v,
                target_current_mA=args.max_current_mA,
            ),
        },
        "edge_results": edge_results,
        "final_state": final_state,
        "broker_snapshot": snapshot,
        "errors": errors,
    }
    _write_json(output_dir / "metadata.json", metadata)
    return metadata


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run electrical live shared-HMP broker validation for Current Annealing plus a "
            "TMA current-sweep channel client. This does not execute a full TMA saved recipe."
        )
    )
    parser.add_argument("--port", default="COM3")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--output-dir", default="artifacts/hmp-live-validation")
    parser.add_argument("--lock-path", default=str(default_lock_path()))
    parser.add_argument("--owner", default="codex-shared-hmp-live-validation")
    parser.add_argument("--current-annealing-channel", type=int, default=1)
    parser.add_argument("--motor-channel", type=int, default=3)
    parser.add_argument("--mini-dma-current-channel", type=int, default=4)
    parser.add_argument("--max-current-mA", type=float, default=80.0)
    parser.add_argument("--ca-voltage-limit-v", type=float, default=32.0)
    parser.add_argument("--mini-voltage-limit-v", type=float, default=32.0)
    parser.add_argument("--ca-step-mA", type=float, default=2.0)
    parser.add_argument("--ca-dwell-s", type=float, default=0.5)
    parser.add_argument("--ca-stop-after-s", type=float, default=45.0)
    parser.add_argument("--mini-ramp-rate-mA-s", type=float, default=0.8)
    parser.add_argument("--client-start-stagger-s", type=float, default=1.0)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    summary = run_validation(args)
    print(json.dumps(_json_ready(summary), indent=2, sort_keys=True))
    return 1 if summary.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
