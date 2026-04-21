"""Video analysis helpers for the microwire database builder."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class VideoExtractionResult:
    """Aggregated metrics extracted from a fabrication video."""

    video_path: Path
    frame_paths: List[Path] = field(default_factory=list)
    texts: List[str] = field(default_factory=list)
    temperatures_c: List[float] = field(default_factory=list)
    underpressures: List[float] = field(default_factory=list)
    winding_speeds_m_per_min: List[float] = field(default_factory=list)
    glass_feed_mm_per_min: List[float] = field(default_factory=list)

    def median_temperature(self) -> Optional[float]:
        return None

    def median_underpressure(self) -> Optional[float]:
        return None

    def median_winding_speed(self) -> Optional[float]:
        return None

    def median_glass_feed(self) -> Optional[float]:
        return None


def extract_video_metrics(
    video_path: Path,
    frame_interval: float = 30.0,
    max_frames: int = 200,
    logger: Optional[logging.Logger] = None,
    frame_output_dir: Optional[Path] = None,
) -> VideoExtractionResult:
    """Return an empty metrics summary.

    Automatic OCR extraction for fabrication videos has been retired in favour
    of manual review and entry in the builder UI.
    """

    _ = frame_interval, max_frames, frame_output_dir
    log = logger or logging.getLogger("microwire_video")
    log.info(
        "Video OCR has been removed; skipping automatic metric extraction for %s",
        video_path,
    )
    return VideoExtractionResult(video_path=video_path)


__all__ = [
    "VideoExtractionResult",
    "extract_video_metrics",
]
