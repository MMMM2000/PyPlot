from __future__ import annotations

import json
import socket
import socketserver
from threading import Thread
from typing import Any

from .broker import BenchProfile, SharedPowerSupplyBroker


class BrokerRequestHandler(socketserver.StreamRequestHandler):
    broker: SharedPowerSupplyBroker

    def handle(self) -> None:
        for raw_line in self.rfile:
            try:
                request = json.loads(raw_line.decode("utf-8"))
                response = self.server.handle_request(request)  # type: ignore[attr-defined]
            except Exception as exc:
                response = {"ok": False, "error": str(exc)}
            self.wfile.write((json.dumps(response) + "\n").encode("utf-8"))
            self.wfile.flush()


class BrokerTcpServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], broker: SharedPowerSupplyBroker) -> None:
        super().__init__(address, BrokerRequestHandler)
        self.broker = broker

    def server_close(self) -> None:
        try:
            self.broker.stop_scheduler()
        finally:
            super().server_close()

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        action = str(request.get("action") or "")
        broker = self.broker
        if action == "detect":
            return {"ok": True, "snapshot": broker.snapshot()}
        if action == "load_profile":
            profile = BenchProfile.from_dict(dict(request.get("profile") or {}))
            return {"ok": True, "profile": broker.load_profile(profile).to_dict()}
        if action == "save_profile":
            broker.confirm_profile(name=str(request.get("name") or broker.bench_profile.name))
            return {"ok": True, "profile": broker.bench_profile.to_dict()}
        if action == "assign_role":
            config = broker.assign_role(
                channel=int(request["channel"]),
                role=str(request["role"]),
                confirmed=bool(request.get("confirmed", False)),
                voltage_limit_v=request.get("voltage_limit_v"),
                current_limit_a=request.get("current_limit_a"),
            )
            return {"ok": True, "channel": config.to_dict()}
        if action == "lease":
            lease = broker.lease(
                channel=int(request["channel"]),
                owner=str(request["owner"]),
                role=str(request["role"]),
            )
            return {"ok": True, "lease": lease.to_dict()}
        if action == "release":
            broker.release(channel=int(request["channel"]), lease_id=str(request["lease_id"]))
            return {"ok": True}
        if action == "configure_channel":
            broker.configure_channel(
                channel=int(request["channel"]),
                lease_id=str(request["lease_id"]),
                voltage_v=float(request["voltage_v"]),
                current_a=float(request["current_a"]),
                output_on=bool(request["output_on"]),
            )
            return {"ok": True}
        if action == "set_current":
            broker.set_current(
                channel=int(request["channel"]),
                lease_id=str(request["lease_id"]),
                current_mA=float(request["current_mA"]),
            )
            return {"ok": True}
        if action == "set_output":
            broker.set_output(
                channel=int(request["channel"]),
                lease_id=str(request["lease_id"]),
                output_on=bool(request["output_on"]),
            )
            return {"ok": True}
        if action == "output_state":
            return {"ok": True, "output_on": broker.output_state(channel=int(request["channel"]))}
        if action == "measure_channel":
            return {"ok": True, "readback": broker.measure_channel(channel=int(request["channel"]))}
        if action == "configure_polling":
            broker.configure_polling(
                channel=int(request["channel"]),
                interval_s=float(request["interval_s"]),
            )
            return {"ok": True}
        if action == "disable_polling":
            broker.disable_polling(channel=int(request["channel"]))
            return {"ok": True}
        if action == "schedule_current":
            broker.schedule_current(
                channel=int(request["channel"]),
                lease_id=str(request["lease_id"]),
                current_mA=float(request["current_mA"]),
            )
            return {"ok": True}
        if action == "latest_readback":
            max_age = request.get("max_age_s")
            return {
                "ok": True,
                "readback": broker.latest_readback(
                    channel=int(request["channel"]),
                    max_age_s=None if max_age is None else float(max_age),
                    fallback_to_measure=bool(request.get("fallback_to_measure", True)),
                ),
            }
        if action == "start_scheduler":
            broker.start_scheduler(tick_s=float(request.get("tick_s", 0.05)))
            return {"ok": True}
        if action == "stop_scheduler":
            broker.stop_scheduler()
            return {"ok": True}
        if action == "snapshot":
            return {"ok": True, "snapshot": broker.snapshot()}
        raise ValueError(f"Unsupported broker action: {action}")


