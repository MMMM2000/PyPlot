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
        if os.name == "nt" and len(str(tmp_root.resolve())) > 60:
            tmp_root = Path("C:/tmp")
        if tmp_root.name != "pyplot-tests":
            tmp_root = tmp_root / "pyplot-tests"
        tmp_root.mkdir(parents=True, exist_ok=True)
        os.environ["TMPDIR"] = str(tmp_root)
        os.environ["TEMP"] = str(tmp_root)
        os.environ["TMP"] = str(tmp_root)
        os.environ.setdefault(
            "MICROWIRE_BUILDER_STORAGE_ROOT",
            str(tmp_root / "microwire-data-builder"),
        )
        tempfile.tempdir = str(tmp_root)
    except Exception:
        pass


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if os.name != "nt":
        return
    # Windows/Qt can crash natively if TMA starts after the Microwire GUI tests.
    original_index = {item: index for index, item in enumerate(items)}
    microwire_index = min(
        (
            index
            for index, item in enumerate(items)
            if item.path.as_posix().endswith("tests/test_microwire_data_builder.py")
            or item.path.as_posix().endswith("tests/test_microwire_eda.py")
        ),
        default=None,
    )
    if microwire_index is None:
        return

    def _sort_key(item: pytest.Item) -> float:
        path = item.path.as_posix()
        if path.endswith("tests/test_mini_dma_logger.py"):
            return float(microwire_index) - 0.5
        return float(original_index[item])

    items.sort(key=_sort_key)


@pytest.fixture(scope="session")
def qapp_args() -> list[str]:
    platform = os.environ.get("QT_QPA_PLATFORM", "").strip()
    if platform:
        return ["-platform", platform]
    return []
