"""
Python back‑end for the Stress Dependence HTML app.

This module exposes a single function, ``run_from_lt``, which is called from
LabTalk via the ``run_stress.ogs`` script.  It receives parameters as strings
and numbers, performs any necessary parsing, loads measurement files,
computes baseline‑corrected raw and mean curves, and produces Origin graphs
via functions defined in the existing ``plotting.stress_dependence.core``
module from your repository.  By using your original Python code for
processing, this file avoids reimplementing complex logic and ensures
consistency with your existing workflow.

To locate and import the ``core`` module, this script searches relative to
its own location for a parent directory containing a ``plotting`` package.
This allows the app to function regardless of whether the repository is
named ``microwire-data-plotting-logging``, ``python_plot-main``, or
something else, as long as the ``plotting/stress_dependence/core.py`` file
exists two levels above this script.

This file is intended to run inside Origin's embedded Python environment,
which provides access to the ``originpro`` package for graph creation.
Ensure that ``originpro`` is installed and available within Origin.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import List


def _find_repo_root() -> Path:
    """Find the repository root by looking for a 'plotting' directory.

    Starting from this file's directory, walk up a few parent directories
    until a directory containing ``plotting/stress_dependence/core.py`` is
    found.  Raises RuntimeError if not found.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "plotting" / "stress_dependence" / "core.py"
        if cand.exists():
            return parent
    raise RuntimeError(
        "Could not locate repository root containing plotting/stress_dependence/core.py"
    )


def _load_core_module() -> object:
    """Dynamically import the core module from the plotting package."""
    repo_root = _find_repo_root()
    core_path = repo_root / "plotting" / "stress_dependence" / "core.py"
    spec = importlib.util.spec_from_file_location("stress_core", core_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create spec for {core_path}")
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)  # type: ignore[assignment]
    return core


def _parse_bool(value: str | int | float) -> bool:
    """Convert various truthy values to a boolean."""
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def run_from_lt(
    files_str: str,
    vars_str: str,
    baseline: str,
    show: int | str,
    save: int | str,
    outdir: str,
    otp: str,
    png: str,
) -> None:
    """Entry point called from LabTalk.

    Parameters
    ----------
    files_str:
        Pipe ('|') separated list of measurement file paths.
    vars_str:
        Comma separated list of variables to plot (e.g., ``"sum,dT,T1,T2"``).
    baseline:
        Baseline mode: ``"first"`` or ``"min"``.
    show:
        Show plots on screen (1 or 0).
    save:
        Save plots to disk (1 or 0).
    outdir:
        Directory where plots should be saved when ``save`` is true.  If
        empty, the default output directory from the core module will be
        used.
    otp:
        Unused placeholder for an Origin graph template.  Currently ignored.
    png:
        Unused placeholder for exporting a combined PNG.  Currently ignored.
    """
    # Convert parameters
    files = [f for f in files_str.split("|") if f]
    variables = [v.strip() for v in vars_str.split(",") if v.strip()]
    if not files:
        raise ValueError("No input files provided")
    if not variables:
        raise ValueError("No variables selected for plotting")
    baseline = baseline.strip() or "first"
    do_show = _parse_bool(show)
    do_save = _parse_bool(save)
    outdir = outdir.strip()

    # Load the core module
    core = _load_core_module()

    # Patch global options in the core module to match user selections
    # Baseline mode ('first' or 'min')
    if hasattr(core, "BASELINE_MODE"):
        setattr(core, "BASELINE_MODE", baseline)
    # Whether to show/save plots
    if hasattr(core, "SHOW_PLOTS"):
        setattr(core, "SHOW_PLOTS", bool(do_show))
    if hasattr(core, "SAVE_PLOTS"):
        setattr(core, "SAVE_PLOTS", bool(do_save))
    # Output directory override
    if outdir:
        if hasattr(core, "OUTPUT_DIR"):
            setattr(core, "OUTPUT_DIR", outdir)
    # We always want to use the Origin backend for plotting.  Force it by
    # overriding the BACKEND global if present.  The core module will then
    # call plot_variable_origin for each plot.
    if hasattr(core, "BACKEND"):
        setattr(core, "BACKEND", "origin")

    # Load data from all files.  This returns a DataFrame with metadata
    # columns (composition, title, sample_end, anneal, dir, load, etc.) and
    # numeric columns (T1, T2, dT, sum).  It also drops invalid files.
    data = core.load_data(files)  # type: ignore[no-untyped-call]

    # Group by composition/title/sample_end/anneal, then plot each variable
    groups = data.groupby(["composition", "title", "sample_end", "anneal"])
    for _, grp in groups:
        for var in variables:
            try:
                # core.plot_variable_origin handles baseline subtraction,
                # jitter, means, processed curves, and graph creation via
                # originpro.  It also sets titles and axis labels.
                core.plot_variable_origin(grp, var)  # type: ignore[no-untyped-call]
            except Exception as e:
                # Print to Origin's results window for debugging
                print(f"Plot failed for {var}: {e}")

    # If the user requested to save plots, core.plot_variable_origin will
    # automatically save each graph as part of the group.  Additional
    # export of a combined PNG via the 'png' argument is not implemented
    # in this version.
    return None