from __future__ import annotations

import sys
from pathlib import Path
import os
import tempfile


def pytest_configure() -> None:
    """Ensure the bundled Veusz sources are importable for the selftests."""

    root = Path(__file__).resolve().parent.parent
    veusz_path = root / "veusz-master"
    if veusz_path.exists() and str(veusz_path) not in sys.path:
        sys.path.insert(0, str(veusz_path))
    try:
        tmp_root = Path("/tmp/pyplot-tests")
        tmp_root.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("TMPDIR", str(tmp_root))
        os.environ.setdefault("TEMP", str(tmp_root))
        os.environ.setdefault("TMP", str(tmp_root))
        tempfile.tempdir = str(tmp_root)
    except Exception:
        pass
