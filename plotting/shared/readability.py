"""Readability helpers shared across PyPlot plugins and legacy dialogs."""

from __future__ import annotations

from typing import Any, Tuple

import matplotlib.pyplot as plt
from matplotlib import colors as mcolors
from matplotlib.axes import Axes
from matplotlib.collections import PathCollection
from matplotlib.patches import Patch
from PyQt6 import QtCore, QtWidgets

from .settings import get_settings


def _settings() -> QtCore.QSettings:
    return get_settings()


def get_readability(key: str) -> bool:
    """Return whether readability overrides are enabled for ``key``."""

    return bool(_settings().value(f"{key}_readability", False, type=bool))


def set_readability(key: str, value: bool) -> None:
    """Persist the readability toggle for ``key``."""

    _settings().setValue(f"{key}_readability", bool(value))


class ReadabilityControls:
    """Container for readability widgets so callers can read back state."""

    def __init__(self) -> None:
        self.legend_show: QtWidgets.QCheckBox
        self.legend_size: QtWidgets.QSpinBox
        self.legend_orient: QtWidgets.QComboBox
        self.legend_loc: QtWidgets.QComboBox
        self.legend_symbol: QtWidgets.QCheckBox
        self.legend_symbol_size: QtWidgets.QDoubleSpinBox
        self.legend_color_match: QtWidgets.QCheckBox
        self.tick_show: QtWidgets.QCheckBox
        self.tick_size: QtWidgets.QSpinBox
        self.axis_show: QtWidgets.QCheckBox
        self.axis_size: QtWidgets.QSpinBox
        self.title_show: QtWidgets.QCheckBox
        self.title_size: QtWidgets.QSpinBox


