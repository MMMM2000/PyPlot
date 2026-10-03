"""Canonical source entry point for the TMA Logger."""

from __future__ import annotations

from data_logging.mini_dma_logger.mini_dma_logger import MainWindow, main

__all__ = ["MainWindow", "main"]


if __name__ == "__main__":
    main()
