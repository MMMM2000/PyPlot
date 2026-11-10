"""A materialized runtime directory determines both server and client identity."""
from concurrent.futures import ThreadPoolExecutor

from data_logging.microscope_logger import control


def changing_identity(monkeypatch):
    """Model MSIX final-path resolution changing after mkdir, without app data."""
    real_endpoint = control._endpoint

    def endpoint(directory):
        return real_endpoint(directory if directory.exists() else directory / "before-materialization")

    monkeypatch.setattr(control, "_endpoint", endpoint)
    return real_endpoint


def test_start_rebinds_identity_after_runtime_materializes(qtbot, tmp_path, monkeypatch):
    canonical_endpoint = changing_identity(monkeypatch)
    directory = tmp_path / "private-runtime"
    owner = control.ControlServer(lambda payload: {"ok": True, "action": payload["action"]},
                                  runtime_dir=directory)
    provisional = owner.endpoint
    assert not directory.exists()
    assert owner.start()
    try:
        assert owner.endpoint != provisional
        assert owner.endpoint == canonical_endpoint(directory)
        assert control._discovery(directory)["endpoint"] == owner.endpoint
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(control.send_command, {"action": "status"}, runtime_dir=directory)
            qtbot.waitUntil(result.done, timeout=10000)
            assert result.result() == {"ok": True, "action": "status"}
    finally:
        owner.close()


def test_materialized_identity_keeps_duplicate_guard_and_restart(qtbot, tmp_path, monkeypatch):
    canonical_endpoint = changing_identity(monkeypatch)
    directory = tmp_path / "private-runtime"
    first = control.ControlServer(lambda _: {"ok": True}, runtime_dir=directory)
    assert first.start()
    second = control.ControlServer(lambda _: {"ok": True}, runtime_dir=directory)
    try:
        assert not second.start()
        assert first.endpoint == second.endpoint == canonical_endpoint(directory)
    finally:
        first.close()
    assert second.start()
    try:
        assert control._discovery(directory)["endpoint"] == canonical_endpoint(directory)
    finally:
        second.close()


def test_existing_runtime_uses_unchanged_identity(qtbot, tmp_path):
    directory = tmp_path / "existing-runtime"
    directory.mkdir()
    owner = control.ControlServer(lambda _: {"ok": True}, runtime_dir=directory)
    expected = owner.endpoint
    assert owner.start()
    try:
        assert owner.endpoint == expected == control._endpoint(directory)
        assert control._discovery(directory)["endpoint"] == expected
    finally:
        owner.close()
