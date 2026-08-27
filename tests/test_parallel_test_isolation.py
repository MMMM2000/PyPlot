from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_xdist_worker_uses_private_process_wide_paths() -> None:
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "").strip()
    if not worker_id:
        pytest.skip("requires pytest-xdist")

    for key in (
        "TEMP",
        "TMP",
        "TMPDIR",
        "MPLCONFIGDIR",
        "MICROWIRE_BUILDER_STORAGE_ROOT",
        "PYTEST_QSETTINGS_ROOT",
    ):
        path = Path(os.environ[key])
        assert worker_id in path.parts, f"{key} is not isolated for {worker_id}: {path}"
        assert path.is_dir(), f"{key} does not exist for {worker_id}: {path}"
