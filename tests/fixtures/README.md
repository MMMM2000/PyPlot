# Test Fixtures

This directory stores stable, minimal input fixtures and expected outputs used by
parser and smoke tests.

Layout:
- `dma_iso_stress/`: TA DMA IsoStress text fixture + expected parsed output.
- `vsm_temperature_scan/`: VSM scan text fixture + expected parsed output.

Keep fixtures deterministic and intentionally small so tests stay fast.
