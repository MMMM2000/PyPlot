"""Canonical test-only import for the spawned fake TMA backend."""

from data_logging.mini_dma_logger.fake_production_backend import (
    FakeProductionTmaWindow,
    create_fake_production_backend,
)

__all__ = ["FakeProductionTmaWindow", "create_fake_production_backend"]
