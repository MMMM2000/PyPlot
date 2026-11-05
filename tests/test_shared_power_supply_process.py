from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import time

import pytest

from data_logging.shared_power_supply.broker import (
    ROLE_MINI_DMA_CURRENT,
    ROLE_MINI_DMA_MOTOR,
)
from data_logging.shared_power_supply.process import (
    BrokerChannelConfig,
    BrokerProcessConfig,
    SharedPowerSupplyBrokerProcess,
    _connect_driver_with_bounded_handoff_retry,
)
from data_logging.shared_power_supply.protocol import BrokerJsonClient


def _fake_config() -> BrokerProcessConfig:
    return BrokerProcessConfig(
        port_name="FAKE-HMP",
        baudrate=115200,
        host="127.0.0.1",
        port=0,
        channels=(
            BrokerChannelConfig(
                channel=4,
                role=ROLE_MINI_DMA_CURRENT,
                voltage_limit_v=1.0,
            ),
            BrokerChannelConfig(
                channel=3,
                role=ROLE_MINI_DMA_MOTOR,
                voltage_limit_v=5.0,
                current_limit_a=0.5,
            ),
        ),
        parent_pid=os.getpid(),
        driver_factory_module="data_logging.shared_power_supply.fake_driver",
        driver_factory_name="create_fake_hmp_driver",
    )


def test_spawned_broker_owns_driver_and_serves_snapshot() -> None:
    process = SharedPowerSupplyBrokerProcess(_fake_config())
    try:
        process.start()
        ready = process.wait_until_ready(timeout_s=5.0)

        assert ready.owner_pid != os.getpid()
        assert ready.profile_id == "hmp4040"
        snapshot = BrokerJsonClient(
            host=ready.host,
            port=ready.port,
            timeout_s=1.0,
        ).snapshot()
        assert snapshot["model"] == "hmp4040"
        assert snapshot["bench_profile"]["port_identity"] == "FAKE-HMP"
    finally:
        assert process.close(timeout_s=2.0, force=True)


def test_spawned_broker_parent_loss_turns_every_output_off(
    tmp_path: Path,
) -> None:
    audit_path = tmp_path / "broker-audit.txt"
    config = replace(
        _fake_config(),
        parent_pid=2_147_483_647,
        parent_loss_grace_s=0.05,
        driver_factory_options_json=json.dumps(
            {"audit_path": str(audit_path)}
        ),
    )
    process = SharedPowerSupplyBrokerProcess(config)
    process.start()
    process.wait_until_ready(timeout_s=5.0)

    deadline_s = time.monotonic() + 5.0
    while process.is_alive() and time.monotonic() < deadline_s:
        time.sleep(0.01)

    assert process.is_alive() is False
    assert process.exitcode == 0
    audit = audit_path.read_text(encoding="utf-8")
    for channel in range(1, 5):
        assert f"output:{channel}:off" in audit
    assert audit.rstrip().endswith("close")


def test_spawned_broker_reports_factory_startup_failure() -> None:
    process = SharedPowerSupplyBrokerProcess(
        replace(
            _fake_config(),
            driver_factory_module="not_a_real_tma_fake_driver",
        )
    )
    try:
        process.start()
        with pytest.raises(RuntimeError, match="not_a_real_tma_fake_driver"):
            process.wait_until_ready(timeout_s=5.0)
    finally:
        assert process.close(timeout_s=2.0, force=True)


@pytest.mark.parametrize("attempt", range(5))
def test_fast_spawn_failure_never_degrades_to_opaque_exit_code(attempt: int) -> None:
    process = SharedPowerSupplyBrokerProcess(
        replace(
            _fake_config(),
            driver_factory_module=f"not_a_real_tma_driver_{attempt}",
        )
    )
    try:
        process.start()
        with pytest.raises(RuntimeError) as failure:
            process.wait_until_ready(timeout_s=5.0)
        message = str(failure.value)
        assert f"not_a_real_tma_driver_{attempt}" in message
        assert "exited with code" not in message
    finally:
        assert process.close(timeout_s=2.0, force=True)


def test_driver_connect_retries_transient_windows_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    class _Driver:
        def connect(self) -> None:
            attempts.append(len(attempts) + 1)
            if len(attempts) < 3:
                raise PermissionError(5, "port still closing")

    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    _connect_driver_with_bounded_handoff_retry(_Driver())

    assert attempts == [1, 2, 3]
