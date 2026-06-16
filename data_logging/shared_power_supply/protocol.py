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

    def __init__(self, *, host: str = "127.0.0.1", port: int, timeout_s: float = 8.0) -> None:
        self.host = str(host or "127.0.0.1")
        self.port = int(port)
        self.timeout_s = float(timeout_s)

    def request(self, action: str, **payload: Any) -> dict[str, Any]:
        request = {"action": action, **payload}
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout_s) as client:
                client.sendall((json.dumps(request) + "\n").encode("utf-8"))
                chunks: list[bytes] = []
                while True:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if b"\n" in chunk:
                        break
        except OSError as exc:
            raise BrokerConnectionError(host=self.host, port=self.port, action=action, cause=exc) from exc
        raw_response = b"".join(chunks)
        if not raw_response.strip():
            raise BrokerConnectionError(
                host=self.host,
                port=self.port,
                action=action,
                cause=RuntimeError("broker closed the connection without a response"),
            )
        try:
            response = json.loads(raw_response.decode("utf-8"))
        except Exception as exc:
            raise BrokerConnectionError(host=self.host, port=self.port, action=action, cause=exc) from exc
        if not response.get("ok"):
            raise BrokerRequestError(
                action=action,
                message=str(response.get("error") or "Shared HMP broker request failed."),
            )
        return response

    def snapshot(self) -> dict[str, Any]:
        return dict(self.request("snapshot")["snapshot"])

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


class BrokerConnectionError(RuntimeError):
    """Raised when a broker endpoint cannot be reached or returns malformed data."""

    def __init__(self, *, host: str, port: int, action: str, cause: Exception) -> None:
        self.host = str(host)
        self.port = int(port)
        self.action = str(action)
        self.cause = cause
        detail = str(cause).strip() or cause.__class__.__name__
        super().__init__(
            f"Shared HMP broker at {self.host}:{self.port} did not answer during {self.action}: {detail}"
        )


class BrokerRequestError(RuntimeError):
    """Raised when the broker is reachable but refuses the requested action."""

    def __init__(self, *, action: str, message: str) -> None:
        self.action = str(action)
        self.message = str(message)
        super().__init__(f"Shared HMP broker refused {self.action}: {self.message}")


def broker_failure_diagnostic(exc: Exception, *, context: str = "Shared HMP broker") -> str:
    """Return an operator-facing explanation for common shared-broker failures."""

    text = str(exc).strip() or exc.__class__.__name__
    lower = text.lower()
    if isinstance(exc, BrokerConnectionError):
        return (
            f"{context}: broker missing or not reachable at {exc.host}:{exc.port} "
            f"while running {exc.action}. Start the shared HMP broker, check host/port, "
            "or close stale broker processes before retrying."
        )
    if "access is denied" in lower or "permissionerror" in lower or "could not open port" in lower:
        return (
            f"{context}: direct HMP serial access was denied. Another app or broker likely owns the "
            "COM port; keep this logger in shared-broker mode and connect to the broker endpoint instead."
        )
    if "already leased" in lower:
        return f"{context}: channel lease refused because the selected channel is already leased ({text})."
    if "assigned to" in lower and "not" in lower:
        return f"{context}: wrong channel or wrong role for the selected broker profile ({text})."
    if "confirmed role" in lower or "wiring is not confirmed" in lower:
        return f"{context}: broker profile is missing confirmed wiring for the selected channel ({text})."
    if "valid lease" in lower or "lease mismatch" in lower:
        return f"{context}: stale channel lease detected after a broker restart or release ({text})."
    if "exceeds ch" in lower and "limit" in lower:
        return f"{context}: channel limit is stale or too low for the requested setpoint ({text})."
    if "not valid for" in lower and "valid channels" in lower:
        return f"{context}: wrong HMP model/profile for the requested channel ({text})."
    if isinstance(exc, BrokerRequestError):
        return f"{context}: broker request was refused ({exc.message})."
    return f"{context}: {text}"
