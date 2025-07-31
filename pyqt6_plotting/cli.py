"""Command line interface for launching plotting GUIs."""
from __future__ import annotations
import argparse

from .stress_dependence import stress_gui
from .hsw_load_compare import load_compare_gui
from .maxion_continuous import maxion_gui
from .hsw_distribution import distribution_gui
from .temperature_sensitivity import temp_gui
from .temperature_dependence import temp_dep_gui

PLOTTERS = {
    "stress": stress_gui.main,
    "load-compare": load_compare_gui.main,
    "maxion": maxion_gui.main,
    "distribution": distribution_gui.main,
    "temperature": temp_gui.main,
    "temp-dependence": temp_dep_gui.main,
}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Launch plotting GUIs")
    parser.add_argument("tool", choices=PLOTTERS.keys(), help="Tool to launch")
    args = parser.parse_args(argv)
    PLOTTERS[args.tool]()


if __name__ == "__main__":
    main()
