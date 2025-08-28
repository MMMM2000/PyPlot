import os
import re
from pathlib import Path
from typing import Dict, List, Tuple, Any

from PyQt6 import QtWidgets

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from ..utils import save_figure
from ..backends import wants_matplotlib, wants_origin

# Defaults
CORE_BINS = 50
CORE_MIN = 3
OUTPUT_DIR = Path.cwd()
SHOW_PLOTS = True
SAVE_PLOTS = False
SAME_HIST_Y = True
SAVE_FORMAT = "png"
PNG_DPI = 1000
BACKEND = "matplotlib"

FNAME_RE = re.compile(
    r"^(?P<comp>.+?)\s+"
    r"(?P<title>\S+)\s+"
    r"(?P<sample_end>\S+[ab])\s+"
    r"(?P<anneal>\S+)\s+"
    r"(?P<load>\d+(?:,\d+)?)(?P<dir>[ab])$"
)


def parse_float_str(x: str) -> float:
    s = x.strip().replace(" ", "")
    comma_count = s.count(',')
    dot_count = s.count('.')
    if comma_count and dot_count:
        if s.rfind(',') > s.rfind('.'):
            decimal_sep, group_sep = ',', '.'
        else:
            decimal_sep, group_sep = '.', ','
        s = s.replace(group_sep, '')
        if decimal_sep != '.':
            s = s.replace(decimal_sep, '.')
    elif comma_count == 1 and dot_count == 0:
        s = s.replace(',', '.')
    elif dot_count > 1 and comma_count == 0:
        s = s.replace('.', '')
    return float(s)


