from __future__ import annotations

import os
import re
from pathlib import Path

from PyQt6 import QtCore


def create_qsettings(organization: str, application: str) -> QtCore.QSettings:
    """Create application settings, using a disposable INI file in tests.

    On Windows the organization/application constructor always selects the
    registry-backed native format.  That bypasses ``setDefaultFormat`` and
    makes isolated GUI tests either mutate real user preferences or fail when
    registry access is unavailable.  Production keeps the native store; test
    runs can opt into a workspace-local INI store with
    ``PYTEST_QSETTINGS_ROOT``.
    """

    raw_root = os.environ.get("PYTEST_QSETTINGS_ROOT", "").strip()
    if not raw_root:
        return QtCore.QSettings(organization, application)

    root = Path(raw_root)
    root.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{organization}-{application}")
    return QtCore.QSettings(
        str(root / f"{safe_name}.ini"),
        QtCore.QSettings.Format.IniFormat,
    )
