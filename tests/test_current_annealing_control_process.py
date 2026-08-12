from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from data_logging.mini_dma_logger.control_process import (
    BackendFactorySpec,
    ControlEventKind,
    ControlPolicy,
    ControlSessionIdentity,
    ControlStartRequest,
    TmaControlProcess,
)
from data_logging.current_annealing_logger import process_backend as backend_mod


def test_process_backend_import_is_headless_from_non_repo_script_path(tmp_path: Path) -> None:
    script = tmp_path / "import_backend.py"
    script.write_text(
        "from data_logging.current_annealing_logger.process_backend import "
        "create_current_annealing_backend\n"
        "print(type(create_current_annealing_backend()).__name__)\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "CurrentAnnealingProcessBackend"


def test_real_broker_client_uses_supported_timeout_keyword(
    tmp_path: Path, monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class _Client:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def lease(self, **_kwargs):
            return {"lease_id": "lease"}

        def start_scheduler(self, **_kwargs) -> None:
            return None

        def configure_polling(self, **_kwargs):
            return {"polling": {"effective_hz": 2.0}}

        def configure_channel(self, **_kwargs) -> None:
            return None

        def release(self, **_kwargs) -> None:
            return None

    monkeypatch.setattr(backend_mod, "BrokerJsonClient", _Client)
    backend = backend_mod.CurrentAnnealingProcessBackend()
    config = {
        "simulate": False,
        "run_dir": str(tmp_path / "real-client"),
        "metadata": {"sample": {"name": "real client keyword"}},
        "broker_host": "127.0.0.1",
        "broker_port": 8765,
        "broker_timeout_s": 1.25,
        "channel": 1,
        "requested_hz": 2.0,
        "voltage_limit_V": 1.0,
        "start_current_mA": 1.0,
        "max_current_mA": 2.0,
        "ramp_rate_mA_s": 1.0,
    }
    request = ControlStartRequest(
        identity=ControlSessionIdentity("real-client-keyword", 1),
        policy=ControlPolicy.PRAGUE,
        control_interval_s=0.02,
        snapshot_interval_s=0.1,
        parent_heartbeat_timeout_s=2.0,
        config_json=json.dumps(config),
    )
    backend.start(request)
    backend.close()
    assert captured == {"host": "127.0.0.1", "port": 8765, "timeout_s": 1.25}


def test_open_circuit_at_voltage_limit_cannot_complete_as_normal_sweep(tmp_path: Path) -> None:
    class _OpenCircuitClient:
        def __init__(self) -> None:
            self.output_on = False

        def lease(self, **_kwargs):
            return {"lease_id": "open-circuit"}

        def start_scheduler(self, **_kwargs) -> None:
            return None

        def configure_polling(self, **_kwargs):
            return {"polling": {"effective_hz": 20.0}}

        def configure_channel(self, **kwargs) -> None:
            self.output_on = bool(kwargs["output_on"])

        def schedule_current(self, **_kwargs) -> None:
            return None

        def latest_readback(self, **_kwargs):
            return {
                "current_mA": 0.1,
                "voltage_V": 1.0,
                "timestamp_s": time.monotonic(),
                "age_s": 0.0,
            }

        def release(self, **_kwargs) -> None:
            return None

    client = _OpenCircuitClient()
    backend = backend_mod.CurrentAnnealingProcessBackend(client_factory=lambda _config: client)
    config = {
        "run_dir": str(tmp_path / "open-at-limit"),
        "metadata": {"sample": {"name": "open at compliance"}},
        "broker_host": "127.0.0.1",
        "broker_port": 8765,
        "channel": 1,
        "requested_hz": 20.0,
        "voltage_limit_V": 1.0,
        "start_current_mA": 1.0,
        "max_current_mA": 1.1,
        "ramp_rate_mA_s": 0.01,
        "reverse_enabled": True,
        "loops": 1,
        "minimum_contact_current_mA": 0.5,
        "contact_grace_s": 0.0,
        "contact_loss_samples": 2,
    }
    request = ControlStartRequest(
        identity=ControlSessionIdentity("open-at-limit", 1),
        policy=ControlPolicy.PRAGUE,
        control_interval_s=0.02,
        snapshot_interval_s=0.1,
        parent_heartbeat_timeout_s=2.0,
        config_json=json.dumps(config),
    )
    backend.start(request)
    now = time.monotonic()
    backend.tick(now + 0.02)
    with pytest.raises(RuntimeError, match="Measured current disappeared"):
        backend.tick(now + 0.04)
    metadata = json.loads((tmp_path / "open-at-limit" / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["session_state"] == "failed"
    assert metadata["stop"]["reason"] == "contact_lost"
    assert client.output_on is False


def test_current_annealing_process_owns_recipe_and_authoritative_logging(tmp_path: Path) -> None:
    run_dir = tmp_path / "simulated_run01"
    config = {
        "simulate": True,
        "run_dir": str(run_dir),
        "metadata": {"sample": {"name": "simulated"}, "recipe": {"loops": 1}},
        "broker_host": "127.0.0.1",
        "broker_port": 8765,
        "channel": 1,
        "requested_hz": 20.0,
        "voltage_limit_V": 32.0,
        "start_current_mA": 1.0,
        "max_current_mA": 2.0,
        "ramp_rate_mA_s": 20.0,
        "reverse_enabled": True,
        "loops": 1,
        "diameter_um": 10.0,
    }
    identity = ControlSessionIdentity("current-annealing-test", 1)
    request = ControlStartRequest(
        identity=identity,
        policy=ControlPolicy.PRAGUE,
        control_interval_s=0.01,
        snapshot_interval_s=0.02,
        parent_heartbeat_timeout_s=2.0,
        config_json=json.dumps(config),
    )
    process = TmaControlProcess(
        heartbeat_interval_s=0.05,
        backend_factory_spec=BackendFactorySpec(
            module="data_logging.current_annealing_logger.process_backend",
            factory="create_current_annealing_backend",
        ),
    )
    try:
        process.start_process()
        process.wait_until_ready(timeout_s=8.0)
        process.start_session(request)
        deadline = time.monotonic() + 12.0
        complete = False
        while time.monotonic() < deadline and process.is_alive():
            if any(event.kind is ControlEventKind.RECIPE_COMPLETE for event in process.poll_events()):
                complete = True
                break
            time.sleep(0.02)
        assert complete
    finally:
        process.close(timeout_s=3.0, force=True)

    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["session_state"] == "completed"
    assert metadata["point_count"] > 0
    assert (run_dir / "measurement.csv").is_file()
    assert (run_dir / "run_summary.png").is_file()


def test_current_annealing_process_rejects_broken_contact_before_heating(tmp_path: Path) -> None:
    run_dir = tmp_path / "open_circuit_run01"
    config = {
        "simulate": True,
        "simulate_open_circuit": True,
        "run_dir": str(run_dir),
        "metadata": {"sample": {"name": "open circuit"}, "recipe": {"loops": 1}},
        "broker_host": "127.0.0.1",
        "broker_port": 8765,
        "channel": 1,
        "requested_hz": 20.0,
        "voltage_limit_V": 32.0,
        "start_current_mA": 1.0,
        "max_current_mA": 20.0,
        "ramp_rate_mA_s": 1.0,
        "reverse_enabled": True,
        "loops": 1,
        "contact_grace_s": 0.05,
        "contact_loss_samples": 2,
    }
    identity = ControlSessionIdentity("open-circuit-test", 1)
    request = ControlStartRequest(
        identity=identity,
        policy=ControlPolicy.PRAGUE,
        control_interval_s=0.01,
        snapshot_interval_s=0.02,
        parent_heartbeat_timeout_s=2.0,
        config_json=json.dumps(config),
    )
    process = TmaControlProcess(
        heartbeat_interval_s=0.05,
        backend_factory_spec=BackendFactorySpec(
            module="data_logging.current_annealing_logger.process_backend",
            factory="create_current_annealing_backend",
        ),
    )
    try:
        process.start_process()
        process.wait_until_ready(timeout_s=8.0)
        process.start_session(request)
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and process.is_alive():
            time.sleep(0.02)
        detail, _traceback = process.poll_fault_detail()
        assert "Measured current disappeared" in detail
    finally:
        process.close(timeout_s=2.0, force=True)
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["session_state"] == "failed"
    assert metadata["stop"]["reason"] == "contact_lost"
