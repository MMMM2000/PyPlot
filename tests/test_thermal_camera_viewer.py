from __future__ import annotations

from experiments.thermal_camera_viewer import (
    BINARY_HEADER,
    BINARY_MAGIC,
    BINARY_VERSION,
    FRAME_HEIGHT,
    FRAME_PIXELS,
    FRAME_WIDTH,
    FrameRateTracker,
    ThermalCameraViewer,
    ThermalFrame,
    parse_binary_frame,
    parse_frame_lines,
    pop_binary_frames,
)


def _frame_lines() -> list[str]:
    lines = ["FRAME_BEGIN,1234,25.50"]
    value = 20.0
    for y in range(FRAME_HEIGHT):
        row = [f"{value + x + y:.2f}" for x in range(FRAME_WIDTH)]
        lines.append("ROW," + str(y) + "," + ",".join(row))
    lines.append("FRAME_END")
    return lines


def test_parse_frame_lines_returns_complete_thermal_frame() -> None:
    frame = parse_frame_lines(_frame_lines())

    assert frame is not None
    assert frame.elapsed_ms == 1234
    assert frame.ambient_c == 25.5
    assert len(frame.values) == FRAME_WIDTH * FRAME_HEIGHT
    assert frame.minimum_c == 20.0
    assert frame.maximum_c == 20.0 + (FRAME_WIDTH - 1) + (FRAME_HEIGHT - 1)


def test_parse_frame_lines_rejects_incomplete_frame() -> None:
    lines = _frame_lines()
    del lines[10]

    assert parse_frame_lines(lines) is None


def _binary_packet(*, elapsed_ms: int = 4321, ambient_centi: int = 2550) -> bytes:
    values = [2000 + index for index in range(FRAME_PIXELS)]
    body = bytearray(
        BINARY_HEADER.pack(
            BINARY_MAGIC,
            BINARY_VERSION,
            FRAME_WIDTH,
            FRAME_HEIGHT,
            0,
            7,
            elapsed_ms,
            ambient_centi,
        )
    )
    for value in values:
        body.extend(int(value).to_bytes(2, "little", signed=True))
    checksum = sum(body) & 0xFFFF
    body.extend(checksum.to_bytes(2, "little", signed=False))
    return bytes(body)


def test_parse_binary_frame_returns_complete_thermal_frame() -> None:
    frame = parse_binary_frame(_binary_packet())

    assert frame is not None
    assert frame.elapsed_ms == 4321
    assert frame.ambient_c == 25.5
    assert len(frame.values) == FRAME_PIXELS
    assert frame.values[0] == 20.0
    assert frame.values[-1] == (2000 + FRAME_PIXELS - 1) / 100.0


def test_pop_binary_frames_resynchronizes_after_noise() -> None:
    buffer = bytearray(b"noise")
    buffer.extend(_binary_packet(elapsed_ms=1))
    buffer.extend(_binary_packet(elapsed_ms=2))

    frames = pop_binary_frames(buffer)

    assert [frame.elapsed_ms for frame in frames] == [1, 2]
    assert buffer == bytearray()


def test_frame_rate_tracker_prefers_device_timestamps_over_gui_delivery_time() -> None:
    tracker = FrameRateTracker()
    values = tuple(25.0 for _ in range(FRAME_PIXELS))

    tracker.record(ThermalFrame(1000, 25.0, values), received_at=10.000)
    fps = tracker.record(ThermalFrame(1500, 25.0, values), received_at=10.001)

    assert fps == 2.0


def test_viewer_uses_baud_rate_dropdown(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = ThermalCameraViewer()
    qtbot.addWidget(window)

    assert window.baud_combo.currentData() == 921600
    assert window.baud_combo.findData(115200) >= 0
