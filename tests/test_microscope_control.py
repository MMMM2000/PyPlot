"""Local named-pipe command tests; no camera, research data or user settings."""
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import sys
import time

from PyQt6 import QtCore, QtNetwork
import pytest

from data_logging.microscope_logger import control


@pytest.fixture
def server(qtbot, tmp_path):
    owner_thread = QtCore.QThread.currentThread()
    calls = []

    def handler(payload):
        assert QtCore.QThread.currentThread() == owner_thread
        calls.append(payload)
        if payload["action"] == "fail":
            raise ValueError("Invented command failure")
        return {"ok": True, "received": payload}

    instance = control.ControlServer(handler, runtime_dir=tmp_path / "private-runtime")
    assert instance.start()
    yield instance, calls
    instance.close()


def _background(qtbot, function):
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(function)
        qtbot.waitUntil(future.done, timeout=15000)
        return future.result()


def _raw(endpoint, envelope):
    socket = QtNetwork.QLocalSocket()
    socket.connectToServer(endpoint)
    assert socket.waitForConnected(2000)
    socket.write(envelope)
    socket.waitForBytesWritten(2000)
    deadline = time.monotonic() + 3
    response = bytearray()
    while b"\n" not in response:
        if socket.bytesAvailable():
            response.extend(bytes(socket.readAll()))
        elif time.monotonic() < deadline:
            socket.waitForReadyRead(100)
        else:
            raise AssertionError("No raw response received")
    socket.abort()
    return json.loads(response)


def test_roundtrip_is_dispatched_on_gui_thread(server, qtbot):
    owner, calls = server
    payload = {"action": "configure", "composition": "Synthetic Alloy", "objective": "4×"}
    result = _background(qtbot, lambda: control.send_command(payload, runtime_dir=owner.runtime_dir))
    assert result == {"ok": True, "received": payload}
    assert calls == [payload]
    assert "token" not in json.dumps(result)


def test_large_request_and_response_cross_pipe_buffers(server, qtbot):
    owner, calls = server
    payload = {"action": "configure", "notes": "synthetic-" * 50000}
    result = _background(qtbot, lambda: control.send_command(payload, runtime_dir=owner.runtime_dir))
    assert result == {"ok": True, "received": payload}
    assert calls == [payload]


def test_delayed_fragmented_response_is_read_until_complete(server, qtbot, monkeypatch):
    owner, _ = server
    expected = {"ok": True, "notes": "synthetic reply " * 20000}
    encoded = control._json_bytes(expected)

    def reply_in_parts(socket, _response):
        chunks = iter(encoded[index:index + 8192] for index in range(0, len(encoded), 8192))
        timer = QtCore.QTimer(socket)
        timer.setInterval(5)

        def send_part():
            chunk = next(chunks, None)
            if chunk is None:
                timer.stop()
                socket.disconnectFromServer()
            else:
                socket.write(chunk)

        timer.timeout.connect(send_part)
        timer.start()

    monkeypatch.setattr(owner, "_reply", reply_in_parts)
    result = _background(qtbot, lambda: control.send_command(
        {"action": "status"}, runtime_dir=owner.runtime_dir, timeout_ms=3000))
    assert result == expected


def test_trickling_incomplete_response_obeys_one_deadline(server, qtbot, monkeypatch):
    owner, _ = server

    def reply_without_terminator(socket, _response):
        socket.write(b'{"ok":true}')
        timer = QtCore.QTimer(socket)
        timer.setInterval(10)
        timer.timeout.connect(lambda: socket.write(b" "))
        timer.start()

    monkeypatch.setattr(owner, "_reply", reply_without_terminator)
    started = time.monotonic()
    with pytest.raises(control.ControlError) as caught:
        _background(qtbot, lambda: control.send_command(
            {"action": "status"}, runtime_dir=owner.runtime_dir, timeout_ms=150))
    assert caught.value.code == "timeout"
    assert time.monotonic() - started < 2


def test_peer_closing_partial_response_reports_connection_failure(server, qtbot, monkeypatch):
    owner, _ = server

    def incomplete_reply(socket, _response):
        socket.write(b'{"ok":')
        socket.disconnectFromServer()

    monkeypatch.setattr(owner, "_reply", incomplete_reply)
    started = time.monotonic()
    with pytest.raises(control.ControlError) as caught:
        _background(qtbot, lambda: control.send_command(
            {"action": "status"}, runtime_dir=owner.runtime_dir, timeout_ms=5000))
    assert caught.value.code == "connection_failed"
    assert "complete response" in str(caught.value)
    assert time.monotonic() - started < 2


def test_window_control_start_is_idempotent(qtbot, tmp_path):
    from data_logging.microscope_logger.microscope_logger import MainWindow
    settings = QtCore.QSettings(str(tmp_path / "settings.ini"), QtCore.QSettings.Format.IniFormat)
    window = MainWindow(enumerate_cameras=False, settings=settings)
    qtbot.addWidget(window)
    assert window.start_control(tmp_path / "window-runtime")
    owner = window.control_server
    original = owner.discovery_path.read_bytes()
    assert window.start_control(tmp_path / "window-runtime")
    assert window.control_server is owner
    assert owner.discovery_path.read_bytes() == original
    window.close()
    assert not owner.discovery_path.exists()


def test_second_owner_cannot_replace_live_discovery(server):
    owner, _ = server
    original = owner.discovery_path.read_bytes()
    second = control.ControlServer(lambda _: {}, runtime_dir=owner.runtime_dir)
    assert not second.start()
    assert owner.discovery_path.read_bytes() == original
    second.close()
    assert owner.discovery_path.exists()


