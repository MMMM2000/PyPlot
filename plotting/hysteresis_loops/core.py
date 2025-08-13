
import os
import re
from typing import List, Tuple, Sequence

import numpy as np
import matplotlib.pyplot as plt


def load_loop(path: str) -> Tuple[np.ndarray, np.ndarray]:
    data = np.loadtxt(path, usecols=(0, 1))
    if data.ndim == 1:
        data = data.reshape(1, -1)
    x = data[:, 0]
    y = data[:, 1]
    return x, y

_RE_TEMP = re.compile(r"(\d+)\s*([°]?[Cc])", re.IGNORECASE)

def _parse_meta(filename: str) -> Tuple[str, float, str]:
    """Return (base_name, sort_key, label).

    - base_name: e.g. "FeSiBP 159_9 s3"
    - sort_key: -inf for as-cast, otherwise numeric temperature
    - label: "as-cast" or "350°C"
    """
    name = os.path.splitext(os.path.basename(filename))[0]
    norm = name.lower().replace('-', '').replace('_', '')
    # detect as-cast
    if 'ascast' in norm or ('asc' in norm and 'cast' in norm):
        parts = name.split()
        base = ' '.join(parts[:-1]) if len(parts) > 1 else name
        return base, float('-inf'), 'as-cast'
    m = _RE_TEMP.search(name)
    if m:
        t = float(m.group(1))
        base = name[:m.start()].strip()
        return base, t, f"{int(t)}°C"
    # fallback: treat as as-cast
    parts = name.split()
    base = ' '.join(parts[:-1]) if len(parts) > 1 else name
    return base, float('-inf'), 'as-cast'


def _stacked(loaded: Sequence[Tuple[str, np.ndarray, np.ndarray]]) -> plt.Figure:
    """Stacked figure: as-cast on top, highest temp bottom; zero spacing."""
    # Parse for ordering
    metas = [(_parse_meta(p), p, x, y) for (p, x, y) in loaded]
    metas.sort(key=lambda t: (t[0][1], t[0][2]))  # by sort_key then label
    base_name = metas[0][0][0]
    n = len(metas)
    fig, axes = plt.subplots(n, 1, sharex=True, figsize=(7, 1.6*n), gridspec_kw={'hspace': 0})
    if n == 1:
        axes = [axes]
    for ax, ((base, _, label), path, x, y) in zip(axes, metas):
        ax.plot(x, y, lw=1.2)
        ax.grid(True, linestyle='--', alpha=0.3)
        ax.text(0.02, 0.96, label, transform=ax.transAxes, va='top', ha='left')
        ax.set_ylabel('F (Wb)')
    for ax in axes[:-1]:
        ax.label_outer()
    axes[-1].set_xlabel('H (A/m)')
    fig.suptitle(base_name)
    fig.subplots_adjust(hspace=0, top=0.9, left=0.1, right=0.95, bottom=0.1)
    return fig


def plot_loops(paths: Sequence[str], mode: str = "Combined", show: bool = True):
    """Plot hysteresis loops.

    mode: "Combined" (one axes with legend), "Stacked" (zero spacing),
          or "Separate" (one window per file).
    """
    loaded: List[Tuple[str, np.ndarray, np.ndarray]] = []
    for path in paths:
        x, y = load_loop(path)
        loaded.append((path, x, y))

    if mode.lower() == "stacked":
        fig = _stacked(loaded)
        if show:
            plt.show()
        return [fig]

    if mode.lower() == "combined":
        fig, ax = plt.subplots()
        base_title = None
        for path, x, y in loaded:
            base, _, label = _parse_meta(path)
            if base_title is None:
                base_title = base
            ax.plot(x, y, lw=1.2, label=label)
        ax.grid(True, linestyle='--', alpha=0.3)
        ax.set_xlabel("H (A/m)")
        ax.set_ylabel("F (Wb)")
        if base_title:
            ax.set_title(base_title)
        ax.legend(title="Anneal", loc="best")
        fig.tight_layout()
        if show:
            plt.show()
        return [fig]

    # Separate
    figs: List[plt.Figure] = []
    for path, x, y in loaded:
        fig, ax = plt.subplots()
        ax.plot(x, y, lw=1.2)
        ax.set_xlabel("H (A/m)")
        ax.set_ylabel("F (Wb)")
        base, _, label = _parse_meta(path)
        ax.set_title(f"{base} — {label}")
        ax.grid(True, linestyle="--", alpha=0.3)
        fig.tight_layout()
        figs.append(fig)
    if show:
        plt.show()
    return figs
