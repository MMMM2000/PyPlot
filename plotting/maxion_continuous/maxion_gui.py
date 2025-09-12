from __future__ import annotations

import sys
from PyQt6 import QtWidgets, QtCore

import pathlib

if __package__ is None or __package__ == "":
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from plotting.maxion_continuous import core as orig
    from plotting.utils import apply_system_theme, create_file_widget
else:
    from . import core as orig
    from ..utils import apply_system_theme, create_file_widget


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Maxion Continuous Settings")
        layout = QtWidgets.QGridLayout(self)

        self.settings = QtCore.QSettings("microwire", "maxion_continuous")

        self.files, file_widget = create_file_widget(self)
        layout.addWidget(file_widget, 0, 0, 1, 2)

        self.show_cb = QtWidgets.QCheckBox("Show plots"); self.show_cb.setChecked(orig.SHOW_PLOTS)
        self.save_cb = QtWidgets.QCheckBox("Save plots"); self.save_cb.setChecked(orig.SAVE_PLOTS)
        self.out_dir_edit = QtWidgets.QLineEdit(orig.OUTPUT_DIR)
        browse_btn = QtWidgets.QPushButton("Browse")

        def browse_out() -> None:
            d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select output directory", self.out_dir_edit.text())
            if d:
                self.out_dir_edit.setText(d)

        browse_btn.clicked.connect(browse_out)

        out_group = QtWidgets.QGroupBox("Output")
        out_layout = QtWidgets.QGridLayout(out_group)
        out_layout.addWidget(self.show_cb, 0, 0)
        out_layout.addWidget(self.save_cb, 1, 0)
        self.backend_combo = QtWidgets.QComboBox(); self.backend_combo.addItems(["Matplotlib", "Origin", "Both"])
        self.backend_combo.setCurrentIndex(0)
        out_layout.addWidget(QtWidgets.QLabel("Backend:"), 2, 0)
        out_layout.addWidget(self.backend_combo, 2, 1)
        self.fmt_combo = QtWidgets.QComboBox(); self.fmt_combo.addItems(["png", "pdf", "svg"]); self.fmt_combo.setCurrentText(orig.SAVE_FORMAT)
        self.dpi_spin = QtWidgets.QSpinBox(); self.dpi_spin.setRange(72, 3000); self.dpi_spin.setValue(int(orig.PNG_DPI))
        out_layout.addWidget(QtWidgets.QLabel("Format:"), 3, 0)
        out_layout.addWidget(self.fmt_combo, 3, 1)
        out_layout.addWidget(QtWidgets.QLabel("PNG dpi:"), 4, 0)
        out_layout.addWidget(self.dpi_spin, 4, 1)
        out_layout.addWidget(QtWidgets.QLabel("Directory:"), 5, 0)
        out_layout.addWidget(self.out_dir_edit, 6, 0)
        out_layout.addWidget(browse_btn, 6, 1)

        mode_group = QtWidgets.QGroupBox("Data to plot")
        mode_layout = QtWidgets.QVBoxLayout(mode_group)
        self.raw_rb = QtWidgets.QRadioButton("Raw"); self.raw_rb.setChecked(orig.PLOT_MODE == "raw")
        self.proc_rb = QtWidgets.QRadioButton("Processed"); self.proc_rb.setChecked(orig.PLOT_MODE == "processed")
        self.both_rb = QtWidgets.QRadioButton("Both"); self.both_rb.setChecked(orig.PLOT_MODE == "both")
        mode_layout.addWidget(self.raw_rb)
        mode_layout.addWidget(self.proc_rb)
        mode_layout.addWidget(self.both_rb)

        proc_group = QtWidgets.QGroupBox("Processed curve")
        proc_layout = QtWidgets.QGridLayout(proc_group)
        self.med_spin = QtWidgets.QSpinBox(); self.med_spin.setRange(1, 9999); self.med_spin.setValue(int(orig.MED_WINDOW))
        self.ma_spin = QtWidgets.QSpinBox(); self.ma_spin.setRange(1, 9999); self.ma_spin.setValue(int(orig.MA_WINDOW))
        proc_layout.addWidget(QtWidgets.QLabel("Med window:"), 0, 0)
        proc_layout.addWidget(self.med_spin, 0, 1)
        proc_layout.addWidget(QtWidgets.QLabel("MA window:"), 0, 2)
        proc_layout.addWidget(self.ma_spin, 0, 3)

        style_group = QtWidgets.QGroupBox("Scatter")
        style_layout = QtWidgets.QGridLayout(style_group)
        self.marker_spin = QtWidgets.QDoubleSpinBox()
        self.marker_spin.setRange(0.1, 99.9)
        self.marker_spin.setSingleStep(0.1)
        self.marker_spin.setValue(float(orig.MARKER_SIZE))
        style_layout.addWidget(QtWidgets.QLabel("Marker size:"), 0, 0)
        style_layout.addWidget(self.marker_spin, 0, 1)

        # Readability (collapsible)
        header = QtWidgets.QToolButton()
        header.setText("Readability")
        header.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        header.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        header.setCheckable(True)
        header.setChecked(False)

        self._read_container = QtWidgets.QWidget()
        read_layout = QtWidgets.QGridLayout(self._read_container)
        self.readable_cb = QtWidgets.QCheckBox("Improve readability")
        self.readable_cb.setChecked(
            bool(self.settings.value("readable", orig.IMPROVE_READABILITY, type=bool))
        )
        self.text_size_spin = QtWidgets.QSpinBox()
        self.text_size_spin.setRange(6, 72)
        self.text_size_spin.setValue(int(self.settings.value("text_size", orig.TEXT_SIZE, type=int)))
        self.title_size_spin = QtWidgets.QSpinBox()
        self.title_size_spin.setRange(6, 96)
        self.title_size_spin.setValue(int(self.settings.value("title_size", orig.TITLE_SIZE, type=int)))
        read_layout.addWidget(self.readable_cb, 0, 0, 1, 2)
        read_layout.addWidget(QtWidgets.QLabel("Text size:"), 1, 0)
        read_layout.addWidget(self.text_size_spin, 1, 1)
        read_layout.addWidget(QtWidgets.QLabel("Title size:"), 2, 0)
        read_layout.addWidget(self.title_size_spin, 2, 1)

        self.run_btn = QtWidgets.QPushButton("Run")
        self.run_btn.clicked.connect(self.run)

        layout.addWidget(out_group, 1, 0)
        layout.addWidget(mode_group, 1, 1)
        layout.addWidget(proc_group, 2, 0)
        layout.addWidget(style_group, 2, 1)
        layout.addWidget(header, 3, 0, 1, 2)
        layout.addWidget(self._read_container, 4, 0, 1, 2)
        layout.addWidget(self.run_btn, 5, 0, 1, 2)

        self._read_container.setVisible(False)

        def _toggle_section(checked: bool) -> None:
            self._read_container.setVisible(checked)
            header.setArrowType(QtCore.Qt.ArrowType.DownArrow if checked else QtCore.Qt.ArrowType.RightArrow)

        header.toggled.connect(_toggle_section)

        def _toggle_readable(checked: bool) -> None:
            self.text_size_spin.setEnabled(checked)
            self.title_size_spin.setEnabled(checked)

        _toggle_readable(self.readable_cb.isChecked())
        self.readable_cb.toggled.connect(_toggle_readable)

    def run(self) -> None:
        if not self.files:
            QtWidgets.QMessageBox.warning(self, "No files", "Select files first.")
            return
        orig.SHOW_PLOTS = self.show_cb.isChecked()
        orig.SAVE_PLOTS = self.save_cb.isChecked()
        orig.OUTPUT_DIR = self.out_dir_edit.text()
        orig.PLOT_MODE = "raw" if self.raw_rb.isChecked() else "processed" if self.proc_rb.isChecked() else "both"
        orig.MARKER_SIZE = self.marker_spin.value()
        orig.MED_WINDOW = int(self.med_spin.value())
        orig.MA_WINDOW = int(self.ma_spin.value())
        orig.SAVE_FORMAT = self.fmt_combo.currentText()
        orig.PNG_DPI = int(self.dpi_spin.value())
        orig.IMPROVE_READABILITY = self.readable_cb.isChecked()
        orig.TEXT_SIZE = int(self.text_size_spin.value())
        orig.TITLE_SIZE = int(self.title_size_spin.value())
        self.settings.setValue("readable", orig.IMPROVE_READABILITY)
        self.settings.setValue("text_size", orig.TEXT_SIZE)
        self.settings.setValue("title_size", orig.TITLE_SIZE)
        backend = ["matplotlib", "origin", "both"][self.backend_combo.currentIndex()]
        orig.main(self.files, backend=backend)


class ProgressDialog:
    def __init__(self, total: int):
        self.dialog = QtWidgets.QProgressDialog("Processing...", "Cancel", 0, total)
        self.dialog.setWindowTitle("Processing")
        self.dialog.canceled.connect(self.cancel)
        self.dialog.setAutoClose(False)
        self.dialog.setAutoReset(False)
        self.dialog.show()
        self.cancelled = False
        self.root = self

    def update(self) -> None:
        self.dialog.setValue(self.dialog.value() + 1)
        QtWidgets.QApplication.processEvents()

    def cancel(self) -> None:
        self.cancelled = True
        self.dialog.close()

    def destroy(self) -> None:
        self.dialog.close()


def main() -> None:
    app = QtWidgets.QApplication.instance()
    owns = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        apply_system_theme(app)
        owns = True
    orig.ProgressDialog = ProgressDialog
    dlg = SettingsDialog()
    dlg.show()
    if owns:
        app.exec()


if __name__ == "__main__":
    main()

