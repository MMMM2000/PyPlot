"""Interactive microscope capture, reference calibration and sample-linked widths."""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtMultimedia import QCamera, QMediaCaptureSession, QMediaDevices, QVideoSink

from plotting.shared.theme import ensure_app_theme
from .measurements import Calibration, make_calibration, measure_line, save_session
from .samples import load_project_samples, load_fabrication_samples


class ImageView(QtWidgets.QGraphicsView):
    line_drawn = QtCore.pyqtSignal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QtWidgets.QGraphicsScene(self))
        self.image_item = self.scene().addPixmap(QtGui.QPixmap())
        self.pending = None
        self.measure_enabled = False
        self.lines = []
        self.setMinimumSize(400, 300)
        self.setBackgroundBrush(QtGui.QColor("#24282d"))
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)

    def set_image(self, image, *, fit=False):
        self.image_item.setPixmap(QtGui.QPixmap.fromImage(image))
        self.scene().setSceneRect(0, 0, image.width(), image.height())
        if fit:
            self.fit_image()

    def fit_image(self):
        if not self.image_item.pixmap().isNull():
            self.fitInView(self.image_item, QtCore.Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_image()

    def wheelEvent(self, event):
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self.scale(factor, factor)

    def clear_lines(self):
        self.pending = None
        for item in self.lines:
            self.scene().removeItem(item)
        self.lines.clear()

    def add_line(self, start, end, label, color="#25dfb0"):
        pen = QtGui.QPen(QtGui.QColor(color), 2)
        pen.setCosmetic(True)
        line = self.scene().addLine(*start, *end, pen)
        text = self.scene().addText(label)
        text.setDefaultTextColor(QtGui.QColor(color))
        text.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        text.setPos(end[0], end[1])
        self.lines.extend([line, text])

    def mousePressEvent(self, event):
        if not self.measure_enabled or event.button() != QtCore.Qt.MouseButton.LeftButton:
            self.pending = None
            return super().mousePressEvent(event)
        point = self.mapToScene(event.position().toPoint())
        if not self.image_item.boundingRect().contains(point):
            return
        value = (point.x(), point.y())
        if self.pending is None:
            self.pending = value
            self.setToolTip("Click the opposite edge. Right-click cancels the first point.")
        else:
            start, self.pending = self.pending, None
            self.line_drawn.emit(start, value)


class CatalogTask(QtCore.QObject):
    loaded = QtCore.pyqtSignal(str, str, object)
    failed = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    def __init__(self, kind, path):
        super().__init__()
        self.kind, self.path = kind, path
        self.cancelled = threading.Event()

    def run(self):
        try:
            loader = load_project_samples if self.kind == "project" else load_fabrication_samples
            choices = loader(self.path, cancel_callback=self.cancelled.is_set) if self.kind == "fabrication" else loader(self.path)
            if not self.cancelled.is_set():
                self.loaded.emit(self.kind, str(self.path.resolve()), choices)
        except Exception as exc:
            if not self.cancelled.is_set():
                self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, *, enumerate_cameras=True, settings=None):
        super().__init__()
        self.setWindowTitle("Microscope Diameter — PyPlot")
        self.resize(1280, 860)
        self.settings = settings or QtCore.QSettings("microwire", "microscope_logger")
        self.camera = None
        self.session = None
        self.sink = None
        self.media_devices = None
        self.last_frame = None
        self.image = QtGui.QImage()
        self.frame_camera_id = ""
        self.frame_format = None
        self.preview_generation = 0
        self.preview_fitted = False
        self.image_identity = ""
        self.source_file_hash = None
        self.captured_utc = None
        self.loaded_utc = None
        self.calibration_evidence = None
        self.source_file = None
        self.records = []
        self.calibration = None
        self.last_line = None
        self.profiles = {}
        self.catalogs = {}
        self.catalog_tasks = []
        self.last_display = 0.0
        self.dirty = False
        self._build_ui()
        self._load_settings()
        if enumerate_cameras:
            self.refresh_cameras()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QHBoxLayout(central)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(325)
        scroll.setMaximumWidth(420)
        panel = QtWidgets.QWidget()
        controls = QtWidgets.QVBoxLayout(panel)
        scroll.setWidget(panel)
        layout.addWidget(scroll)

        camera_box = QtWidgets.QGroupBox("Image")
        form = QtWidgets.QFormLayout(camera_box)
        self.camera_combo = QtWidgets.QComboBox()
        self.camera_combo.setMinimumContentsLength(14)
        self.camera_combo.currentIndexChanged.connect(self._camera_changed)
        form.addRow("Camera", self.camera_combo)
        self.format_combo = QtWidgets.QComboBox()
        form.addRow("Resolution", self.format_combo)
        self.refresh_button = QtWidgets.QPushButton("Refresh cameras")
        self.refresh_button.clicked.connect(self.refresh_cameras)
        form.addRow(self.refresh_button)
        self.preview_button = QtWidgets.QPushButton("Start preview")
        self.preview_button.clicked.connect(self.toggle_preview)
        form.addRow(self.preview_button)
        self.capture_button = QtWidgets.QPushButton("Capture frame to measure")
        self.capture_button.setEnabled(False)
        self.capture_button.clicked.connect(self.capture_frame)
        form.addRow(self.capture_button)
        open_button = QtWidgets.QPushButton("Open image…")
        open_button.clicked.connect(self.choose_image)
        form.addRow(open_button)
        controls.addWidget(camera_box)

        sample_box = QtWidgets.QGroupBox("Sample")
        form = QtWidgets.QFormLayout(sample_box)
        self.project_path = QtWidgets.QLineEdit()
        self.fabrication_path = QtWidgets.QLineEdit()
        for label, field, kind in (("Project (.pydpj)", self.project_path, "project"),
                                   ("Fabrication folder", self.fabrication_path, "fabrication")):
            row = QtWidgets.QWidget()
            row_layout = QtWidgets.QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(field)
            browse = QtWidgets.QPushButton("…")
            browse.setMaximumWidth(32)
            browse.clicked.connect(lambda checked=False, k=kind: self._browse_catalog(k))
            row_layout.addWidget(browse)
            load = QtWidgets.QPushButton("Load")
            load.clicked.connect(lambda checked=False, k=kind: self._load_catalog(k))
            row_layout.addWidget(load)
            form.addRow(label, row)
        self.composition_combo = QtWidgets.QComboBox()
        self.composition_combo.setEditable(True)
        self.wire_combo = QtWidgets.QComboBox()
        self.wire_combo.setEditable(True)
        self.composition_combo.currentTextChanged.connect(self._composition_changed)
        self.wire_combo.currentTextChanged.connect(self._mark_dirty)
        form.addRow("Composition", self.composition_combo)
        form.addRow("Microwire / piece", self.wire_combo)
        self.sample_status = QtWidgets.QLabel("Load a source, or type a sample identity.")
        self.sample_status.setWordWrap(True)
        form.addRow(self.sample_status)
        controls.addWidget(sample_box)

        calibration_box = QtWidgets.QGroupBox("Scale and measurement")
        form = QtWidgets.QFormLayout(calibration_box)
        self.objective_combo = QtWidgets.QComboBox()
        self.objective_combo.setEditable(True)
        self.objective_combo.addItems(["4×", "10×", "40×"])
        self.objective_combo.currentTextChanged.connect(self._context_changed)
        form.addRow("Objective", self.objective_combo)
        self.zoom_spin = QtWidgets.QDoubleSpinBox()
        self.zoom_spin.setRange(1, 20)
        self.zoom_spin.setDecimals(2)
        self.zoom_spin.setValue(1)
        self.zoom_spin.valueChanged.connect(self._context_changed)
        form.addRow("Acquisition digital zoom", self.zoom_spin)
        self.scale_status = QtWidgets.QLabel("Uncalibrated · pixel measurements")
        self.scale_status.setWordWrap(True)
        form.addRow(self.scale_status)
        self.action_combo = QtWidgets.QComboBox()
        self.action_combo.addItems(["Measure width", "Draw reference length"])
        form.addRow("Two-click tool", self.action_combo)
        self.kind_combo = QtWidgets.QComboBox()
        for label, value in (("Apparent width", "apparent"), ("Outer diameter", "outer"),
                             ("Metal-core width", "core")):
            self.kind_combo.addItem(label, value)
        form.addRow("Boundary measured", self.kind_combo)
        self.known_um = QtWidgets.QDoubleSpinBox()
        self.known_um.setRange(0.001, 1000000)
        self.known_um.setDecimals(3)
        self.known_um.setValue(100)
        form.addRow("Reference length (µm)", self.known_um)
        self.reference_source = QtWidgets.QLineEdit()
        self.reference_source.setPlaceholderText("Reference name / certificate / known size")
        form.addRow("Reference", self.reference_source)
        self.calibrate_button = QtWidgets.QPushButton("Calibrate using reference line")
        self.calibrate_button.clicked.connect(self.calibrate)
        form.addRow(self.calibrate_button)
        self.clear_scale_button = QtWidgets.QPushButton("Use pixels only")
        self.clear_scale_button.clicked.connect(self.clear_calibration)
        form.addRow(self.clear_scale_button)
        help_label = QtWidgets.QLabel("Capture or open an image. Click opposite edges perpendicular to the wire. Wheel zooms the view; acquisition zoom above describes the microscope setting.")
        help_label.setWordWrap(True)
        form.addRow(help_label)
        controls.addWidget(calibration_box)
        self.notes = QtWidgets.QPlainTextEdit()
        self.notes.setPlaceholderText("Specimen position, preparation, focus or other notes")
        self.notes.setMaximumHeight(75)
        self.notes.textChanged.connect(self._mark_dirty)
        controls.addWidget(self.notes)
        self.save_button = QtWidgets.QPushButton("Save image and measurements…")
        self.save_button.clicked.connect(self.choose_save)
        controls.addStretch()

        right = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.view = ImageView()
        self.view.line_drawn.connect(self.line_drawn)
        right.addWidget(self.view)
        bottom = QtWidgets.QWidget()
        bottom_layout = QtWidgets.QVBoxLayout(bottom)
        toolbar = QtWidgets.QHBoxLayout()
        toolbar.addWidget(QtWidgets.QLabel("Measurements on this image"))
        toolbar.addStretch()
        toolbar.addWidget(self.save_button)
        fit = QtWidgets.QPushButton("Fit image")
        fit.clicked.connect(self.view.fit_image)
        toolbar.addWidget(fit)
        clear = QtWidgets.QPushButton("Clear measurements")
        clear.clicked.connect(self.clear_measurements)
        toolbar.addWidget(clear)
        bottom_layout.addLayout(toolbar)
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["#", "Width (px)", "Width (µm)", "Boundary", "Composition", "Microwire"])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        bottom_layout.addWidget(self.table)
        right.addWidget(bottom)
        right.setSizes([600, 180])
        layout.addWidget(right, stretch=1)
        self.statusBar().showMessage("Choose a camera and start preview, or open an image.")

    def _load_settings(self):
        self.project_path.setText(self.settings.value("project_path", "", type=str))
        self.fabrication_path.setText(self.settings.value("fabrication_path", "", type=str))
        try:
            self.profiles = json.loads(self.settings.value("calibration_profiles", "{}", type=str))
            if not isinstance(self.profiles, dict):
                self.profiles = {}
        except (ValueError, TypeError):
            self.profiles = {}

    def refresh_cameras(self):
        if self.camera is not None:
            return
        if self.media_devices is None:
            self.media_devices = QMediaDevices(self)
        self.camera_combo.clear()
        self.camera_combo.addItem("Select camera…", None)
        for device in QMediaDevices.videoInputs():
            self.camera_combo.addItem(device.description(), device)
        for index in range(1, self.camera_combo.count()):
            if "CamGCC" in self.camera_combo.itemText(index):
                self.camera_combo.setCurrentIndex(index)
                break

    def _camera_changed(self):
        self.format_combo.clear()
        device = self.camera_combo.currentData()
        if device is None:
            return
        formats = sorted(device.videoFormats(), key=lambda f: (
            f.resolution().width() * f.resolution().height(), f.maxFrameRate()), reverse=True)
        for fmt in formats:
            self.format_combo.addItem(f"{fmt.resolution().width()} × {fmt.resolution().height()} · {fmt.maxFrameRate():g} fps · {fmt.pixelFormat().name.removeprefix('Format_')}", fmt)

    def _set_connected(self, connected):
        self.camera_combo.setEnabled(not connected)
        self.format_combo.setEnabled(not connected)
        self.refresh_button.setEnabled(not connected)
        self.preview_button.setText("Stop preview" if connected else "Start preview")
        self.view.measure_enabled = not connected and not self.image.isNull()

    def toggle_preview(self):
        if self.camera is not None:
            self.stop_preview()
            return
        device, fmt = self.camera_combo.currentData(), self.format_combo.currentData()
        if device is None or fmt is None:
            self.statusBar().showMessage("Select a camera and resolution first.")
            return
        if not self._confirm_replace():
            return
        self.records.clear()
        self.view.clear_lines()
        self._refresh_table()
        self.image = QtGui.QImage()
        self.dirty = False
        self.last_frame = None
        self.source_file = None
        self.camera = QCamera(device, self)
        self.camera.setCameraFormat(fmt)
        self.session = QMediaCaptureSession(self)
        self.sink = QVideoSink(self)
        self.session.setCamera(self.camera)
        self.session.setVideoSink(self.sink)
        self.preview_generation += 1
        generation = self.preview_generation
        self.preview_fitted = False
        self.sink.videoFrameChanged.connect(lambda frame: self._on_frame(frame, generation))
        self.camera.errorOccurred.connect(lambda error, message: self._camera_error(error, message, generation))
        self.frame_camera_id = bytes(device.id()).decode("utf-8", "replace")
        self._set_connected(True)
        self.camera.start()
        self.statusBar().showMessage("Starting preview. Close other software using this camera if it is busy.")

    def _on_frame(self, frame, generation=None):
        if self.camera is None or (generation is not None and generation != self.preview_generation):
            return
        image = frame.toImage()
        if image.isNull():
            return
        self.last_frame = image.copy()
        self.capture_button.setEnabled(True)
        now = time.monotonic()
        if now - self.last_display >= 1 / 15:
            self.view.set_image(image, fit=not self.preview_fitted)
            self.preview_fitted = True
            self.last_display = now
        self.statusBar().showMessage(f"Live · {image.width()} × {image.height()} · capture a frame to measure")

    def _camera_error(self, error, message, generation=None):
        if self.camera is None or (generation is not None and generation != self.preview_generation):
            return
        self.stop_preview()
        self.statusBar().showMessage(f"Camera: {message}. Close other camera applications and retry.")

    def stop_preview(self):
        self.preview_generation += 1
        if self.camera is not None:
            self.sink.videoFrameChanged.disconnect()
            self.camera.errorOccurred.disconnect()
            self.camera.stop()
            self.session.setCamera(None)
            self.session.setVideoSink(None)
            self.camera.deleteLater()
            self.sink.deleteLater()
            self.session.deleteLater()
            self.camera = self.session = self.sink = None
        self.capture_button.setEnabled(False)
        self._set_connected(False)

    def capture_frame(self):
        if self.last_frame is None:
            return
        image = self.last_frame.copy()
        fmt = self.camera.cameraFormat() if self.camera is not None else None
        self.frame_format = None if fmt is None else dict(
            width=fmt.resolution().width(), height=fmt.resolution().height(),
            pixel_format=fmt.pixelFormat().name, max_fps=fmt.maxFrameRate())
        self.stop_preview()
        self.set_image(image, camera_id=self.frame_camera_id)

    def choose_image(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open microscope image", "", "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp)")
        if path:
            self.load_image(Path(path))

    def load_image(self, path):
        try:
            raw = Path(path).read_bytes()
        except OSError as exc:
            self.statusBar().showMessage(str(exc))
            return False
        image = QtGui.QImage.fromData(raw)
        if image.isNull():
            self.statusBar().showMessage("Could not read that image.")
            return False
        if not self._confirm_replace():
            return False
        self.stop_preview()
        self.set_image(image)
        self.source_file = str(Path(path).resolve())
        self.source_file_hash = hashlib.sha256(raw).hexdigest()
        self.frame_format = None
        self.loaded_utc = self.captured_utc
        self.captured_utc = None
        return True

    def set_image(self, image, *, camera_id=""):
        self.image = image.copy()
        self.frame_camera_id = camera_id
        self.source_file = None
        self.source_file_hash = None
        self.last_frame = None
        self.captured_utc = datetime.now(timezone.utc).isoformat()
        self.loaded_utc = None
        rgba = image.convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
        self.image_identity = hashlib.sha256(rgba.constBits().asstring(rgba.sizeInBytes())).hexdigest()
        self.records.clear()
        self.last_line = None
        self.view.clear_lines()
        self.view.set_image(image, fit=True)
        self.view.measure_enabled = True
        self._refresh_table()
        self._context_changed()
        self.dirty = True
        self.statusBar().showMessage("Click opposite edges to measure, or draw a reference line to calibrate.")

    def _context(self):
        return dict(objective=self.objective_combo.currentText().strip(),
                    image_size=(self.image.width(), self.image.height()),
                    camera_id=self.frame_camera_id, zoom=self.zoom_spin.value())

    def _profile_key(self):
        context = self._context()
        # Unknown camera: a scale can only be restored for these exact pixels.
        if not self.frame_camera_id:
            context["image_identity"] = self.image_identity
        return json.dumps(context, sort_keys=True)

    def _context_changed(self):
        if not hasattr(self, "scale_status"):
            return
        self.calibration = None
        self.calibration_evidence = None
        if not self.image.isNull():
            try:
                value = self.profiles.get(self._profile_key())
                if value:
                    self.calibration_evidence = value.get("evidence")
                    value = dict(value.get("calibration", value))
                    value["image_size"] = tuple(value["image_size"])
                    self.calibration = Calibration(**value)
            except (KeyError, TypeError, ValueError):
                self.calibration = None
        self.scale_status.setText("Uncalibrated · pixel measurements" if self.calibration is None
                                  else f"Calibrated · {self.calibration.um_per_pixel:.6g} µm/pixel")
        self._mark_dirty()

    def _mark_dirty(self, *_args):
        if not self.image.isNull():
            self.dirty = True

    def clear_calibration(self):
        self.profiles.pop(self._profile_key(), None)
        self._context_changed()

    def _sample(self):
        composition, wire = self.composition_combo.currentText().strip(), self.wire_combo.currentText().strip()
        matches = [choice for kind in ("project", "fabrication") for choice in self.catalogs.get(kind, [])
                   if choice.composition == composition and choice.microwire == wire]
        if matches:
            result = asdict(matches[0])
            result["matching_sources"] = [asdict(choice) for choice in matches]
            return result
        return dict(composition=composition, microwire=wire, source_kind="manual")

    def line_drawn(self, start, end):
        if self.image.isNull() or self.camera is not None:
            return
        try:
            record = measure_line(start, end, calibration=self.calibration,
                                  kind=self.kind_combo.currentData(), **self._context())
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))
            return
        if self.action_combo.currentIndex() == 1:
            self.last_line = (start, end)
            self.view.add_line(start, end, "reference", "#ffd35a")
            self.statusBar().showMessage(f"Reference line: {record['width_px']:.2f} px. Enter its known length, then calibrate.")
            return
        record["sample"] = self._sample()
        record["calibration_evidence"] = self.calibration_evidence
        self.records.append(record)
        self.view.add_line(start, end, str(len(self.records)))
        self.dirty = True
        self._refresh_table()

    def calibrate(self):
        if self.last_line is None or self.image.isNull():
            self.statusBar().showMessage("Choose Draw reference length and click the two ends of a known distance first.")
            return
        if not self.reference_source.text().strip():
            self.statusBar().showMessage("Describe the known reference before calibrating.")
            return
        try:
            length = measure_line(*self.last_line, **self._context())["width_px"]
            self.calibration = make_calibration(length, self.known_um.value(),
                                               source=self.reference_source.text().strip(), **self._context())
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))
            return
        # Preserve the calibration image separately from display annotations.
        if self.settings.format() == QtCore.QSettings.Format.IniFormat:
            reference_dir = Path(self.settings.fileName()).parent / "microscope-calibrations"
        else:
            reference_dir = Path(QtCore.QStandardPaths.writableLocation(
                QtCore.QStandardPaths.StandardLocation.AppLocalDataLocation)) / "microscope-calibrations"
        try:
            data = self._image_png()
            digest = hashlib.sha256(data).hexdigest()
            reference_dir.mkdir(parents=True, exist_ok=True)
            reference_path = reference_dir / f"{digest}.png"
            if not reference_path.exists():
                with reference_path.open("xb") as stream:
                    stream.write(data)
            elif hashlib.sha256(reference_path.read_bytes()).hexdigest() != digest:
                raise ValueError("Saved reference image failed its integrity check")
        except (ValueError, OSError) as exc:
            self.calibration = None
            self.statusBar().showMessage(f"Could not preserve calibration reference: {exc}")
            return
        self.calibration_evidence = dict(image_path=str(reference_path.resolve()),
                                        image_sha256=digest, start=list(self.last_line[0]),
                                        end=list(self.last_line[1]), created_utc=datetime.now(timezone.utc).isoformat())
        self.profiles[self._profile_key()] = dict(calibration=asdict(self.calibration), evidence=self.calibration_evidence)
        self._context_changed()
        self.action_combo.setCurrentIndex(0)
        self.dirty = True
        self.statusBar().showMessage("Scale saved for new measurements. Existing rows retain their original calibration.")

    def _refresh_table(self):
        self.table.setRowCount(len(self.records))
        for row, record in enumerate(self.records):
            sample = record.get("sample", {})
            values = [str(row + 1), f"{record['width_px']:.2f}",
                      "—" if record["width_um"] is None else f"{record['width_um']:.3f}",
                      record["kind"], sample.get("composition", ""), sample.get("microwire", "")]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QtWidgets.QTableWidgetItem(value))

    def _confirm_replace(self):
        return not self.dirty or QtWidgets.QMessageBox.question(
            self, "Unsaved image or measurements", "Discard the unsaved image and measurements?",
            QtWidgets.QMessageBox.StandardButton.Discard | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel) == QtWidgets.QMessageBox.StandardButton.Discard

    def clear_measurements(self):
        if self.records and not self._confirm_replace():
            return
        self.records.clear()
        self.view.clear_lines()
        self.last_line = None
        self._refresh_table()
        self.dirty = True

    def _browse_catalog(self, kind):
        if kind == "project":
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose Builder project", "", "Builder project (*.pydpj)")
        else:
            path = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose fabrication folder")
        if path:
            (self.project_path if kind == "project" else self.fabrication_path).setText(path)
            self._load_catalog(kind)

    def _load_catalog(self, kind):
        field = self.project_path if kind == "project" else self.fabrication_path
        path = field.text().strip()
        if not path:
            return
        if self.catalog_tasks:
            self.sample_status.setText("A source is still loading. Please wait.")
            return
        self.catalogs.pop(kind, None)
        self._refresh_samples()
        task = CatalogTask(kind, Path(path))
        task.loaded.connect(self._catalog_received)
        task.failed.connect(self.sample_status.setText)
        task.finished.connect(lambda: self.catalog_tasks.remove(task) if task in self.catalog_tasks else None)
        self.catalog_tasks.append(task)
        self.sample_status.setText("Reading sample identities…")
        threading.Thread(target=task.run, daemon=True).start()

    def _catalog_received(self, kind, path, choices):
        field = self.project_path if kind == "project" else self.fabrication_path
        if str(Path(field.text().strip()).resolve()) != path:
            self.sample_status.setText("Source path changed while loading. Press Load to read the new path.")
            return
        self._catalog_loaded(kind, choices)

    def _catalog_loaded(self, kind, choices):
        self.catalogs[kind] = choices
        self._refresh_samples()
        self.sample_status.setText(f"Loaded {len(choices)} sample identities. Project bindings take priority for shared identities.")

    def _refresh_samples(self):
        previous = self.composition_combo.currentText()
        previous_wire = self.wire_combo.currentText()
        self.composition_combo.blockSignals(True)
        self.composition_combo.clear()
        values = sorted({choice.composition for choices in self.catalogs.values() for choice in choices})
        self.composition_combo.addItems(values)
        if previous:
            self.composition_combo.setCurrentText(previous)
        self.composition_combo.blockSignals(False)
        self._composition_changed()
        if previous and previous_wire:
            self.wire_combo.setCurrentText(previous_wire)

    def _composition_changed(self):
        composition = self.composition_combo.currentText()
        self.wire_combo.clear()
        self.wire_combo.addItems(sorted({choice.microwire for choices in self.catalogs.values()
                                        for choice in choices if choice.composition == composition}))

    def choose_save(self):
        if self.image.isNull():
            self.statusBar().showMessage("Capture or open an image first.")
            return
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Save a new measurement session")
        if directory:
            try:
                path = self.export_session(Path(directory))
                self.statusBar().showMessage(f"Saved {path}")
            except (ValueError, OSError) as exc:
                self.statusBar().showMessage(f"Could not save: {exc}")

    def _image_png(self):
        if self.image.isNull():
            raise ValueError("No captured image")
        data = QtCore.QByteArray()
        buffer = QtCore.QBuffer(data)
        buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
        if not self.image.save(buffer, "PNG"):
            raise ValueError("Could not encode original image")
        buffer.close()
        return bytes(data)

    def export_session(self, directory):
        data = self._image_png()
        metadata = dict(self._context(), sample=self._sample(), notes=self.notes.toPlainText(),
                        calibration=asdict(self.calibration) if self.calibration else None,
                        calibration_evidence=self.calibration_evidence,
                        source_file=self.source_file, captured_utc=self.captured_utc, loaded_utc=self.loaded_utc,
                        camera_format=self.frame_format)
        if self.source_file:
            metadata["source_file_sha256"] = self.source_file_hash
        records = copy.deepcopy(self.records)
        metadata = copy.deepcopy(metadata)
        references = {}
        for evidence in [metadata.get("calibration_evidence"),
                         *[record.get("calibration_evidence") for record in records]]:
            if evidence:
                reference = Path(evidence["image_path"]).read_bytes()
                digest = evidence["image_sha256"]
                if hashlib.sha256(reference).hexdigest() != digest:
                    raise ValueError("Calibration reference image has changed; restore it before exporting")
                references[digest] = reference
                evidence["image_file"] = f"reference_{digest}.png"
        path = save_session(directory, data, records, metadata, reference_images=references)
        self.dirty = False
        return path

    def closeEvent(self, event):
        if not self._confirm_replace():
            event.ignore()
            return
        self.stop_preview()
        for task in self.catalog_tasks:
            task.cancelled.set()
        self.settings.setValue("project_path", self.project_path.text())
        self.settings.setValue("fabrication_path", self.fabrication_path.text())
        self.settings.setValue("calibration_profiles", json.dumps(self.profiles))
        super().closeEvent(event)


def demo_image():
    image = QtGui.QImage(1280, 720, QtGui.QImage.Format.Format_RGB32)
    image.fill(QtGui.QColor("#eeeeeb"))
    painter = QtGui.QPainter(image)
    painter.fillRect(60, 320, 1160, 80, QtGui.QColor("#25282b"))
    painter.end()
    return image


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--demo", action="store_true", help="Open a synthetic image without camera access")
    args, qt_args = parser.parse_known_args(argv)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([sys.argv[0], *qt_args])
    ensure_app_theme(app)
    window = MainWindow(enumerate_cameras=not args.demo)
    if args.demo:
        window.set_image(demo_image())
        window.dirty = False
    elif args.image:
        window.load_image(args.image)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
