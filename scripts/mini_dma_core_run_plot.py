"""Generate a phone-friendly TMA per-run core plot."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_logging.mini_dma_logger.run_core_plot import main


if __name__ == "__main__":
    raise SystemExit(main())
