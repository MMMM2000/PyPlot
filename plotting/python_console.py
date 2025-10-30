"""Reusable PyQt6 Python console widget for PyPlot applications."""

from __future__ import annotations

import code
import io
import sys
from contextlib import redirect_stdout, redirect_stderr
from typing import Mapping, MutableMapping, cast

from PyQt6 import QtCore, QtGui, QtWidgets


class PythonConsoleWidget(QtWidgets.QWidget):
    """A lightweight interactive console backed by :class:`code.InteractiveConsole`."""

    executed = QtCore.pyqtSignal(str, object)

    _PRIMARY_PROMPT = ">>> "
    _SECONDARY_PROMPT = "... "

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        *,
        banner: str | None = None,
        namespace: MutableMapping[str, object] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("pythonConsoleWidget")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.output = QtWidgets.QPlainTextEdit(self)
        self.output.setObjectName("pythonConsoleOutput")
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        mono_font = QtGui.QFontDatabase.systemFont(
            QtGui.QFontDatabase.SystemFont.FixedFont
        )
        if mono_font is not None:
            self.output.setFont(mono_font)
        layout.addWidget(self.output, 1)

        prompt_layout = QtWidgets.QHBoxLayout()
        prompt_layout.setContentsMargins(8, 4, 8, 8)
        prompt_layout.setSpacing(6)

        self._prompt_label = QtWidgets.QLabel(self._PRIMARY_PROMPT, self)
        self._prompt_label.setObjectName("pythonConsolePrompt")
        prompt_layout.addWidget(self._prompt_label)

        self._input = QtWidgets.QLineEdit(self)
        self._input.setObjectName("pythonConsoleInput")
        self._input.returnPressed.connect(self._execute_current_line)
        self._input.installEventFilter(self)
        prompt_layout.addWidget(self._input, 1)

        layout.addLayout(prompt_layout)
        self.setFocusProxy(self._input)

        self._console = code.InteractiveConsole(locals=dict(namespace or {}))
        self._pending_lines: list[str] = []
        self._history: list[str] = []
        self._history_index = 0

        if banner:
            banner_text = banner.rstrip() + "\n"
            self._append_text(banner_text)

    # ------------------------------------------------------------------ Qt API
    def focusInEvent(self, event: QtGui.QFocusEvent) -> None:  # noqa: D401 - Qt override
        """Ensure the input line receives focus when the widget is activated."""

        super().focusInEvent(event)
        self._input.setFocus()

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if obj is self._input and event.type() == QtCore.QEvent.Type.KeyPress:
            key_event = cast(QtGui.QKeyEvent, event)
            key = key_event.key()
            if key in (QtCore.Qt.Key.Key_Up, QtCore.Qt.Key.Key_Down):
                self._navigate_history(key)
                return True
        return super().eventFilter(obj, event)

    # ----------------------------------------------------------------- helpers
    def set_environment(self, mapping: Mapping[str, object]) -> None:
        """Expose additional names to the console namespace."""

        if not mapping:
            return
        self._console.locals.update(mapping)

    def clear(self) -> None:
        """Clear the console output and reset prompts."""

        self.output.clear()
        self._prompt_label.setText(self._PRIMARY_PROMPT)
        self._pending_lines.clear()

    def _navigate_history(self, key: int) -> None:
        if not self._history:
            return
        if key == QtCore.Qt.Key.Key_Up:
            self._history_index = max(0, self._history_index - 1)
        elif key == QtCore.Qt.Key.Key_Down:
            self._history_index = min(len(self._history), self._history_index + 1)

        if self._history_index == len(self._history):
            self._input.clear()
        else:
            entry = self._history[self._history_index]
            self._input.setText(entry)
            self._input.setCursorPosition(len(entry))

    def _execute_current_line(self) -> None:
        text = self._input.text()
        if not self._pending_lines and not text.strip():
            # Ignore stray blank lines when not composing a statement.
            return

        self._input.clear()
        prompt = self._SECONDARY_PROMPT if self._pending_lines else self._PRIMARY_PROMPT
        self._append_text(f"{prompt}{text}\n")
        self._pending_lines.append(text)

        more, result = self._push_to_console(text)
        self._prompt_label.setText(self._SECONDARY_PROMPT if more else self._PRIMARY_PROMPT)

        if not more:
            block = "\n".join(self._pending_lines).strip("\n")
            self._pending_lines.clear()
            if block.strip():
                self._history.append(block)
            self._history_index = len(self._history)
            self.executed.emit(block, result)

    def _append_text(self, text: str) -> None:
        cursor = self.output.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()

    def _push_to_console(self, line: str) -> tuple[bool, object | None]:
        buffer = io.StringIO()
        last_result: object | None = None
        previous_displayhook = sys.displayhook

        def displayhook(value: object) -> None:
            nonlocal last_result
            last_result = value
            if value is None:
                return
            buffer.write(repr(value) + "\n")

        try:
            sys.displayhook = displayhook
            with redirect_stdout(buffer), redirect_stderr(buffer):
                more = self._console.push(line)
        finally:
            sys.displayhook = previous_displayhook

        output = buffer.getvalue()
        if output:
            self._append_text(output)

        return more, last_result


__all__ = ["PythonConsoleWidget"]

