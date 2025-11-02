"""Origin integration helpers shared across PyPlot modules."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterator, cast

from PyQt6 import QtWidgets


@contextmanager
def origin_session() -> Iterator[Any]:
    """Return an Origin session that is closed on exit."""

    import originpro as op  # lazy import
    try:
        op.set_show()
    except Exception:
        pass
    try:
        yield cast(Any, op)
    finally:
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


__all__ = ["origin_session", "release_origin", "schedule_origin_release"]
