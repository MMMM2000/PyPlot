"""Video analysis helpers for the microwire database builder."""

from __future__ import annotations

import logging
import math
import re
import tempfile
import sys
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

import numpy as np

MICRO_SIGN = "\u00b5"

sys.modules.setdefault("microwire_data_builder.video", sys.modules.get(__name__))
_METRIC_PATTERN = re.compile(r"(-?\d+(?:[.,]\d+)?)")

try:
    from .ocr import get_paddle_ocr
except ImportError:
    module_name = "microwire_data_builder.ocr"
    module_path = Path(__file__).with_name("ocr.py")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        get_paddle_ocr = module.get_paddle_ocr
    else:
        raise


@dataclass
class VideoExtractionResult:
    """Aggregated OCR metrics extracted from a fabrication video."""

    video_path: Path
    frame_paths: List[Path] = field(default_factory=list)
    texts: List[str] = field(default_factory=list)
    temperatures_c: List[float] = field(default_factory=list)
    underpressures: List[float] = field(default_factory=list)
    winding_speeds_m_per_min: List[float] = field(default_factory=list)
    glass_feed_mm_per_min: List[float] = field(default_factory=list)

    def median_temperature(self) -> Optional[float]:
        if not self.temperatures_c:
            return None
        return float(np.median(self.temperatures_c))

    def median_underpressure(self) -> Optional[float]:
        if not self.underpressures:
            return None
        return float(np.median(self.underpressures))

    def median_winding_speed(self) -> Optional[float]:
        if not self.winding_speeds_m_per_min:
            return None
        return float(np.median(self.winding_speeds_m_per_min))

    def median_glass_feed(self) -> Optional[float]:
        if not self.glass_feed_mm_per_min:
            return None
        return float(np.median(self.glass_feed_mm_per_min))


def extract_video_metrics(
    video_path: Path,
    frame_interval: float = 30.0,
    max_frames: int = 200,
    logger: Optional[logging.Logger] = None,
    frame_output_dir: Optional[Path] = None,
) -> VideoExtractionResult:
    """Sample frames from *video_path* and attempt to OCR process metrics.

    The function favours optional dependencies. If OpenCV or PaddleOCR are
    not installed (or the runtime fails to initialise), an empty result is
    returned and a warning is logged instead of raising an exception.
    """

    result = VideoExtractionResult(video_path=video_path)
    log = logger or logging.getLogger("microwire_video")

    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError:
        log.warning("OpenCV (cv2) is not installed; skipping video analysis for %s", video_path)
        return result

    ocr = get_paddle_ocr(log)
    if ocr is None:
        log.warning(
            "PaddleOCR is unavailable; skipping video analysis for %s", video_path
        )
        return result

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        log.warning("Unable to open video %s", video_path)
        return result

    fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
    if not fps or not math.isfinite(fps):
        fps = 25.0
    total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    frame_step = max(int(round(frame_interval * fps)), 1)

    if frame_output_dir is None:
        tmp_root = Path(tempfile.gettempdir()) / "microwire_video_frames"
        tmp_root.mkdir(parents=True, exist_ok=True)
        frame_output_dir = tmp_root

    if total_frames > 0:
        start_frame = min(int(total_frames * 0.5), max(total_frames - 1, 0))
        end_frame = int(total_frames * 0.9)
        if end_frame <= start_frame:
            start_frame = 0
            end_frame = total_frames
        if start_frame:
            capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    else:
        start_frame = 0
        end_frame = None

    frame_index = start_frame
    harvested = 0
    while True:
        if end_frame is not None and frame_index >= end_frame:
            break
        ret, frame = capture.read()
        if not ret:
            break
        if (frame_index - start_frame) % frame_step == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            enhanced = cv2.equalizeHist(gray)
            bgr_image = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
            try:
                ocr_result = ocr.ocr(bgr_image, cls=True)
            except Exception:
                log.warning(
                    "PaddleOCR failed while analysing %s; aborting video extraction",
                    video_path,
                    exc_info=True,
                )
                break
            lines: List[str] = []
            for entry in ocr_result or []:
                if not entry:
                    continue
                for detection in entry:
                    if not detection:
                        continue
                    try:
                        _, data = detection
                    except (TypeError, ValueError):
                        continue
                    if not data:
                        continue
                    token = (data[0] or "").strip()
                    if token:
                        lines.append(token)
            text = "\n".join(lines)
            result.texts.append(text)
            result.temperatures_c.extend(
                _extract_metric_candidates(text, ("temp", "temperature"), ("c", f"{MICRO_SIGN}c"))
            )
            result.underpressures.extend(
                _extract_metric_candidates(text, ("underpressure", "vacuum", "under pres", "podtlak"))
            )
            result.winding_speeds_m_per_min.extend(
                _extract_metric_candidates(text, ("winding speed",), ("m/min", "mmin"))
            )
            result.glass_feed_mm_per_min.extend(
                _extract_metric_candidates(text, ("glass feed", "glass feeding"), ("mm/min", "mmmin"))
            )
            out_path = frame_output_dir / f"{video_path.stem}_{frame_index:06d}.png"
            try:
                cv2.imwrite(str(out_path), frame)
            except Exception:
                pass
            else:
                result.frame_paths.append(out_path)
            harvested += 1
            if harvested >= max_frames:
                break
        frame_index += 1

    capture.release()
    return result


def _extract_metric_candidates(
    text: str,
    keywords: Iterable[str],
    unit_candidates: Iterable[str] = (),
) -> List[float]:
    """Return numeric candidates found on lines containing *keywords*."""

    values: List[float] = []
    lowered = text.lower()
    lines = [line for line in lowered.splitlines() if line.strip()]
    for line in lines:
        if not any(keyword in line for keyword in keywords):
            continue
        for match in _METRIC_PATTERN.finditer(line):
            raw_value = match.group(1).replace(",", ".")
            try:
                value = float(raw_value)
            except ValueError:
                continue
            if not math.isfinite(value):
                continue
            values.append(value)
    if not values and unit_candidates:
        for unit in unit_candidates:
            pattern = re.compile(rf"(-?\d+(?:[.,]\d+)?)\s*{re.escape(unit)}", re.IGNORECASE)
            for match in pattern.finditer(lowered):
                raw_value = match.group(1).replace(",", ".")
                try:
                    value = float(raw_value)
                except ValueError:
                    continue
                if math.isfinite(value):
                    values.append(value)
    return values


__all__ = [
    "VideoExtractionResult",
    "extract_video_metrics",
]





