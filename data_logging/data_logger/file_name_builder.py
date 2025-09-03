from __future__ import annotations

import re

from PyQt6 import QtWidgets, QtGui


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
        self.textChanged.connect(self._validate)

    def set_validation(self, pattern: str, message: str) -> None:
        """Set a custom validation regex and warning message."""

        self._pattern = re.compile(pattern)
        self._warning = message
        self._validate(self.text())

    # slots -----------------------------------------------------------------
    def _validate(self, text: str) -> None:  # pragma: no cover - trivial
        self._warn_action.setVisible(bool(text) and not self._pattern.fullmatch(text))

    def _show_warning(self) -> None:  # pragma: no cover - trivial
        QtWidgets.QMessageBox.warning(self, "Invalid input", self._warning)


class FileNameBuilderWidget(QtWidgets.QWidget):
    """Widget for composing structured file names."""

    def __init__(self, parent: QtWidgets.QWidget, target: QtWidgets.QLineEdit) -> None:
        super().__init__(parent)
        self.target = target

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
        self.s_comp.setMinimumWidth(160)
        self.s_comp.setText("FeSiBP")
        self.s_comp.set_validation(r"^[A-Za-z0-9]+$", "Use only letters and numbers")
        form.addRow("Composition:", self.s_comp)

        self.s_sample = InfoLineEdit("Microwire identifier, e.g., 156_2")
        self.s_sample.setMinimumWidth(160)
        self.s_sample.setText("156_2")
        self.s_sample.set_validation(r"^[A-Za-z0-9_]+$", "Use only letters, numbers, or '_' ")
        form.addRow("Microwire:", self.s_sample)

        self.s_number = InfoLineEdit("Sample number, e.g., s2-1")
        self.s_number.setMinimumWidth(100)
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
        self.s_anneal.setMinimumWidth(160)
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
        self.t_comp.setMinimumWidth(160)
        self.t_comp.setText("FeSiBP")
        self.t_comp.set_validation(r"^[A-Za-z0-9]+$", "Use only letters and numbers")
        tform.addRow("Composition:", self.t_comp)

        self.t_sample = InfoLineEdit("Microwire identifier, e.g., 156_2")
        self.t_sample.setMinimumWidth(160)
        self.t_sample.setText("156_2")
        self.t_sample.set_validation(r"^[A-Za-z0-9_]+$", "Use only letters, numbers, or '_' ")
        tform.addRow("Microwire:", self.t_sample)

        self.t_number = InfoLineEdit("Sample number, e.g., s2-1")
        self.t_number.setMinimumWidth(100)
        self.t_number.setText("s2-1")
        self.t_number.set_validation(
            r"^[A-Za-z0-9]+-[A-Za-z0-9]+$",
            "Use pattern like s2-2 with a single '-'",
        )
        tform.addRow("Sample number:", self.t_number)

        self.t_anneal = InfoLineEdit("Annealing description, e.g., 74mA")
        self.t_anneal.setMinimumWidth(160)
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
        self.m_desc.setMinimumWidth(160)
        mform.addRow("Description:", self.m_desc)
        self.m_coils = QtWidgets.QComboBox()
        self.m_coils.addItems(["2", "3"])
        mform.addRow("Coils:", self.m_coils)
        self.stacked.addWidget(maxw)

        # Placeholder for custom format
        self.stacked.addWidget(QtWidgets.QWidget())

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

        self.on_format_change(0)

    def on_format_change(self, idx: int) -> None:
        self.stacked.setCurrentIndex(idx)
        custom = self.combo_format.currentText() == "Custom"
        self.target.setReadOnly(not custom)
        if not custom:
            self.update_name()

    def update_name(self) -> None:
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


__all__ = ["FileNameBuilderWidget", "InfoLineEdit"]
