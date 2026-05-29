from __future__ import annotations

from experiments.thermal_camera_viewer import (
    BINARY_HEADER,
    BINARY_MAGIC,
    BINARY_VERSION,
    FRAME_HEIGHT,
    FRAME_PIXELS,
    FRAME_WIDTH,
    ROI_START_COL,
    ROI_WIDTH,
    RAW_FRAME_WORDS,
    RAW_HEADER,
    RAW_COMPACT_WORDS,
    RAW_REQUIRED_AUX_END,
    RAW_REQUIRED_AUX_START,
    EEPROM_MAGIC,
    EEPROM_PACKET_SIZE,
    RAW_MAGIC,
    RAW_PAYLOAD_BYTES,
    RAW_VERSION,
    FrameRateTracker,
    ThermalCameraViewer,
    ThermalFrame,
    parse_cube_eeprom_packet,
    parse_binary_frame,
    parse_frame_lines,
    parse_raw_cube_frame,
    pop_binary_frames,
    pop_cube_packets,
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


def _raw_cube_packet(*, elapsed_ms: int = 2468, read_us: int = 17520) -> bytes:
    values = [index - 384 for index in range(RAW_FRAME_WORDS)]
    payload = bytearray()
    for value in values:
        payload.extend(int(value).to_bytes(2, "big", signed=True))
    body = bytearray(
        RAW_HEADER.pack(
            RAW_MAGIC,
            RAW_VERSION,
            1,
            RAW_FRAME_WORDS,
            9,
            elapsed_ms,
            read_us,
            0x0011,
            0x1281,
            RAW_PAYLOAD_BYTES,
        )
    )
    body.extend(payload)
    checksum = sum(body) & 0xFFFF
    body.extend(checksum.to_bytes(2, "little", signed=False))
    return bytes(body)


def _compact_raw_cube_packet(*, subpage: int = 1, words: int = RAW_COMPACT_WORDS) -> bytes:
    values = [index for index in range(words)]
    payload = bytearray()
    for value in values:
        payload.extend(int(value).to_bytes(2, "big", signed=False))
    body = bytearray(
        RAW_HEADER.pack(
            RAW_MAGIC,
            RAW_VERSION,
            0x40 | subpage,
            words,
            10,
            1234,
            9900,
            subpage,
            0x1000,
            words * 2,
        )
    )
    body.extend(payload)
    checksum = sum(body) & 0xFFFF
    body.extend(checksum.to_bytes(2, "little", signed=False))
    return bytes(body)


def _cube_eeprom_packet() -> bytes:
    values = [0x2400 + index for index in range(RAW_FRAME_WORDS)]
    payload = bytearray()
    for value in values:
        payload.extend(int(value).to_bytes(2, "big", signed=False))
    body = bytearray(
        RAW_HEADER.pack(
            EEPROM_MAGIC,
            RAW_VERSION,
            0,
            RAW_FRAME_WORDS,
            0,
            123,
            456,
            0,
            0,
            RAW_PAYLOAD_BYTES,
        )
    )
    body.extend(payload)
    checksum = sum(body) & 0xFFFF
    body.extend(checksum.to_bytes(2, "little", signed=False))
    return bytes(body)


def test_parse_raw_cube_frame_returns_diagnostic_frame() -> None:
    frame = parse_raw_cube_frame(_raw_cube_packet())

    assert frame is not None
    assert frame.elapsed_ms == 2468
    assert frame.ambient_c is None
    assert frame.unit == "raw"
    assert frame.raw_read_us == 17520
    assert frame.sequence == 9
    assert frame.status == 0x0011
    assert frame.control == 0x1281
    assert frame.raw_words[:3] == (0xFE80, 0xFE81, 0xFE82)
    assert len(frame.values) == FRAME_PIXELS
    assert frame.values[0] == -384.0
    assert frame.values[-1] == 383.0


def test_parse_compact_raw_cube_frame_maps_interleaved_rows() -> None:
    frame = parse_raw_cube_frame(_compact_raw_cube_packet(subpage=1))

    assert frame is not None
    assert frame.raw_words[0] == 0
    assert frame.raw_words[FRAME_WIDTH + ROI_START_COL] == 0
    assert frame.raw_words[FRAME_WIDTH + ROI_START_COL + 1] == 1
    assert frame.raw_words[(3 * FRAME_WIDTH) + ROI_START_COL - 1] == 0
    assert frame.raw_words[(3 * FRAME_WIDTH) + ROI_START_COL] == ROI_WIDTH
    assert frame.raw_pixel_mask[FRAME_WIDTH + ROI_START_COL]
    assert not frame.raw_pixel_mask[FRAME_WIDTH + ROI_START_COL - 1]
    assert frame.raw_words[RAW_REQUIRED_AUX_START] == RAW_COMPACT_WORDS - (RAW_REQUIRED_AUX_END - RAW_REQUIRED_AUX_START + 1)
    assert frame.raw_words[RAW_REQUIRED_AUX_END] == RAW_COMPACT_WORDS - 1
    assert frame.raw_words[RAW_FRAME_WORDS - 1] == 0


def test_parse_compact_raw_cube_frame_infers_roi_width_from_word_count() -> None:
    words = (12 * 24) + (RAW_REQUIRED_AUX_END - RAW_REQUIRED_AUX_START + 1)
    frame = parse_raw_cube_frame(_compact_raw_cube_packet(subpage=0, words=words))

    assert frame is not None
    assert frame.raw_pixel_mask[4]
    assert frame.raw_pixel_mask[27]
    assert not frame.raw_pixel_mask[3]
    assert not frame.raw_pixel_mask[28]
    assert frame.raw_words[4] == 0
    assert frame.raw_words[27] == 23


def test_parse_cube_eeprom_packet_returns_unsigned_words() -> None:
    words = parse_cube_eeprom_packet(_cube_eeprom_packet())

    assert words is not None
    assert len(words) == RAW_FRAME_WORDS
    assert words[:3] == (0x2400, 0x2401, 0x2402)
    assert words[-1] == 0x2400 + RAW_FRAME_WORDS - 1


def test_pop_cube_packets_resynchronizes_after_noise() -> None:
    buffer = bytearray(b"noise")
    buffer.extend(_cube_eeprom_packet())
    buffer.extend(_raw_cube_packet(elapsed_ms=1))
    buffer.extend(_raw_cube_packet(elapsed_ms=2))

    eeprom_packets, frames = pop_cube_packets(buffer)

    assert len(eeprom_packets) == 1
    assert eeprom_packets[0][0] == 0x2400
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

    assert window.baud_combo.currentData() == 2000000
    assert window.baud_combo.findData(115200) >= 0


def test_viewer_uses_refresh_rate_dropdown(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = ThermalCameraViewer()
    qtbot.addWidget(window)

    assert window.refresh_rate_combo.currentData() == 5
    assert window.refresh_rate_combo.findData(6) >= 0
    assert window.refresh_rate_combo.findData(7) >= 0
