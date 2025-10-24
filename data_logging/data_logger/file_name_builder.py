from __future__ import annotations

import re

COMPOSITION_TOTAL_TARGET = 100.0
COMPOSITION_TOTAL_TOLERANCE = 0.5
_COMPOSITION_PART_RE = re.compile(r"([A-Za-z]{1,3})(\d+(?:[.,]\d+)?)")

from PyQt6 import QtWidgets, QtGui, QtCore


class InfoLineEdit(QtWidgets.QLineEdit):
    """Line edit with inline info and warning icons."""

    def __init__(self, info: str = "", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        style = self.style()
        if style is None:
            raise RuntimeError("Widget style is unavailable")

        info_icon = style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxInformation)
        info_action = self.addAction(info_icon, QtWidgets.QLineEdit.ActionPosition.TrailingPosition)
        if info_action is None:
            raise RuntimeError("Failed to create info action")
        self._info_action: QtGui.QAction = info_action
        self._info_action.triggered.connect(lambda: QtWidgets.QMessageBox.information(self, "Field info", info))

        warn_icon = style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxWarning)
        warn_action = self.addAction(warn_icon, QtWidgets.QLineEdit.ActionPosition.TrailingPosition)
        if warn_action is None:
            raise RuntimeError("Failed to create warning action")
        self._warn_action: QtGui.QAction = warn_action
        self._warn_action.triggered.connect(self._show_warning)
        self._warn_action.setVisible(False)

        # Disallow spaces and dashes by default to keep file names parseable
        self._pattern = re.compile(r"^[\w,.]*$")
        self._warning = "Only letters, numbers, comma, period, and '_' are allowed."
        self._extra_warning_active = False
        self._extra_warning_text: str | None = None
        self.textChanged.connect(self._validate)

    def set_validation(self, pattern: str, message: str) -> None:
        """Set a custom validation regex and warning message."""

        self._pattern = re.compile(pattern)
        self._warning = message
        self._validate(self.text())

    def set_extra_warning(self, active: bool, message: str | None = None) -> None:
        """Toggle an auxiliary warning message without blocking input."""

        self._extra_warning_active = bool(active)
        if message is not None:
            self._extra_warning_text = message
        elif not active:
            self._extra_warning_text = None
        self._validate(self.text())

    # slots -----------------------------------------------------------------
    def _validate(self, text: str) -> None:  # pragma: no cover - trivial
        pattern_ok = not text or bool(self._pattern.fullmatch(text))
        show = bool(text) and (not pattern_ok or self._extra_warning_active)
        self._warn_action.setVisible(show)

    def _show_warning(self) -> None:  # pragma: no cover - trivial
        text = self.text()
        pattern_ok = not text or bool(self._pattern.fullmatch(text))
        messages: list[str] = []
        if not pattern_ok:
            messages.append(self._warning)
        if self._extra_warning_active and self._extra_warning_text:
            messages.append(self._extra_warning_text)
        if not messages:
            messages.append(self._warning)
        title = "Invalid input" if not pattern_ok else "Input warning"
        if not pattern_ok and self._extra_warning_active:
            title = "Input warning"
        QtWidgets.QMessageBox.warning(self, title, "\n\n".join(messages))


def estimate_composition_total(text: str) -> float | None:
    """Return the summed percentages from a composition token, if available."""

    matches = list(_COMPOSITION_PART_RE.finditer(text or ""))
    if not matches:
        return None
    total = 0.0
    for match in matches:
        raw = match.group(2).replace(",", ".")
        try:
            total += float(raw)
        except ValueError:
            continue
    return total


def composition_warning_state(text: str, *, tolerance: float = COMPOSITION_TOTAL_TOLERANCE) -> tuple[bool, float | None]:
    """Return whether the composition percentages deviate from 100 within tolerance."""

    total = estimate_composition_total(text)
    if total is None:
        return False, None
    if abs(total - COMPOSITION_TOTAL_TARGET) <= tolerance:
        return False, total
    return True, total


