from __future__ import annotations

from plotting.pyplot.cursor_status import cursor_readout_text


class _FakeMetrics:
    def horizontalAdvance(self, text: str) -> int:
        return len(text)

    def elidedText(self, text: str, _mode: object, width: int) -> str:
        if width <= 0:
            return ""
        if len(text) <= width:
            return text
        if width == 1:
            return "."
        return text[: width - 1] + "."


def test_cursor_readout_uses_placeholder_for_missing_values() -> None:
    assert cursor_readout_text(None, 1.0) == "x: --   y: --"
    assert cursor_readout_text(1.0, None) == "x: --   y: --"


def test_cursor_readout_uses_compact_format_when_width_is_limited() -> None:
    metrics = _FakeMetrics()
    expanded = cursor_readout_text(1234.567, -9876.543, available_px=0, metrics=metrics)
    compact = "1235, -9877"
    compact_width = len(compact)
    expanded_width = len(expanded)
    assert expanded_width > compact_width

    displayed = cursor_readout_text(
        1234.567,
        -9876.543,
        available_px=compact_width + 1,
        metrics=metrics,
    )
    assert displayed == compact


def test_cursor_readout_elides_when_space_is_very_small() -> None:
    metrics = _FakeMetrics()
    expanded = cursor_readout_text(1234.567, -9876.543, available_px=0, metrics=metrics)
    displayed = cursor_readout_text(
        1234.567,
        -9876.543,
        available_px=8,
        metrics=metrics,
    )
    assert displayed
    assert displayed != expanded
