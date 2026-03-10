"""Origin integration helpers shared across PyPlot modules."""

from __future__ import annotations

import re
import math
import sys
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, cast

from PyQt6 import QtWidgets

_ORIGIN_TOKEN_RE = re.compile(r"[^0-9A-Za-z_]")


def _ensure_origin_sdk_on_path() -> None:
    """Prefer the bundled `origin_ext_python/originpro-main` tree."""

    candidate = Path(__file__).resolve().parents[1] / "origin_ext_python" / "originpro-main"
    if not candidate.exists():
        return
    path_str = str(candidate)
    if path_str in sys.path:
        return
    sys.path.insert(0, path_str)


@contextmanager
def origin_session(*, keep_open: bool = False) -> Iterator[Any]:
    """Return an Origin session; optionally leave Origin open on exit."""

    _ensure_origin_sdk_on_path()
    import originpro as op  # lazy import
    # OriginExt on some Windows setups can leave an invalid automation pointer
    # after a prior crash. Prefer re-attaching before any LT/set_show calls.
    attach = getattr(op, "attach", None)
    if callable(attach):
        try:
            attach()
        except Exception:
            pass
    try:
        op.set_show()
    except Exception:
        pass
    # Probe automation health early so callers get a deterministic error dialog
    # instead of delayed low-level OriginExt crashes.
    probe_ok = False
    lt_int = getattr(op, "lt_int", None)
    if callable(lt_int):
        try:
            _ = lt_int("@V")
            probe_ok = True
        except Exception:
            probe_ok = False
    if not probe_ok:
        # One retry via attach can recover stale pointers.
        if callable(attach):
            try:
                attach()
            except Exception:
                pass
        if callable(lt_int):
            try:
                _ = lt_int("@V")
                probe_ok = True
            except Exception:
                probe_ok = False
    if not probe_ok:
        raise RuntimeError(
            "Origin automation is unavailable (failed to initialize COM session). "
            "Please start Origin once, then retry Open in Origin."
        )
    try:
        yield cast(Any, op)
    finally:
        if keep_open:
            try:
                cast(Any, op).detach()
            except Exception:
                pass
            try:
                schedule_origin_release()
            except Exception:
                pass
        else:
            try:
                cast(Any, op).exit()
            except Exception:
                pass


_ORIGIN_RELEASED = False
_ORIGIN_RELEASE_REGISTERED = False
_ORIGIN_RELEASE_SLOTS: list[Callable[[], None]] = []


def release_origin() -> None:
    """Release control of Origin so the application can be closed."""

    global _ORIGIN_RELEASED
    if _ORIGIN_RELEASED:
        return

    try:
        _ensure_origin_sdk_on_path()
        import originpro as op  # type: ignore
    except Exception:
        return

    try:
        cast(Any, op).detach()
    except Exception:
        pass

    _ORIGIN_RELEASED = True


def schedule_origin_release() -> None:
    """Ensure Origin detaches once the application shuts down."""

    global _ORIGIN_RELEASE_REGISTERED

    if _ORIGIN_RELEASED or _ORIGIN_RELEASE_REGISTERED:
        return

    app = QtWidgets.QApplication.instance()
    if app is None:
        release_origin()
        return

    def _detach() -> None:
        release_origin()

    try:
        app.aboutToQuit.connect(_detach)  # type: ignore[arg-type]
    except Exception:
        release_origin()
        return

    _ORIGIN_RELEASE_SLOTS.append(_detach)
    _ORIGIN_RELEASE_REGISTERED = True


def hide_origin_workbook(origin: Any | None, workbook: Any | None, graph: Any | None = None) -> None:
    """Hide an Origin workbook window so only the graphs remain visible."""

    if workbook is None:
        return

    activator = getattr(workbook, "activate", None)
    if callable(activator):
        try:
            activator()
        except Exception:
            pass

    commands = ("win -h 1;", "window -h 1;", "win -hc 1;", "window -hc 1;")
    executors = (
        getattr(workbook, "lt_exec", None),
        getattr(origin, "lt_exec", None) if origin is not None else None,
    )
    for cmd in commands:
        for executor in executors:
            if not callable(executor):
                continue
            try:
                executor(cmd)
            except Exception:
                continue
            if graph is not None:
                activator = getattr(graph, "activate", None)
                if callable(activator):
                    try:
                        activator()
                    except Exception:
                        pass
            return


def escape_origin_text(text: str) -> str:
    """Escape text for use in LabTalk string literals."""

    return _origin_display_text(text).replace('"', '""')


def _origin_display_text(text: str) -> str:
    """Normalize text for broad Origin font/template compatibility."""

    value = unicodedata.normalize("NFKC", str(text or ""))
    return (
        value
        .replace("\u2013", "-")  # en dash
        .replace("\u2014", "-")  # em dash
        .replace("\u2212", "-")  # math minus
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )


