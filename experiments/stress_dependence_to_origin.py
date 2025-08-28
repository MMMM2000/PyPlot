# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportUnboundVariable=false
r"""
stress_dependence_to_origin.py
------------------------------
Origin version of your "stress dependence" plot.

Behavior
--------
- If you pass arguments: accepts files, folders, and globs (folders are recursive).
- If you pass NO arguments: opens a MULTI-FILE picker; if you cancel, asks for a FOLDER.
- Builds one graph per (composition, title, sample_end, anneal).
- Saves ONE .opju and leaves Origin OPEN.

Quick start
-----------
pip install originpro pandas numpy

Examples
--------
python stress_dependence_to_origin.py "C:\\data\\*.txt"
python stress_dependence_to_origin.py "C:\\data\\folder_with_txt"
"""

from __future__ import annotations

import argparse
import glob
import os
import re
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import originpro as op

# -------------------------- Defaults / knobs --------------------------
BASELINE_MODE = "first"          # "first" or "min"
OFFSET = 0.5                     # half-shift for a/b means on X
JITTER_SPAN = 0.5                # raw jitter ± range
RAW_COLORS = {"a": "#45A1D6", "b": "#F09C67"}
MEAN_COLORS = {"a": "#00306E", "b": "#965308"}
RAW_SYMBOL_SIZE = 1              # Origin points
MEAN_SYMBOL_SIZE = 8             # Origin points
MEAN_LINE_WIDTH = 3
OUT_DIR = Path("./origin_output")
SHOW_ORIGIN = True               # show Origin UI by default
# ---------------------------------------------------------------------

# Correct filename parsing (raw string with single backslashes in regex constructs)
FNAME_RE = re.compile(
    r"^(?P<composition>.+?)\s+"
    r"(?P<title>\S+)\s+"
    r"(?P<sample_end>\S+[ab])\s+"
    r"(?P<anneal>\S+)\s+"
    r"(?P<load>\d+(?:,\d+)?)(?P<dir>[ab])$"
)

LABELS = {
    "T1": "T1 (µs)",
    "T2": "T2 (µs)",
    "dT": "ΔT (µs)",
    "sum": "T1+T2 (µs)",
}

def parse_metadata(stem: str) -> Dict[str, str] | None:
    m = FNAME_RE.match(stem)
    if not m:
        return None
    d = m.groupdict()
    d["load"] = float(d["load"].replace(",", "."))
    return d  # composition, title, sample_end, anneal, load, dir


