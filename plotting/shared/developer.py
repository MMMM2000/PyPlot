"""Developer options shared across PyPlot utilities."""

from __future__ import annotations

import weakref

from PyQt6 import QtCore, QtWidgets

class _DeveloperOptions(QtCore.QObject):
    keep_files_changed = QtCore.pyqtSignal(bool)
    experiments_visibility_changed = QtCore.pyqtSignal(bool)
    ocr_debug_changed = QtCore.pyqtSignal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._settings = QtCore.QSettings("microwire", "plotting")
        self._keep_files = bool(self._settings.value("developer_keep_files", False, type=bool))
        self._show_experiments = bool(self._settings.value("developer_show_experiments", True, type=bool))
        self._ocr_debug = bool(self._settings.value("developer_ocr_debug", False, type=bool))
        self._keep_actions: list[weakref.ReferenceType[QtWidgets.QAction]] = []
        self._experiment_actions: list[weakref.ReferenceType[QtWidgets.QAction]] = []
        self._ocr_debug_actions: list[weakref.ReferenceType[QtWidgets.QAction]] = []

    def keep_files(self) -> bool:
        return self._keep_files

    def show_experiments(self) -> bool:
        return self._show_experiments

    def ocr_debug(self) -> bool:
        return self._ocr_debug

    def set_keep_files(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._keep_files:
            return
        self._keep_files = enabled
        self._settings.setValue("developer_keep_files", enabled)
        self.keep_files_changed.emit(enabled)

    def set_show_experiments(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._show_experiments:
            return
        self._show_experiments = enabled
        self._settings.setValue("developer_show_experiments", enabled)
        self.experiments_visibility_changed.emit(enabled)
        for ref in list(self._experiment_actions):
            action = ref()
            if isinstance(action, QtWidgets.QAction):
                with QtCore.QSignalBlocker(action):
                    action.setChecked(enabled)

    def set_ocr_debug(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._ocr_debug:
            return
        self._ocr_debug = enabled
        self._settings.setValue("developer_ocr_debug", enabled)
        self.ocr_debug_changed.emit(enabled)
        for ref in list(self._ocr_debug_actions):
            action = ref()
            if isinstance(action, QtWidgets.QAction):
                with QtCore.QSignalBlocker(action):
                    action.setChecked(enabled)

    def create_menu(self, parent: QtWidgets.QWidget) -> QtWidgets.QMenu:
        menu = QtWidgets.QMenu("&Developer", parent)

        keep_action = menu.addAction("Keep &File Selections")
        if keep_action is not None:
            keep_action.setObjectName("mw_keep_files")
            keep_action.setCheckable(True)
            keep_action.setChecked(self._keep_files)
            keep_action.toggled.connect(self.set_keep_files)
            self._keep_actions.append(weakref.ref(keep_action))

        exp_action = menu.addAction("Show &Experiments Tab")
        if exp_action is not None:
            exp_action.setObjectName("mw_show_experiments")
            exp_action.setCheckable(True)
            exp_action.setChecked(self._show_experiments)
            exp_action.toggled.connect(self.set_show_experiments)
            self._experiment_actions.append(weakref.ref(exp_action))

        ocr_action = menu.addAction("Enable &OCR Debug Signals")
        if ocr_action is not None:
            ocr_action.setObjectName("mw_enable_ocr_debug")
            ocr_action.setCheckable(True)
            ocr_action.setChecked(self._ocr_debug)
            ocr_action.toggled.connect(self.set_ocr_debug)
            self._ocr_debug_actions.append(weakref.ref(ocr_action))

        return menu


_DEVELOPER_OPTIONS: _DeveloperOptions | None = None


def developer_options() -> _DeveloperOptions:
    global _DEVELOPER_OPTIONS
    if _DEVELOPER_OPTIONS is None:
        _DEVELOPER_OPTIONS = _DeveloperOptions()
    return _DEVELOPER_OPTIONS


__all__ = ["developer_options"]
