from __future__ import annotations

import sys
from pathlib import Path
import os
import tempfile

import pytest


def _configure_qt_headless_defaults() -> None:
    # Default to offscreen Qt so GUI smoke tests run in headless shells.
    if os.environ.get("PYTEST_GUI_HEADLESS", "1") != "0":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("PYTEST_QT_API", "pyqt6")


def pytest_configure() -> None:
    """Ensure the bundled Veusz sources are importable for the selftests."""

    _configure_qt_headless_defaults()

    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    veusz_path = root / "veusz-master"
    if veusz_path.exists() and str(veusz_path) not in sys.path:
        sys.path.insert(0, str(veusz_path))
    try:
        base_tmp = (
            os.environ.get("TMPDIR")
            or os.environ.get("TEMP")
            or os.environ.get("TMP")
            or tempfile.gettempdir()
        )
        tmp_root = Path(base_tmp)
        if tmp_root.name != "pyplot-tests":
            tmp_root = tmp_root / "pyplot-tests"
        tmp_root.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("TMPDIR", str(tmp_root))
        os.environ.setdefault("TEMP", str(tmp_root))
        os.environ.setdefault("TMP", str(tmp_root))
        tempfile.tempdir = str(tmp_root)
    except Exception:
        pass


@pytest.fixture(scope="session")
def qapp_args() -> list[str]:
    platform = os.environ.get("QT_QPA_PLATFORM", "").strip()
    if platform:
        return ["-platform", platform]
    return []