def create_readability_group(
    key: str, orig_module
) -> Tuple[ReadabilityControls, QtWidgets.QGroupBox]:
    """Return a fully featured readability group and its controls."""

    s = _settings()
    ctrl = ReadabilityControls()
    grp = QtWidgets.QGroupBox("Readability")
    lay = QtWidgets.QGridLayout(grp)

    setattr(orig_module, "IMPROVE_READABILITY", True)
    if not hasattr(orig_module, "LEGEND_LOCATION"):
        setattr(orig_module, "LEGEND_LOCATION", "inside")

    ctrl.legend_size = QtWidgets.QSpinBox()
    ctrl.legend_size.setRange(6, 72)
    ctrl.legend_size.setValue(
        int(s.value(f"{key}_legend_size", getattr(orig_module, "LEGEND_SIZE", 18), type=int))
    )
    ctrl.legend_show = QtWidgets.QCheckBox("Show")
    ctrl.legend_show.setChecked(
        bool(s.value(f"{key}_show_legend", getattr(orig_module, "SHOW_LEGEND", True), type=bool))
    )
    ctrl.legend_orient = QtWidgets.QComboBox()
    ctrl.legend_orient.addItems(["Auto", "Vertical", "Horizontal"])
    ctrl.legend_orient.setCurrentText(
        s.value(
            f"{key}_legend_orient",
            getattr(orig_module, "LEGEND_ORIENTATION", "auto"),
            type=str,
        ).capitalize()
    )
    ctrl.legend_loc = QtWidgets.QComboBox()
    ctrl.legend_loc.addItem("Inside", "inside")
    ctrl.legend_loc.addItem("Outside (right)", "outside_right")
    stored_loc = str(
        s.value(
            f"{key}_legend_location",
            getattr(orig_module, "LEGEND_LOCATION", "inside"),
            type=str,
        )
    ).strip().lower()
    if stored_loc not in {"inside", "outside_right"}:
        stored_loc = "inside"
    idx = ctrl.legend_loc.findData(stored_loc)
    ctrl.legend_loc.setCurrentIndex(idx if idx >= 0 else 0)
    orig_module.LEGEND_LOCATION = stored_loc

    ctrl.legend_symbol_size = QtWidgets.QDoubleSpinBox()
    ctrl.legend_symbol_size.setRange(1.0, 50.0)
    ctrl.legend_symbol_size.setValue(
        float(
            s.value(
                f"{key}_legend_symbol_size",
                getattr(orig_module, "LEGEND_SYMBOL_SIZE", 10),
                type=float,
            )
        )
    )
    ctrl.legend_symbol = QtWidgets.QCheckBox("Show symbols")
    ctrl.legend_symbol.setChecked(
        bool(
            s.value(
                f"{key}_legend_symbols",
                getattr(orig_module, "LEGEND_SHOW_SYMBOLS", False),
                type=bool,
            )
        )
    )
    ctrl.legend_color_match = QtWidgets.QCheckBox("Match legend text to curve colors")
    ctrl.legend_color_match.setChecked(
        bool(
            s.value(
                f"{key}_legend_match_colors",
                getattr(orig_module, "LEGEND_MATCH_COLORS", False),
                type=bool,
            )
        )
    )

    ctrl.tick_size = QtWidgets.QSpinBox()
    ctrl.tick_size.setRange(6, 72)
    ctrl.tick_size.setValue(
        int(s.value(f"{key}_tick_size", getattr(orig_module, "TICK_SIZE", 18), type=int))
    )
    ctrl.tick_show = QtWidgets.QCheckBox("Show")
    ctrl.tick_show.setChecked(
        bool(s.value(f"{key}_show_ticks", getattr(orig_module, "SHOW_TICK_LABELS", True), type=bool))
    )

    ctrl.axis_size = QtWidgets.QSpinBox()
    ctrl.axis_size.setRange(6, 72)
    ctrl.axis_size.setValue(
        int(s.value(f"{key}_axis_size", getattr(orig_module, "AXIS_LABEL_SIZE", 18), type=int))
    )
    ctrl.axis_show = QtWidgets.QCheckBox("Show")
    ctrl.axis_show.setChecked(
        bool(s.value(f"{key}_show_axis", getattr(orig_module, "SHOW_AXIS_LABELS", True), type=bool))
    )

    ctrl.title_size = QtWidgets.QSpinBox()
    ctrl.title_size.setRange(6, 96)
    ctrl.title_size.setValue(
        int(s.value(f"{key}_title_size", getattr(orig_module, "TITLE_SIZE", 22), type=int))
    )
    ctrl.title_show = QtWidgets.QCheckBox("Show")
    ctrl.title_show.setChecked(
        bool(s.value(f"{key}_show_title", getattr(orig_module, "SHOW_TITLE", True), type=bool))
    )

    lay.addWidget(QtWidgets.QLabel("Legend text size:"), 0, 0)
    lay.addWidget(ctrl.legend_size, 0, 1)
    lay.addWidget(ctrl.legend_show, 0, 2)
    lay.addWidget(QtWidgets.QLabel("Legend orientation:"), 1, 0)
    lay.addWidget(ctrl.legend_orient, 1, 1, 1, 2)
    lay.addWidget(QtWidgets.QLabel("Legend location:"), 2, 0)
    lay.addWidget(ctrl.legend_loc, 2, 1, 1, 2)
    lay.addWidget(ctrl.legend_color_match, 3, 0, 1, 3)
    lay.addWidget(QtWidgets.QLabel("Legend symbol size:"), 4, 0)
    lay.addWidget(ctrl.legend_symbol_size, 4, 1)
    lay.addWidget(ctrl.legend_symbol, 4, 2)
    lay.addWidget(QtWidgets.QLabel("Tick label size:"), 5, 0)
    lay.addWidget(ctrl.tick_size, 5, 1)
    lay.addWidget(ctrl.tick_show, 5, 2)
    lay.addWidget(QtWidgets.QLabel("Axis label size:"), 6, 0)
    lay.addWidget(ctrl.axis_size, 6, 1)
    lay.addWidget(ctrl.axis_show, 6, 2)
    lay.addWidget(QtWidgets.QLabel("Title size:"), 7, 0)
    lay.addWidget(ctrl.title_size, 7, 1)
    lay.addWidget(ctrl.title_show, 7, 2)

    def _toggle_legend(checked: bool) -> None:
        ctrl.legend_size.setEnabled(checked)
        ctrl.legend_orient.setEnabled(checked)
        ctrl.legend_loc.setEnabled(checked)
        ctrl.legend_symbol.setEnabled(checked)
        ctrl.legend_symbol_size.setEnabled(checked and ctrl.legend_symbol.isChecked())
        ctrl.legend_color_match.setEnabled(checked)

    def _toggle_symbol(checked: bool) -> None:
        ctrl.legend_symbol_size.setEnabled(checked and ctrl.legend_show.isChecked())

    ctrl.legend_show.toggled.connect(_toggle_legend)
    ctrl.legend_symbol.toggled.connect(_toggle_symbol)
    ctrl.tick_show.toggled.connect(lambda checked: ctrl.tick_size.setEnabled(checked))
    ctrl.axis_show.toggled.connect(lambda checked: ctrl.axis_size.setEnabled(checked))
    ctrl.title_show.toggled.connect(lambda checked: ctrl.title_size.setEnabled(checked))

    _toggle_legend(ctrl.legend_show.isChecked())
    _toggle_symbol(ctrl.legend_symbol.isChecked())
    ctrl.legend_loc.setEnabled(ctrl.legend_show.isChecked())
    ctrl.legend_color_match.setEnabled(ctrl.legend_show.isChecked())
    ctrl.tick_size.setEnabled(ctrl.tick_show.isChecked())
    ctrl.axis_size.setEnabled(ctrl.axis_show.isChecked())
    ctrl.title_size.setEnabled(ctrl.title_show.isChecked())

    return ctrl, grp


