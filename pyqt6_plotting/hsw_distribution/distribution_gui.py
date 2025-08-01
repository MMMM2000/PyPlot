from __future__ import annotations
import sys
import os
import pathlib
from typing import List, Dict, Any

from PyQt6 import QtWidgets
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

if __package__ is None or __package__ == "":
    # When executed directly, ensure the repository root is on sys.path so the
    # ``pyqt6_plotting`` package can be imported correctly.
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from pyqt6_plotting.utils import apply_system_theme, select_files_or_folder
else:
    from ..utils import apply_system_theme, select_files_or_folder


def ask_files() -> List[str]:
    paths = select_files_or_folder()
    if not paths:
        sys.exit("No files selected.")
    return list(paths)


def ask_options() -> Dict[str, Any] | None:
    dialog = QtWidgets.QDialog()
    dialog.setWindowTitle("Hsw Distribution Settings")
    layout = QtWidgets.QGridLayout(dialog)

    raw_cb = QtWidgets.QCheckBox("Raw TT/HH vs Index"); raw_cb.setChecked(True)
    trim_cb = QtWidgets.QCheckBox("Show trimmed data"); trim_cb.setChecked(True)
    hist_cb = QtWidgets.QCheckBox("Counts Histogram"); hist_cb.setChecked(True)
    ind_cb = QtWidgets.QCheckBox("Individual ln(dp/dh)"); ind_cb.setChecked(True)
    comb_cb = QtWidgets.QCheckBox("Combined ln(dp/dh)"); comb_cb.setChecked(True)

    bin_group = QtWidgets.QGroupBox("Final Histogram Binning")
    bin_layout = QtWidgets.QGridLayout(bin_group)
    auto_rb = QtWidgets.QRadioButton("Automatic"); auto_rb.setChecked(True)
    manual_rb = QtWidgets.QRadioButton("Manual Δh =")
    width_edit = QtWidgets.QDoubleSpinBox(); width_edit.setDecimals(6); width_edit.setRange(1e-6, 1.0); width_edit.setSingleStep(1e-4); width_edit.setValue(1e-4)
    share_bins_cb = QtWidgets.QCheckBox("Shared bins TT/HH")
    bin_layout.addWidget(auto_rb, 0, 0)
    bin_layout.addWidget(manual_rb, 1, 0)
    bin_layout.addWidget(width_edit, 1, 1)
    bin_layout.addWidget(share_bins_cb, 2, 0, 1, 2)

    core_group = QtWidgets.QGroupBox("Histogram-Core Filter")
    core_layout = QtWidgets.QGridLayout(core_group)
    bins_spin = QtWidgets.QSpinBox(); bins_spin.setRange(1, 9999); bins_spin.setValue(50)
    min_spin = QtWidgets.QSpinBox(); min_spin.setRange(1, 9999); min_spin.setValue(3)
    core_layout.addWidget(QtWidgets.QLabel("n_bins:"), 0, 0)
    core_layout.addWidget(bins_spin, 0, 1)
    core_layout.addWidget(QtWidgets.QLabel("min_count:"), 1, 0)
    core_layout.addWidget(min_spin, 1, 1)

    naming_group = QtWidgets.QGroupBox("Column naming")
    naming_layout = QtWidgets.QVBoxLayout(naming_group)
    tthh_rb = QtWidgets.QRadioButton("TT && HH"); tthh_rb.setChecked(True)
    t1t2_rb = QtWidgets.QRadioButton("T1 && T2")
    naming_layout.addWidget(tthh_rb)
    naming_layout.addWidget(t1t2_rb)

    run_btn = QtWidgets.QPushButton("Run")
    run_btn.clicked.connect(dialog.accept)

    opts_layout = QtWidgets.QVBoxLayout()
    for w in (raw_cb, trim_cb, hist_cb, ind_cb, comb_cb):
        opts_layout.addWidget(w)
    opts_widget = QtWidgets.QWidget()
    opts_widget.setLayout(opts_layout)

    layout.addWidget(opts_widget, 0, 0)
    layout.addWidget(bin_group, 0, 1)
    layout.addWidget(core_group, 1, 1)
    layout.addWidget(naming_group, 1, 0)
    layout.addWidget(run_btn, 2, 0, 1, 2)
    dialog.setLayout(layout)

    if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        return None

    return {
        "raw": raw_cb.isChecked(),
        "show_trimmed": trim_cb.isChecked(),
        "hist": hist_cb.isChecked(),
        "ind_log": ind_cb.isChecked(),
        "comb_log": comb_cb.isChecked(),
        "bin_mode": "auto" if auto_rb.isChecked() else "manual",
        "bin_width": width_edit.value(),
        "share_bins": share_bins_cb.isChecked(),
        "core_bins": bins_spin.value(),
        "core_min": min_spin.value(),
        "labels": ("TT", "HH") if tthh_rb.isChecked() else ("T1", "T2"),
    }


