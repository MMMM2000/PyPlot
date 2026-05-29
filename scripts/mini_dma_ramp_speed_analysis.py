"""Command-line wrapper for Mini DMA current-ramp speed comparison."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_logging.mini_dma_logger.ramp_speed_analysis import main


if __name__ == "__main__":
    raise SystemExit(main())