def sync_readability(key: str, ctrl: ReadabilityControls, orig_module) -> None:
    """Copy readability UI state into ``orig_module`` and persist to settings."""

    orig_module.IMPROVE_READABILITY = True
    orig_module.SHOW_LEGEND = ctrl.legend_show.isChecked()
    orig_module.LEGEND_SIZE = int(ctrl.legend_size.value())
    orig_module.LEGEND_ORIENTATION = ctrl.legend_orient.currentText().lower()
    loc_data = ctrl.legend_loc.currentData()
    orig_module.LEGEND_LOCATION = str(loc_data).lower() if loc_data else "inside"
    orig_module.LEGEND_SHOW_SYMBOLS = ctrl.legend_symbol.isChecked()
    orig_module.LEGEND_SYMBOL_SIZE = float(ctrl.legend_symbol_size.value())
    orig_module.LEGEND_MATCH_COLORS = ctrl.legend_color_match.isChecked()
    orig_module.SHOW_TICK_LABELS = ctrl.tick_show.isChecked()
    orig_module.TICK_SIZE = int(ctrl.tick_size.value())
    orig_module.SHOW_AXIS_LABELS = ctrl.axis_show.isChecked()
    orig_module.AXIS_LABEL_SIZE = int(ctrl.axis_size.value())
    orig_module.SHOW_TITLE = ctrl.title_show.isChecked()
    orig_module.TITLE_SIZE = int(ctrl.title_size.value())
    s = _settings()
    s.setValue(f"{key}_show_legend", orig_module.SHOW_LEGEND)
    s.setValue(f"{key}_legend_size", orig_module.LEGEND_SIZE)
    s.setValue(f"{key}_legend_orient", orig_module.LEGEND_ORIENTATION)
    s.setValue(f"{key}_legend_location", orig_module.LEGEND_LOCATION)
    s.setValue(f"{key}_legend_symbols", orig_module.LEGEND_SHOW_SYMBOLS)
    s.setValue(f"{key}_legend_symbol_size", orig_module.LEGEND_SYMBOL_SIZE)
    s.setValue(f"{key}_legend_match_colors", orig_module.LEGEND_MATCH_COLORS)
    s.setValue(f"{key}_show_ticks", orig_module.SHOW_TICK_LABELS)
    s.setValue(f"{key}_tick_size", orig_module.TICK_SIZE)
    s.setValue(f"{key}_show_axis", orig_module.SHOW_AXIS_LABELS)
    s.setValue(f"{key}_axis_size", orig_module.AXIS_LABEL_SIZE)
    s.setValue(f"{key}_show_title", orig_module.SHOW_TITLE)
    s.setValue(f"{key}_title_size", orig_module.TITLE_SIZE)


def apply_readability_fonts(title_size: int = 22, base_size: int = 18) -> None:
    """Apply shared font sizes to Matplotlib."""

    plt.rcParams.update({"font.size": base_size, "axes.titlesize": title_size})


