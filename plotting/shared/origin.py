"""Origin integration helpers shared across PyPlot modules."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, cast

from PyQt6 import QtWidgets


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


__all__ = ["origin_session", "release_origin", "schedule_origin_release", "hide_origin_workbook"]
