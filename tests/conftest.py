from __future__ import annotations

import sys
from pathlib import Path


def pytest_configure() -> None:
    """Ensure the bundled Veusz sources are importable for the selftests."""

    root = Path(__file__).resolve().parent.parent
    veusz_path = root / "veusz-master"
    if veusz_path.exists() and str(veusz_path) not in sys.path:
        sys.path.insert(0, str(veusz_path))