class FileNameBuilderWidget(QtWidgets.QWidget):
    """Widget for composing structured file names."""

    def __init__(self, parent: QtWidgets.QWidget, target: QtWidgets.QLineEdit) -> None:
        super().__init__(parent)
        self.target = target
        self.settings = QtCore.QSettings("microwire", "data_logger")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.combo_format = QtWidgets.QComboBox(self)
        self.combo_format.addItems(["Stress", "Temperature", "Maxion", "Custom"])
        layout.addWidget(self.combo_format)

        self.stacked = QtWidgets.QStackedWidget(self)
        layout.addWidget(self.stacked)

        # Stress format -------------------------------------------------
        stress = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(stress)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(4)
        self.s_comp = InfoLineEdit("Chemical composition of the sample, e.g., FeSiBP or FeSiB")
        self.s_comp.setMinimumWidth(300)
        self.s_comp.setText("FeSiBP")
        self.s_comp.set_validation(r"^[A-Za-z0-9]+$", "Use only letters and numbers")
        form.addRow("Composition:", self.s_comp)

        self.s_sample = InfoLineEdit("Microwire identifier, e.g., 156_2")
        self.s_sample.setMinimumWidth(300)
        self.s_sample.setText("156_2")
        self.s_sample.set_validation(r"^[A-Za-z0-9_]+$", "Use only letters, numbers, or '_' ")
        form.addRow("Microwire:", self.s_sample)

        self.s_number = InfoLineEdit("Sample number, e.g., s2-1")
        self.s_number.setMinimumWidth(260)
        self.s_number.setText("s2-1")
        self.s_number.set_validation(
            r"^[A-Za-z0-9]+-[A-Za-z0-9]+$",
            "Use pattern like s2-2 with a single '-'",
        )
        form.addRow("Sample number:", self.s_number)
        self.s_end = QtWidgets.QComboBox()
        self.s_end.addItem("Marked end (a)", "a")
        self.s_end.addItem("Unmarked end (b)", "b")
        form.addRow("Sample end:", self.s_end)
        self.s_anneal = InfoLineEdit(
            "Annealing description, e.g., ascast, 300C, 74mA"
        )
        self.s_anneal.setMinimumWidth(300)
        self.s_anneal.setText("74mA")
        form.addRow("Annealing:", self.s_anneal)
        self.s_load = QtWidgets.QDoubleSpinBox()
        self.s_load.setDecimals(1)
        self.s_load.setSingleStep(2.5)
        self.s_load.setRange(0, 1e6)  # Disallow negative loads
        self.s_load.setValue(2.5)
        form.addRow("Load:", self.s_load)
        self.s_dir = QtWidgets.QComboBox()
        self.s_dir.addItem("Loading (a)", "a")
        self.s_dir.addItem("Unloading (b)", "b")
        form.addRow("Load dir:", self.s_dir)
        self.stacked.addWidget(stress)

        # Temperature format -------------------------------------------
        temp = QtWidgets.QWidget()
        tform = QtWidgets.QFormLayout(temp)
        tform.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        tform.setHorizontalSpacing(8)
        tform.setVerticalSpacing(4)
        self.t_comp = InfoLineEdit("Chemical composition of the sample, e.g., FeSiBP or FeSiB")
        self.t_comp.setMinimumWidth(300)
        self.t_comp.setText("FeSiBP")
        self.t_comp.set_validation(r"^[A-Za-z0-9]+$", "Use only letters and numbers")
        tform.addRow("Composition:", self.t_comp)

        self.t_sample = InfoLineEdit("Microwire identifier, e.g., 156_2")
        self.t_sample.setMinimumWidth(300)
        self.t_sample.setText("156_2")
        self.t_sample.set_validation(r"^[A-Za-z0-9_]+$", "Use only letters, numbers, or '_' ")
        tform.addRow("Microwire:", self.t_sample)

        self.t_number = InfoLineEdit("Sample number, e.g., s2-1")
        self.t_number.setMinimumWidth(260)
        self.t_number.setText("s2-1")
        self.t_number.set_validation(
            r"^[A-Za-z0-9]+-[A-Za-z0-9]+$",
            "Use pattern like s2-2 with a single '-'",
        )
        tform.addRow("Sample number:", self.t_number)

        self.t_anneal = InfoLineEdit("Annealing description, e.g., 74mA")
        self.t_anneal.setMinimumWidth(300)
        self.t_anneal.setText("74mA")
        tform.addRow("Annealing:", self.t_anneal)

        self.t_temp = QtWidgets.QComboBox()
        self.t_temp.setMinimumWidth(100)
        self.t_temp.addItems(["25C", "25-100C", "100C"])
        tform.addRow("Temperature:", self.t_temp)
        self.stacked.addWidget(temp)

        # Maxion format -------------------------------------------------
        maxw = QtWidgets.QWidget()
        mform = QtWidgets.QFormLayout(maxw)
        mform.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        mform.setHorizontalSpacing(8)
        mform.setVerticalSpacing(4)
        self.m_head = QtWidgets.QSpinBox()
        self.m_head.setRange(1, 6)
        mform.addRow("Head:", self.m_head)
        self.m_desc = InfoLineEdit("Description of the experiment")
        self.m_desc.setMinimumWidth(300)
        mform.addRow("Description:", self.m_desc)
        self.m_coils = QtWidgets.QComboBox()
        self.m_coils.addItems(["2", "3"])
        mform.addRow("Coils:", self.m_coils)
        self.stacked.addWidget(maxw)

        # Placeholder for custom format
        self.stacked.addWidget(QtWidgets.QWidget())

        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch(1)
        self.reset_btn = QtWidgets.QPushButton("Reset")
        btn_layout.addWidget(self.reset_btn)
        layout.addLayout(btn_layout)

        # connections ---------------------------------------------------
        self.combo_format.currentIndexChanged.connect(self.on_format_change)
        for w in [
            self.s_comp,
            self.s_sample,
            self.s_number,
            self.s_end,
            self.s_anneal,
            self.s_load,
            self.s_dir,
            self.t_comp,
            self.t_sample,
            self.t_number,
            self.t_anneal,
            self.t_temp,
            self.m_head,
            self.m_desc,
            self.m_coils,
        ]:
            if isinstance(w, QtWidgets.QLineEdit):
                w.textChanged.connect(self.update_name)
            elif isinstance(w, QtWidgets.QComboBox):
                w.currentIndexChanged.connect(self.update_name)
            elif isinstance(w, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
                w.valueChanged.connect(self.update_name)
        for edit in (self.s_comp, self.t_comp):
            edit.textChanged.connect(self._handle_composition_text_changed)
        self.reset_btn.clicked.connect(self.reset_defaults)
        self.load_settings()
        self._refresh_composition_warnings()
        self.on_format_change(self.combo_format.currentIndex())

    def on_format_change(self, idx: int) -> None:
        self.stacked.setCurrentIndex(idx)
        custom = self.combo_format.currentText() == "Custom"
        self.target.setReadOnly(not custom)
        if not custom:
            self.update_name()

    def update_name(self) -> None:
        self._refresh_composition_warnings()
        fmt = self.combo_format.currentText()
        if fmt == "Stress":
            comp = self.s_comp.text().strip()
            sample = self.s_sample.text().strip()
            number = self.s_number.text().strip()
            end = self.s_end.currentData()
            anneal = self.s_anneal.text().strip()
            load_val = self.s_load.value()
            load = f"{int(load_val)}" if load_val.is_integer() else f"{load_val}".replace(".", ",")
            direction = self.s_dir.currentData()
            name = f"{comp} {sample} {number}{end} {anneal} {load}{direction}"
        elif fmt == "Temperature":
            comp = self.t_comp.text().strip()
            sample = self.t_sample.text().strip()
            number = self.t_number.text().strip()
            anneal = self.t_anneal.text().strip()
            temp = self.t_temp.currentText().strip()
            name = f"{comp} {sample} {number} {anneal} {temp}"
        elif fmt == "Maxion":
            head = self.m_head.value()
            desc = self.m_desc.text().strip()
            coils = self.m_coils.currentText()
            name = f"{head} {desc} {coils} coils"
        else:
            return
        self.target.setText(name)
        self.save_settings()

    # settings ---------------------------------------------------------
    def save_settings(self) -> None:
        s = self.settings
        s.setValue("format", self.combo_format.currentIndex())
        s.setValue("s_comp", self.s_comp.text())
        s.setValue("s_sample", self.s_sample.text())
        s.setValue("s_number", self.s_number.text())
        s.setValue("s_end", self.s_end.currentIndex())
        s.setValue("s_anneal", self.s_anneal.text())
        s.setValue("s_load", self.s_load.value())
        s.setValue("s_dir", self.s_dir.currentIndex())
        s.setValue("t_comp", self.t_comp.text())
        s.setValue("t_sample", self.t_sample.text())
        s.setValue("t_number", self.t_number.text())
        s.setValue("t_anneal", self.t_anneal.text())
        s.setValue("t_temp", self.t_temp.currentIndex())
        s.setValue("m_head", self.m_head.value())
        s.setValue("m_desc", self.m_desc.text())
        s.setValue("m_coils", self.m_coils.currentIndex())
        s.setValue("custom_text", self.target.text())

    def load_settings(self) -> None:
        s = self.settings
        widgets: list[QtWidgets.QWidget] = [
            self.combo_format,
            self.s_comp,
            self.s_sample,
            self.s_number,
            self.s_end,
            self.s_anneal,
            self.s_load,
            self.s_dir,
            self.t_comp,
            self.t_sample,
            self.t_number,
            self.t_anneal,
            self.t_temp,
            self.m_head,
            self.m_desc,
            self.m_coils,
        ]
        for w in widgets:
            w.blockSignals(True)
        self.combo_format.setCurrentIndex(int(s.value("format", 0)))
        self.s_comp.setText(s.value("s_comp", "FeSiBP"))
        self.s_sample.setText(s.value("s_sample", "156_2"))
        self.s_number.setText(s.value("s_number", "s2-1"))
        self.s_end.setCurrentIndex(int(s.value("s_end", 0)))
        self.s_anneal.setText(s.value("s_anneal", "74mA"))
        self.s_load.setValue(float(s.value("s_load", 2.5)))
        self.s_dir.setCurrentIndex(int(s.value("s_dir", 0)))
        self.t_comp.setText(s.value("t_comp", "FeSiBP"))
        self.t_sample.setText(s.value("t_sample", "156_2"))
        self.t_number.setText(s.value("t_number", "s2-1"))
        self.t_anneal.setText(s.value("t_anneal", "74mA"))
        self.t_temp.setCurrentIndex(int(s.value("t_temp", 0)))
        self.m_head.setValue(int(s.value("m_head", 1)))
        self.m_desc.setText(s.value("m_desc", ""))
        self.m_coils.setCurrentIndex(int(s.value("m_coils", 0)))
        self.target.setText(s.value("custom_text", ""))
        for w in widgets:
            w.blockSignals(False)
        self._refresh_composition_warnings()

    def reset_defaults(self) -> None:
        self.settings.clear()
        self.load_settings()
        self.update_name()

    # composition helpers ------------------------------------------------
    def _handle_composition_text_changed(self, _text: str) -> None:
        edit = self.sender()
        if isinstance(edit, QtWidgets.QLineEdit):
            self._update_composition_warning(edit)

    def _refresh_composition_warnings(self) -> None:
        for edit in (self.s_comp, self.t_comp):
            self._update_composition_warning(edit)

    def _update_composition_warning(self, edit: QtWidgets.QLineEdit | None) -> None:
        if edit is None or not hasattr(edit, "set_extra_warning"):
            return
        warn, total = composition_warning_state(edit.text())
        if warn and total is not None:
            message = f"Element percentages add up to {total:.2f} %, expected 100."
            edit.set_extra_warning(True, message)
        else:
            edit.set_extra_warning(False)


__all__ = [
    "FileNameBuilderWidget",
    "InfoLineEdit",
    "estimate_composition_total",
    "composition_warning_state",
]
