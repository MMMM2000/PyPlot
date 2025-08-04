import sys
import os
from pathlib import Path
import re
from typing import Any, cast, List

from PyQt6 import QtCore, QtWidgets, QtSerialPort
from PyQt6.QtSerialPort import QSerialPortInfo

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parent))
    from logger_ui import Ui_MainWindow
else:
    from .logger_ui import Ui_MainWindow

# =============================================================================
#                            USER CONFIGURATION
#
# 1) LOG_DIR: default directory where logged data will be stored. Modify this
#    path to your preferred location. The value can still be overridden via
#    the --log-dir command line option or the LOG_DIR environment variable.
# Use a logs folder in the user's home directory by default. This path works on
# all platforms and can be overridden via the ``LOG_DIR`` environment variable
# or the ``--log-dir`` command line option.
LOG_DIR = str(Path.home() / "python_plot_logs")

# 2) DEFAULT_PORT_COMMAND: command pre-filled in the command box when the GUI
#    starts. Adjust to match the most common command for your logger.
DEFAULT_PORT_COMMAND = ">2050;1270;1;"

# 3) DEFAULT_LOG_FILE_NAME: suggested file name for new recordings. This value
#    only affects the default text shown in the GUI. The ``.txt`` extension is
#    added automatically when saving.
DEFAULT_LOG_FILE_NAME = "FeSiBP 156_2 s2-1a 74mA 2,5a"
# =============================================================================

DEFAULT_LOG_DIR = os.getenv("LOG_DIR", LOG_DIR)

# Keep references to windows created via :func:`main` to prevent them from
# being garbage-collected when launched from another Qt application.
WINDOWS: list[QtWidgets.QWidget] = []


