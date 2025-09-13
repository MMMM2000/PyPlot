
from __future__ import annotations
import os, sys, re, pathlib
from typing import List, Tuple
import numpy as np
import matplotlib.pyplot as plt
from PyQt6 import QtWidgets

if __package__ is None or __package__ == "":
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from plotting.utils import (
        apply_system_theme,
        create_file_widget,
        show_plots,
        run_with_console,
        get_readability,
        set_readability,
        apply_readability_fonts,
    )
else:
    from ..utils import (
        apply_system_theme,
        create_file_widget,
        show_plots,
        run_with_console,
        get_readability,
        set_readability,
        apply_readability_fonts,
    )

IMPROVE_READABILITY = False

def _load_loop(path: str) -> Tuple[np.ndarray, np.ndarray]:
    data = np.loadtxt(path, usecols=(0,1))
    if data.ndim == 1:
        data = data.reshape(1, -1)
    x = data[:,0]
    y = data[:,1]
    return x, y

_RE_TEMP = re.compile(r'(\d+)\s*([°]?[Cc])')
def _parse_meta(filename: str) -> Tuple[str, float, str]:
    """
    Returns (base_name, sort_key, label)
    - base_name: e.g., "FeSiBP 159_9 s3"
    - sort_key: for ordering (as-cast -> -inf, else numeric temp)
    - label: "as-cast" or "350°C"
    """
    name = os.path.splitext(os.path.basename(filename))[0]
    tokens = name.split()
    # Handle as-cast variants
    n = name.lower().replace('-', '').replace('_','')
    if 'ascast' in n or 'as' in n and 'cast' in n:
        base = ' '.join(tokens[:-1]) if len(tokens) > 1 else name
        return base, float('-inf'), 'as-cast'
    m = _RE_TEMP.search(name)
    if m:
        temp = float(m.group(1))
        base = name[:m.start()].strip()
        label = f"{int(temp)}°C"
        return base, temp, label
    # Fallback: no temp found -> treat as-cast
    base = ' '.join(tokens[:-1]) if len(tokens) > 1 else name
    return base, float('-inf'), 'as-cast'

def plot_stacked(paths: List[str]) -> None:
    if IMPROVE_READABILITY:
        apply_readability_fonts()
    # Parse and sort with as-cast top, highest temp bottom
    metas = [(_parse_meta(p) + (p,)) for p in paths]  # (base, sort, label, path)
    # Choose the most common base for suptitle
    from collections import Counter
    base_counts = Counter(b for b,_,_,_ in metas)
    base_title, _ = base_counts.most_common(1)[0]
    metas.sort(key=lambda t: (t[1], t[2]))  # by temp (as-cast=-inf) then label
    # Create stacked figure
    n = len(metas)
    fig, axes = plt.subplots(n, 1, sharex=True, figsize=(7, 1.8*n), gridspec_kw={'hspace': 0})
    if n == 1:
        axes = [axes]
    for ax, (base, _, label, path) in zip(axes, metas):
        x, y = _load_loop(path)
        ax.plot(x, y, lw=1.2)
        ax.grid(True, linestyle='--', alpha=0.3)
        ax.text(0.02, 0.95, label, transform=ax.transAxes, va='top', ha='left')
        ax.set_ylabel('F (Wb)')
        # Hide x tick labels for all but last
    for ax in axes[:-1]:
        ax.label_outer()
        ax.set_xlabel('')
    axes[-1].set_xlabel('H (A/m)')
    fig.suptitle(base_title)
    fig.tight_layout()
    show_plots()

def plot_separate(paths: List[str]) -> None:
    if IMPROVE_READABILITY:
        apply_readability_fonts()
    for p in paths:
        base, _, label = _parse_meta(p)
        x, y = _load_loop(p)
        fig, ax = plt.subplots()
        ax.plot(x,y, lw=1.2)
        ax.grid(True, linestyle='--', alpha=0.3)
        ax.set_title(f"{base} — {label}")
        ax.set_xlabel('H (A/m)')
        ax.set_ylabel('F (Wb)')
        fig.tight_layout()
    show_plots()

def plot_combined(paths: List[str]) -> None:
    if IMPROVE_READABILITY:
        apply_readability_fonts()
    fig, ax = plt.subplots()
    base_title = None
    for p in paths:
        base, _, label = _parse_meta(p)
        if base_title is None:
            base_title = base
        x, y = _load_loop(p)
        ax.plot(x, y, lw=1.2, label=label)
    ax.grid(True, linestyle='--', alpha=0.3)
    ax.set_title(base_title or 'Hysteresis Loops')
    ax.set_xlabel('H (A/m)')
    ax.set_ylabel('F (Wb)')
    ax.legend(title='Anneal', loc='best')
    fig.tight_layout()
    show_plots()

class SettingsDialog(QtWidgets.QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Hysteresis Loops")
        layout = QtWidgets.QGridLayout(self)

        self.files, file_widget = create_file_widget(self, ext=".dat")
        layout.addWidget(file_widget, 0, 0, 1, 2)

        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["Stacked", "Combined", "Separate"])

        self.run_btn = QtWidgets.QPushButton("Plot")
        self.run_btn.clicked.connect(self.run)

        layout.addWidget(QtWidgets.QLabel("Mode:"), 1, 0)
        layout.addWidget(self.mode_combo, 1, 1)
        self.read_cb = QtWidgets.QCheckBox("Improve readability")
        self.read_cb.setChecked(get_readability("hysteresis_hyst"))
        layout.addWidget(self.read_cb, 2, 0, 1, 2)
        self.console = QtWidgets.QPlainTextEdit(); self.console.setReadOnly(True); self.console.setMaximumHeight(120)
        layout.addWidget(self.run_btn, 3, 0, 1, 2)
        layout.addWidget(self.console, 4, 0, 1, 2)

    def run(self) -> None:
        if not self.files:
            QtWidgets.QMessageBox.warning(self, "No files", "Select files first.")
            return
        mode = self.mode_combo.currentText()
        if mode == 'Stacked':
            func = lambda: plot_stacked(self.files)
        elif mode == 'Combined':
            func = lambda: plot_combined(self.files)
        else:
            func = lambda: plot_separate(self.files)
        global IMPROVE_READABILITY
        IMPROVE_READABILITY = self.read_cb.isChecked()
        set_readability("hysteresis_hyst", IMPROVE_READABILITY)
        run_with_console(func, self.console)


def main() -> None:
    app = QtWidgets.QApplication.instance()
    owns = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        apply_system_theme(app)
        owns = True
    dlg = SettingsDialog()
    dlg.show()
    if owns:
        app.exec()


if __name__ == '__main__':
    main()