def start_broker_server(
    broker: SharedPowerSupplyBroker,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
) -> tuple[BrokerTcpServer, Thread]:
    server = BrokerTcpServer((host, port), broker)
    thread = Thread(target=server.serve_forever, name="shared-hmp-broker", daemon=True)
    thread.start()
    return server, thread


class BrokerJsonClient:
    """Small JSON-line client for the local shared HMP broker."""

    def __init__(self, *, host: str = "127.0.0.1", port: int) -> None:
        self.host = str(host or "127.0.0.1")
        self.port = int(port)

    def request(self, action: str, **payload: Any) -> dict[str, Any]:
        request = {"action": action, **payload}
        with socket.create_connection((self.host, self.port), timeout=2.0) as client:
            client.sendall((json.dumps(request) + "\n").encode("utf-8"))
            chunks: list[bytes] = []
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
        response = json.loads(b"".join(chunks).decode("utf-8"))
        if not response.get("ok"):
            raise RuntimeError(str(response.get("error") or "Shared HMP broker request failed."))
        return response

    def lease(self, *, channel: int, owner: str, role: str) -> dict[str, Any]:
        return dict(self.request("lease", channel=channel, owner=owner, role=role)["lease"])

    def release(self, *, channel: int, lease_id: str) -> None:
        self.request("release", channel=channel, lease_id=lease_id)

    def configure_channel(
        self,
        *,
        channel: int,
        lease_id: str,
        voltage_v: float,
        current_a: float,
        output_on: bool,
    ) -> None:
        self.request(
            "configure_channel",
            channel=channel,
            lease_id=lease_id,
            voltage_v=voltage_v,
            current_a=current_a,
            output_on=output_on,
        )

    def set_current(self, *, channel: int, lease_id: str, current_mA: float) -> None:
        self.request("set_current", channel=channel, lease_id=lease_id, current_mA=current_mA)

    def set_output(self, *, channel: int, lease_id: str, output_on: bool) -> None:
        self.request("set_output", channel=channel, lease_id=lease_id, output_on=output_on)

    def output_state(self, *, channel: int) -> bool | None:
        value = self.request("output_state", channel=channel).get("output_on")
        return None if value is None else bool(value)

    def measure_channel(self, *, channel: int) -> dict[str, float | None]:
        return dict(self.request("measure_channel", channel=channel)["readback"])

    def configure_polling(self, *, channel: int, interval_s: float) -> None:
        self.request("configure_polling", channel=channel, interval_s=interval_s)

    def disable_polling(self, *, channel: int) -> None:
        self.request("disable_polling", channel=channel)

    def schedule_current(self, *, channel: int, lease_id: str, current_mA: float) -> None:
        self.request("schedule_current", channel=channel, lease_id=lease_id, current_mA=current_mA)

    def latest_readback(
        self,
        *,
        channel: int,
        max_age_s: float | None = None,
        fallback_to_measure: bool = True,
    ) -> dict[str, Any]:
        return dict(
            self.request(
                "latest_readback",
                channel=channel,
                max_age_s=max_age_s,
                fallback_to_measure=fallback_to_measure,
            )["readback"]
        )

    def start_scheduler(self, *, tick_s: float = 0.05) -> None:
        self.request("start_scheduler", tick_s=tick_s)

    def stop_scheduler(self) -> None:
        self.request("stop_scheduler")

    def snapshot(self) -> dict[str, Any]:
        return dict(self.request("snapshot")["snapshot"])
