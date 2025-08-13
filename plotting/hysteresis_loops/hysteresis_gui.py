
from __future__ import annotations
import os, sys, re, pathlib
from typing import List, Tuple
import numpy as np
import matplotlib.pyplot as plt
from PyQt6 import QtWidgets

# Try to import theme helper if available
try:
    from ..utils import apply_system_theme  # when run in package
except Exception:
    try:
        sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
        from plotting.utils import apply_system_theme  # when run directly
    except Exception:
        def apply_system_theme(app):  # no-op fallback
            return

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
    plt.show()

def plot_separate(paths: List[str]) -> None:
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
    plt.show()

def plot_combined(paths: List[str]) -> None:
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
    plt.show()

def _select_dat_files(start_dir: str | None=None) -> list[str]:
    dlg = QtWidgets.QFileDialog()
    dlg.setFileMode(QtWidgets.QFileDialog.FileMode.ExistingFiles)
    dlg.setNameFilters(['Data files (*.dat *.txt)', 'All files (*.*)'])
    if start_dir:
        dlg.setDirectory(start_dir)
    if dlg.exec():
        return [str(p) for p in dlg.selectedFiles()]
    return []

def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    apply_system_theme(app)
    files = _select_dat_files()
    if not files:
        sys.exit(0)
    # Ask mode
    combo = QtWidgets.QInputDialog()
    modes = ['Stacked', 'Combined', 'Separate']
    ok, mode = QtWidgets.QInputDialog.getItem(None, 'Plot mode', 'Mode:', modes, 0, False)
    if not ok:
        sys.exit(0)
    if mode == 'Stacked':
        plot_stacked(files)
    elif mode == 'Combined':
        plot_combined(files)
    else:
        plot_separate(files)

if __name__ == '__main__':  # pragma: no cover
    main()
