"""Command-line wrapper for TMA fatigue/current-sweep learning summaries."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_logging.mini_dma_logger.fatigue_learning import main


if __name__ == "__main__":
    raise SystemExit(main())
