"""Create a derived AC TSV with empty-coil baseline-subtracted LCR columns."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_main():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "data_logging" / "ac_susceptibility_logger" / "offline_baseline.py"
    spec = importlib.util.spec_from_file_location("ac_lcr_offline_baseline", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.main


if __name__ == "__main__":
    raise SystemExit(_load_main()())