class InfoLineEdit(QtWidgets.QLineEdit):
    """Line edit with inline info and warning icons.

    The trailing "i" icon displays contextual help for the field. A red
    exclamation mark appears when validation fails; clicking it shows the
    associated error message.
    """

    def __init__(self, info: str = "", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        style = self.style()

        info_icon = style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxInformation)
        self._info_action = self.addAction(info_icon, QtWidgets.QLineEdit.ActionPosition.TrailingPosition)
        self._info_action.triggered.connect(lambda: QtWidgets.QMessageBox.information(self, "Field info", info))

        warn_icon = style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxWarning)
        self._warn_action = self.addAction(warn_icon, QtWidgets.QLineEdit.ActionPosition.TrailingPosition)
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

        # Stress format
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
        # Display descriptive text but keep the raw value for file naming
        self.s_end.addItem("Marked end (a)", "a")
        self.s_end.addItem("Unmarked end (b)", "b")
        form.addRow("Sample end:", self.s_end)
        self.s_anneal = InfoLineEdit("Annealing description, e.g., 74mA")
        self.s_anneal.setMinimumWidth(160)
        self.s_anneal.setText("74mA")
        form.addRow("Annealing:", self.s_anneal)
        self.s_load = QtWidgets.QDoubleSpinBox()
        self.s_load.setDecimals(1)
        self.s_load.setSingleStep(2.5)
        # Disallow negative loads
        self.s_load.setRange(0, 1e6)
        self.s_load.setValue(2.5)
        form.addRow("Load:", self.s_load)
        self.s_dir = QtWidgets.QComboBox()
        self.s_dir.addItem("Loading (a)", "a")
        self.s_dir.addItem("Unloading (b)", "b")
        form.addRow("Load dir:", self.s_dir)
        self.stacked.addWidget(stress)

        # Temperature format
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

        self.t_temp = InfoLineEdit("Measurement temperature, e.g., 300K")
        self.t_temp.setMinimumWidth(100)
        self.t_temp.setText("300K")
        self.t_temp.set_validation(r"^[A-Za-z0-9_.]+$", "Use only letters, numbers, '_' or '.'")
        tform.addRow("Temperature:", self.t_temp)
        self.stacked.addWidget(temp)

        # Maxion format
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

        # connections
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
            temp = self.t_temp.text().strip()
            name = f"{comp} {sample} {number} {anneal} {temp}"
        elif fmt == "Maxion":
            head = self.m_head.value()
            desc = self.m_desc.text().strip()
            coils = self.m_coils.currentText()
            name = f"{head} {desc} {coils} coils"
        else:
            return
        self.target.setText(name)

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, log_dir=DEFAULT_LOG_DIR):
        super().__init__()
        self.log_dir = log_dir
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.setWindowTitle("Data Logger")

        self.ui.lineEdit_log_dir.setText(self.log_dir)
        self.ui.pushButton_browse_dir.clicked.connect(self.choose_log_dir)

        # runtime state
        self.port_response = ""
        self.connected     = False
        self.port_name     = ""
        self.baudrate      = int(self.ui.comboBox_baudrate.currentText())
        self.ui.groupBox_commands.setEnabled(False)

        self.serial = QtSerialPort.QSerialPort()
        self.lock   = QtCore.QMutex()

        # update the on-screen response label every 10 ms
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_response_label)
        self.timer.start(10)

        # logging state
        self.log_file     = None  # will become an open file in start_logging()
        self.sample_count = 2000
        self.sample_idx   = 0
        self.logging_on   = False

        # set up progress bar (Pylance needs cast to know it exists)
        cast(Any, self.ui).progressBar_logging.setMaximum(self.sample_count)
        cast(Any, self.ui).pushButton_cancel.setEnabled(False)
        cast(Any, self.ui).progressBar_logging.setValue(0)
        self.ui.checkBox_subdir.setChecked(False)

        os.makedirs(self.log_dir, exist_ok=True)

        # fill port list and set defaults
        self.populate_ports()
        self.ui.comboBox_baudrate.setCurrentIndex(0)  # highest bitrate
        self.baudrate = int(self.ui.comboBox_baudrate.currentText())

        # show only the base name without extension
        self.ui.lineEdit_log_file.setText(DEFAULT_LOG_FILE_NAME)
        self.ui.lineEdit_log_file.returnPressed.connect(self.start_logging)
        self.ui.lineEdit_port_command.setText(DEFAULT_PORT_COMMAND)

        # expand layout to fit name builder
        self.resize(639, 750)
        self.ui.pushButton_connect_port.move(60, 410)
        self.ui.groupBox_commands.move(40, 460)

        # hide legacy build-name button
        self.ui.pushButton_build_name.hide()

        # create name builder widget
        self.file_box = QtWidgets.QGroupBox("File name", self.ui.centralWidget)
        self.file_box.setGeometry(QtCore.QRect(40, 150, 561, 240))
        box_layout = QtWidgets.QVBoxLayout(self.file_box)
        self.name_builder = FileNameBuilderWidget(self.file_box, self.ui.lineEdit_log_file)
        box_layout.addWidget(self.name_builder)
        # allow quick logging via Enter in the load field
        self.name_builder.s_load.lineEdit().returnPressed.connect(self.start_logging)

        # connect signals
        self.ui.pushButton_connect_port.clicked.connect(self.toggle_connection)
        self.ui.comboBox_port.currentIndexChanged.connect(self.update_port_name)
        self.ui.comboBox_baudrate.currentIndexChanged.connect(self.update_baudrate)
        self.ui.pushButton_send_command.clicked.connect(self.send_command)
        self.ui.pushButton_record.clicked.connect(self.start_logging)
        self.ui.pushButton_cancel.clicked.connect(self.cancel_logging)

    def populate_ports(self):
        """Scan available serial ports and populate the combo box."""
        self.ui.comboBox_port.clear()
        for info in QSerialPortInfo.availablePorts():
            label = info.portName()
            if info.description():
                label += f" - {info.description()}"
            self.ui.comboBox_port.addItem(label, userData=info.portName())
        if self.ui.comboBox_port.count() > 0:
            self.port_name = self.ui.comboBox_port.currentData()

    def toggle_connection(self):
        """Open or close the serial port on button click."""
        if not self.connected:
            self.serial.setPortName(self.port_name)
            self.serial.setBaudRate(self.baudrate)
            self.serial.setFlowControl(QtSerialPort.QSerialPort.FlowControl.NoFlowControl)
            self.serial.setDataBits(QtSerialPort.QSerialPort.DataBits.Data8)
            self.serial.setParity(QtSerialPort.QSerialPort.Parity.NoParity)
            self.serial.setStopBits(QtSerialPort.QSerialPort.StopBits.OneStop)
            if self.serial.open(QtCore.QIODeviceBase.OpenModeFlag.ReadWrite):
                self.serial.clear()
                self.serial.readyRead.connect(self.read_from_port)
                self.connected = True
                self.ui.pushButton_connect_port.setText("Disconnect")
                self.ui.groupBox_commands.setEnabled(True)
        else:
            self.serial.close()
            self.connected = False
            self.ui.pushButton_connect_port.setText("Connect to port")
            self.ui.groupBox_commands.setEnabled(False)

    def update_port_name(self):
        """Keep self.port_name in sync with the combo box selection."""
        self.port_name = self.ui.comboBox_port.currentData()

    def update_baudrate(self):
        """Keep self.baudrate in sync with the combo box selection."""
        self.baudrate = int(self.ui.comboBox_baudrate.currentText())

    def choose_log_dir(self):
        """Prompt for a new directory in which to save log files."""
        new_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select log directory", self.log_dir
        )
        if new_dir:
            self.log_dir = new_dir
            self.ui.lineEdit_log_dir.setText(new_dir)

    def read_from_port(self):
        """
        Read a line from the serial port whenever data arrives.
        Decode from ASCII, update the display, and log to file if active.
        """
        if not self.serial.canReadLine():
            return

        self.lock.lock()
        raw = self.serial.readLine()
        # PyQt6 returns a QByteArray; at runtime bytes(raw) works fine.
        raw_bytes = bytes(raw)            # type: ignore[arg-type]
        self.port_response = raw_bytes.decode('ascii')

        if self.logging_on:
            assert self.log_file is not None

            # strip leading '>' if present, then write
            self.log_file.write(self.port_response.lstrip(">"))
            self.sample_idx += 1
            cast(Any, self.ui).progressBar_logging.setValue(self.sample_idx)

            if self.sample_idx >= self.sample_count:
                self.log_file.close()
                self.logging_on = False
                self.ui.pushButton_record.setEnabled(True)
                self.ui.pushButton_cancel.setEnabled(False)

        self.lock.unlock()

    def update_response_label(self):
        """Refresh the on-screen label with the latest port_response."""
        self.ui.label_port_response.setText(self.port_response)

    def send_command(self):
        """Send the text from the command line edit down the serial port."""
        cmd = self.ui.lineEdit_port_command.text() + "\n"
        self.serial.write(cmd.encode('ascii'))

    def start_logging(self):
        """
        Prompt the user for a log-file location, open the file,
        and begin writing incoming samples to it.
        """
        file_base = self.ui.lineEdit_log_file.text()
        initial   = os.path.join(self.log_dir, f"{file_base}.txt")
        path, _   = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Select log file",
            initial,
            "Text files (*.txt)"
        )
        if not path:
            return

        if not path.endswith(".txt"):
            path += ".txt"

        use_sub = self.ui.checkBox_subdir.isChecked()
        file_base = os.path.splitext(os.path.basename(path))[0]
        if use_sub:
            parts = file_base.split()
            if len(parts) > 1:
                folder = " ".join(parts[:-1])
                folder_path = os.path.join(os.path.dirname(path), folder)
                os.makedirs(folder_path, exist_ok=True)
                full_path = os.path.join(folder_path, f"{file_base}.txt")
                self.log_dir = folder_path
            else:
                full_path = path
                self.log_dir = os.path.dirname(path)
        else:
            full_path = path
            self.log_dir = os.path.dirname(path)
        self.ui.lineEdit_log_dir.setText(self.log_dir)
        self.ui.lineEdit_log_file.setText(file_base)
        try:
            self.log_file = open(full_path, "w")
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to open {full_path}: {exc}")
            return

        self.sample_count = self.ui.spinBox_log_sample_count.value()
        self.sample_idx   = 0
        self.logging_on   = True

        self.ui.pushButton_record.setEnabled(False)
        self.ui.pushButton_cancel.setEnabled(True)

        cast(Any, self.ui).progressBar_logging.setMaximum(self.sample_count)
        cast(Any, self.ui).progressBar_logging.setValue(0)

    def cancel_logging(self):
        """Abort the current logging session."""
        if not self.logging_on:
            return
        assert self.log_file is not None

        self.log_file.close()
        self.logging_on = False
        self.ui.pushButton_record.setEnabled(True)
        self.ui.pushButton_cancel.setEnabled(False)

def main(argv: List[str] | None = None) -> QtWidgets.QWidget:
    """Launch the data logger window and return the created widget.

    When called from another running Qt application (e.g. :class:`launcher.MasterLauncher`)
    no additional :class:`~PyQt6.QtWidgets.QApplication` instance will be created
    and control is returned immediately after showing the window. The caller's
    event loop continues running in this case.
    """

    import argparse

    parser = argparse.ArgumentParser(description="Serial data logger (PyQt6)")
    parser.add_argument(
        "--log-dir",
        help="Directory to save logs [env: LOG_DIR]",
    )
    args = parser.parse_args(argv)

    log_dir = args.log_dir or DEFAULT_LOG_DIR

    app = QtWidgets.QApplication.instance()
    owns_app = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        owns_app = True

    window = MainWindow(log_dir)
    window.show()

    WINDOWS.append(window)

    if owns_app:
        sys.exit(app.exec())
    return window

if __name__ == "__main__":
    main()
