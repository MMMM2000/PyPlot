
import os
import re
from typing import Any, List, Tuple, Sequence

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from ..backends import wants_matplotlib, wants_origin
from ..config import load_config
from ..utils import show_plots, apply_readability_fonts, apply_readability

_CFG = load_config().get("hysteresis_loops", {})
MODE = _CFG.get("MODE", "Combined")
SHOW_PLOTS = bool(_CFG.get("SHOW_PLOTS", True))
BACKEND = str(_CFG.get("BACKEND", "matplotlib"))
IMPROVE_READABILITY = False
SHOW_LEGEND = bool(_CFG.get("SHOW_LEGEND", True))
LEGEND_SIZE = int(_CFG.get("LEGEND_SIZE", 18))
LEGEND_ORIENTATION = str(_CFG.get("LEGEND_ORIENTATION", "auto"))
LEGEND_SHOW_SYMBOLS = bool(_CFG.get("LEGEND_SHOW_SYMBOLS", False))
LEGEND_SYMBOL_SIZE = float(_CFG.get("LEGEND_SYMBOL_SIZE", 10))
TICK_SIZE = int(_CFG.get("TICK_SIZE", 18))
AXIS_LABEL_SIZE = int(_CFG.get("AXIS_LABEL_SIZE", 18))
TITLE_SIZE = int(_CFG.get("TITLE_SIZE", 22))
SHOW_TICK_LABELS = bool(_CFG.get("SHOW_TICK_LABELS", True))
SHOW_AXIS_LABELS = bool(_CFG.get("SHOW_AXIS_LABELS", True))
SHOW_TITLE = bool(_CFG.get("SHOW_TITLE", True))


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


def _stacked(loaded: Sequence[Tuple[str, np.ndarray, np.ndarray]]) -> Figure:
    """Stacked figure: as-cast on top, highest temp bottom; zero spacing."""
    # Parse for ordering
    metas = [(_parse_meta(p), p, x, y) for (p, x, y) in loaded]
    metas.sort(key=lambda t: (t[0][1], t[0][2]))  # by sort_key then label
    base_name = metas[0][0][0]
    n = len(metas)
    fig, axes = plt.subplots(n, 1, sharex=True, figsize=(7, 1.2 * n), gridspec_kw={'hspace': 0})
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
    for ax in axes:
        apply_readability(ax, globals())
    return fig


def _origin_plot_combined(loaded: Sequence[Tuple[str, np.ndarray, np.ndarray]]) -> None:
    import originpro as op  # lazy import
    origin_any: Any = op
    # Ensure Origin UI is visible when plotting
    try:
        origin_any.set_show()
    except Exception:
        pass

    try:
        origin_any.exit()
    except Exception:
        pass

    book: Any = origin_any.new_book('w', lname="Hysteresis (Python)")
    book.activate()
    gp: Any = origin_any.new_graph(template='line')
    gl: Any = gp[0]

    base_title = None
    for path, x, y in loaded:
        base, _t, label = _parse_meta(path)
        if base_title is None:
            base_title = base
        wks: Any = origin_any.new_sheet('w', lname=label)
        wks.from_list(0, x)
        wks.from_list(1, y)
        wks.cols_axis('XY')
        gl.add_plot(wks, coly=1, colx=0, type='y')

    try:
        gp.activate()
        origin_any.lt_exec('page.antialias=1;')
        origin_any.lt_exec('layer -aa 1;')
        origin_any.lt_exec('lab -xb "H (A/m)";')
        origin_any.lt_exec('lab -yl "F (Wb)";')
        if base_title:
            esc = base_title.replace('"', "'")
            origin_any.lt_exec(f'title -s "{esc}";')
        origin_any.lt_exec('legend;')
    except Exception:
        pass

    try:
        origin_any.exit()
    except Exception:
        pass


def _origin_plot_separate(loaded: Sequence[Tuple[str, np.ndarray, np.ndarray]]) -> None:
    import originpro as op  # lazy import
    origin_any: Any = op
    # Ensure Origin UI is visible when plotting
    try:
        origin_any.set_show()
    except Exception:
        pass

    book: Any = origin_any.new_book('w', lname="Hysteresis (Python)")
    book.activate()
    for path, x, y in loaded:
        base, _t, label = _parse_meta(path)
        wks: Any = origin_any.new_sheet('w', lname=label)
        wks.from_list(0, x)
        wks.from_list(1, y)
        wks.cols_axis('XY')
        gp: Any = origin_any.new_graph(template='line')
        gl: Any = gp[0]
        gl.add_plot(wks, coly=1, colx=0, type='y')
        try:
            gp.activate()
            origin_any.lt_exec('page.antialias=1;')
            origin_any.lt_exec('layer -aa 1;')
            esc = (f"{base} - {label}").replace('"', "'")
            origin_any.lt_exec(f'title -s "{esc}";')
            origin_any.lt_exec('lab -xb "H (A/m)";')
            origin_any.lt_exec('lab -yl "F (Wb)";')
        except Exception:
            pass

    try:
        origin_any.exit()
    except Exception:
        pass


def plot_loops(
    paths: Sequence[str],
    mode: str = "Combined",
    show: bool = True,
    backend: str = BACKEND,
) -> Figure | List[Figure] | None:
    """Plot hysteresis loops.

    mode: "Combined" (one axes with legend), "Stacked" (zero spacing),
          or "Separate" (one window per file).
    """
    if IMPROVE_READABILITY:
        apply_readability_fonts()
    if not wants_matplotlib(backend) and wants_origin(backend):
        loaded: List[Tuple[str, np.ndarray, np.ndarray]] = []
        for path in paths:
            x, y = load_loop(path)
            loaded.append((path, x, y))
        m = mode.lower()
        if m == "combined":
            _origin_plot_combined(loaded)
        elif m == "separate":
            _origin_plot_separate(loaded)
        else:
            # Fallback: combined when 'stacked' requested (multi-layer stacking is complex)
            _origin_plot_combined(loaded)
        return None

    loaded: List[Tuple[str, np.ndarray, np.ndarray]] = []
    for path in paths:
        x, y = load_loop(path)
        loaded.append((path, x, y))

    if mode.lower() == "stacked":
        fig = _stacked(loaded)
        if show:
            show_plots()
        return fig

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
        apply_readability(ax, globals())
        if show:
            show_plots()
        return fig

    # Separate
    figs: List[Figure] = []
    for path, x, y in loaded:
        fig, ax = plt.subplots()
        ax.plot(x, y, lw=1.2)
        ax.set_xlabel("H (A/m)")
        ax.set_ylabel("F (Wb)")
        base, _, label = _parse_meta(path)
        ax.set_title(f"{base} — {label}")
        ax.grid(True, linestyle="--", alpha=0.3)
        fig.tight_layout()
        apply_readability(ax, globals())
        figs.append(fig)
    if show:
        show_plots()
    return figs


def main(files: List[str], backend: str = BACKEND) -> None:
    plot_loops(files, mode=MODE, show=SHOW_PLOTS and wants_matplotlib(backend), backend=backend)