class ProgressDialog:
    def __init__(self, total: int):
        self.dialog = QtWidgets.QProgressDialog("Processing...", "Cancel", 0, total)
        self.dialog.setWindowTitle("Processing")
        self.dialog.canceled.connect(self.cancel)
        self.dialog.setAutoClose(False)
        self.dialog.setAutoReset(False)
        self.dialog.show()
        self.cancelled = False
        self.root = self

    def update(self):
        self.dialog.setValue(self.dialog.value() + 1)
        QtWidgets.QApplication.processEvents()

    def cancel(self):
        self.cancelled = True
        self.dialog.close()

    def destroy(self):
        self.dialog.close()


def core_mask(values: np.ndarray, n_bins: int, min_count: int):
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


def main() -> None:
    paths = ask_files()
    cfg = ask_options()
    if cfg is None:
        return

    labels = cfg.get("labels", ("TT", "HH"))
    raw_data: Dict[str, pd.DataFrame] = {}
    data: Dict[str, pd.DataFrame] = {}
    masks: Dict[str, np.ndarray] = {}
    for path in paths:
        name = os.path.splitext(os.path.basename(path))[0]
        raw = pd.read_csv(path, sep=";", header=None, usecols=[0, 1], names=list(labels))
        raw.columns = ["TT", "HH"]
        raw["TTn0"] = raw["TT"] / raw["TT"].max()
        raw["HHn0"] = raw["HH"] / raw["HH"].max()

        n_bins = max(2, int(cfg["core_bins"]))
        min_ct = max(1, int(cfg["core_min"]))
        m_t, _, _ = core_mask(raw["TTn0"].values, n_bins, min_ct)
        m_h, _, _ = core_mask(raw["HHn0"].values, n_bins, min_ct)
        mask = m_t & m_h

        filtered = raw.loc[mask, ["TT", "HH"]].reset_index(drop=True)
        filtered["TTn"] = filtered["TT"] / filtered["TT"].max()
        filtered["HHn"] = filtered["HH"] / filtered["HH"].max()

        raw_data[name] = raw
        data[name] = filtered
        masks[name] = mask

    hist: Dict[str, Dict[str, Dict[str, np.ndarray]]] = {}
    for name, df in data.items():
        hist[name] = {}
        vals_tt = df["TTn"].values
        vals_hh = df["HHn"].values
        for col, vals in [(labels[0], vals_tt), (labels[1], vals_hh)]:
            hmin, hmax = vals.min(), vals.max()
            if cfg["bin_mode"] == "auto":
                if cfg["share_bins"]:
                    B_tt = find_auto_bins(vals_tt)
                    B_hh = find_auto_bins(vals_hh)
                    bins = min(B_tt, B_hh)
                else:
                    bins = find_auto_bins(vals)
                counts, edges = np.histogram(vals, bins=bins, range=(hmin, hmax))
            else:
                dh = cfg["bin_width"]
                edges = np.arange(hmin, hmax + dh, dh)
                counts, _ = np.histogram(vals, bins=edges)
            centers = 0.5 * (edges[:-1] + edges[1:])
            dh_val = edges[1] - edges[0]
            Ni = np.cumsum(counts[::-1])[::-1]
            hazard = counts / (Ni + 1e-12)
            dp = (hazard / dh_val) / (hazard.sum() + 1e-12)
            hist[name][col] = {"centers": centers, "counts": counts, "dp": dp, "dh": dh_val}

    num_per = (
        (1 if cfg["raw"] else 0)
        + (2 if cfg["hist"] else 0)
        + (2 if cfg["ind_log"] else 0)
        + (1 if cfg["comb_log"] else 0)
    )
    total_plots = len(data) * num_per
    progress = ProgressDialog(total_plots) if total_plots else None

    for name, df in data.items():
        if progress and progress.cancelled:
            break
        mask = masks[name]
        raw = raw_data[name]

        if cfg["raw"]:
            plt.figure(figsize=(6, 3))
            plt.scatter(df.index + 1, df["TT"], s=2, label=f"{labels[0]} inlier")
            plt.scatter(df.index + 1, df["HH"], s=2, label=f"{labels[1]} inlier", color="C1")
            if cfg["show_trimmed"]:
                trimmed = ~mask
                plt.scatter(np.where(trimmed)[0] + 1, raw["TT"][trimmed], s=20, c="r", marker="x", label=f"{labels[0]} trimmed")
                plt.scatter(np.where(trimmed)[0] + 1, raw["HH"][trimmed], s=20, c="m", marker="x", label=f"{labels[1]} trimmed")
            plt.title(f"{name} — Raw with Histogram-Core filter")
            plt.xlabel("Index"); plt.ylabel("Switching Field")
            plt.legend(fontsize="x-small"); plt.tight_layout()
            if progress:
                progress.update()
        if progress and progress.cancelled:
            break
        if cfg["hist"]:
            for col, h in hist[name].items():
                plt.figure()
                plt.bar(h["centers"], h["counts"], width=h["dh"], edgecolor="k", alpha=0.6)
                plt.title(f"{name} — {col}: counts")
                plt.xlabel("h = H/Hsw,max"); plt.ylabel("Counts")
                plt.grid(ls="--", alpha=0.3)
                if progress:
                    progress.update()
            if progress and progress.cancelled:
                break
        if cfg["ind_log"] and not (progress and progress.cancelled):
            for col, h in hist[name].items():
                valid = h["dp"] > 0
                x = (1 - h["centers"][valid]) ** 1.5
                y = np.log(h["dp"][valid])
                plt.figure()
                plt.plot(x, y, "-o", markersize=4)
                plt.title(f"{name} — {col}: ln(dp/dh) vs Δh^(3/2)")
                plt.xlabel(r"$\Delta h^{3/2}$"); plt.ylabel(r"$\ln(dp/dh)$")
                plt.grid(ls="--", alpha=0.3)
                if progress:
                    progress.update()
            if progress and progress.cancelled:
                break
        if cfg["comb_log"] and not (progress and progress.cancelled):
            plt.figure()
            for col, h in hist[name].items():
                valid = h["dp"] > 0
                plt.plot((1 - h["centers"][valid]) ** 1.5, np.log(h["dp"][valid]), "-o", markersize=4, label=col)
            plt.title(f"{name} — Combined ln(dp/dh)")
            plt.xlabel(r"$\Delta h^{3/2}$"); plt.ylabel(r"$\ln(dp/dh)$")
            plt.legend(); plt.grid(ls="--", alpha=0.3)
            if progress:
                progress.update()

    if progress and not progress.cancelled:
        progress.destroy()
    elif progress and progress.cancelled:
        plt.close('all')
        print("Cancelled.")
        return

    plt.show()


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    apply_system_theme(app)
    main()
