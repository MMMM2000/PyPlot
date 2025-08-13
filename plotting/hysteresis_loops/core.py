import os
from typing import List, Tuple, Sequence

import numpy as np
import matplotlib.pyplot as plt


def load_loop(path: str) -> Tuple[np.ndarray, np.ndarray]:
    """Load a hysteresis loop file.

    The input files contain at least two whitespace separated columns.  The first
    column is interpreted as the magnetic field ``H`` and the second column as
    the flux ``F``.  Additional columns are ignored.
    """
    data = np.loadtxt(path, usecols=(0, 1))
    if data.ndim == 1:
        data = data.reshape(-1, 2)
    return data[:, 0], data[:, 1]


def plot_loops(paths: Sequence[str], mode: str = "Combined", show: bool = True):
    """Plot hysteresis loops contained in ``paths``.

    Parameters
    ----------
    paths:
        Iterable of file paths.
    mode:
        One of ``"Combined"``, ``"Separate"`` or ``"Stacked"`` specifying the
        plotting style.
    show:
        Whether to show the resulting figures via :func:`matplotlib.pyplot.show`.

    Returns
    -------
    The created :class:`~matplotlib.figure.Figure` instance(s).
    """
    loaded = [(p, *load_loop(p)) for p in paths]
    mode = mode.lower()

    if mode == "combined":
        fig, ax = plt.subplots()
        for path, x, y in loaded:
            ax.plot(x, y, label=os.path.basename(path))
        ax.set_xlabel("H (A/m)")
        ax.set_ylabel("F (Wb)")
        ax.grid(True, linestyle="--", alpha=0.3)
        ax.legend()
        fig.tight_layout()
        if show:
            plt.show()
        return fig

    if mode == "stacked":
        n = len(loaded)
        fig, axes = plt.subplots(nrows=n, ncols=1, sharex=True, figsize=(6, 2.5 * n))
        if n == 1:
            axes = [axes]
        for ax, (path, x, y) in zip(axes, loaded):
            ax.plot(x, y)
            ax.set_ylabel(os.path.basename(path))
            ax.grid(True, linestyle="--", alpha=0.3)
        axes[-1].set_xlabel("H (A/m)")
        fig.tight_layout()
        if show:
            plt.show()
        return fig

    # Separate
    figs: List[plt.Figure] = []
    for path, x, y in loaded:
        fig, ax = plt.subplots()
        ax.plot(x, y)
        ax.set_xlabel("H (A/m)")
        ax.set_ylabel("F (Wb)")
        ax.set_title(os.path.basename(path))
        ax.grid(True, linestyle="--", alpha=0.3)
        fig.tight_layout()
        figs.append(fig)
    if show:
        plt.show()
    return figs
