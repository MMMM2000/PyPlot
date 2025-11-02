from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Sequence, cast

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from PyQt6 import QtCore, QtGui, QtWidgets

from plotting.shared.theme import ensure_app_theme
from plotting.shared.utils import install_standard_menu, show_plots, run_with_console, arrange_top_layout
from plotting.shared.paths import prepare_output_dir, get_last_output_dir, set_last_output_dir
from plotting.shared.toolkit import (
    restore_backend_choice,
    store_backend_choice,
    selected_backend,
    create_file_widget,
)
from plotting.shared.readability import create_readability_group, sync_readability
from plotting.shared.backends import wants_matplotlib, wants_origin


class SettingsDialog(QtWidgets.QDialog):
    """Interactive configuration panel for Hsw distribution plots."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Hsw Distribution")

        self.files, file_widget = create_file_widget(self, key="hsw_distribution")
        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(150)

        options = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(options)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)

        self.raw_cb = QtWidgets.QCheckBox("Raw TT/HH vs Index")
        self.raw_cb.setChecked(True)
        self.trim_cb = QtWidgets.QCheckBox("Show trimmed data")
        self.trim_cb.setChecked(True)
        self.hist_cb = QtWidgets.QCheckBox("Counts Histogram")
        self.hist_cb.setChecked(True)
        self.ind_cb = QtWidgets.QCheckBox("Individual ln(dp/dh)")
        self.ind_cb.setChecked(True)
        self.comb_cb = QtWidgets.QCheckBox("Combined ln(dp/dh)")
        self.comb_cb.setChecked(True)

        opts_layout = QtWidgets.QVBoxLayout()
        for widget in (self.raw_cb, self.trim_cb, self.hist_cb, self.ind_cb, self.comb_cb):
            opts_layout.addWidget(widget)
        opts_layout.addStretch(1)
        opts_widget = QtWidgets.QWidget()
        opts_widget.setLayout(opts_layout)

        bin_group = QtWidgets.QGroupBox("Final Histogram Binning")
        bin_layout = QtWidgets.QGridLayout(bin_group)
        self.auto_rb = QtWidgets.QRadioButton("Automatic")
        self.auto_rb.setChecked(True)
        self.manual_rb = QtWidgets.QRadioButton("Manual Δh =")
        self.width_edit = QtWidgets.QDoubleSpinBox()
        self.width_edit.setDecimals(6)
        self.width_edit.setRange(1e-6, 1.0)
        self.width_edit.setSingleStep(1e-4)
        self.width_edit.setValue(1e-4)
        self.share_bins_cb = QtWidgets.QCheckBox("Shared bins TT/HH")
        bin_layout.addWidget(self.auto_rb, 0, 0, 1, 2)
        bin_layout.addWidget(self.manual_rb, 1, 0)
        bin_layout.addWidget(self.width_edit, 1, 1)
        bin_layout.addWidget(self.share_bins_cb, 2, 0, 1, 2)

        core_group = QtWidgets.QGroupBox("Histogram-Core Filter")
        core_layout = QtWidgets.QGridLayout(core_group)
        self.bins_spin = QtWidgets.QSpinBox()
        self.bins_spin.setRange(1, 9999)
        self.bins_spin.setValue(50)
        self.min_spin = QtWidgets.QSpinBox()
        self.min_spin.setRange(1, 9999)
        self.min_spin.setValue(3)
        core_layout.addWidget(QtWidgets.QLabel("n_bins:"), 0, 0)
        core_layout.addWidget(self.bins_spin, 0, 1)
        core_layout.addWidget(QtWidgets.QLabel("min_count:"), 1, 0)
        core_layout.addWidget(self.min_spin, 1, 1)

        naming_group = QtWidgets.QGroupBox("Column naming")
        naming_layout = QtWidgets.QVBoxLayout(naming_group)
        self.tthh_rb = QtWidgets.QRadioButton("TT && HH")
        self.tthh_rb.setChecked(True)
        self.t1t2_rb = QtWidgets.QRadioButton("T1 && T2")
        naming_layout.addWidget(self.tthh_rb)
        naming_layout.addWidget(self.t1t2_rb)
        naming_layout.addStretch(1)

        out_group = QtWidgets.QGroupBox("Output")
        out_layout = QtWidgets.QGridLayout(out_group)
        out_layout.setContentsMargins(8, 8, 8, 8)
        out_layout.setHorizontalSpacing(12)
        self.backend_combo = QtWidgets.QComboBox()
        self.backend_combo.addItems(["Matplotlib", "Origin", "Both"])
        restore_backend_choice("hsw_distribution", self.backend_combo, "matplotlib")
        out_layout.addWidget(QtWidgets.QLabel("Backend"), 0, 0)
        out_layout.addWidget(self.backend_combo, 0, 1)

        grid.addWidget(opts_widget, 0, 0)
        grid.addWidget(bin_group, 0, 1)
        grid.addWidget(naming_group, 1, 0)
        grid.addWidget(core_group, 1, 1)
        grid.addWidget(out_group, 2, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        footer = QtWidgets.QHBoxLayout()
        footer.setContentsMargins(12, 6, 12, 12)
        footer.addStretch(1)
        self.run_btn = QtWidgets.QPushButton("Run")
        self.run_btn.clicked.connect(self.run)
        footer.addWidget(self.run_btn)

        arrange_top_layout(
            self,
            file_widget,
            options,
            self.console,
            footer=footer,
            help_topic="plot_hsw_distribution",
        )

    def _gather_config(self) -> Dict[str, Any]:
        backend = store_backend_choice(
            "hsw_distribution", selected_backend(self.backend_combo)
        )
        return {
            "raw": self.raw_cb.isChecked(),
            "show_trimmed": self.trim_cb.isChecked(),
            "hist": self.hist_cb.isChecked(),
            "ind_log": self.ind_cb.isChecked(),
            "comb_log": self.comb_cb.isChecked(),
            "bin_mode": "auto" if self.auto_rb.isChecked() else "manual",
            "bin_width": self.width_edit.value(),
            "share_bins": self.share_bins_cb.isChecked(),
            "core_bins": self.bins_spin.value(),
            "core_min": self.min_spin.value(),
            "labels": ("TT", "HH") if self.tthh_rb.isChecked() else ("T1", "T2"),
            "backend": backend,
        }

    def options(self) -> Dict[str, Any]:
        """Return the currently selected configuration."""

        return self._gather_config()

    def run(self) -> None:
        if not self.files:
            QtWidgets.QMessageBox.warning(
                self,
                "No files",
                "Select one or more Hsw distribution files first.",
            )
            return

        config = self._gather_config()
        run_with_console(
            lambda: run_distribution(self.files, config),
            self.console,
        )


def run_distribution(paths: Sequence[str], config: Dict[str, Any]) -> None:
    """Execute the Hsw distribution analysis for ``paths`` using ``config``."""

    labels = config.get("labels", ("TT", "HH"))
    raw_data: Dict[str, pd.DataFrame] = {}
    data: Dict[str, pd.DataFrame] = {}
    masks: Dict[str, np.ndarray] = {}
    for path in paths:
        name = os.path.splitext(os.path.basename(path))[0]
        raw = pd.read_csv(path, sep=";", header=None, usecols=[0, 1], names=list(labels))
        raw.columns = ["TT", "HH"]
        raw["TTn0"] = raw["TT"] / raw["TT"].max()
        raw["HHn0"] = raw["HH"] / raw["HH"].max()

        n_bins = max(2, int(config["core_bins"]))
        min_ct = max(1, int(config["core_min"]))
        m_t, _, _ = core_mask(raw["TTn0"].to_numpy(dtype=float), n_bins, min_ct)
        m_h, _, _ = core_mask(raw["HHn0"].to_numpy(dtype=float), n_bins, min_ct)
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
        vals_tt = df["TTn"].to_numpy(dtype=float)
        vals_hh = df["HHn"].to_numpy(dtype=float)
        for col, vals in [(labels[0], vals_tt), (labels[1], vals_hh)]:
            hmin, hmax = vals.min(), vals.max()
            if config["bin_mode"] == "auto":
                if config["share_bins"]:
                    B_tt = find_auto_bins(vals_tt)
                    B_hh = find_auto_bins(vals_hh)
                    bins = min(B_tt, B_hh)
                else:
                    bins = find_auto_bins(vals)
                counts, edges = np.histogram(vals, bins=bins, range=(hmin, hmax))
            else:
                dh = config["bin_width"]
                edges = np.arange(hmin, hmax + dh, dh)
                counts, _ = np.histogram(vals, bins=edges)
            centers = 0.5 * (edges[:-1] + edges[1:])
            dh_val = edges[1] - edges[0]
            Ni = np.cumsum(counts[::-1])[::-1]
            hazard = counts / (Ni + 1e-12)
            dp = (hazard / dh_val) / (hazard.sum() + 1e-12)
            hist[name][col] = {"centers": centers, "counts": counts, "dp": dp, "dh": dh_val}

    num_per = (
        (1 if config["raw"] else 0)
        + (2 if config["hist"] else 0)
        + (2 if config["ind_log"] else 0)
        + (1 if config["comb_log"] else 0)
    )
    total_plots = len(data) * num_per
    progress = ProgressDialog(total_plots) if total_plots else None

    backend = config.get("backend", "matplotlib")
    for name, df in data.items():
        if progress and progress.cancelled:
            break
        mask = masks[name]
        raw = raw_data[name]

        if config["raw"] and wants_matplotlib(backend):
            plt.figure(figsize=(6, 3))
            plt.scatter(df.index + 1, df["TT"], s=2, label=f"{labels[0]} inlier")
            plt.scatter(df.index + 1, df["HH"], s=2, label=f"{labels[1]} inlier", color="C1")
            if config["show_trimmed"]:
                trimmed = ~mask
                plt.scatter(
                    np.where(trimmed)[0] + 1,
                    raw["TT"][trimmed],
                    s=20,
                    c="r",
                    marker="x",
                    label=f"{labels[0]} trimmed",
                )
                plt.scatter(
                    np.where(trimmed)[0] + 1,
                    raw["HH"][trimmed],
                    s=20,
                    c="m",
                    marker="x",
                    label=f"{labels[1]} trimmed",
                )
            plt.title(f"{name} — Raw with Histogram-Core filter")
            plt.xlabel("Index")
            plt.ylabel("Switching Field")
            plt.legend(fontsize="x-small")
            plt.tight_layout()
            if progress:
                progress.update()
        if progress and progress.cancelled:
            break
        if config["hist"] and wants_matplotlib(backend):
            for col, h in hist[name].items():
                plt.figure()
                plt.bar(h["centers"], h["counts"], width=h["dh"], edgecolor="k", alpha=0.6)
                plt.title(f"{name} — {col}: counts")
                plt.xlabel("h = H/Hsw,max")
                plt.ylabel("Counts")
                plt.grid(ls="--", alpha=0.3)
                if progress:
                    progress.update()
            if progress and progress.cancelled:
                break
        if config["ind_log"] and wants_matplotlib(backend) and not (progress and progress.cancelled):
            for col, h in hist[name].items():
                valid = h["dp"] > 0
                x = (1 - h["centers"][valid]) ** 1.5
                y = np.log(h["dp"][valid])
                plt.figure()
                plt.plot(x, y, "-o", markersize=4)
                plt.title(f"{name} — {col}: ln(dp/dh) vs Δh^(3/2)")
                plt.xlabel(r"$\Delta h^{3/2}$")
                plt.ylabel(r"$\ln(dp/dh)$")
                plt.grid(ls="--", alpha=0.3)
                if progress:
                    progress.update()
            if progress and progress.cancelled:
                break
        if config["comb_log"] and wants_matplotlib(backend) and not (progress and progress.cancelled):
            plt.figure()
            for col, h in hist[name].items():
                valid = h["dp"] > 0
                plt.plot(
                    (1 - h["centers"][valid]) ** 1.5,
                    np.log(h["dp"][valid]),
                    "-o",
                    markersize=4,
                    label=col,
                )
            plt.title(f"{name} — Combined ln(dp/dh)")
            plt.xlabel(r"$\Delta h^{3/2}$")
            plt.ylabel(r"$\ln(dp/dh)$")
            plt.legend()
            plt.grid(ls="--", alpha=0.3)
            if progress:
                progress.update()

    if wants_origin(backend):
        _export_origin(hist, labels)

    if progress:
        if progress.cancelled:
            plt.close("all")
            print("Cancelled.")
            return
        progress.destroy()

    show_plots()


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


def _export_origin(
    hist: Dict[str, Dict[str, Dict[str, np.ndarray]]],
    labels: tuple[str, str],
) -> None:
    try:
        import originpro as op
    except Exception as exc:  # pragma: no cover - optional dependency
        print(f"Origin plot failed: {exc}")
        return

    origin_any: Any = cast(Any, op)
    try:
        origin_any.set_show()
    except Exception:
        pass
    try:
        gp: Any = origin_any.new_graph(template="scatter")
    except Exception as exc:
        print(f"Origin plot failed: {exc}")
        return

    gl0: Any = gp[0]
    first_layer = True
    for name in sorted(hist.keys()):
        gl = gl0 if first_layer else gp.add_layer()  # type: ignore[attr-defined]
        first_layer = False
        for col, color in ((labels[0], "#1f77b4"), (labels[1], "#ff7f0e")):
            h = hist[name][col]
            valid = h["dp"] > 0
            x = (1 - h["centers"][valid]) ** 1.5
            y = np.log(h["dp"][valid])
            w: Any = origin_any.new_sheet("w", lname=f"{name}_{col}")
            w.from_list(0, x.tolist())
            w.from_list(1, y.tolist())
            w.cols_axis("XY")
            plot_obj: Any = gl.add_plot(w, coly=1, colx=0, type="y")
            if plot_obj is not None:
                try:
                    plot_obj.color = color
                    plot_obj.symbol_shape = 2
                except Exception:
                    pass
        try:
            gl.rescale()
        except Exception:
            pass
    try:
        gp.activate()
        origin_any.lt_exec("page.antialias=1; layer -aa 1;")
        origin_any.lt_exec(r'lab -xb "$\Delta h^{3/2}$"; lab -yl "ln(dp/dh)"; legend;')
    except Exception:
        pass
    try:
        origin_any.exit()
    except Exception:
        pass


def main() -> QtWidgets.QWidget | None:
    app = QtWidgets.QApplication.instance()
    created_app = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        ensure_app_theme(app)
        created_app = True
    dialog = SettingsDialog()
    dialog.show()
    if created_app:
        app.exec()
        return None
    return dialog


if __name__ == "__main__":  # pragma: no cover - manual launch
    main()
