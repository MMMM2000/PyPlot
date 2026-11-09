"""Interactive microscope capture, reference calibration and sample-linked widths."""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import threading
import time

import numpy as np

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtMultimedia import QCamera, QMediaCaptureSession, QMediaDevices, QVideoSink

from plotting.shared.theme import ensure_app_theme
from .measurements import Calibration, make_calibration, measure_line, save_session
from .samples import load_project_samples, load_fabrication_samples, list_fabrication_compositions
from .edges import parallel_width, refine_parallel_edges
from .sources_config import read_tma_source_defaults


class ImageView(QtWidgets.QGraphicsView):
    line_drawn = QtCore.pyqtSignal(object, object)
    guides_drawn = QtCore.pyqtSignal(object, object, object)
    view_scale_changed = QtCore.pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QtWidgets.QGraphicsScene(self))
        self.image_item = self.scene().addPixmap(QtGui.QPixmap())
        self.pending = None
        self.tool = "line"
        self.guide_points = []
        self.measure_enabled = False
        self.lines = []
        self.setMinimumSize(400, 300)
        self.setBackgroundBrush(QtGui.QColor("#24282d"))
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)

    def set_image(self, image, *, fit=False, preview=False):
        displayed = image.scaled(1600, 1600, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                 QtCore.Qt.TransformationMode.FastTransformation) if preview and image.width() > 1600 else image
        self.image_item.setPixmap(QtGui.QPixmap.fromImage(displayed))
        self.image_item.setScale(image.width() / displayed.width())
        self.scene().setSceneRect(0, 0, image.width(), image.height())
        if fit:
            self.fit_image()

    def fit_image(self):
        if not self.image_item.pixmap().isNull():
            self.fitInView(self.image_item, QtCore.Qt.AspectRatioMode.KeepAspectRatio)
            self.view_scale_changed.emit(self.transform().m11())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_image()

    def wheelEvent(self, event):
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self.scale(factor, factor)
        self.view_scale_changed.emit(self.transform().m11())
        event.accept()

    def clear_lines(self):
        self.pending = None
        self.guide_points.clear()
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
            self.guide_points.clear()
            return super().mousePressEvent(event)
        point = self.mapToScene(event.position().toPoint())
        if not self.image_item.boundingRect().contains(point):
            return
        value = (point.x(), point.y())
        if self.tool != "line":
            self.guide_points.append(value)
            if len(self.guide_points) == 3:
                points, self.guide_points = self.guide_points, []
                self.guides_drawn.emit(*points)
            return
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
    folders_loaded = QtCore.pyqtSignal(str, object)

    def __init__(self, kind, path, composition=None):
        super().__init__()
        self.kind, self.path = kind, path
        self.composition = composition
        self.cancelled = threading.Event()

    def run(self):
        try:
            if self.kind == "fabrication" and not self.composition:
                folders = list_fabrication_compositions(self.path, cancel_callback=self.cancelled.is_set)
                if not self.cancelled.is_set():
                    self.folders_loaded.emit(str(self.path.resolve()), folders)
                return
            loader = load_project_samples if self.kind == "project" else load_fabrication_samples
            choices = loader(self.path, composition=self.composition, cancel_callback=self.cancelled.is_set) if self.kind == "fabrication" else loader(self.path)
            if not self.cancelled.is_set():
                self.loaded.emit(self.kind, str(self.path.resolve()), choices)
        except Exception as exc:
            if not self.cancelled.is_set():
                self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class ProjectAnnotationTask(QtCore.QObject):
    succeeded = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    def __init__(self, operation):
        super().__init__()
        self.operation = operation

    def run(self):
        try:
            self.succeeded.emit(self.operation())
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, *, enumerate_cameras=True, settings=None, use_tma_defaults=None):
        super().__init__()
        self.setWindowTitle("Microscope Diameter — PyPlot")
        self.resize(1280, 860)
        self.settings = settings or QtCore.QSettings("microwire", "microscope_logger")
        self.use_tma_defaults = (settings is None and enumerate_cameras) if use_tma_defaults is None else use_tma_defaults
        self.control_server = None
        self.active_session = None
        self.autosave_error = ""
        self.frames_received = 0
        self.autosave_timer = QtCore.QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(750)
        self.autosave_timer.timeout.connect(self._autosave)
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
        self.restored_sample = None
        self.sample_annotations = {}
        self.annotation_task = None
        self.annotation_review = None
        self.annotation_job = {"state": "idle"}
        self.project_update_receipts = []
        self.calibration = None
        self.calibration_error = ""
        self.last_line = None
        self.profiles = {}
        self.catalogs = {}
        self.catalog_tasks = []
        self.fabrication_compositions = []
        self.fabrication_loaded_root = None
        self.last_display = 0.0
        self.last_conversion = 0.0
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
        self.output_path = QtWidgets.QLineEdit()
        output_row = QtWidgets.QWidget()
        output_layout = QtWidgets.QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.addWidget(self.output_path)
        output_browse = QtWidgets.QPushButton("…")
        output_browse.setMaximumWidth(32)
        output_browse.clicked.connect(self.choose_output)
        output_layout.addWidget(output_browse)
        form.addRow("Output folder", output_row)
        self.auto_save = QtWidgets.QCheckBox("Auto-save captures and measurements")
        self.auto_save.setChecked(True)
        form.addRow(self.auto_save)
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
        self.open_session_button = QtWidgets.QPushButton("Open saved session…")
        self.open_session_button.clicked.connect(self.choose_session)
        form.addRow(self.open_session_button)
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
        self.composition_combo.activated.connect(self._maybe_load_fabrication)
        self.composition_combo.lineEdit().editingFinished.connect(self._maybe_load_fabrication)
        self.wire_combo.currentTextChanged.connect(self._sample_changed)
        form.addRow("Composition", self.composition_combo)
        form.addRow("Microwire / piece", self.wire_combo)
        self.brittle_toggle = QtWidgets.QCheckBox("Brittle · not assessed")
        self.brittle_toggle.setTristate(True)
        self.brittle_toggle.setCheckState(QtCore.Qt.CheckState.PartiallyChecked)
        self.brittle_toggle.setToolTip("Checked: brittle. Unchecked: not brittle. Dash: not assessed. Recorded for this sample; project changes require Save Brittle to project.")
        self.brittle_toggle.checkStateChanged.connect(self._brittle_changed)
        form.addRow(self.brittle_toggle)
        self.save_brittle_button = QtWidgets.QPushButton("Save Brittle to project…")
        self.save_brittle_button.clicked.connect(self._review_brittle_gui)
        form.addRow(self.save_brittle_button)
        self.annotation_status = QtWidgets.QLabel("Project annotation has not been changed.")
        self.annotation_status.setWordWrap(True)
        form.addRow(self.annotation_status)
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
        self.objective_combo.setToolTip("Manual selection: update this when physically changing the objective.")
        form.addRow("Objective (manual)", self.objective_combo)
        self.zoom_spin = QtWidgets.QDoubleSpinBox()
        self.zoom_spin.setRange(1, 20)
        self.zoom_spin.setDecimals(2)
        self.zoom_spin.setValue(1)
        self.zoom_spin.setToolTip("Record the microscope's acquisition zoom manually. This field does not operate camera zoom. Mouse-wheel view enlargement is separate.")
        self.zoom_spin.valueChanged.connect(self._context_changed)
        form.addRow("Acquisition zoom (manual)", self.zoom_spin)
        self.scale_status = QtWidgets.QLabel("Uncalibrated · pixel measurements")
        self.scale_status.setWordWrap(True)
        form.addRow(self.scale_status)
        self.action_combo = QtWidgets.QComboBox()
        self.action_combo.addItems(["Measure width", "Draw reference length", "Parallel edges (3 clicks)", "Auto edges from guides (3 clicks)"])
        self.action_combo.currentIndexChanged.connect(self._tool_changed)
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
        help_label = QtWidgets.QLabel("Width: click opposite edges. Parallel/auto: click two points along one edge, then one point on the opposite edge. Review automatic overlays. Wheel zoom changes only the view.")
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
        self.view.guides_drawn.connect(self.guides_drawn)
        right.addWidget(self.view)
        bottom = QtWidgets.QWidget()
        bottom_layout = QtWidgets.QVBoxLayout(bottom)
        toolbar = QtWidgets.QHBoxLayout()
        toolbar.addWidget(QtWidgets.QLabel("Measurements on this image"))
        self.view_scale_label = QtWidgets.QLabel("View 100%")
        self.view_scale_label.setToolTip("Display scaling only; measurements always use the original image pixels.")
        self.view.view_scale_changed.connect(lambda scale: self.view_scale_label.setText(f"View {100 * scale:.0f}%"))
        toolbar.addWidget(self.view_scale_label)
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
        defaults = read_tma_source_defaults(self.settings) if self.use_tma_defaults else {
            key: self.settings.value(key, "", type=str)
            for key in ("project_path", "fabrication_path", "output_directory")}
        self.project_path.setText(defaults["project_path"])
        self.fabrication_path.setText(defaults["fabrication_path"])
        self.output_path.setText(defaults["output_directory"])
        self.auto_save.setChecked(self.settings.value("auto_save", True, type=bool))
        try:
            self.settings.sync()
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
        self.frames_received = 0
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
        if not frame.isValid():
            return
        self.frames_received += 1
        now = time.monotonic()
        # Drop excess callbacks before decoding so preview cannot build a queue
        # of full-resolution image copies behind GUI or command events.
        if now - self.last_conversion < 0.1:
            return
        self.last_conversion = now
        image = frame.toImage()
        if image.isNull():
            return
        self.last_frame = image.copy()
        self.capture_button.setEnabled(True)
        if now - self.last_display >= 0.1:
            self.view.set_image(self.last_frame, fit=not self.preview_fitted, preview=True)
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
        self._autosave()

    def choose_image(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open microscope image", "", "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp)")
        if path:
            self.load_image(Path(path))

    def choose_session(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Open saved microscope session", self.output_path.text())
        if not path:
            return False
        try:
            if not self.restore_session(path):
                return False
        except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
            self.statusBar().showMessage(f"Could not open saved session: {exc}")
            return False
        self.statusBar().showMessage(f"Opened saved session {path}")
        return True

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
        self.active_session = None
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

    def _profile_key(self, *, context=None, image_identity=None):
        context = dict(self._context() if context is None else context)
        # Unknown camera: a scale can only be restored for these exact pixels.
        if not context["camera_id"]:
            context["image_identity"] = self.image_identity if image_identity is None else image_identity
        return json.dumps(context, sort_keys=True)

    def _context_changed(self):
        if not hasattr(self, "scale_status"):
            return
        self.calibration = None
        self.calibration_evidence = None
        self.calibration_error = ""
        if not self.image.isNull():
            try:
                value = self.profiles.get(self._profile_key())
                if value:
                    if not isinstance(value, dict):
                        raise ValueError("Saved scale is not a calibration profile")
                    calibration = Calibration(**value.get("calibration", value))
                    if not calibration.matches(**self._context()):
                        raise ValueError("Saved scale does not match this acquisition setup")
                    evidence = self._validated_calibration_evidence(value.get("evidence"), calibration)
                    self.calibration, self.calibration_evidence = calibration, evidence
            except (OSError, KeyError, TypeError, ValueError, OverflowError) as exc:
                self.calibration_error = f"Saved calibration unavailable: {exc}"
        self._show_calibration_status()
        self._mark_dirty()

    def _show_calibration_status(self):
        text = ("Uncalibrated · pixel measurements" if self.calibration is None
                else f"Calibrated · {self.calibration.um_per_pixel:.6g} µm/pixel")
        if self.calibration_error:
            text += f" · {self.calibration_error}"
        self.scale_status.setText(text)

    @staticmethod
    def _validated_calibration_evidence(evidence, calibration=None):
        """Verify retained original bytes before enabling a physical scale."""
        from .measurements import _png_size
        if not isinstance(evidence, dict):
            raise ValueError("Calibration reference evidence is missing")
        digest = evidence.get("image_sha256")
        if (not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)):
            raise ValueError("Invalid calibration image hash")
        if not isinstance(evidence.get("image_path"), str) or not evidence["image_path"]:
            raise ValueError("Calibration reference image path is missing")
        raw = Path(evidence["image_path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Calibration reference image has changed")
        dimensions = _png_size(raw)
        if calibration is not None:
            if dimensions != calibration.image_size:
                raise ValueError("Calibration reference image size does not match its scale")
            distance = measure_line(evidence["start"], evidence["end"],
                                    image_size=dimensions, objective=calibration.objective)["width_px"]
            if not math.isclose(distance, calibration.reference_pixels, rel_tol=1e-12):
                raise ValueError("Calibration reference endpoints do not match its scale")
        return copy.deepcopy(evidence)

    def _active_calibration(self):
        # A deleted or changed reference must also fail closed between captures.
        if self.calibration is not None:
            try:
                self._validated_calibration_evidence(self.calibration_evidence, self.calibration)
            except (OSError, KeyError, TypeError, ValueError, OverflowError) as exc:
                self.calibration = self.calibration_evidence = None
                self.calibration_error = f"Saved calibration unavailable: {exc}"
                self._show_calibration_status()
        return self.calibration

    def _persist_calibration_profiles(self, profiles):
        """Publish profiles only after QSettings confirms a durable sync."""
        raw = json.dumps(profiles, allow_nan=False)
        previous = self.settings.value("calibration_profiles", "{}", type=str)
        try:
            self.settings.setValue("calibration_profiles", raw)
            self.settings.sync()
            if self.settings.status() != QtCore.QSettings.Status.NoError:
                raise OSError(f"QSettings reported {self.settings.status().name}")
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            # QSettings can retain a failed write in its cache. Do not let a
            # later close silently publish a profile reported as unsuccessful.
            try:
                self.settings.setValue("calibration_profiles", previous)
                self.settings.sync()
            except (OSError, ValueError, TypeError, RuntimeError):
                pass
            raise OSError(f"Could not save calibration profiles: {exc}") from exc
        self.profiles = profiles

    def _mark_dirty(self, *_args):
        if not self.image.isNull():
            self.dirty = True
            if hasattr(self, "auto_save") and self.auto_save.isChecked() and self.output_path.text().strip():
                self.autosave_timer.start()

    def _tool_changed(self):
        if hasattr(self, "view"):
            self.view.tool = "line" if self.action_combo.currentIndex() < 2 else "guides"
            self.view.pending = None
            self.view.guide_points.clear()

    def choose_output(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose microscope output folder", self.output_path.text())
        if path:
            self.output_path.setText(path)
            self.settings.setValue("output_directory", path)

    def _gray_image(self):
        image = self.image.convertToFormat(QtGui.QImage.Format.Format_Grayscale8)
        data = image.constBits().asstring(image.sizeInBytes())
        return np.frombuffer(data, dtype=np.uint8).reshape(image.height(), image.bytesPerLine())[:, :image.width()].copy()

    def guides_drawn(self, a, b, c, *, automatic=None, samples=15):
        if self.image.isNull() or self.camera is not None:
            raise ValueError("Capture or open a still image before measuring")
        automatic = self.action_combo.currentIndex() == 3 if automatic is None else automatic
        try:
            result = refine_parallel_edges(self._gray_image(), a, b, c, samples=samples) if automatic else parallel_width(a, b, c)
            record = measure_line(result["width_start"], result["width_end"], calibration=self._active_calibration(),
                                  kind=self.kind_combo.currentData(), **self._context())
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))
            return None
        record.update(method="automatic_parallel_edges" if automatic else "parallel_edges",
                      guides=dict(a=list(a), b=list(b), c=list(c)),
                      analysis=result, sample=self._sample(), calibration_evidence=self.calibration_evidence,
                      measured_utc=datetime.now(timezone.utc).isoformat(), review_status="Unreviewed")
        self.records.append(record)
        for edge in (result["edge1"], result["edge2"]):
            self.view.add_line(edge[0], edge[1], "", "#ffc857")
        self.view.add_line(result["width_start"], result["width_end"], str(len(self.records)))
        self._refresh_table()
        self._mark_dirty()
        flags = "; ".join(result.get("quality_flags", []))
        self.statusBar().showMessage(f"{'Automatic fit' if automatic else 'Parallel width'}: {record['width_px']:.2f} px. " + (flags or "Review both boundary guides."))
        return record

    def clear_calibration(self):
        profiles = copy.deepcopy(self.profiles)
        profiles.pop(self._profile_key(), None)
        try:
            self._persist_calibration_profiles(profiles)
        except OSError as exc:
            self.statusBar().showMessage(str(exc))
            return False
        self._context_changed()
        self.statusBar().showMessage("Pixel-only mode saved for this acquisition setup. Existing rows retain their original calibration.")
        return True

    def _sample_identity(self):
        composition, wire = self.composition_combo.currentText().strip(), self.wire_combo.currentText().strip()
        matches = [choice for kind in ("project", "fabrication") for choice in self.catalogs.get(kind, [])
                   if choice.composition == composition and choice.microwire == wire]
        if matches:
            result = asdict(matches[0])
            result["matching_sources"] = [asdict(choice) for choice in matches]
            return result
        if self.restored_sample and self.restored_sample.get("composition") == composition and self.restored_sample.get("microwire") == wire:
            return copy.deepcopy(self.restored_sample)
        return dict(composition=composition, microwire=wire, source_kind="manual")

    def _sample(self):
        result = self._sample_identity()
        annotation = self.sample_annotations.get(self._sample_key(), {})
        result.update(brittle=annotation.get("brittle", result.get("brittle")),
                      brittle_observed_utc=annotation.get("observed_utc", result.get("brittle_observed_utc")))
        return result

    def _sample_key(self):
        return self.composition_combo.currentText().strip(), self.wire_combo.currentText().strip()

    def _sample_changed(self, *_args):
        if not hasattr(self, "brittle_toggle"):
            return
        value = self._sample().get("brittle")
        self.brittle_toggle.blockSignals(True)
        self.brittle_toggle.setCheckState(QtCore.Qt.CheckState.PartiallyChecked if value is None else
                                         QtCore.Qt.CheckState.Checked if value else QtCore.Qt.CheckState.Unchecked)
        self.brittle_toggle.blockSignals(False)
        self._brittle_label(value)
        self._mark_dirty()

    def _brittle_label(self, value):
        self.brittle_toggle.setText("Brittle · " + ("not assessed" if value is None else "yes" if value else "no"))

    def _brittle_changed(self, state):
        value = None if state == QtCore.Qt.CheckState.PartiallyChecked else state == QtCore.Qt.CheckState.Checked
        self.sample_annotations[self._sample_key()] = dict(brittle=value, observed_utc=datetime.now(timezone.utc).isoformat())
        self._brittle_label(value)
        self._mark_dirty()

    def _annotation_operation(self, operation, state, callback):
        if self.annotation_task is not None:
            raise ValueError("A project annotation operation is already running")
        task = ProjectAnnotationTask(operation)
        thread = QtCore.QThread(self)
        task.moveToThread(thread)
        self.annotation_task = (thread, task)
        self.annotation_job = {"state": state}
        self.save_brittle_button.setEnabled(False)
        self.annotation_status.setText("Checking project and preparing annotation…" if state == "preparing" else "Saving verified project annotation…")
        thread.started.connect(task.run)
        task.succeeded.connect(callback)
        task.failed.connect(self._annotation_failed)
        task.finished.connect(thread.quit)
        task.finished.connect(task.deleteLater)
        thread.finished.connect(self._annotation_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _annotation_finished(self):
        self.annotation_task = None
        self.save_brittle_button.setEnabled(True)
        if getattr(self, "_annotation_gui_review", False):
            self._annotation_gui_review = False
            QtCore.QTimer.singleShot(0, self._confirm_brittle_publication)

    def _annotation_failed(self, message):
        self.annotation_job = dict(state="failed", error=message)
        self.annotation_status.setText(f"Project annotation failed: {message}")

    def prepare_brittle_update(self, *, workdir=None, expected_sha256=None, gui=False):
        if self.annotation_task is not None:
            raise ValueError("A project annotation operation is already running")
        from .project_annotations import prepare_brittle_update
        sample = self._sample()
        if type(sample.get("brittle")) is not bool:
            raise ValueError("Set Brittle to yes or no; not assessed does not modify the project")
        if not all(self._sample_key()):
            raise ValueError("Select a composition and microwire first")
        source = Path(self.project_path.text().strip()).expanduser().resolve()
        bindings = [sample, *sample.get("matching_sources", [])]
        digest = expected_sha256 or next((item.get("source_sha256") for item in bindings
            if item.get("source_kind") == "project" and Path(item.get("source_path", "")).resolve() == source), None)
        if not digest:
            raise ValueError("Load this project sample first, or supply its expected SHA-256")
        root = Path(workdir) if workdir else Path(self.output_path.text()) / "project_updates"
        if not root.is_absolute():
            raise ValueError("Choose an absolute output folder for project review files")
        self.annotation_review = None
        operation = lambda: prepare_brittle_update(source, root, composition=sample["composition"],
            microwire=sample["microwire"], brittle=sample["brittle"], expected_sha256=digest)
        self._annotation_operation(operation, "preparing", lambda review: self._brittle_prepared(review, gui))

    def _brittle_prepared(self, review, gui=False):
        self.annotation_review = review
        self.annotation_job = dict(state="ready", review=review.to_dict())
        self.annotation_status.setText("Verified annotation is ready to save to the project.")
        if gui:
            # Show the exact prepared change before the final publication action.
            self._annotation_gui_review = True

    def _review_brittle_gui(self):
        try:
            self.prepare_brittle_update(gui=True)
        except (OSError, ValueError) as exc:
            self._annotation_failed(str(exc))

    def _confirm_brittle_publication(self):
        review = self.annotation_review
        if review is None:
            return
        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowTitle("Save sample annotation")
        dialog.setText(f"Save Brittle = {review.brittle} for {review.composition} {review.microwire}?\n\nProject: {review.source_path}\nA verified backup and publication receipt will be retained.")
        dialog.setDetailedText(json.dumps(review.to_dict(), indent=2, ensure_ascii=False))
        dialog.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Save | QtWidgets.QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(QtWidgets.QMessageBox.StandardButton.Cancel)
        if dialog.exec() == QtWidgets.QMessageBox.StandardButton.Save:
            try:
                self.publish_brittle_update()
            except (OSError, ValueError) as exc:
                self._annotation_failed(str(exc))

    def publish_brittle_update(self, *, review_sha256=None):
        from .project_annotations import publish_brittle_update
        review = self.annotation_review
        if review is None:
            raise ValueError("Prepare and review a project annotation first")
        if review_sha256 is not None and review_sha256 != review.candidate_sha256:
            raise ValueError("The requested review does not match the prepared candidate")
        sample = self._sample()
        if (sample["composition"], sample["microwire"], sample["brittle"]) != (review.composition, review.microwire, review.brittle):
            raise ValueError("The selected sample or Brittle state changed; prepare it again")
        if Path(self.project_path.text()).resolve() != Path(review.source_path).resolve():
            raise ValueError("The selected project changed; prepare it again")
        root = Path(review.review_path).parent
        self._annotation_operation(lambda: publish_brittle_update(review, backup=root / "original.pydpj",
            receipt_path=root / "publication_receipt.json"), "publishing", lambda receipt: self._brittle_published(receipt, review))

    def _brittle_published(self, receipt, review):
        self.annotation_job = dict(state="published", receipt=receipt)
        self.project_update_receipts.append(dict(composition=review.composition, microwire=review.microwire,
                                                 brittle=review.brittle, receipt=copy.deepcopy(receipt)))
        self.annotation_review = None
        self.annotation_status.setText("Brittle saved to project. Backup and receipt retained. Reload project suggestions before another update.")
        self._mark_dirty()

    def line_drawn(self, start, end):
        if self.image.isNull() or self.camera is not None:
            return
        try:
            record = measure_line(start, end, calibration=self._active_calibration(),
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
        record["method"] = "manual_line"
        record["review_status"] = "Unreviewed"
        record["measured_utc"] = datetime.now(timezone.utc).isoformat()
        record["calibration_evidence"] = self.calibration_evidence
        self.records.append(record)
        self.view.add_line(start, end, str(len(self.records)))
        self._mark_dirty()
        self._refresh_table()

    def calibrate(self):
        if self.last_line is None or self.image.isNull() or self.camera is not None:
            self.statusBar().showMessage("Choose Draw reference length and click the two ends of a known distance first.")
            return False
        if not self.reference_source.text().strip():
            self.statusBar().showMessage("Describe the known reference before calibrating.")
            return False
        try:
            length = measure_line(*self.last_line, **self._context())["width_px"]
            calibration = make_calibration(length, self.known_um.value(),
                                           source=self.reference_source.text().strip(), **self._context())
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))
            return False
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
                    stream.flush()
                    os.fsync(stream.fileno())
            if hashlib.sha256(reference_path.read_bytes()).hexdigest() != digest:
                raise ValueError("Saved reference image failed its integrity check")
        except (ValueError, OSError) as exc:
            self.statusBar().showMessage(f"Could not preserve calibration reference: {exc}")
            return False
        evidence = dict(image_path=str(reference_path.resolve()), image_sha256=digest,
                        start=list(self.last_line[0]), end=list(self.last_line[1]),
                        created_utc=datetime.now(timezone.utc).isoformat())
        profiles = copy.deepcopy(self.profiles)
        profiles[self._profile_key()] = dict(calibration=asdict(calibration), evidence=evidence)
        try:
            self._persist_calibration_profiles(profiles)
        except OSError as exc:
            self.statusBar().showMessage(str(exc))
            return False
        self._context_changed()
        if self.calibration is None:
            self.statusBar().showMessage(self.calibration_error or "Saved calibration could not be activated")
            return False
        self.action_combo.setCurrentIndex(0)
        self._mark_dirty()
        self.statusBar().showMessage("Scale saved for new measurements. Existing rows retain their original calibration.")
        return True

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
        self._mark_dirty()

    def _browse_catalog(self, kind):
        if kind == "project":
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose Builder project", "", "Builder project (*.pydpj)")
        else:
            path = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose fabrication folder")
        if path:
            (self.project_path if kind == "project" else self.fabrication_path).setText(path)
            self._load_catalog(kind)

    def _load_catalog(self, kind, composition=None):
        field = self.project_path if kind == "project" else self.fabrication_path
        path = field.text().strip()
        if not path:
            return
        if self.catalog_tasks:
            self.sample_status.setText("A source is still loading. Please wait.")
            return
        if kind == "project":
            self.catalogs.pop(kind, None)
        self._refresh_samples()
        composition = composition or self.composition_combo.currentText().strip() if kind == "fabrication" else None
        task = CatalogTask(kind, Path(path), composition=composition)
        task.loaded.connect(lambda k, p, choices: self._catalog_received(k, p, choices, composition))
        task.folders_loaded.connect(self._fabrication_folders_loaded)
        task.failed.connect(self.sample_status.setText)
        task.finished.connect(lambda: self.catalog_tasks.remove(task) if task in self.catalog_tasks else None)
        self.catalog_tasks.append(task)
        self.sample_status.setText("Reading sample identities…")
        threading.Thread(target=task.run, daemon=True).start()

    def _catalog_received(self, kind, path, choices, composition=None):
        field = self.project_path if kind == "project" else self.fabrication_path
        if str(Path(field.text().strip()).resolve()) != path:
            self.sample_status.setText("Source path changed while loading. Press Load to read the new path.")
            return
        if kind == "fabrication":
            self.fabrication_loaded_root = path
            if composition:
                choices = [choice for choice in self.catalogs.get(kind, [])
                           if choice.composition != composition] + choices
        self._catalog_loaded(kind, choices)

    def _fabrication_folders_loaded(self, path, names):
        if str(Path(self.fabrication_path.text().strip()).resolve()) != path:
            return
        self.fabrication_loaded_root = path
        self.fabrication_compositions = names
        self._refresh_samples()
        self.sample_status.setText("Select a composition, then load its production samples.")

    def _maybe_load_fabrication(self, *_args):
        if self.catalog_tasks or not self.fabrication_loaded_root:
            return
        if str(Path(self.fabrication_path.text().strip()).resolve()) == self.fabrication_loaded_root:
            self._load_catalog("fabrication")

    def _catalog_loaded(self, kind, choices):
        self.catalogs[kind] = choices
        self._refresh_samples()
        self.sample_status.setText(f"Loaded {len(choices)} sample identities. Project bindings take priority for shared identities.")

    def _refresh_samples(self):
        previous = self.composition_combo.currentText()
        previous_wire = self.wire_combo.currentText()
        self.composition_combo.blockSignals(True)
        self.composition_combo.clear()
        values = sorted({choice.composition for choices in self.catalogs.values() for choice in choices}
                        | set(self.fabrication_compositions))
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
        self._sample_changed()

    def choose_save(self):
        if self.image.isNull():
            self.statusBar().showMessage("Capture or open an image first.")
            return
        if not self.output_path.text().strip():
            self.choose_output()
        self._autosave(force=True)

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

    def _export_payload(self):
        data = self._image_png()
        metadata = dict(self._context(), sample=self._sample(), notes=self.notes.toPlainText(),
                        calibration=asdict(self.calibration) if self.calibration else None,
                        calibration_evidence=self.calibration_evidence,
                        source_file=self.source_file, captured_utc=self.captured_utc, loaded_utc=self.loaded_utc,
                        camera_format=self.frame_format)
        if self.project_update_receipts:
            metadata["project_updates"] = copy.deepcopy(self.project_update_receipts)
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
        return data, records, metadata, references

    def export_session(self, directory):
        data, records, metadata, references = self._export_payload()
        path = save_session(directory, data, records, metadata, reference_images=references)
        self.dirty = False
        return path

    def _autosave(self, *, force=False):
        self.autosave_timer.stop()
        if self.image.isNull() or (not force and not self.auto_save.isChecked()):
            return None
        raw = self.output_path.text().strip()
        if not raw:
            if force:
                self.statusBar().showMessage("Choose an output folder first.")
            return None
        try:
            directory = Path(raw).expanduser()
            if not directory.is_absolute():
                raise ValueError("Choose an absolute output folder path")
            if self.active_session is None or self.active_session.parent.resolve() != directory.resolve():
                self.active_session = self.export_session(directory)
                path = self.active_session
            else:
                from .session_revisions import update_session
                _, records, metadata, references = self._export_payload()
                path = update_session(self.active_session, records, metadata, reference_images=references)
                self.dirty = False
            self.autosave_error = ""
            self.statusBar().showMessage(f"Saved {path}")
            return path
        except (OSError, ValueError, KeyError) as exc:
            self.autosave_error = str(exc)
            self.dirty = True
            self.statusBar().showMessage(f"Auto-save failed: {exc}. Image and measurements remain in memory.")
            return None

    def start_control(self, runtime_dir=None):
        if self.control_server is not None and self.control_server.running:
            return True
        from .control import ControlServer
        self.control_server = ControlServer(self.handle_command, runtime_dir=runtime_dir, parent=self)
        return self.control_server.start()

    def command_status(self):
        self._active_calibration()
        return dict(ok=True, preview_active=self.camera is not None, frames_received=self.frames_received,
                    image_size=[self.image.width(), self.image.height()], sample=self._sample(),
                    objective=self.objective_combo.currentText(), objective_manual=True,
                    calibration_status="Calibrated" if self.calibration else "Uncalibrated",
                    calibration=asdict(self.calibration) if self.calibration else None,
                    calibration_evidence=copy.deepcopy(self.calibration_evidence),
                    calibration_error=self.calibration_error,
                    acquisition_zoom=self.zoom_spin.value(), acquisition_zoom_manual=True,
                    view_zoom=self.view.transform().m11(),
                    measurements=self.records, output_directory=self.output_path.text(),
                    active_session=str(self.active_session) if self.active_session else None,
                    autosave_error=self.autosave_error, sources_loading=bool(self.catalog_tasks),
                    project_path=self.project_path.text(), fabrication_path=self.fabrication_path.text(),
                    camera_names=[self.camera_combo.itemText(i) for i in range(1, self.camera_combo.count())],
                    selected_camera=self.camera_combo.currentText(), selected_format=self.format_combo.currentText(),
                    catalog_counts={kind: len(choices) for kind, choices in self.catalogs.items()},
                    project_annotation=copy.deepcopy(self.annotation_job),
                    source_status=self.sample_status.text())

    def restore_session(self, directory):
        from .measurements import _size, _validated_record
        from .session_revisions import _read_base, _read_latest
        directory = Path(directory).resolve(strict=True)
        base, base_bytes, dimensions, _ = _read_base(directory)
        pointer = _read_latest(directory, base, hashlib.sha256(base_bytes).hexdigest())
        manifest = directory / "session.json"
        if pointer:
            manifest = (directory / json.loads(pointer)["manifest"]).resolve()
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        try:
            if (not isinstance(payload, dict) or not isinstance(payload.get("measurements"), list)
                    or not isinstance(payload.get("metadata"), dict)):
                raise ValueError("Session measurements and metadata are invalid")
            records = [_validated_record(record, dimensions) for record in payload["measurements"]]
            metadata = payload["metadata"]
            if _size(metadata.get("image_size", dimensions)) != dimensions:
                raise ValueError("Session image size does not match its original image")
            objective, camera_id = metadata.get("objective", "4x"), metadata.get("camera_id", "")
            if not isinstance(objective, str) or not objective.strip() or not isinstance(camera_id, str):
                raise ValueError("Session objective or camera identifier is invalid")
            zoom = float(metadata.get("zoom", 1))
            if not math.isfinite(zoom) or not self.zoom_spin.minimum() <= zoom <= self.zoom_spin.maximum():
                raise ValueError("Session acquisition zoom is outside the supported input range")
            if not math.isclose(zoom, round(zoom, self.zoom_spin.decimals()), rel_tol=0, abs_tol=1e-12):
                raise ValueError("Session acquisition zoom exceeds the supported input precision")
            context = dict(objective=objective.strip().replace("x", "×"), image_size=dimensions,
                           camera_id=camera_id, zoom=zoom)
            calibration = Calibration(**metadata["calibration"]) if metadata.get("calibration") else None
            if calibration is not None and not calibration.matches(**context):
                raise ValueError("Session calibration does not match its acquisition setup")
            sample = metadata.get("sample", {})
            if (not isinstance(sample, dict) or any(not isinstance(sample.get(key, ""), str)
                                                  for key in ("composition", "microwire"))
                    or not isinstance(metadata.get("notes", ""), str)):
                raise ValueError("Session sample or notes are invalid")
        except (KeyError, TypeError, OverflowError) as exc:
            raise ValueError(f"Invalid microscope session: {exc}") from exc
        raw = (directory / "image.png").read_bytes()
        if hashlib.sha256(raw).hexdigest() != base["image"]["sha256"]:
            raise ValueError("Capture original image has changed")
        image = QtGui.QImage.fromData(raw)
        if image.isNull():
            raise ValueError("Could not decode original capture")
        for evidence in [metadata.get("calibration_evidence"),
                         *[record.get("calibration_evidence") for record in records]]:
            if evidence is not None:
                if not isinstance(evidence, dict):
                    raise ValueError("Calibration reference evidence is invalid")
                digest = evidence.get("image_sha256")
                if (not isinstance(digest, str) or len(digest) != 64
                        or any(c not in "0123456789abcdef" for c in digest)):
                    raise ValueError("Invalid calibration image hash")
                path = directory / f"reference_{digest}.png"
                evidence["image_path"] = str(path)
                self._validated_calibration_evidence(evidence)
        evidence = metadata.get("calibration_evidence")
        if calibration is not None:
            try:
                evidence = self._validated_calibration_evidence(evidence, calibration)
            except (KeyError, TypeError, OverflowError) as exc:
                raise ValueError(f"Invalid session calibration evidence: {exc}") from exc
        if not self._confirm_replace():
            return False
        if calibration is not None:
            rgba = image.convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
            identity = hashlib.sha256(rgba.constBits().asstring(rgba.sizeInBytes())).hexdigest()
            profiles = copy.deepcopy(self.profiles)
            key = self._profile_key(context=context, image_identity=identity)
            profiles[key] = dict(calibration=asdict(calibration), evidence=evidence)
            self._persist_calibration_profiles(profiles)
        self.stop_preview()
        self.set_image(image, camera_id=camera_id)
        self.objective_combo.setCurrentText(objective.replace("x", "×"))
        self.zoom_spin.setValue(zoom)
        self.records = records
        self.restored_sample = copy.deepcopy(sample)
        self.composition_combo.setCurrentText(sample.get("composition", ""))
        self.wire_combo.setCurrentText(sample.get("microwire", ""))
        self.sample_annotations[self._sample_key()] = dict(brittle=sample.get("brittle"), observed_utc=sample.get("brittle_observed_utc"))
        self._sample_changed()
        self.notes.setPlainText(metadata.get("notes", ""))
        self.project_update_receipts = copy.deepcopy(metadata.get("project_updates", []))
        self.source_file = metadata.get("source_file")
        self.source_file_hash = metadata.get("source_file_sha256")
        self.captured_utc = metadata.get("captured_utc")
        self.loaded_utc = metadata.get("loaded_utc")
        self.frame_format = metadata.get("camera_format")
        self.calibration_evidence = evidence
        self.calibration = calibration
        self.calibration_error = ""
        self._show_calibration_status()
        for index, record in enumerate(records, 1):
            for edge in (record.get("analysis") or {}).get("edge1", []), (record.get("analysis") or {}).get("edge2", []):
                if edge:
                    self.view.add_line(edge[0], edge[1], "", "#ffc857")
            self.view.add_line(record["start"], record["end"], str(index))
        self._refresh_table()
        self.active_session = directory
        self.autosave_timer.stop()
        self.dirty = False
        return True

    def handle_command(self, command):
        action = command.get("action")
        if action in {"measure", "parallel", "auto"} and "kind" in command:
            index = self.kind_combo.findData(command["kind"])
            if index < 0:
                raise ValueError("Unknown boundary kind")
            self.kind_combo.setCurrentIndex(index)
        if action == "status":
            return self.command_status()
        if action == "calibrate":
            from .calibration_commands import calibrate_from_command
            return calibrate_from_command(self, command)
        if action == "configure":
            allowed = {"action", "objective", "zoom", "composition", "microwire", "output_directory",
                       "project_path", "fabrication_path", "camera_name", "width", "height", "auto_save", "kind", "brittle"}
            if set(command) - allowed:
                raise ValueError("Unknown configuration field")
            if self.camera is not None and any(k in command for k in ("camera_name", "width", "height")):
                raise ValueError("Stop preview before changing camera or mode")
            if "objective" in command and not str(command["objective"]).strip():
                raise ValueError("An objective label is required")
            if "zoom" in command:
                value = float(command["zoom"])
                if not math.isfinite(value) or not self.zoom_spin.minimum() <= value <= self.zoom_spin.maximum():
                    raise ValueError("Acquisition zoom is outside the supported input range")
            if "auto_save" in command and not isinstance(command["auto_save"], bool):
                raise ValueError("auto_save must be true or false")
            if "brittle" in command and command["brittle"] is not None and type(command["brittle"]) is not bool:
                raise ValueError("brittle must be true, false or null")
            if command.get("output_directory") and not Path(str(command["output_directory"])).expanduser().is_absolute():
                raise ValueError("Choose an absolute output folder path")
            if "kind" in command and self.kind_combo.findData(command["kind"]) < 0:
                raise ValueError("Unknown boundary kind")
            camera_index = self.camera_combo.currentIndex()
            if "camera_name" in command:
                camera_index = self.camera_combo.findText(str(command["camera_name"]))
                if camera_index < 0:
                    raise ValueError("Camera not found")
            if "width" in command or "height" in command:
                device = self.camera_combo.itemData(camera_index)
                if device is None or not any(
                    fmt.resolution().width() == int(command.get("width", 0)) and
                    fmt.resolution().height() == int(command.get("height", 0)) for fmt in device.videoFormats()):
                    raise ValueError("Requested camera mode unavailable")
            for key, widget in (("project_path", self.project_path), ("fabrication_path", self.fabrication_path),
                                ("output_directory", self.output_path)):
                if key in command:
                    widget.setText(str(command[key]))
            if "objective" in command:
                self.objective_combo.setCurrentText(str(command["objective"]).replace("x", "×"))
            if "zoom" in command:
                self.zoom_spin.setValue(float(command["zoom"]))
            if "composition" in command:
                self.composition_combo.setCurrentText(str(command["composition"]))
            if "microwire" in command:
                self.wire_combo.setCurrentText(str(command["microwire"]))
            if "brittle" in command:
                value = command["brittle"]
                self.brittle_toggle.setCheckState(QtCore.Qt.CheckState.PartiallyChecked if value is None else
                                                 QtCore.Qt.CheckState.Checked if value else QtCore.Qt.CheckState.Unchecked)
            if "auto_save" in command:
                self.auto_save.setChecked(bool(command["auto_save"]))
            if "kind" in command:
                index = self.kind_combo.findData(command["kind"])
                if index < 0:
                    raise ValueError("Unknown boundary kind")
                self.kind_combo.setCurrentIndex(index)
            if "camera_name" in command:
                index = self.camera_combo.findText(str(command["camera_name"]))
                if index < 0:
                    raise ValueError("Camera not found")
                self.camera_combo.setCurrentIndex(index)
            if "width" in command or "height" in command:
                matches = [i for i in range(self.format_combo.count())
                           if self.format_combo.itemData(i).resolution().width() == int(command.get("width", 0))
                           and self.format_combo.itemData(i).resolution().height() == int(command.get("height", 0))]
                if not matches:
                    raise ValueError("Requested camera mode unavailable")
                self.format_combo.setCurrentIndex(matches[0])
        elif action == "prepare_brittle_update":
            self.prepare_brittle_update(workdir=command.get("workdir"), expected_sha256=command.get("expected_sha256"))
        elif action == "publish_brittle_update":
            if not command.get("review_sha256"):
                raise ValueError("Specify the reviewed candidate SHA-256")
            self.publish_brittle_update(review_sha256=command["review_sha256"])
        elif action == "start":
            if self.camera is None:
                if command.get("discard") is True:
                    self.dirty = False
                if self.dirty and self.auto_save.isChecked():
                    self._autosave()
                if self.dirty:
                    raise ValueError("Save the current image before starting another preview")
                self.toggle_preview()
                if self.camera is None:
                    raise ValueError(self.statusBar().currentMessage())
        elif action == "stop":
            self.stop_preview()
        elif action == "capture":
            if self.camera is None or self.last_frame is None:
                raise ValueError("No live camera frame is ready")
            self.capture_frame()
        elif action == "measure":
            self.action_combo.setCurrentIndex(0)
            previous = len(self.records)
            self.line_drawn(tuple(command["start"]), tuple(command["end"]))
            if len(self.records) == previous:
                raise ValueError(self.statusBar().currentMessage() or "No still image is ready")
            self._autosave()
        elif action in {"parallel", "auto"}:
            result = self.guides_drawn(command["a"], command["b"], command["c"], automatic=action == "auto", samples=command.get("samples", 15))
            if result is None:
                raise ValueError(self.statusBar().currentMessage())
            self._autosave()
        elif action == "save":
            if command.get("output_directory"):
                self.output_path.setText(str(command["output_directory"]))
            if self._autosave(force=True) is None:
                raise ValueError(self.autosave_error or "Could not save current image")
        elif action == "load_image":
            if self.dirty and command.get("discard") is not True:
                raise ValueError("Save the current image before replacing it")
            previous_dirty = self.dirty
            self.dirty = False
            if not self.load_image(Path(command["path"])):
                self.dirty = previous_dirty
                raise ValueError(self.statusBar().currentMessage())
        elif action == "load_sources":
            if command.get("cancel"):
                for task in self.catalog_tasks:
                    task.cancelled.set()
                self.sample_status.setText("Loading cancellation requested.")
                return self.command_status()
            kind = command.get("kind", "project")
            if kind not in {"project", "fabrication"}:
                raise ValueError("Unknown source kind")
            self._load_catalog(kind, composition=command.get("composition"))
        elif action == "restore_session":
            if self.dirty:
                self._autosave()
            if self.dirty:
                raise ValueError("Save the current image before restoring another capture")
            if not self.restore_session(command["path"]):
                raise ValueError("Session restore cancelled")
        elif action == "close":
            if self.annotation_task is not None:
                raise ValueError("Wait for the project annotation operation before closing")
            self.stop_preview()
            if self.dirty:
                self._autosave()
            if self.dirty:
                raise ValueError("Save the current image before closing")
            QtCore.QTimer.singleShot(100, self.close)
        else:
            raise ValueError("Unknown microscope command")
        return self.command_status()

    def closeEvent(self, event):
        if self.annotation_task is not None:
            self.statusBar().showMessage("Wait for the project annotation operation before closing.")
            event.ignore()
            return
        if not self._confirm_replace():
            event.ignore()
            return
        self.stop_preview()
        self.autosave_timer.stop()
        if self.control_server is not None:
            self.control_server.close()
        for task in self.catalog_tasks:
            task.cancelled.set()
        self.settings.setValue("project_path", self.project_path.text())
        self.settings.setValue("fabrication_path", self.fabrication_path.text())
        self.settings.setValue("output_directory", self.output_path.text())
        self.settings.setValue("auto_save", self.auto_save.isChecked())
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
    parser.add_argument("--runtime-dir", type=Path, help="Private local CLI discovery directory")
    parser.add_argument("--no-control", action="store_true", help="Disable local CLI control")
    args, qt_args = parser.parse_known_args(argv)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([sys.argv[0], *qt_args])
    ensure_app_theme(app)
    window = MainWindow(enumerate_cameras=not args.demo)
    if args.demo:
        window.auto_save.setChecked(False)
        window.set_image(demo_image())
        window.dirty = False
    elif args.image:
        window.load_image(args.image)
    window.show()
    if not args.no_control:
        try:
            window.start_control(args.runtime_dir)
        except Exception as exc:
            window.statusBar().showMessage(f"Local command control unavailable: {exc}")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
