from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any, Dict

# Name of the bundled configuration file. ``importlib.resources`` is used to
# access the file so it can be located whether the project is run from source
# or from a PyInstaller one-file bundle where files are unpacked to a temporary
# directory.  Relying on a normal filesystem path fails in the latter case when
# the data file is not copied next to the module.
_DEFAULT_CONFIG_NAME = "default_config.json"


def load_config(path: str | None = None) -> Dict[str, Any]:
    """Load JSON configuration values.

    Parameters
    ----------
    path:
        Optional path to a JSON configuration file. If omitted, the default
        config packaged with the library is used.
    """
    cfg_path = Path(path) if path else None
    if cfg_path is not None:
        with cfg_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    # Load the configuration shipped within the ``plotting`` package using
    # ``importlib.resources``.  This works both from a regular installation and
    # when the package has been bundled by tools like PyInstaller.
    with resources.open_text(__package__, _DEFAULT_CONFIG_NAME, encoding="utf-8") as fh:
        return json.load(fh)
