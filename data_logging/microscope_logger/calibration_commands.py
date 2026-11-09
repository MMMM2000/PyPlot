"""Validated command access to the visible reference-calibration workflow."""
from __future__ import annotations

from collections.abc import Mapping
import math

from .measurements import make_calibration, measure_line


def _coordinates(value: object, name: str) -> tuple[float, float]:
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or any(isinstance(coordinate, bool) for coordinate in value)):
        raise ValueError(f"{name} must contain two finite native image coordinates")
    try:
        result = tuple(float(coordinate) for coordinate in value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must contain two finite native image coordinates") from exc
    if not all(math.isfinite(coordinate) for coordinate in result):
        raise ValueError(f"{name} must contain two finite native image coordinates")
    return result


def calibrate_from_command(window, command: Mapping[str, object]) -> dict:
    """Calibrate a still image through the same controls as the GUI.

    Validate the entire request before changing its existing reference, tool,
    scale or provenance. Reference preservation errors from the GUI are reported
    as command failures; successful calibration follows the normal autosave path.
    """
    if set(command) - {"action", "start", "end", "known_um", "source"}:
        raise ValueError("Unknown calibration field")
    if window.camera is not None or window.image.isNull():
        raise ValueError("Capture or open a frozen still image before calibrating")
    start = _coordinates(command.get("start"), "start")
    end = _coordinates(command.get("end"), "end")
    source = command.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("A nonempty calibration reference source is required")
    source = source.strip()
    raw_known = command.get("known_um")
    if isinstance(raw_known, bool):
        raise ValueError("known_um must be a finite positive reference length")
    try:
        known_um = float(raw_known)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("known_um must be a finite positive reference length") from exc
    if (not math.isfinite(known_um) or known_um <= 0
            or not window.known_um.minimum() <= known_um <= window.known_um.maximum()):
        raise ValueError("known_um is outside the supported positive reference-length range")
    # QDoubleSpinBox rounds to its declared decimals. Refuse unsupported precision
    # rather than record a different reference length from the submitted value.
    if not math.isclose(known_um, round(known_um, window.known_um.decimals()),
                        rel_tol=0, abs_tol=1e-12):
        raise ValueError(f"known_um supports at most {window.known_um.decimals()} decimal places")
    context = window._context()
    length = measure_line(start, end, **context)["width_px"]
    make_calibration(length, known_um, source=source, **context)

    window.action_combo.setCurrentIndex(1)
    window.line_drawn(start, end)
    window.known_um.setValue(known_um)
    window.reference_source.setText(source)
    if not window.calibrate():
        raise ValueError(window.statusBar().currentMessage() or "Reference calibration failed")
    window._autosave()
    return window.command_status()


__all__ = ["calibrate_from_command"]
