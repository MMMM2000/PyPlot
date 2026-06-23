"""Command-line wrapper for the Mini DMA full-run software simulator."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_logging.mini_dma_logger.full_run_simulator import main


if __name__ == "__main__":
    raise SystemExit(main())
