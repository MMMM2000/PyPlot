from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple, cast

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from plotting.shared.backends import wants_matplotlib, wants_origin
from plotting.shared.origin import origin_session


def parse_float_str(value: str) -> float:
    text = str(value).strip().replace(" ", "")
    comma_count = text.count(",")
    dot_count = text.count(".")
    if comma_count and dot_count:
        if text.rfind(",") > text.rfind("."):
            decimal_sep, group_sep = ",", "."
        else:
            decimal_sep, group_sep = ".", ","
        text = text.replace(group_sep, "")
        if decimal_sep != ".":
            text = text.replace(decimal_sep, ".")
    elif comma_count == 1 and dot_count == 0:
        text = text.replace(",", ".")
    elif dot_count > 1 and comma_count == 0:
        text = text.replace(".", "")
    return float(text)


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
    count = len(vals)
    for bins in range(count, 1, -1):
        counts, _ = np.histogram(vals, bins=bins, range=(hmin, hmax))
        if np.all(counts > 0):
            return bins
    return max(2, min(50, count // 2))


def _histogram_payload(
    paths: Sequence[str],
    labels: tuple[str, str],
    *,
    core_bins: int,
    core_min: int,
    bin_mode: str,
    bin_width: float,
    share_bins: bool,
):
    raw_data: Dict[str, pd.DataFrame] = {}
    filtered_data: Dict[str, pd.DataFrame] = {}
    masks: Dict[str, np.ndarray] = {}
    hist: Dict[str, Dict[str, Dict[str, np.ndarray]]] = {}
    for path in paths:
        name = os.path.splitext(os.path.basename(path))[0]
        raw = pd.read_csv(
            path,
            sep=";",
            header=None,
            usecols=[0, 1],
            names=list(labels),
            converters={labels[0]: parse_float_str, labels[1]: parse_float_str},
        )
        raw.columns = ["TT", "HH"]
        raw["TTn0"] = raw["TT"] / raw["TT"].max()
        raw["HHn0"] = raw["HH"] / raw["HH"].max()
        mask_t, _, _ = core_mask(raw["TTn0"].to_numpy(dtype=float), max(2, int(core_bins)), max(1, int(core_min)))
        mask_h, _, _ = core_mask(raw["HHn0"].to_numpy(dtype=float), max(2, int(core_bins)), max(1, int(core_min)))
        mask = mask_t & mask_h
        filtered = raw.loc[mask, ["TT", "HH"]].reset_index(drop=True)
        filtered["TTn"] = filtered["TT"] / filtered["TT"].max()
        filtered["HHn"] = filtered["HH"] / filtered["HH"].max()
        raw_data[name] = raw
        filtered_data[name] = filtered
        masks[name] = mask
        hist[name] = {}
        vals_tt = filtered["TTn"].to_numpy(dtype=float)
        vals_hh = filtered["HHn"].to_numpy(dtype=float)
        for column, vals in ((labels[0], vals_tt), (labels[1], vals_hh)):
            hmin, hmax = vals.min(), vals.max()
            if bin_mode == "auto":
                if share_bins:
                    bins = min(find_auto_bins(vals_tt), find_auto_bins(vals_hh))
                else:
                    bins = find_auto_bins(vals)
                counts, edges = np.histogram(vals, bins=bins, range=(hmin, hmax))
            else:
                edges = np.arange(hmin, hmax + bin_width, bin_width)
                counts, _ = np.histogram(vals, bins=edges)
            centers = 0.5 * (edges[:-1] + edges[1:])
            dh = edges[1] - edges[0]
            ni = np.cumsum(counts[::-1])[::-1]
            hazard = counts / (ni + 1e-12)
            dp = (hazard / dh) / (hazard.sum() + 1e-12)
            hist[name][column] = {"centers": centers, "counts": counts, "dp": dp, "dh": dh}
    return raw_data, filtered_data, masks, hist


def build_figures(paths: Sequence[str], config: Dict[str, Any]) -> List[Tuple[Any, str]]:
    labels = config.get("labels", ("TT", "HH"))
    backend = config.get("backend", "matplotlib")
    raw_data, filtered_data, masks, hist = _histogram_payload(
        paths,
        labels,
        core_bins=int(config["core_bins"]),
        core_min=int(config["core_min"]),
        bin_mode=str(config["bin_mode"]),
        bin_width=float(config["bin_width"]),
        share_bins=bool(config["share_bins"]),
    )
    figures: List[Tuple[Any, str]] = []
    if not wants_matplotlib(backend):
        return figures
    for name, filtered in filtered_data.items():
        mask = masks[name]
        raw = raw_data[name]
        if config["raw"]:
            fig, ax = plt.subplots(figsize=(6, 3))
            ax.scatter(filtered.index + 1, filtered["TT"], s=2, label=f"{labels[0]} inlier")
            ax.scatter(filtered.index + 1, filtered["HH"], s=2, label=f"{labels[1]} inlier", color="C1")
            if config["show_trimmed"]:
                trimmed = ~mask
                ax.scatter(np.where(trimmed)[0] + 1, raw["TT"][trimmed], s=20, c="r", marker="x", label=f"{labels[0]} trimmed")
                ax.scatter(np.where(trimmed)[0] + 1, raw["HH"][trimmed], s=20, c="m", marker="x", label=f"{labels[1]} trimmed")
            ax.set_title(f"{name} — Raw with Histogram-Core filter")
            ax.set_xlabel("Index")
            ax.set_ylabel("Switching Field")
            ax.legend(fontsize="x-small")
            ax.grid(True, ls="--", alpha=0.3)
            fig.tight_layout()
            figures.append((fig, f"{name}_raw"))
        if config["hist"]:
            for column, payload in hist[name].items():
                fig, ax = plt.subplots()
                ax.bar(payload["centers"], payload["counts"], width=payload["dh"], edgecolor="k", alpha=0.6)
                ax.set_title(f"{name} — {column}: counts")
                ax.set_xlabel("h = H/Hsw,max")
                ax.set_ylabel("Counts")
                ax.grid(ls="--", alpha=0.3)
                fig.tight_layout()
                figures.append((fig, f"{name}_{column}_counts"))
        if config["ind_log"]:
            for column, payload in hist[name].items():
                valid = payload["dp"] > 0
                fig, ax = plt.subplots()
                ax.plot((1 - payload["centers"][valid]) ** 1.5, np.log(payload["dp"][valid]), "-o", markersize=4)
                ax.set_title(f"{name} — {column}: ln(dp/dh) vs Δh^(3/2)")
                ax.set_xlabel(r"$\Delta h^{3/2}$")
                ax.set_ylabel(r"$\ln(dp/dh)$")
                ax.grid(ls="--", alpha=0.3)
                fig.tight_layout()
                figures.append((fig, f"{name}_{column}_log"))
        if config["comb_log"]:
            fig, ax = plt.subplots()
            for column, payload in hist[name].items():
                valid = payload["dp"] > 0
                ax.plot((1 - payload["centers"][valid]) ** 1.5, np.log(payload["dp"][valid]), "-o", markersize=4, label=column)
            ax.set_title(f"{name} — Combined ln(dp/dh)")
            ax.set_xlabel(r"$\Delta h^{3/2}$")
            ax.set_ylabel(r"$\ln(dp/dh)$")
            ax.legend()
            ax.grid(ls="--", alpha=0.3)
            fig.tight_layout()
            figures.append((fig, f"{name}_combined_log"))
    return figures


def export_origin(paths: Sequence[str], config: Dict[str, Any]) -> None:
    labels = config.get("labels", ("TT", "HH"))
    _raw, _filtered, _masks, hist = _histogram_payload(
        paths,
        labels,
        core_bins=int(config["core_bins"]),
        core_min=int(config["core_min"]),
        bin_mode=str(config["bin_mode"]),
        bin_width=float(config["bin_width"]),
        share_bins=bool(config["share_bins"]),
    )
    with origin_session(keep_open=True) as op:
        origin_any: Any = cast(Any, op)
        graph: Any = origin_any.new_graph(template="scatter")
        base_layer: Any = graph[0]
        first_layer = True
        for name in sorted(hist.keys()):
            layer = base_layer if first_layer else graph.add_layer()
            first_layer = False
            for column, color in ((labels[0], "#1f77b4"), (labels[1], "#ff7f0e")):
                payload = hist[name][column]
                valid = payload["dp"] > 0
                sheet: Any = origin_any.new_sheet("w", lname=f"{name}_{column}")
                sheet.from_list(0, ((1 - payload["centers"][valid]) ** 1.5).tolist())
                sheet.from_list(1, np.log(payload["dp"][valid]).tolist())
                sheet.cols_axis("XY")
                plot_obj: Any = layer.add_plot(sheet, coly=1, colx=0, type="y")
                if plot_obj is not None:
                    try:
                        plot_obj.color = color
                        plot_obj.symbol_shape = 2
                    except Exception:
                        pass
            try:
                layer.rescale()
            except Exception:
                pass
        try:
            graph.activate()
            origin_any.lt_exec('page.antialias=1; layer -aa 1;')
            origin_any.lt_exec('lab -xb "$\\Delta h^{3/2}$"; lab -yl "ln(dp/dh)"; legend;')
        except Exception:
            pass
