"""Command-line wrapper for Mini DMA predictive controller replay."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_logging.mini_dma_logger.predictive_replay import main


if __name__ == "__main__":
    raise SystemExit(main())
