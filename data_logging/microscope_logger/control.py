"""Local command control for a running PyPlot microscope window.

Only a user-local Qt named pipe is exposed: no TCP port, shell execution or remote
service. Requests and responses are single JSON objects. The discovery token is
read from a private local file and is never included in command output.

Examples::

    uv run python -m data_logging.microscope_logger.control status
    uv run python -m data_logging.microscope_logger.control configure --objective 4x
    uv run python -m data_logging.microscope_logger.control request --json command.json

Successful responses pass through the application's object unchanged. Transport
errors use {"ok": false, "error": {"code": ..., "message": ...}} and exit 1.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
from typing import Callable
import uuid

from PyQt6 import QtCore, QtNetwork


MAX_MESSAGE_BYTES = 1024 * 1024
PROTOCOL_VERSION = 1
_CLIENT_APP = None


class ControlError(RuntimeError):
    def __init__(self, message: str, code: str = "connection_failed"):
        super().__init__(message)
        self.code = code


def runtime_directory(runtime_dir: str | Path | None = None) -> Path:
    if runtime_dir is not None:
        return Path(runtime_dir).expanduser().resolve()
    override = os.environ.get("PYPLOT_MICROSCOPE_RUNTIME_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    # GenericDataLocation is independent of QApplication's executable/app name,
    # unlike AppLocalDataLocation, so the CLI and GUI discover the same server.
    base = Path(QtCore.QStandardPaths.writableLocation(
        QtCore.QStandardPaths.StandardLocation.GenericDataLocation))
    identity = f"{Path(__file__).resolve().parent}|{getpass.getuser()}"
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return base / "PyPlot" / "microscope-control" / key


def _endpoint(directory: Path) -> str:
    identity = f"{directory.resolve()}|{getpass.getuser()}"
    return "pyplot-microscope-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8") + b"\n"


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def _alive(endpoint: str) -> bool:
    probe = QtNetwork.QLocalSocket()
    probe.connectToServer(endpoint)
    connected = probe.waitForConnected(150)
    probe.abort()
    return connected


def _write_private(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_json_bytes(value))
            stream.flush()
        os.replace(temporary, path)
        # On Windows this leaves inherited user-profile ACLs intact. The named
        # pipe itself is separately restricted with UserAccessOption.
        path.chmod(0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


class ControlServer(QtCore.QObject):
    """Dispatch local commands on the owning Qt application thread.

    ``start`` returns False if another instance owns this runtime directory.
    The caller retains this object for the lifetime of its main window.
    """

    def __init__(self, handler: Callable[[dict], dict], *,
                 runtime_dir: str | Path | None = None, parent=None):
        super().__init__(parent)
        self.handler = handler
        self.runtime_dir = runtime_directory(runtime_dir)
        self.endpoint = _endpoint(self.runtime_dir)
        self.discovery_path = self.runtime_dir / "control.json"
        self.server = QtNetwork.QLocalServer(self)
        self.server.setSocketOptions(QtNetwork.QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._new_connections)
        self.lock = None
        self.token = ""
        self.connections: dict[QtNetwork.QLocalSocket, bytearray] = {}
        self.running = False

    def start(self) -> bool:
        if self.running:
            return True
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        # Windows packaged-app storage can resolve to its physical LocalCache
        # path only after the directory exists. Bind the same materialized path
        # that discovery clients resolve, rather than the pre-creation spelling.
        self.endpoint = _endpoint(self.runtime_dir)
        lock = QtCore.QLockFile(str(self.runtime_dir / "server.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            return False
        # Never replace a responding endpoint, even if its discovery/lock file
        # was unexpectedly altered. Remove only our derived, nonresponding name.
        if _alive(self.endpoint):
            lock.unlock()
            return False
        QtNetwork.QLocalServer.removeServer(self.endpoint)
        if not self.server.listen(self.endpoint):
            message = self.server.errorString()
            lock.unlock()
            raise ControlError(f"Could not start microscope control: {message}")
        self.lock = lock
        self.token = secrets.token_hex(32)
        try:
            _write_private(self.discovery_path, {
                "protocol_version": PROTOCOL_VERSION, "endpoint": self.endpoint,
                "token": self.token, "pid": os.getpid(),
            })
        except OSError:
            self.server.close()
            self.lock.unlock()
            self.lock = None
            self.token = ""
            raise
        self.running = True
        return True

    def close(self) -> None:
        if not self.running:
            return
        self.running = False
        self.server.close()
        for socket in tuple(self.connections):
            socket.abort()
            self._drop(socket)
        try:
            value = json.loads(self.discovery_path.read_text(encoding="utf-8"))
            if value.get("token") == self.token and value.get("endpoint") == self.endpoint:
                self.discovery_path.unlink()
        except (OSError, ValueError, AttributeError):
            pass
        if self.lock is not None:
            self.lock.unlock()
            self.lock = None
        self.token = ""

    def _new_connections(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.setReadBufferSize(MAX_MESSAGE_BYTES + 1)
            self.connections[socket] = bytearray()
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(lambda s=socket: self._drop(s))
            timer = QtCore.QTimer(socket)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda s=socket: self._reply(s, _error(
                "request_timeout", "A complete command was not received in time")))
            timer.start(5000)
            if socket.bytesAvailable():
                self._read(socket)

    def _drop(self, socket) -> None:
        if self.connections.pop(socket, None) is not None:
            socket.deleteLater()

    def _reply(self, socket, response: dict) -> None:
        if socket not in self.connections:
            return
        try:
            encoded = _json_bytes(response)
            if len(encoded) > MAX_MESSAGE_BYTES:
                encoded = _json_bytes(_error("response_too_large", "Command response exceeds the local protocol limit"))
        except (TypeError, ValueError):
            encoded = _json_bytes(_error("invalid_response", "Application returned a non-JSON response"))
        socket.write(encoded)
        socket.flush()
        socket.disconnectFromServer()

    def _read(self, socket) -> None:
        buffer = self.connections.get(socket)
        if buffer is None:
            return
        buffer.extend(bytes(socket.readAll()))
        if len(buffer) > MAX_MESSAGE_BYTES:
            self._reply(socket, _error("request_too_large", "Command exceeds the local protocol limit"))
            return
        if b"\n" not in buffer:
            return
        line, remainder = bytes(buffer).split(b"\n", 1)
        if remainder.strip():
            self._reply(socket, _error("invalid_request", "Send one command per connection"))
            return
        try:
            envelope = json.loads(line)
            if not isinstance(envelope, dict):
                raise ValueError("Command envelope must be an object")
            supplied = envelope.get("token")
            if not isinstance(supplied, str) or not secrets.compare_digest(supplied, self.token):
                self._reply(socket, _error("unauthorized", "Local control authentication failed"))
                return
            payload = envelope.get("command")
            if not isinstance(payload, dict) or not isinstance(payload.get("action"), str):
                raise ValueError("Command must contain an action string")
            response = self.handler(payload)
            if not isinstance(response, dict):
                raise ValueError("Application command handler must return an object")
        except (ValueError, UnicodeError) as exc:
            response = _error("invalid_request", str(exc).replace(self.token, "[redacted]"))
        except Exception as exc:
            response = _error("command_failed", str(exc).replace(self.token, "[redacted]"))
        self._reply(socket, response)


def _discovery(directory: Path) -> dict:
    try:
        path = directory / "control.json"
        if path.stat().st_size > 8192:
            raise ValueError("Discovery record is too large")
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or value.get("protocol_version") != PROTOCOL_VERSION
                or value.get("endpoint") != _endpoint(directory)
                or not isinstance(value.get("token"), str) or len(value["token"]) != 64
                or any(c not in "0123456789abcdef" for c in value["token"])):
            raise ValueError("Discovery record is invalid")
        return value
    except (OSError, ValueError, TypeError) as exc:
        raise ControlError("No valid microscope control endpoint was found. Open the microscope app first.",
                           "not_running") from exc


def send_command(payload: dict, *, runtime_dir: str | Path | None = None,
                 timeout_ms: int = 10000) -> dict:
    """Send one explicit application command and return its JSON object."""
    global _CLIENT_APP
    if not isinstance(payload, dict) or not isinstance(payload.get("action"), str):
        raise ControlError("Command must be an object with an action string", "invalid_request")
    if timeout_ms < 1 or timeout_ms > 120000:
        raise ControlError("timeout_ms must be between 1 and 120000", "invalid_request")
    if QtCore.QCoreApplication.instance() is None:
        _CLIENT_APP = QtCore.QCoreApplication([])
    value = _discovery(runtime_directory(runtime_dir))
    try:
        encoded = _json_bytes({"token": value["token"], "command": payload})
    except (TypeError, ValueError) as exc:
        raise ControlError("Command is not valid finite JSON", "invalid_request") from exc
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise ControlError("Command exceeds the local protocol limit", "request_too_large")
    # Use the socket's asynchronous notifications even though the public API is
    # synchronous. In particular, a Windows pipe write can finish between
    # bytesToWrite() and waitForBytesWritten(); a false return from the latter
    # does not necessarily mean that the command timed out.
    loop = QtCore.QEventLoop()
    timer = QtCore.QTimer()
    timer.setSingleShot(True)
    timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
    socket = QtNetwork.QLocalSocket()
    socket.setReadBufferSize(MAX_MESSAGE_BYTES + 1)
    response = bytearray()
    finished = False
    connected = False
    failure = None

    def fail(message: str, code: str = "connection_failed") -> None:
        nonlocal finished, failure
        if not finished:
            failure = ControlError(message, code)
            finished = True
            loop.quit()

    def read_response() -> None:
        nonlocal finished
        if finished:
            return
        if socket.bytesAvailable():
            response.extend(bytes(socket.readAll()))
        if len(response) > MAX_MESSAGE_BYTES:
            fail("Application response exceeds the protocol limit", "response_too_large")
        elif b"\n" in response:
            finished = True
            loop.quit()

    def write_command() -> None:
        nonlocal connected
        connected = True
        if socket.write(encoded) != len(encoded):
            fail("Could not send the complete microscope command")

    def connection_failed(*_args) -> None:
        # A peer can finish its reply and close before readyRead is delivered.
        # Drain the remaining bytes before deciding that the reply was lost.
        read_response()
        if not finished:
            fail("The microscope app disconnected before returning a complete response"
                 if connected else "The microscope app is not responding. Reopen it and retry.")

    def timed_out() -> None:
        fail("Timed out sending the microscope command" if socket.bytesToWrite()
             else "Timed out waiting for the microscope response", "timeout")

    socket.connected.connect(write_command)
    socket.readyRead.connect(read_response)
    socket.disconnected.connect(connection_failed)
    socket.errorOccurred.connect(connection_failed)
    timer.timeout.connect(timed_out)
    try:
        timer.start(timeout_ms)
        socket.connectToServer(value["endpoint"])
        if not finished:
            loop.exec()
        if failure is not None:
            raise failure
        result = json.loads(bytes(response).split(b"\n", 1)[0])
        if not isinstance(result, dict):
            raise ValueError("Response is not an object")
        return result
    except (ValueError, UnicodeError) as exc:
        raise ControlError("The microscope app returned an invalid response", "invalid_response") from exc
    finally:
        timer.stop()
        timer.timeout.disconnect(timed_out)
        socket.connected.disconnect(write_command)
        socket.readyRead.disconnect(read_response)
        socket.disconnected.disconnect(connection_failed)
        socket.errorOccurred.disconnect(connection_failed)
        socket.abort()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runtime-dir", type=Path, help="Private control directory (or PYPLOT_MICROSCOPE_RUNTIME_DIR)")
    parser.add_argument("--timeout-ms", type=int, default=10000)
    commands = parser.add_subparsers(dest="subcommand", required=True)
    for name in ("status", "start", "capture", "stop", "load-sources", "close"):
        command = commands.add_parser(name)
        if name == "start":
            command.add_argument("--discard", action="store_true", help="Explicitly allow replacing an unsaved image")
        if name == "load-sources":
            command.add_argument("--kind", choices=("project", "fabrication"), default="project")
            command.add_argument("--composition", help="Limit fabrication suggestions to one composition")
            command.add_argument("--cancel", action="store_true", default=None,
                                 help="Request cancellation of the current source load")
    configure = commands.add_parser("configure")
    for name in ("composition", "microwire", "objective", "output-directory", "project-path", "fabrication-path", "camera-name"):
        configure.add_argument("--" + name)
    configure.add_argument("--zoom", type=float)
    configure.add_argument("--width", type=int)
    configure.add_argument("--height", type=int)
    configure.add_argument("--auto-save", action=argparse.BooleanOptionalAction, default=None)
    configure.add_argument("--brittle", choices=("yes", "no", "unknown"), help="Sample annotation; use project update commands to publish it")
    prepare = commands.add_parser("prepare-brittle-update", help="Prepare and verify a disposable Builder candidate")
    prepare.add_argument("--workdir", type=Path)
    prepare.add_argument("--expected-sha256")
    publish = commands.add_parser("publish-brittle-update", help="Publish the exact reviewed candidate with backup and receipt")
    publish.add_argument("--review-sha256", required=True)
    calibrate = commands.add_parser("calibrate", help="Calibrate the frozen image from a known reference length")
    calibrate.add_argument("--start", nargs=2, type=float, required=True, metavar=("X", "Y"))
    calibrate.add_argument("--end", nargs=2, type=float, required=True, metavar=("X", "Y"))
    calibrate.add_argument("--known-um", type=float, required=True)
    calibrate.add_argument("--source", required=True, help="Reference identification and scale description")
    review = commands.add_parser("review", help="Explicitly review or withdraw review of selected calibrated diameters")
    review.add_argument("--measurements", nargs="+", type=int, required=True, help="One-based measurement numbers")
    review.add_argument("--unreview", action="store_true")
    review.add_argument("--note", default="")
    diameter = commands.add_parser("prepare-diameter-update", help="Prepare a verified project update from reviewed diameters")
    diameter.add_argument("--measurements", nargs="+", type=int, required=True)
    diameter.add_argument("--workdir", type=Path)
    diameter.add_argument("--expected-sha256")
    publish_diameter = commands.add_parser("publish-diameter-update", help="Publish the reviewed diameter candidate with backup and receipt")
    publish_diameter.add_argument("--review-sha256", required=True)
    measure = commands.add_parser("measure")
    measure.add_argument("--start", nargs=2, type=float, required=True, metavar=("X", "Y"))
    measure.add_argument("--end", nargs=2, type=float, required=True, metavar=("X", "Y"))
    measure.add_argument("--kind", choices=("apparent", "outer", "core"), default="apparent")
    for name in ("parallel", "auto"):
        command = commands.add_parser(name)
        for point in ("a", "b", "c"):
            command.add_argument("--" + point, nargs=2, type=float, required=True, metavar=("X", "Y"))
        command.add_argument("--kind", choices=("apparent", "outer", "core"), default="apparent")
        if name == "auto":
            command.add_argument("--samples", type=int, default=15)
    save = commands.add_parser("save")
    save.add_argument("--output-directory")
    load = commands.add_parser("load-image")
    load.add_argument("path", type=Path)
    load.add_argument("--discard", action="store_true")
    restore = commands.add_parser("restore-session", help="Restore a saved microscope session")
    restore.add_argument("path", type=Path, metavar="DIRECTORY")
    raw = commands.add_parser("command", help="Send a JSON command object")
    raw.add_argument("json_command")
    request = commands.add_parser("request", help="Send a JSON object from a file or stdin")
    source = request.add_mutually_exclusive_group(required=True)
    source.add_argument("--json", type=Path, dest="json_file")
    source.add_argument("--stdin", action="store_true")
    return parser


def command_from_args(args) -> dict:
    if args.subcommand == "command":
        return json.loads(args.json_command)
    if args.subcommand == "request":
        if args.json_file:
            if args.json_file.stat().st_size > MAX_MESSAGE_BYTES:
                raise ControlError("Command file exceeds the protocol limit", "request_too_large")
            return json.loads(args.json_file.read_text(encoding="utf-8-sig"))
        data = sys.stdin.buffer.read(MAX_MESSAGE_BYTES + 1)
        if len(data) > MAX_MESSAGE_BYTES:
            raise ControlError("Command input exceeds the protocol limit", "request_too_large")
        return json.loads(data.decode("utf-8-sig"))
    payload = {"action": args.subcommand.replace("-", "_")}
    for key, value in vars(args).items():
        if key not in {"subcommand", "runtime_dir", "timeout_ms"} and value is not None:
            payload[key] = {"yes": True, "no": False, "unknown": None}[value] if key == "brittle" else str(value) if isinstance(value, Path) else value
    return payload


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        payload = command_from_args(args)
        result = send_command(payload, runtime_dir=args.runtime_dir, timeout_ms=args.timeout_ms)
    except ControlError as exc:
        result = _error(exc.code, str(exc))
    except (OSError, ValueError, TypeError) as exc:
        result = _error("invalid_request", str(exc))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
