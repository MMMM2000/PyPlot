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


def _worker_path(path: Path, worker_id: str) -> Path:
    if worker_id and path.name != worker_id:
        return path / worker_id
    return path


def _configure_isolated_test_paths() -> None:
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "").strip()
    base_tmp = (
        os.environ.get("TMPDIR")
        or os.environ.get("TEMP")
        or os.environ.get("TMP")
        or tempfile.gettempdir()
    )
    tmp_root = Path(base_tmp)
    isolated_temp = os.environ.get("PYPLOT_TEST_TEMP_ISOLATED") == "1"
    if os.name == "nt" and not isolated_temp and len(str(tmp_root.resolve())) > 60:
        tmp_root = Path("C:/tmp")
    if not isolated_temp and tmp_root.name != "pyplot-tests":
        tmp_root = tmp_root / "pyplot-tests"
    tmp_root = _worker_path(tmp_root, worker_id)
    tmp_root.mkdir(parents=True, exist_ok=True)
    for key in ("TMPDIR", "TEMP", "TMP"):
        os.environ[key] = str(tmp_root)

    isolated_roots = {
        "MICROWIRE_BUILDER_STORAGE_ROOT": tmp_root / "microwire-data-builder",
        "PYTEST_QSETTINGS_ROOT": tmp_root / "qsettings",
        "MPLCONFIGDIR": tmp_root / "matplotlib",
    }
    for key, fallback in isolated_roots.items():
        configured = os.environ.get(key)
        root = _worker_path(Path(configured) if configured else fallback, worker_id)
        root.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(root)
    tempfile.tempdir = str(tmp_root)


def _configure_qsettings_isolation() -> None:
    raw_root = os.environ.get("PYTEST_QSETTINGS_ROOT", "").strip()
    if not raw_root:
        raw_root = str(
            Path.cwd()
            / "artifacts"
            / "pytest-qsettings"
            / f"process-{os.getpid()}"
        )
        os.environ["PYTEST_QSETTINGS_ROOT"] = raw_root
    from PyQt6 import QtCore

    settings_root = Path(raw_root)
    settings_root.mkdir(parents=True, exist_ok=True)
    ini_format = QtCore.QSettings.Format.IniFormat
    QtCore.QSettings.setDefaultFormat(ini_format)
    QtCore.QSettings.setPath(
        ini_format,
        QtCore.QSettings.Scope.UserScope,
        str(settings_root / "user"),
    )
    QtCore.QSettings.setPath(
        ini_format,
        QtCore.QSettings.Scope.SystemScope,
        str(settings_root / "system"),
    )


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
        _configure_isolated_test_paths()
    except Exception:
        pass
    _configure_qsettings_isolation()


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