def apply_readability(ax: Axes, cfg: dict) -> None:
    """Apply common readability settings to ``ax`` using values from ``cfg``."""

    apply_readability_fonts(cfg.get("TITLE_SIZE", 22), cfg.get("TICK_SIZE", 18))

    if not cfg.get("SHOW_TICK_LABELS", True):
        ax.set_xticklabels([])
        ax.set_yticklabels([])
    else:
        ax.tick_params(labelsize=cfg.get("TICK_SIZE", 18))

    if not cfg.get("SHOW_AXIS_LABELS", True):
        ax.set_xlabel("")
        ax.set_ylabel("")
    else:
        ax.xaxis.label.set_fontsize(cfg.get("AXIS_LABEL_SIZE", 18))
        ax.yaxis.label.set_fontsize(cfg.get("AXIS_LABEL_SIZE", 18))

    if not cfg.get("SHOW_TITLE", True):
        ax.set_title("")
    else:
        ax.title.set_fontsize(cfg.get("TITLE_SIZE", 22))

    legend = ax.get_legend()
    if legend:
        if not cfg.get("SHOW_LEGEND", True):
            legend.set_visible(False)
            return

        handles_existing: list[Any] = []
        for attr in ("legendHandles", "legend_handles"):
            found = getattr(legend, attr, None)
            if found:
                handles_existing = list(found)
                break
        labels_existing = [text.get_text() for text in legend.get_texts()]
        entry_count = max(len(labels_existing), len(handles_existing), 1)
        location_raw = str(cfg.get("LEGEND_LOCATION", "inside") or "inside").strip().lower()
        legend.remove()

        legend_loc = "best"
        bbox = None
        if location_raw in {"outside_right", "outside", "outside right"}:
            legend_loc = "center left"
            bbox = (1.02, 0.5)
        elif location_raw not in {"inside", "auto", "best", ""}:
            legend_loc = location_raw

        legend_kwargs: dict[str, object] = {"loc": legend_loc}
        if bbox is not None:
            legend_kwargs["bbox_to_anchor"] = bbox
            legend_kwargs["borderaxespad"] = 0.0

        orient = str(cfg.get("LEGEND_ORIENTATION", "auto") or "auto").strip().lower()
        if orient == "horizontal":
            legend_kwargs["ncol"] = entry_count
        elif orient == "vertical":
            legend_kwargs["ncol"] = 1

        show_symbols = bool(cfg.get("LEGEND_SHOW_SYMBOLS", False))
        if show_symbols:
            legend_kwargs.setdefault("handlelength", 1.6)
            legend_kwargs.setdefault("handletextpad", 0.8)
        else:
            legend_kwargs.setdefault("handlelength", 0.0001)
            legend_kwargs.setdefault("handletextpad", 0.35)

        if handles_existing and labels_existing:
            legend = ax.legend(handles=handles_existing, labels=labels_existing, **legend_kwargs)
        else:
            legend = ax.legend(**legend_kwargs)

        legend.set_visible(True)
        size = cfg.get("LEGEND_SIZE", 18)
        for text in legend.get_texts():
            try:
                text.set_fontsize(size)
            except Exception:
                pass

        handles: list[Any] = []
        for attr in ("legendHandles", "legend_handles"):
            found = getattr(legend, attr, None)
            if found:
                handles = list(found)
                break

        marker_size = cfg.get("LEGEND_SYMBOL_SIZE", 10)
        match_colors = bool(cfg.get("LEGEND_MATCH_COLORS", False))
        for handle in handles:
            if hasattr(handle, "set_markersize"):
                try:
                    handle.set_markersize(marker_size)
                except Exception:
                    pass
            marker_getter = getattr(handle, "get_marker", None)
            marker_setter = getattr(handle, "set_marker", None)
            linestyle_getter = getattr(handle, "get_linestyle", None)
            has_line = False
            if callable(linestyle_getter):
                try:
                    ls = linestyle_getter()
                except Exception:
                    ls = None
                has_line = ls not in (None, "None", "", " ")
            if isinstance(handle, PathCollection):
                try:
                    if show_symbols:
                        handle.set_sizes([marker_size ** 2])
                        handle.set_alpha(1.0)
                    else:
                        handle.set_sizes([0.1])
                        handle.set_alpha(0.0)
                except Exception:
                    pass
            elif isinstance(handle, Patch):
                try:
                    handle.set_alpha(1.0 if show_symbols else 0.0)
                except Exception:
                    pass
            if callable(marker_setter):
                if not show_symbols:
                    try:
                        marker_setter(None)
                    except Exception:
                        try:
                            marker_setter("")
                        except Exception:
                            pass
                elif not has_line and callable(marker_getter):
                    try:
                        current = marker_getter()
                    except Exception:
                        current = None
                    if current in (None, "", " ", "None"):
                        try:
                            marker_setter("o")
                        except Exception:
                            pass

        if match_colors and handles:

            def _extract_color(handle: Any) -> tuple[float, float, float, float] | None:
                candidates: list[Any] = []
                for attr in ("get_color", "get_facecolor", "get_facecolors", "get_edgecolor"):
                    getter = getattr(handle, attr, None)
                    if not callable(getter):
                        continue
                    try:
                        value = getter()
                    except Exception:
                        continue
                    if value is None:
                        continue
                    candidates.append(value)
                for value in candidates:
                    try:
                        rgba = mcolors.to_rgba_array(value)
                    except Exception:
                        continue
                    if len(rgba):
                        return tuple(rgba[0])
                return None

            for handle, text in zip(handles, legend.get_texts()):
                color = _extract_color(handle)
                if color is not None:
                    try:
                        text.set_color(color)
                    except Exception:
                        pass


__all__ = [
    "get_readability",
    "set_readability",
    "ReadabilityControls",
    "create_readability_group",
    "sync_readability",
    "apply_readability_fonts",
    "apply_readability",
]
