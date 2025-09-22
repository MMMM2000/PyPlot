"""Utilities for remembering recent naming field values across loggers."""

from __future__ import annotations

from typing import Dict, List, cast

from PyQt6 import QtCore, QtGui, QtWidgets


class LineEditHistory(QtCore.QObject):
    """Track recent QLineEdit text entries and provide arrow-key navigation."""

    def __init__(
        self,
        settings: QtCore.QSettings,
        max_items: int = 5,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._max_items = max(1, max_items)
        self._histories: Dict[str, List[str]] = {}
        self._widget_keys: Dict[int, str] = {}
        self._widgets: Dict[int, QtWidgets.QLineEdit] = {}
        self._positions: Dict[int, int] = {}

    def register(self, key: str, widget: QtWidgets.QLineEdit | None) -> None:
        """Attach *widget* to the history identified by *key*."""

        if widget is None:
            return
        history = self._histories.setdefault(key, self._load_history(key))
        wid = id(widget)
        self._widget_keys[wid] = key
        self._widgets[wid] = widget
        self._positions[wid] = len(history)
        widget.installEventFilter(self)
        widget.textEdited.connect(lambda _text, w_id=wid: self._reset_position(w_id))

    def remember(self, key: str, value: str) -> None:
        """Store *value* as the most recent entry for *key*."""

        text = value.strip()
        if not text:
            return
        history = self._histories.setdefault(key, self._load_history(key))
        # Move any existing match to the front and cap the list length.
        history = [item for item in history if item != text]
        history.insert(0, text)
        if len(history) > self._max_items:
            del history[self._max_items :]
        self._histories[key] = history
        self._settings.setValue(key, history)
        # Reset navigation positions for widgets tied to this key.
        for wid, stored_key in list(self._widget_keys.items()):
            if stored_key == key:
                self._positions[wid] = len(history)

    # Qt overrides -----------------------------------------------------
    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if event.type() == QtCore.QEvent.Type.KeyPress and isinstance(obj, QtWidgets.QLineEdit):
            wid = id(obj)
            key = self._widget_keys.get(wid)
            if key is None:
                return super().eventFilter(obj, event)
            history = self._histories.get(key, [])
            if not history:
                return super().eventFilter(obj, event)
            key_event = cast(QtGui.QKeyEvent, event)
            if key_event.key() == QtCore.Qt.Key.Key_Up:
                self._navigate_history(wid, key, -1)
                return True
            if key_event.key() == QtCore.Qt.Key.Key_Down:
                self._navigate_history(wid, key, 1)
                return True
        return super().eventFilter(obj, event)

    # Helpers ----------------------------------------------------------
    def _load_history(self, key: str) -> List[str]:
        value = self._settings.value(key, [])
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        if isinstance(value, str):
            return [part for part in value.split("\n") if part.strip()]
        return []

    def _reset_position(self, widget_id: int) -> None:
        key = self._widget_keys.get(widget_id)
        if key is None:
            return
        history = self._histories.get(key, [])
        self._positions[widget_id] = len(history)

    def _navigate_history(self, widget_id: int, key: str, direction: int) -> None:
        widget = self._widgets.get(widget_id)
        history = self._histories.get(key, [])
        if widget is None or not history:
            return
        index = self._positions.get(widget_id, len(history))
        count = len(history)
        if direction < 0:  # Up arrow -> move toward older entries
            if index >= count:
                index = 0
            elif index < count - 1:
                index += 1
            else:
                index = count - 1
        else:  # Down arrow -> move toward newer entries / blank
            if index >= count:
                # Stay blank
                widget.setText("")
                widget.selectAll()
                self._positions[widget_id] = len(history)
                return
            if index <= 0:
                index = count
            else:
                index -= 1
        self._positions[widget_id] = index
        text = history[index] if index < count else ""
        widget.setText(text)
        widget.selectAll()