def load_data(files: List[str]) -> pd.DataFrame:
    files = sorted(files)
    if not files:
        raise FileNotFoundError("No files selected")
    dfs: List[pd.DataFrame] = []
    for fn in files:
        md = parse_metadata(Path(fn).stem)
        if md is None:
            print(f"Skipping (name parse failed): {fn}")
            continue
        try:
            df = pd.read_csv(
                fn, sep=";", header=None,
                names=["T1", "T2", "dT", "sum"],
                engine="python", on_bad_lines="skip",
            )
        except Exception as e:
            print(f"Skipping (read error): {fn} -> {e}")
            continue
        df["filename"] = Path(fn).name
        df["line"] = np.arange(len(df), dtype=int)
        for c in ["T1", "T2", "dT", "sum"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        for k, v in md.items():
            df[k] = v
        dfs.append(df)
    if not dfs:
        raise SystemExit("No valid files selected. Check filenames.")
    return pd.concat(dfs, ignore_index=True)


def compute_tables(grp: pd.DataFrame, var: str):
    # Compute means with explicit DataFrame typing for static checkers
    means: pd.DataFrame = (
        grp.groupby(["dir", "load"], as_index=False)[var].mean()
    )
    first = float(means["load"].min())
    if BASELINE_MODE == "first":
        base = float(means.loc[(means["dir"] == "a") & (means["load"] == first), var].iloc[0])
    else:
        base = float(means.loc[means["dir"] == "a", var].min())

    # jittered raw points (offset for a/b to avoid overlap)
    rng = np.random.default_rng(0)
    x_center = grp["load"] + grp["dir"].map({"a": -OFFSET, "b": +OFFSET})
    x = x_center + rng.uniform(-JITTER_SPAN, +JITTER_SPAN, size=len(grp))
    y = grp[var] - base

    raw = pd.DataFrame({"X": x, "Y": y, "dir": grp["dir"].to_numpy()})
    raw_a = raw[raw["dir"] == "a"][ ["X", "Y"] ].reset_index(drop=True)
    raw_b = raw[raw["dir"] == "b"][ ["X", "Y"] ].reset_index(drop=True)

    # MEANS: X exactly at the applied load (no +/- OFFSET)
    means = means.sort_values(["dir", "load"]).copy()
    means["X"] = means["load"]
    means["Y"] = means[var] - base
    mean_a = means[means["dir"] == "a"][ ["X", "Y"] ].reset_index(drop=True)
    mean_b = means[means["dir"] == "b"][ ["X", "Y"] ].reset_index(drop=True)
    return raw_a, raw_b, mean_a, mean_b


def nice_title(grp: pd.DataFrame, var: str) -> str:
    comp, title, samp, anneal = (
        grp["composition"].iat[0],
        grp["title"].iat[0],
        grp["sample_end"].iat[0],
        grp["anneal"].iat[0],
    )
    return f"{comp} {title} {samp} {anneal} — {LABELS[var]}"


def build_origin_graph(raw_a, raw_b, mean_a, mean_b, title, var):
    if SHOW_ORIGIN:
        op.set_show()

    # Create and ACTIVATE a workbook; new_sheet() adds to active book
    book = op.new_book('w', lname="Stress Dependence (Python)")
    book.activate()

    def push_xy(df: pd.DataFrame, lname: str, legend_label: str):
        wks = op.new_sheet('w', lname=lname)
        wks.from_df(df)  # type: ignore[attr-defined]
        wks.cols_axis('XY')  # type: ignore[attr-defined]
        # Set Long Name of Y column so legend shows nice labels
        try:
            wks.activate()
            op.lt_exec(f'wks.col2.lname$ = "{legend_label}";')
        except Exception:
            pass
        return wks

    w_raw_a  = push_xy(raw_a,  "raw_a",  "a raw")
    w_raw_b  = push_xy(raw_b,  "raw_b",  "b raw")
    w_mean_a = push_xy(mean_a, "mean_a", "a mean")
    w_mean_b = push_xy(mean_b, "mean_b", "b mean")

    gp = op.new_graph(template='scatter')
    gl = gp[0]

    # Add with explicit plot types: raw=scatter, mean=line+symbol
    # Using Origin's API: 's' -> Scatter, 'y' -> Line Symbols
    p_raw_a  = gl.add_plot(w_raw_a,  coly='B', colx='A', type='s')
    p_raw_b  = gl.add_plot(w_raw_b,  coly='B', colx='A', type='s')
    p_mean_a = gl.add_plot(w_mean_a, coly='B', colx='A', type='y')
    p_mean_b = gl.add_plot(w_mean_b, coly='B', colx='A', type='y')

    # Style using supported Plot properties/commands
    try:
        # Colors
        p_raw_a.color = RAW_COLORS["a"]; p_raw_b.color = RAW_COLORS["b"]
        p_mean_a.color = MEAN_COLORS["a"]; p_mean_b.color = MEAN_COLORS["b"]
        # Symbol sizes
        p_raw_a.symbol_size = RAW_SYMBOL_SIZE; p_raw_b.symbol_size = RAW_SYMBOL_SIZE
        p_mean_a.symbol_size = MEAN_SYMBOL_SIZE; p_mean_b.symbol_size = MEAN_SYMBOL_SIZE
        # Symbols filled to match Matplotlib markers
        try:
            p_mean_a.symbol_interior = 1; p_mean_b.symbol_interior = 1
        except Exception:
            pass
        # Ensure raw has NO connecting line; set mean line widths
        p_raw_a.set_cmd('-l 0', '-d 0'); p_raw_b.set_cmd('-l 0', '-d 0')
        p_mean_a.set_cmd(f'-w {MEAN_LINE_WIDTH}')
        p_mean_b.set_cmd(f'-w {MEAN_LINE_WIDTH}')
    except Exception:
        pass

    try:
        gl.rescale()
        gp.activate()

        # Anti-aliasing (page + layer)
        for cmd in ['page.antialias=1;', 'layer -aa 1;']:
            try: op.lt_exec(cmd)
            except Exception: pass

        # Axis labels
        op.lt_exec('lab -xb "Applied load (g)";')
        op.lt_exec('lab -yl "{}";'.format(LABELS[var]))
        # Clear top X and right Y axis titles just in case
        try:
            op.lt_exec('lab -xt "";')
            op.lt_exec('lab -yr "";')
        except Exception:
            pass

        # Hide top X and right Y axes (ticks+labels)
        for cmd in ['layer.x.showAxes=1;', 'layer.y.showAxes=1;', 'layer.x.topticks=0;', 'layer.y.rightticks=0;']:
            try: op.lt_exec(cmd)
            except Exception: pass
        # Then enforce via axis switches if needed (some templates override showAxes)
        for cmd in ['axis -t 0;', 'axis -r 0;']:
            try: op.lt_exec(cmd)
            except Exception: pass

        # Legend (from Long Names), place bottom-right
        op.lt_exec('legend; legend -r;')
        # Try common bottom-right placements; ignore errors if not supported
        for cmd in ['legend -p 5;', 'legend -p br;']:
            try: op.lt_exec(cmd)
            except Exception: pass

        # Title
        op.lt_exec('title -s "{}";'.format(title.replace('"', "'")))
    except Exception:
        pass

    return gp


def run(files: List[str], var: str = "sum", project_path: Path | None = None):
    df = load_data(files)
    groups = df.groupby(["composition", "title", "sample_end", "anneal"])

    if SHOW_ORIGIN:
        op.set_show()

    for _, grp in groups:
        raw_a, raw_b, mean_a, mean_b = compute_tables(grp, var)
        title = nice_title(grp, var)
        _ = build_origin_graph(raw_a, raw_b, mean_a, mean_b, title, var)

    if project_path is not None:
        project_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            op.save(str(project_path))
        except Exception as e:
            print(f"Save project failed: {e}")
    # Leave Origin OPEN


def _expand_inputs(args_files: List[str]) -> List[str]:
    files: List[str] = []
    for pat in args_files:
        p = Path(pat)
        if p.is_dir():
            for root, _dirs, names in os.walk(p):
                for name in names:
                    if name.lower().endswith(".txt"):
                        files.append(str(Path(root) / name))
        elif any(ch in pat for ch in "*?[]"):
            files.extend(glob.glob(pat))
        elif p.is_file():
            files.append(str(p))
    return sorted(set(files))


def _gui_pick_files_or_folder() -> List[str]:
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk(); root.withdraw()
        paths = filedialog.askopenfilenames(
            title="Select stress files (*.txt)",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        files = list(paths)
        if files:
            return files
        folder = filedialog.askdirectory(title="Select folder with stress files")
        if folder:
            out: List[str] = []
            for r, _d, names in os.walk(folder):
                for n in names:
                    if n.lower().endswith(".txt"):
                        out.append(os.path.join(r, n))
            return sorted(out)
        return []
    except Exception:
        try:
            sel = op.file_dialog("*.txt")
            if isinstance(sel, str):
                return [sel]
            return list(sel)
        except Exception:
            return []


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="Files/folders/globs (e.g., C:\\data\\*.txt)")
    ap.add_argument("--var", choices=["sum", "dT", "T1", "T2"], default="sum", help="Variable to plot")
    ap.add_argument("--baseline", choices=["first", "min"], default="first", help="Baseline mode")
    ap.add_argument("--out", default=str(OUT_DIR), help="Output directory")
    ap.add_argument("--hide", action="store_true", help="Hide Origin while running")
    args = ap.parse_args()

    # Apply args (no 'global' here)
    BASELINE_MODE = args.baseline
    OUT_DIR = Path(args.out)
    SHOW_ORIGIN = not args.hide

    files = _expand_inputs(args.files)
    if not files:
        files = _gui_pick_files_or_folder()
    if not files:
        raise SystemExit("No files selected.")

    project_path = OUT_DIR / "stress_dependence_batch.opju"
    run(files, var=args.var, project_path=project_path)