def test_close_releases_endpoint_for_restart(server, qtbot):
    owner, _ = server
    directory = owner.runtime_dir
    owner.close()
    assert not owner.discovery_path.exists()
    replacement = control.ControlServer(lambda _: {"ok": True, "replacement": True}, runtime_dir=directory)
    assert replacement.start()
    try:
        assert _background(qtbot, lambda: control.send_command({"action": "status"}, runtime_dir=directory))["replacement"]
    finally:
        replacement.close()


def test_wrong_token_never_calls_application(server, qtbot):
    owner, calls = server
    result = _background(qtbot, lambda: _raw(owner.endpoint,
        control._json_bytes({"token": "wrong", "command": {"action": "capture"}})))
    assert result["error"]["code"] == "unauthorized"
    assert calls == []
    assert owner.token not in json.dumps(result)


@pytest.mark.parametrize("raw_request", [b"not JSON\n", b"[]\n", b"{}\n{}\n"])
def test_malformed_requests_are_bounded_errors(server, qtbot, raw_request):
    owner, calls = server
    result = _background(qtbot, lambda: _raw(owner.endpoint, raw_request))
    assert not result["ok"]
    assert calls == []


def test_handler_failure_is_json_and_keeps_server_usable(server, qtbot):
    owner, calls = server
    result = _background(qtbot, lambda: control.send_command({"action": "fail"}, runtime_dir=owner.runtime_dir))
    assert not result["ok"]
    assert "Invented command failure" in result["error"]["message"]
    result = _background(qtbot, lambda: control.send_command({"action": "status"}, runtime_dir=owner.runtime_dir))
    assert result["ok"]
    assert len(calls) == 2


def test_request_size_rejected_before_connect(server):
    owner, calls = server
    with pytest.raises(control.ControlError, match="exceeds"):
        control.send_command({"action": "configure", "notes": "x" * control.MAX_MESSAGE_BYTES}, runtime_dir=owner.runtime_dir)
    assert calls == []


def test_missing_or_foreign_discovery_is_not_followed(qapp, tmp_path):
    with pytest.raises(control.ControlError, match="Open the microscope app"):
        control.send_command({"action": "status"}, runtime_dir=tmp_path)
    (tmp_path / "control.json").write_text(json.dumps({"protocol_version": 1,
        "endpoint": "unrelated-private-application", "token": "a" * 64}))
    with pytest.raises(control.ControlError) as caught:
        control.send_command({"action": "status"}, runtime_dir=tmp_path)
    assert caught.value.code == "not_running"


def test_cli_file_request_avoids_shell_json_quoting(server, qtbot, tmp_path):
    owner, _ = server
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"action": "measure", "start": [3, 4], "end": [30, 40]}), encoding="utf-8-sig")
    result = _background(qtbot, lambda: subprocess.run([
        sys.executable, "-m", "data_logging.microscope_logger.control",
        "--runtime-dir", str(owner.runtime_dir), "request", "--json", str(request),
    ], capture_output=True, text=True, encoding="utf-8", timeout=10))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["received"]["end"] == [30, 40]
    assert owner.token not in result.stdout + result.stderr


def test_named_commands_form_explicit_payloads():
    parser = control.build_parser()
    assert control.command_from_args(parser.parse_args([
        "calibrate", "--start", "10", "20", "--end", "110", "20", "--known-um", "50",
        "--source", "Synthetic reference"])) == {
        "action": "calibrate", "start": [10, 20], "end": [110, 20],
        "known_um": 50, "source": "Synthetic reference"}
    assert control.command_from_args(parser.parse_args(["configure", "--objective", "4x", "--width", "2560"])) == {
        "action": "configure", "objective": "4x", "width": 2560}
    assert control.command_from_args(parser.parse_args(["parallel", "--a", "1", "2", "--b", "10", "2", "--c", "1", "20"])) == {
        "action": "parallel", "a": [1, 2], "b": [10, 2], "c": [1, 20], "kind": "apparent"}
    assert control.command_from_args(parser.parse_args(["load-sources"])) == {"action": "load_sources", "kind": "project"}
    assert control.command_from_args(parser.parse_args(["load-sources", "--cancel"])) == {
        "action": "load_sources", "kind": "project", "cancel": True}
    assert control.command_from_args(parser.parse_args([
        "load-sources", "--kind", "fabrication", "--composition", "Ni50Fe27Ga23"])) == {
        "action": "load_sources", "kind": "fabrication", "composition": "Ni50Fe27Ga23"}
    assert control.command_from_args(parser.parse_args(["restore-session", "session-directory"])) == {
        "action": "restore_session", "path": "session-directory"}


def test_runtime_override_is_explicit_and_branch_scoped(monkeypatch, tmp_path):
    monkeypatch.setenv("PYPLOT_MICROSCOPE_RUNTIME_DIR", str(tmp_path / "runtime"))
    assert control.runtime_directory() == (tmp_path / "runtime").resolve()
    assert control.runtime_directory(tmp_path / "explicit") == (tmp_path / "explicit").resolve()
    assert control._endpoint(tmp_path / "one") != control._endpoint(tmp_path / "two")


def test_invalid_finite_json_and_timeout_inputs(server):
    owner, _ = server
    with pytest.raises(control.ControlError, match="finite JSON"):
        control.send_command({"action": "configure", "zoom": float("nan")}, runtime_dir=owner.runtime_dir)
    with pytest.raises(control.ControlError, match="timeout_ms"):
        control.send_command({"action": "status"}, runtime_dir=owner.runtime_dir, timeout_ms=0)


def test_close_preserves_discovery_if_another_owner_replaced_it(server):
    owner, _ = server
    replacement = {"protocol_version": 1, "endpoint": owner.endpoint, "token": "b" * 64}
    owner.discovery_path.write_text(json.dumps(replacement))
    owner.close()
    assert json.loads(owner.discovery_path.read_text()) == replacement