def core_mask(values: np.ndarray, n_bins: int, min_count: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    counts, edges = np.histogram(values, bins=n_bins, range=(values.min(), values.max()))
    dense = np.flatnonzero(counts > min_count)
    if dense.size == 0:
        mask = np.ones_like(values, dtype=bool)
    else:
        lo, hi = dense[0], dense[-1]
        idxs = np.minimum(np.searchsorted(edges, values) - 1, len(counts) - 1)
        mask = (idxs >= lo) & (idxs <= hi)
    return mask, edges, counts


def find_auto_bins(vals: np.ndarray) -> int:
    hmin, hmax = vals.min(), vals.max()
    N = len(vals)
    for B in range(N, 1, -1):
        cnts, _ = np.histogram(vals, bins=B, range=(hmin, hmax))
        if np.all(cnts > 0):
            return B
    return max(2, min(50, N // 2))


def build_histograms(df: pd.DataFrame) -> Dict[str, Dict[str, np.ndarray]]:
    vals_tt = df["TTn"].to_numpy()
    vals_hh = df["HHn"].to_numpy()
    B_tt = find_auto_bins(vals_tt)
    B_hh = find_auto_bins(vals_hh)
    bins = min(B_tt, B_hh)
    hmin = min(vals_tt.min(), vals_hh.min())
    hmax = max(vals_tt.max(), vals_hh.max())
    cnt_tt, edges = np.histogram(vals_tt, bins=bins, range=(hmin, hmax))
    cnt_hh, _ = np.histogram(vals_hh, bins=edges)
    centers = 0.5 * (edges[:-1] + edges[1:])
    dh = edges[1] - edges[0]
    Ni_tt = np.cumsum(cnt_tt[::-1])[::-1]
    haz_tt = cnt_tt / (Ni_tt + 1e-12)
    pdf_tt = (haz_tt / dh) / (haz_tt.sum() + 1e-12)
    Ni_hh = np.cumsum(cnt_hh[::-1])[::-1]
    haz_hh = cnt_hh / (Ni_hh + 1e-12)
    pdf_hh = (haz_hh / dh) / (haz_hh.sum() + 1e-12)
    return {
        "TT": {"centers": centers, "counts": cnt_tt, "dp": pdf_tt, "dh": dh},
        "HH": {"centers": centers, "counts": cnt_hh, "dp": pdf_hh, "dh": dh},
    }


def parse_metadata(stem: str):
    m = FNAME_RE.match(stem)
    if not m:
        return None
    md = m.groupdict()
    md["load"] = float(md["load"].replace(",", "."))
    return md


def load_file(path: str):
    md = parse_metadata(Path(path).stem)
    if not md or md["dir"] != "a":
        return None, None, None, None
    raw = pd.read_csv(
        path,
        sep=";",
        header=None,
        names=["TT", "HH"],
        usecols=[0, 1],
        engine="python",
        on_bad_lines="skip",
        converters={"TT": parse_float_str, "HH": parse_float_str},
    )
    raw.dropna(subset=["TT", "HH"], inplace=True)
    raw["TTn0"] = raw["TT"] / raw["TT"].max()
    raw["HHn0"] = raw["HH"] / raw["HH"].max()
    m_t, _, _ = core_mask(raw["TTn0"].to_numpy(), CORE_BINS, CORE_MIN)
    m_h, _, _ = core_mask(raw["HHn0"].to_numpy(), CORE_BINS, CORE_MIN)
    mask = m_t & m_h
    filtered = raw.loc[mask, ["TT", "HH"]].reset_index(drop=True)
    filtered["TTn"] = filtered["TT"] / filtered["TT"].max()
    filtered["HHn"] = filtered["HH"] / filtered["HH"].max()
    return md, raw, filtered, mask


def main(files: List[str], cfg: Dict[str, Any]):
    backend = cfg.get("BACKEND", BACKEND)
    if not wants_matplotlib(backend) and wants_origin(backend):
        # Compute histograms as in Matplotlib path
        records = []
        for p in files:
            md, raw, filtered, mask = load_file(p)
            if md is None:
                print(f"Skipping {p}")
                continue
            records.append((md, raw, filtered, mask))
        if not records:
            raise SystemExit("No valid ascending-load files selected.")
        records.sort(key=lambda t: t[0]["load"])
        hist_data: Dict[float, Dict[str, Dict[str, np.ndarray]]] = {}
        for md, _raw, filt, _mask in records:
            load = md["load"]
            hist_data[load] = build_histograms(filt)
        try:
            import originpro as op
            book = op.new_book('w', lname="HSW Compare (Python)")
            book.activate()
            # One layer per load
            gp = op.new_graph(template='scatter')
            gl0 = gp[0]
            first = True
            for load, data in hist_data.items():
                gl = gl0 if first else gp.add_layer()  # type: ignore[attr-defined]
                first = False
                for col, color in (("TT", "#1f77b4"), ("HH", "#ff7f0e")):
                    h = data[col]
                    valid = h["dp"] > 0
                    x = (1 - h["centers"][valid]) ** 1.5
                    y = np.log(h["dp"][valid])
                    w = op.new_sheet('w', lname=f'{col}_{load:g}')
                    w.from_list(0, x.tolist())
                    w.from_list(1, y.tolist())
                    w.cols_axis('XY')
                    p = gl.add_plot(w, coly=1, colx=0, type='y')
                    try:
                        p.color = color
                        p.symbol_shape = 2
                    except Exception:
                        pass
                try:
                    gl.rescale()
                except Exception:
                    pass
            try:
                gp.activate()
                op.lt_exec('page.antialias=1;')
                op.lt_exec('layer -aa 1;')
                op.lt_exec('lab -xb "$\\Delta h^{3/2}$";')
                op.lt_exec('lab -yl "ln(dp/dh)";')
                op.lt_exec('legend;')
            except Exception:
                pass
            if cfg.get("hist") or cfg.get("raw"):
                print("Note: Origin output currently includes only the log-compare panels.")
        except Exception as e:
            print(f"Origin plot failed: {e}")
        return
    cfg_show = cfg["show"]
    cfg_save = cfg["save"]
    out_dir = Path(cfg["out_dir"]).expanduser()
    records = []
    for p in files:
        md, raw, filtered, mask = load_file(p)
        if md is None:
            print(f"Skipping {p}")
            continue
        records.append((md, raw, filtered, mask))
    if not records:
        raise SystemExit("No valid ascending-load files selected.")
    records.sort(key=lambda t: t[0]["load"])
    hist_data: Dict[float, Dict[str, Dict[str, np.ndarray]]] = {}
    pdf_ymax = 0.0
    hist_ymax = 0.0
    raw_ymax = 0.0
    raw_ymin = float('inf')
    for md, raw, filt, mask in records:
        load = md["load"]
        hist = build_histograms(filt)
        hist_data[load] = hist
        pdf_ymax = max(pdf_ymax, hist["TT"]["dp"].max(), hist["HH"]["dp"].max())
        hist_ymax = max(hist_ymax, hist["TT"]["counts"].max(), hist["HH"]["counts"].max())
        masked_vals = raw.loc[mask, ["TT", "HH"]].to_numpy(dtype=float)
        raw_ymax = max(raw_ymax, masked_vals.max())
        raw_ymin = min(raw_ymin, masked_vals.min())
    loads = sorted(hist_data.keys())
    nrows = len(loads)
    all_centers = np.concatenate([h["centers"] for hist in hist_data.values() for h in hist.values()])
    x_min, x_max = all_centers.min(), all_centers.max()
    fig_log, ax_log = plt.subplots(nrows=nrows, ncols=1, sharex=True, figsize=(7, 2.0 * nrows), gridspec_kw={"hspace": 0})
    fig_log.subplots_adjust(hspace=0)
    plots: List[Tuple[Figure, str]] = [(fig_log, "log_compare.png")]
    if nrows == 1:
        ax_log = [ax_log]
    log_x_vals = []
    log_y_vals = []
    for load in loads:
        for col in ("TT", "HH"):
            h = hist_data[load][col]
            valid = h["dp"] > 0
            log_x_vals.append((1 - h["centers"][valid])**1.5)
            log_y_vals.append(np.log(h["dp"][valid]))
    log_x_all = np.concatenate(log_x_vals)
    log_y_all = np.concatenate(log_y_vals)
    lx_min = -1e-5
    lx_max = log_x_all.max()
    ly_min, ly_max = log_y_all.min(), log_y_all.max()
    lx_pad = (lx_max - lx_min) * 0.05
    ly_pad = (ly_max - ly_min) * 0.05
    lx_upper = lx_max + lx_pad
    ly_lower = ly_min - ly_pad
    ly_upper = ly_max + ly_pad
    for ax, load in zip(ax_log, loads):
        for col in ("TT", "HH"):
            if cfg[col]:
                h = hist_data[load][col]
                valid = h["dp"] > 0
                ax.plot((1 - h["centers"][valid])**1.5,
                        np.log(h["dp"][valid]), '-o', markersize=4, label=col)
        ax.set_xlim(lx_min, lx_upper)
        ax.set_ylim(ly_lower, ly_upper)
        ax.grid(True, linestyle="--", alpha=0.3)
        ax.text(0.02, 0.05, f"{load:g} g", transform=ax.transAxes, va="bottom")
        if cfg["TT"] and cfg["HH"]:
            ax.legend(fontsize="small")
    ax_log[-1].set_xlabel(r"$\Delta h^{3/2}$")
    for ax in ax_log[:-1]:
        ax.tick_params(axis="x", bottom=False, labelbottom=False)
    ax_log[0].set_title("Combined ln(dp/dh) vs reduced switching field")
    fig_log.supylabel("ln(dp/dh)")

    if cfg["hist"]:
        fig_h, ax_h = plt.subplots(nrows=nrows, ncols=1, sharex=True, figsize=(7, 2.0 * nrows), gridspec_kw={"hspace": 0})
        fig_h.subplots_adjust(hspace=0)
        if nrows == 1:
            ax_h = [ax_h]
        for ax, load in zip(ax_h, loads):
            data = hist_data[load]
            width = data["TT"]["dh"] * 0.4
            centers = data["TT"]["centers"]
            if cfg["TT"]:
                ax.bar(centers - width / 2, data["TT"]["counts"], width=width, label="TT", alpha=0.6)
            if cfg["HH"]:
                ax.bar(centers + width / 2, data["HH"]["counts"], width=width, label="HH", alpha=0.6)
            ylim = hist_ymax if cfg["share_y"] else max(data["TT"]["counts"].max(), data["HH"]["counts"].max())
            ax.set_ylim(0, ylim * 1.05)
            ax.set_xlim(x_min, x_max)
            ax.grid(True, linestyle="--", alpha=0.3)
            ax.text(0.02, 0.85, f"{load:g} g", transform=ax.transAxes, va="top")
            if cfg["TT"] and cfg["HH"]:
                ax.legend(fontsize="small")
        ax_h[-1].set_xlabel("h = H/Hsw,max")
        for ax in ax_h[:-1]:
            ax.tick_params(axis="x", bottom=False, labelbottom=False)
        ax_h[0].set_title("Histogram of Hsw vs load")
        fig_h.supylabel("Counts")
        plots.append((fig_h, "hist_compare"))

    if cfg["raw"]:
        fig_r, ax_r = plt.subplots(nrows=nrows, ncols=1, sharex=True, figsize=(7, 2.0 * nrows), gridspec_kw={"hspace": 0})
        fig_r.subplots_adjust(hspace=0)
        if nrows == 1:
            ax_r = [ax_r]
        for (md, raw, _, mask), ax in zip(records, ax_r):
            load = md["load"]
            idx = np.arange(len(raw)) + 1
            ax.scatter(idx[mask], raw["TT"][mask], s=2, label="TT inlier")
            ax.scatter(idx[mask], raw["HH"][mask], s=2, label="HH inlier", color="C1")
            trimmed = ~mask
            if trimmed.any():
                ax.scatter(idx[trimmed], raw["TT"][trimmed], s=10, c="r", marker="x", label="TT trimmed")
                ax.scatter(idx[trimmed], raw["HH"][trimmed], s=10, c="m", marker="x", label="HH trimmed")
            ax.grid(True, linestyle="--", alpha=0.3)
            ax.text(0.02, 0.85, f"{load:g} g", transform=ax.transAxes, va="top")
            if cfg["TT"] and cfg["HH"]:
                ax.legend(fontsize="x-small")
        ax_r[-1].set_xlabel("Index")
        for ax in ax_r[:-1]:
            ax.tick_params(axis="x", bottom=False, labelbottom=False)
        fig_r.supylabel("Switching Field")
        ax_r[0].set_title("Raw Hsw vs load (Histogram-Core filtered)")
        plots.append((fig_r, "raw_compare"))

    if cfg_save:
        out_dir.mkdir(parents=True, exist_ok=True)
        save_figure(fig_log, out_dir / "log_compare", SAVE_FORMAT, PNG_DPI)
        if cfg["hist"]:
            save_figure(fig_h, out_dir / "hist_compare", SAVE_FORMAT, PNG_DPI)
        if cfg["raw"]:
            save_figure(fig_r, out_dir / "raw_compare", SAVE_FORMAT, PNG_DPI)

    if cfg_show:
        plt.show()
    else:
        plt.close("all")

    if (not cfg_save) and plots and QtWidgets.QApplication.instance() is not None:
        reply = QtWidgets.QMessageBox.question(
            None,
            "Save Plots",
            "Save generated plots?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            out = QtWidgets.QFileDialog.getExistingDirectory(None, "Select output directory", str(out_dir))
            if out:
                os.makedirs(out, exist_ok=True)
                for fig, fname in plots:
                    base = os.path.join(out, fname)
                    save_figure(fig, base, SAVE_FORMAT, PNG_DPI)
