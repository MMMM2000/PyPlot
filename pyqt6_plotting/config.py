from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "default_config.json"


def load_config(path: str | None = None) -> Dict[str, Any]:
    """Load JSON configuration values.

    Parameters
    ----------
    path:
        Optional path to a JSON configuration file. If omitted, the default
        config packaged with the library is used.
    """
    cfg_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    with open(cfg_path, "r", encoding="utf-8") as fh:
        return json.load(fh)