def origin_lt_exec(executor: Any, command: str) -> bool:
    """Execute a LabTalk command, returning True on a non-failing result."""

    if not callable(executor):
        return False
    try:
        result = executor(command)
    except Exception:
        return False
    if result is False:
        return False
    if isinstance(result, (int, float)) and result == 0:
        return False
    return True


def activate_origin_layer(layer: Any) -> None:
    """Best-effort activation of the layer hosting graph labels/axes."""

    if layer is None:
        return
    activator = getattr(layer, "activate", None)
    if callable(activator):
        try:
            activator()
            return
        except Exception:
            pass
    graph = getattr(layer, "graph", None)
    activator = getattr(graph, "activate", None)
    if callable(activator):
        try:
            activator()
        except Exception:
            pass


def origin_safe_token(text: str, *, fallback: str = "Graph", max_len: int | None = None) -> str:
    """Return an Origin-safe short token for graph/page names."""

    cleaned = _ORIGIN_TOKEN_RE.sub("_", str(text).strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = fallback
    if cleaned and cleaned[0].isdigit():
        cleaned = f"{fallback}_{cleaned}"
    if max_len is not None and max_len > 0:
        cleaned = cleaned[:max_len]
    return cleaned


def origin_title_xy(layer: Any) -> tuple[float, float] | None:
    """Compute a centered title position above the plot area in data coords."""

    get_float = getattr(layer, "get_float", None)
    if not callable(get_float):
        return None
    try:
        x_from = float(get_float("x.from"))
        x_to = float(get_float("x.to"))
        y_from = float(get_float("y.from"))
        y_to = float(get_float("y.to"))
    except Exception:
        return None
    values = (x_from, x_to, y_from, y_to)
    if not all(math.isfinite(value) for value in values):
        return None
    x_span = x_to - x_from
    y_span = y_to - y_from
    if x_span <= 0.0 or y_span <= 0.0:
        return None
    # Keep the title above the plotting range. Shared dual-axis exports in
    # particular need a little extra clearance so the title does not collide
    # with top-axis labels or legends.
    return ((x_from + x_to) / 2.0, y_to + (y_span * 0.05))


def _origin_title_font_size(text: str, default: float) -> float:
    length = len(str(text or "").strip())
    if length >= 46:
        return min(default, 14.0)
    if length >= 30:
        return min(default, 16.0)
    return default


def position_origin_title_label(
    label_obj: Any,
    *,
    layer: Any | None = None,
    font_size: float = 18.0,
    title_text: str = "",
) -> None:
    """Style and position an Origin text label as a graph title."""

    if label_obj is None:
        return
    if hasattr(label_obj, "show"):
        try:
            label_obj.show = True
        except Exception:
            pass
    set_int = getattr(label_obj, "set_int", None)
    if callable(set_int):
        for key, value in (
            ("show", 1),
            ("attach", 0),
            ("horzalign", 1),
            ("vertalign", 2),
        ):
            try:
                set_int(key, int(value))
            except Exception:
                continue
    target_x = 50.0
    target_y = 108.0
    if layer is not None:
        computed = origin_title_xy(layer)
        if computed is not None:
            target_x, target_y = computed
    set_float = getattr(label_obj, "set_float", None)
    if callable(set_float):
        resolved_font_size = _origin_title_font_size(title_text, float(font_size))
        for key, value in (
            ("x", target_x),
            ("y", target_y),
            ("fsize", float(resolved_font_size)),
        ):
            try:
                set_float(key, float(value))
            except Exception:
                continue


def set_origin_graph_title(
    origin_any: Any,
    graph: Any,
    primary_layer: Any,
    title: str,
) -> None:
    """Set a graph title with robust placement across Origin templates."""

    display_title = _origin_display_text(title)
    safe_title = escape_origin_text(display_title)
    try:
        graph.lname = display_title
    except Exception:
        pass
    try:
        graph.name = origin_safe_token(display_title, fallback="Graph", max_len=13)
    except Exception:
        pass

    activate_origin_layer(primary_layer)
    layer_lt_exec = getattr(primary_layer, "lt_exec", None)
    if callable(layer_lt_exec):
        origin_lt_exec(layer_lt_exec, f'label -s -n title "{safe_title}";')

    label_getter = getattr(primary_layer, "label", None)
    if callable(label_getter):
        for label_name in ("Title", "title", "py_title"):
            try:
                title_label = label_getter(label_name)
            except Exception:
                title_label = None
            if title_label is None:
                continue
            try:
                title_label.text = display_title
            except Exception:
                continue
            position_origin_title_label(title_label, layer=primary_layer, title_text=display_title)
            return

    add_label = getattr(primary_layer, "add_label", None)
    if callable(add_label):
        try:
            manual = add_label(display_title)
        except Exception:
            manual = None
        if manual is not None:
            position_origin_title_label(manual, layer=primary_layer, title_text=display_title)
            try:
                manual.name = "py_title"
            except Exception:
                pass
            return

    graph_lt_exec = getattr(graph, "lt_exec", None)
    if callable(graph_lt_exec):
        if origin_lt_exec(graph_lt_exec, f'title -s "{safe_title}";'):
            return
    origin_exec = getattr(origin_any, "lt_exec", None)
    if callable(origin_exec):
        origin_lt_exec(origin_exec, f'title -s "{safe_title}";')


def set_origin_axis_title(layer: Any, axis_name: str, title: str) -> None:
    """Set an axis title using object API with LabTalk fallback."""

    display_title = _origin_display_text(title)
    key = str(axis_name).lower()
    axis_obj = None
    axis_method = getattr(layer, "axis", None)
    if callable(axis_method):
        try:
            axis_obj = axis_method(axis_name)
        except Exception:
            axis_obj = None
    title_set = False
    if axis_obj is not None:
        label_obj = getattr(axis_obj, "label", None)
        if label_obj is not None and hasattr(label_obj, "text"):
            try:
                label_obj.text = display_title
                title_set = True
            except Exception:
                pass
        if not title_set:
            for attr in ("title", "text"):
                if hasattr(axis_obj, attr):
                    try:
                        setattr(axis_obj, attr, display_title)
                        title_set = True
                        break
                    except Exception:
                        continue

    cmd: str | None = None
    if not title_set:
        safe_title = escape_origin_text(display_title)
        if key == "x":
            cmd = f'label -s -xb "{safe_title}";'
        elif key == "y":
            cmd = f'label -s -yl "{safe_title}";'
        elif key == "x2":
            cmd = f'label -s -xt "{safe_title}";'
        elif key == "y2":
            cmd = f'label -s -yr "{safe_title}";'
        else:
            cmd = None
        if cmd is None:
            return
    activate_origin_layer(layer)
    lt_exec = getattr(layer, "lt_exec", None)
    if callable(lt_exec) and cmd is not None:
        origin_lt_exec(lt_exec, cmd)
    if callable(lt_exec):
        # Keep axis text neutral/consistent in shared exports instead of inheriting
        # the first curve color for each layer.
        if key in {"x", "y", "x2", "y2"}:
            origin_lt_exec(lt_exec, f"layer.{key}.color = color(black);")
        if key == "y2":
            origin_lt_exec(lt_exec, "layer.y2.showlabel = 1;")
    if key not in {"y", "y2"}:
        return

    label_tokens = ("yl", "YL", "Yl") if key == "y" else ("yr", "YR", "Yr")
    label_getter = getattr(layer, "label", None)
    if not callable(label_getter):
        return
    axis_label = None
    for token in label_tokens:
        try:
            axis_label = label_getter(token)
        except Exception:
            axis_label = None
        if axis_label is not None:
            break
    if axis_label is None:
        return

    set_int = getattr(axis_label, "set_int", None)
    if callable(set_int):
        try:
            set_int("show", 1)
        except Exception:
            pass

    set_float = getattr(axis_label, "set_float", None)
    layer_get_float = getattr(layer, "get_float", None)
    if not callable(set_float) or not callable(layer_get_float):
        return
    label_get_float = getattr(axis_label, "get_float", None)
    existing_label_x: float | None = None
    if callable(label_get_float):
        try:
            candidate = float(label_get_float("x"))
        except Exception:
            candidate = math.nan
        if math.isfinite(candidate):
            existing_label_x = candidate
    try:
        x_from = float(layer_get_float("x.from"))
        x_to = float(layer_get_float("x.to"))
        y_from = float(layer_get_float("y.from"))
        y_to = float(layer_get_float("y.to"))
    except Exception:
        return
    if not all(math.isfinite(value) for value in (x_from, x_to, y_from, y_to)):
        return
    x_span = x_to - x_from
    y_span = y_to - y_from
    if x_span <= 0.0 or y_span <= 0.0:
        return

    # Keep titles just outside axes (not inside data area) while avoiding export clipping.
    offset = x_span * 0.03
    target_x = x_to + offset
    if key == "y":
        target_y = (y_from + y_to) / 2.0
        try:
            set_float("y", target_y)
        except Exception:
            pass
        return
    if key == "y2" and existing_label_x is not None and existing_label_x > 130.0:
        target_x = 130.0
    target_y = (y_from + y_to) / 2.0

    try:
        set_float("x", target_x)
    except Exception:
        pass
    try:
        set_float("y", target_y)
    except Exception:
        pass


__all__ = [
    "origin_session",
    "release_origin",
    "schedule_origin_release",
    "hide_origin_workbook",
    "escape_origin_text",
    "origin_lt_exec",
    "activate_origin_layer",
    "origin_safe_token",
    "origin_title_xy",
    "position_origin_title_label",
    "set_origin_graph_title",
    "set_origin_axis_title",
]
