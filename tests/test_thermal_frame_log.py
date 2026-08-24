from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_logging.mini_dma_logger.thermal_frame_log import (
    SessionThermalFrameTarget,
    THERMAL_FRAME_LOG_FORMAT,
    iter_thermal_frame_records,
    thermal_frame_record_from_object,
)


def _frame(sequence: int = 7) -> SimpleNamespace:
    return SimpleNamespace(
        sequence=sequence,
        elapsed_ms=1234,
        width=3,
        height=2,
        flags=5,
        ambient_c=24.25,
        raw_read_us=15000,
        values=(20.0, 21.0, 22.0, 23.0, 24.0, 25.5),
    )


def test_thermal_frame_sidecar_round_trips_calibrated_pixels(tmp_path: Path) -> None:
    path = tmp_path / "ir_frames.bin"
    target = SessionThermalFrameTarget(path, queue_capacity=4, flush_frames=1)
    record = thermal_frame_record_from_object(
        _frame(),
        host_timestamp_s=1000.5,
        session_start_wall_s=999.0,
    )

    assert target.submit(record) is None
    target.detach_and_close()
    assert target.wait_closed(2.0)

    outcome = target.outcome()
    assert outcome["complete"] is True
    assert outcome["format"] == THERMAL_FRAME_LOG_FORMAT
    assert outcome["accepted_rows"] == 1
    assert outcome["written_rows"] == 1
    assert outcome["lost_rows"] == 0

    (loaded,) = tuple(iter_thermal_frame_records(path))
    assert loaded.sequence == 7
    assert loaded.device_elapsed_ms == 1234
    assert loaded.host_timestamp_s == pytest.approx(1000.5)
    assert loaded.session_elapsed_s == pytest.approx(1.5)
    assert (loaded.width, loaded.height) == (3, 2)
    assert loaded.flags == 5
    assert loaded.ambient_c == pytest.approx(24.25)
    assert loaded.raw_read_us == 15000
    assert loaded.values_c == pytest.approx(_frame().values)


def test_thermal_frame_reader_rejects_truncated_tail(tmp_path: Path) -> None:
    path = tmp_path / "ir_frames.bin"
    target = SessionThermalFrameTarget(path, flush_frames=1)
    assert target.submit(
        thermal_frame_record_from_object(
            _frame(), host_timestamp_s=time.time(), session_start_wall_s=time.time() - 1.0
        )
    ) is None
    target.detach_and_close()
    assert target.wait_closed(2.0)
    path.write_bytes(path.read_bytes()[:-3])

    with pytest.raises(ValueError, match="truncated frame"):
        tuple(iter_thermal_frame_records(path))


@pytest.mark.parametrize(
    ("width", "height", "values"),
    [(0, 2, (1.0, 2.0)), (2, 2, (1.0, 2.0, 3.0))],
)
def test_thermal_frame_snapshot_rejects_invalid_shape(
    width: int,
    height: int,
    values: tuple[float, ...],
) -> None:
    frame = _frame()
    frame.width = width
    frame.height = height
    frame.values = values

    with pytest.raises(ValueError, match="invalid thermal frame dimensions"):
        thermal_frame_record_from_object(
            frame, host_timestamp_s=1.0, session_start_wall_s=0.0
        )
