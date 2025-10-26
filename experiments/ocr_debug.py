"""Interactive microscope OCR playground for PaddleOCR and Tesseract."""

from __future__ import annotations

import logging
import math
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageQt
from PyQt6 import QtCore, QtGui, QtWidgets

from plotting.utils import ensure_app_theme

from microwire_data_builder.core import _extract_microscope_diameters, _normalise_microscope_text, _parse_microscope_candidates
from microwire_data_builder.ocr import get_paddle_ocr

try:  # pragma: no cover - optional dependency
    import pytesseract  # type: ignore[import-not-found]
    from pytesseract import TesseractNotFoundError  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency
    pytesseract = None
    TesseractNotFoundError = RuntimeError


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}


VARIANT_ORDER: Tuple[str, ...] = (
    "base",
    "grayscale",
    "contrast",
    "sharpen",
    "autocontrast",
    "invert",
    "binary",
    "binary_invert",
    "red_mask",
    "red_binary",
    "fourier",
)


OUTPUT_MODE_OPTIONS: Tuple[Tuple[str, str], ...] = (
    ("Raw text", "raw"),
    ("d/D markers ([1])", "markers"),
)


PRIMARY_MARKER_PATTERN = re.compile(r"\[\s*1\s*\]\s*(\d+(?:[.,]\d+)?)")


def _marker_values_from_detections(detections: Sequence[object]) -> List[float]:
    values: List[float] = []
    for detection in detections or []:
        marker = getattr(detection, "marker", None)
        if marker != 1:
            continue
        value = getattr(detection, "value", None)
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(numeric) or numeric <= 0:
            continue
        values.append(numeric)
    return values


def _marker_values_from_texts(texts: Sequence[str]) -> List[float]:
    values: List[float] = []
    for text in texts or []:
        if not text:
            continue
        normalised = _normalise_microscope_text(text)
        for match in PRIMARY_MARKER_PATTERN.finditer(normalised):
            raw_value = match.group(1).replace(",", ".")
            try:
                numeric = float(raw_value)
            except ValueError:
                continue
            if not math.isfinite(numeric) or numeric <= 0:
                continue
            values.append(numeric)
    return values


def _normalise_marker_values(values: Sequence[float]) -> List[float]:
    ordered: List[float] = []
    seen: set[float] = set()
    for value in values:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(numeric) or numeric <= 0:
            continue
        rounded = round(numeric, 3)
        if rounded in seen:
            continue
        seen.add(rounded)
        ordered.append(rounded)
    return ordered


def _filter_marker_texts(texts: Sequence[str]) -> List[str]:
    filtered: List[str] = []
    for text in texts or []:
        if not text:
            continue
        normalised = _normalise_microscope_text(text)
        if "[1" in normalised or "1]" in normalised:
            filtered.append(text)
    return filtered


def _fourier_sharpen(image: Image.Image) -> Image.Image:
    array = np.array(image.convert("L"), dtype=np.float32)
    if array.ndim != 2 or array.size == 0:
        return image
    rows, cols = array.shape
    crow, ccol = rows // 2, cols // 2
    if crow == 0 or ccol == 0:
        return image
    freq = np.fft.fft2(array)
    shift = np.fft.fftshift(freq)
    y = np.arange(rows, dtype=np.float32)[:, None]
    x = np.arange(cols, dtype=np.float32)[None, :]
    distance = np.sqrt((y - float(crow)) ** 2 + (x - float(ccol)) ** 2)
    radius = max(min(rows, cols) * 0.08, 1.0)
    mask = np.ones_like(shift, dtype=np.complex128)
    mask[distance <= radius] = 0.1
    filtered = shift * mask
    inv_shift = np.fft.ifftshift(filtered)
    sharpened = np.fft.ifft2(inv_shift).real
    sharpened = np.clip(sharpened, 0, 255)
    try:
        return Image.fromarray(sharpened.astype("uint8"))
    except Exception:
        return image


def _variant_images(image: Image.Image, variants: Sequence[str]) -> Dict[str, Image.Image]:
    base = image.convert("RGB")
    grayscale = ImageOps.grayscale(base)
    enhanced = ImageEnhance.Contrast(grayscale).enhance(2.0)
    sharpened = enhanced.filter(ImageFilter.UnsharpMask(radius=2, percent=175))
    autocontrasted = ImageOps.autocontrast(grayscale)
    inverted = ImageOps.invert(grayscale)
    binary = grayscale.point(lambda p: 255 if p > 160 else 0, mode="1")
    binary_gray = ImageOps.autocontrast(binary.convert("L"))
    def _red_enhance(image: Image.Image) -> Optional[Image.Image]:
        try:
            array = np.array(image.convert("RGB"), dtype=np.float32)
        except Exception:
            return None
        if array.ndim != 3 or array.shape[2] != 3:
            return None
        red = array[..., 0]
        green = array[..., 1]
        blue = array[..., 2]
        emphasised = red - 0.45 * green - 0.45 * blue + 80.0
        emphasised = np.clip(emphasised, 0, 255)
        emphasised = emphasised.astype("uint8")
        enhanced_image = Image.fromarray(emphasised, mode="L")
        return ImageOps.autocontrast(enhanced_image)

    red_mask = _red_enhance(base)
    red_binary = None
    if red_mask is not None:
        red_binary = red_mask.point(lambda p: 255 if p > 140 else 0, mode="1").convert("L")

    mapping = {
        "base": base,
        "grayscale": grayscale,
        "contrast": enhanced,
        "sharpen": sharpened,
        "autocontrast": autocontrasted,
        "invert": inverted,
        "binary": binary_gray,
        "binary_invert": ImageOps.invert(binary_gray),
        "red_mask": red_mask,
        "red_binary": red_binary,
        "fourier": _fourier_sharpen(grayscale),
    }
    result: Dict[str, Image.Image] = {}
    for name in variants:
        variant = mapping.get(name)
        if variant is None:
            continue
        result[name] = variant
    return result


@dataclass
class EngineResult:
    label: str
    texts: List[str]
    values: List[float]

    def to_lines(self) -> List[str]:
        formatted: List[str] = []
        if self.values:
            value_text = ", ".join(f"{value:.3f}" for value in self.values)
        else:
            value_text = "—"
        formatted.append(f"  {self.label}: values={value_text}")
        for text in self.texts:
            wrapped = textwrap.wrap(text, width=96)
            if not wrapped:
                continue
            formatted.extend(f"    {line}" for line in wrapped)
        if not self.texts:
            formatted.append("    (no text recognised)")
        return formatted


class PreviewImageLabel(QtWidgets.QLabel):
    """Clickable image preview that opens a full-resolution dialog."""

    def __init__(self, title: str, image: Image.Image, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._title = title
        self._image = image
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.setToolTip("Double-click to open the full-resolution variant")

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:  # pragma: no cover - interactive
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        try:
            qimage = ImageQt.ImageQt(self._image)
            pixmap = QtGui.QPixmap.fromImage(qimage)
        except Exception:
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(f"{self._title} – full preview")
        dialog_layout = QtWidgets.QVBoxLayout(dialog)
        dialog_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        container = QtWidgets.QWidget()
        container_layout = QtWidgets.QVBoxLayout(container)
        container_layout.setContentsMargins(12, 12, 12, 12)
        image_label = QtWidgets.QLabel()
        image_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        image_label.setPixmap(pixmap)
        container_layout.addWidget(image_label)
        scroll.setWidget(container)
        dialog_layout.addWidget(scroll)
        width = min(max(pixmap.width() + 32, 480), 1440)
        height = min(max(pixmap.height() + 48, 360), 1024)
        dialog.resize(width, height)
        dialog.exec()

class OcrDebugWidget(QtWidgets.QWidget):
    """Simple playground for microscope OCR preprocessing experiments."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Microscope OCR Debug")
        self.resize(960, 640)
        self._logger = logging.getLogger("microwire_data_builder.ocr_debug")
        self._last_scanned_folder: Optional[Path] = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        directory_row = QtWidgets.QHBoxLayout()
        directory_row.setSpacing(8)
        self.directory_edit = QtWidgets.QLineEdit()
        self.directory_edit.setPlaceholderText("Folder containing microscope images…")
        default_dir = Path("sample_data/database_builder/microscope")
        if default_dir.exists():
            self.directory_edit.setText(str(default_dir.resolve()))
        browse_button = QtWidgets.QPushButton("Browse…")
        browse_button.clicked.connect(self._browse_for_directory)
        directory_row.addWidget(QtWidgets.QLabel("Image folder:"))
        directory_row.addWidget(self.directory_edit, 1)
        directory_row.addWidget(browse_button)
        layout.addLayout(directory_row)

        self.directory_edit.editingFinished.connect(self._refresh_image_list)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)

        left_widget = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        images_label = QtWidgets.QLabel("Images (select one or more):")
        left_layout.addWidget(images_label)
        self.image_list = QtWidgets.QListWidget()
        self.image_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.image_list.setUniformItemSizes(True)
        self.image_list.setMinimumHeight(200)
        self.image_list.setMinimumWidth(220)
        left_layout.addWidget(self.image_list, 1)

        variants_label = QtWidgets.QLabel("Preprocessing variants:")
        left_layout.addWidget(variants_label)
        self.variant_list = QtWidgets.QListWidget()
        self.variant_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        for variant in VARIANT_ORDER:
            item = QtWidgets.QListWidgetItem(variant)
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.CheckState.Checked)
            self.variant_list.addItem(item)
        self.variant_list.setMinimumHeight(160)
        left_layout.addWidget(self.variant_list)

        engine_row = QtWidgets.QHBoxLayout()
        engine_row.setSpacing(6)
        engine_label = QtWidgets.QLabel("Engine:")
        self.engine_combo = QtWidgets.QComboBox()
        self.engine_combo.addItems(["PaddleOCR", "Tesseract", "Both"])
        engine_row.addWidget(engine_label)
        engine_row.addWidget(self.engine_combo, 1)
        left_layout.addLayout(engine_row)

        mode_row = QtWidgets.QHBoxLayout()
        mode_row.setSpacing(6)
        mode_label = QtWidgets.QLabel("Output:")
        self.output_mode_combo = QtWidgets.QComboBox()
        for label, data in OUTPUT_MODE_OPTIONS:
            self.output_mode_combo.addItem(label, data)
        mode_row.addWidget(mode_label)
        mode_row.addWidget(self.output_mode_combo, 1)
        left_layout.addLayout(mode_row)

        run_button = QtWidgets.QPushButton("Run OCR")
        run_button.clicked.connect(self.run_analysis)
        left_layout.addWidget(run_button)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        left_layout.addWidget(self.progress_bar)
        left_layout.addStretch(1)

        splitter.addWidget(left_widget)

        preview_widget = QtWidgets.QWidget()
        preview_layout = QtWidgets.QVBoxLayout(preview_widget)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(8)
        self.preview_title = QtWidgets.QLabel("Preview: (select an image)")
        self.preview_title.setWordWrap(True)
        preview_layout.addWidget(self.preview_title)
        self.preview_area = QtWidgets.QScrollArea()
        self.preview_area.setWidgetResizable(True)
        self.preview_area.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.preview_area.setMinimumWidth(360)
        self.preview_area.setMinimumHeight(240)
        self.preview_widget = QtWidgets.QWidget()
        self.preview_layout = QtWidgets.QVBoxLayout(self.preview_widget)
        self.preview_layout.setContentsMargins(6, 6, 6, 6)
        self.preview_layout.setSpacing(16)
        self.preview_area.setWidget(self.preview_widget)
        preview_layout.addWidget(self.preview_area, 1)
        splitter.addWidget(preview_widget)

        output_widget = QtWidgets.QWidget()
        output_layout = QtWidgets.QVBoxLayout(output_widget)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(8)
        output_label = QtWidgets.QLabel("OCR output")
        output_layout.addWidget(output_label)
        self.output_edit = QtWidgets.QPlainTextEdit()
        self.output_edit.setReadOnly(True)
        self.output_edit.setMinimumWidth(280)
        output_layout.addWidget(self.output_edit, 1)
        splitter.addWidget(output_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([260, 540, 360])

        note = QtWidgets.QLabel(
            "PaddleOCR runs through the microwire data builder pipeline without the automatic"
            " Tesseract fallback, while the Output selector controls whether raw strings or"
            " bracketed d/D values are shown. Tesseract executes only the preprocessing"
            " variants you enable above."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.image_list.itemSelectionChanged.connect(self._update_previews)
        self.variant_list.itemChanged.connect(self._handle_variant_change)

        self._refresh_image_list()

    # UI helpers -----------------------------------------------------------------
    def _browse_for_directory(self) -> None:
        current = Path(self.directory_edit.text().strip() or ".").resolve()
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Select microscope image folder", str(current))
        if directory:
            self.directory_edit.setText(directory)
            self._refresh_image_list()

    def _variant_states(self) -> Dict[str, bool]:
        states: Dict[str, bool] = {}
        for index in range(self.variant_list.count()):
            item = self.variant_list.item(index)
            states[item.text()] = item.checkState() == QtCore.Qt.CheckState.Checked
        return states

    def _selected_variants(self) -> List[str]:
        states = self._variant_states()
        enabled = [name for name, active in states.items() if active]
        return enabled or list(VARIANT_ORDER)

    def _handle_variant_change(self, _: QtWidgets.QListWidgetItem) -> None:
        self._update_previews()

    def _expected_measurement_label(self, image_path: Path) -> Optional[str]:
        name = image_path.name.lower()
        if "core" in name:
            return "d"
        if "glass" in name:
            return "D"
        return None

    def _format_output(
        self,
        label: str,
        image_path: Path,
        texts: Sequence[str],
        values: Sequence[float],
        detections: Sequence[object],
        output_mode: str,
    ) -> List[str]:
        if output_mode != "markers":
            return EngineResult(
                label=label,
                texts=list(texts),
                values=list(values),
            ).to_lines()

        expected = self._expected_measurement_label(image_path)
        heading = label
        if expected:
            heading = f"{label} ({expected})"

        marker_values = _marker_values_from_detections(detections)
        marker_values.extend(_marker_values_from_texts(texts))
        marker_values = _normalise_marker_values(marker_values)
        if marker_values:
            value_text = ", ".join(f"{value:.3f}" for value in marker_values)
        else:
            value_text = "—"

        lines: List[str] = [f"  {heading}: values={value_text}"]

        debug_texts: List[str] = []
        for detection in detections or []:
            if getattr(detection, "marker", None) == 1:
                detected_text = getattr(detection, "text", None)
                if detected_text:
                    debug_texts.append(detected_text)
        debug_texts.extend(_filter_marker_texts(texts))

        seen: set[str] = set()
        for text in debug_texts:
            normalised = _normalise_microscope_text(text)
            if normalised in seen:
                continue
            seen.add(normalised)
            wrapped = textwrap.wrap(text, width=96)
            if wrapped:
                lines.extend(f"    {line}" for line in wrapped)

        if len(lines) == 1:
            lines.append("    (no [1] markers recognised)")
        return lines

    # OCR execution ---------------------------------------------------------------
    def run_analysis(self) -> None:
        folder = Path(self.directory_edit.text().strip() or ".")
        if not folder.exists():
            QtWidgets.QMessageBox.warning(self, "OCR Debug", f"Folder {folder} does not exist.")
            return
        if folder != self._last_scanned_folder:
            self._refresh_image_list()

        images = self._selected_images(folder)
        if not images:
            QtWidgets.QMessageBox.information(self, "OCR Debug", "No microscope images selected or found in the folder.")
            return

        engine_choice = self.engine_combo.currentText()
        run_paddle = engine_choice in {"PaddleOCR", "Both"}
        run_tesseract = engine_choice in {"Tesseract", "Both"}
        selected_variants = self._selected_variants()
        output_mode = self.output_mode_combo.currentData() or "raw"

        output_lines: List[str] = []
        self._begin_progress(len(images))
        for image_path in images:
            output_lines.append(f"Image: {image_path.name}")
            if run_paddle:
                paddle_lines = self._run_paddle(image_path, output_mode)
                output_lines.extend(paddle_lines)
            if run_tesseract:
                tesseract_lines = self._run_tesseract(image_path, selected_variants, output_mode)
                output_lines.extend(tesseract_lines)
            output_lines.append("")
            self._advance_progress()
        self.output_edit.setPlainText("\n".join(output_lines).rstrip())
        self._end_progress()

    def _run_paddle(self, image_path: Path, output_mode: str) -> List[str]:
        ocr = get_paddle_ocr(self._logger)
        if ocr is None:
            return ["  PaddleOCR: unavailable (install paddlepaddle/paddleocr)"]

        try:
            result = _extract_microscope_diameters(
                image_path,
                self._logger,
                allow_tesseract_fallback=False,
            )
        except Exception as exc:  # pragma: no cover - defensive
            return [f"  PaddleOCR error: {exc}"]

        texts = list(getattr(result, "texts", []))
        values = list(getattr(result, "values", []))
        detections = list(getattr(result, "detections", []))
        lines = self._format_output(
            "PaddleOCR",
            image_path,
            texts,
            values,
            detections,
            output_mode,
        )

        if output_mode == "raw":
            raw_map = self._collect_paddle_raw_texts(ocr, image_path)
            if raw_map:
                for variant, tokens in raw_map.items():
                    display = ", ".join(tokens) if tokens else "(no text)"
                    lines.append(f"    raw/{variant}: {display}")
            else:
                lines.append("    raw detections: (no text returned)")

        return lines

    def _collect_paddle_raw_texts(
        self,
        ocr,
        image_path: Path,
    ) -> Dict[str, List[str]]:
        raw_texts: Dict[str, List[str]] = {}
        try:
            with Image.open(image_path) as raw:
                base = raw.convert("RGB")
        except Exception:
            return raw_texts

        selected = set(self._selected_variants())
        variants = {"base": base}
        variants.update(_variant_images(base, VARIANT_ORDER))

        for name, image in variants.items():
            if image is None:
                continue
            if name != "base" and name not in selected:
                continue
            array = np.array(image.convert("RGB"))
            if array.ndim != 3 or array.shape[2] != 3:
                continue
            bgr = array[:, :, ::-1].copy()
            try:
                result = ocr.ocr(bgr, cls=True)
            except Exception:
                continue
            tokens: List[str] = []
            for entry in result or []:
                if not entry:
                    continue
                for detection in entry:
                    if not detection:
                        continue
                    try:
                        data = detection[1]
                    except (TypeError, IndexError):
                        continue
                    if not data:
                        continue
                    text = str(data[0] or "").strip()
                    if text:
                        tokens.append(text)
            if tokens:
                raw_texts[name] = tokens

        return raw_texts

    def _run_tesseract(
        self,
        image_path: Path,
        variants: Sequence[str],
        output_mode: str,
    ) -> List[str]:
        if pytesseract is None:  # pragma: no cover - optional dependency
            return ["  Tesseract: pytesseract is not installed"]
        try:
            pytesseract.get_tesseract_version()
        except TesseractNotFoundError:
            return ["  Tesseract: tesseract binary not found"]
        except Exception as exc:  # pragma: no cover - defensive
            return [f"  Tesseract error: {exc}"]

        try:
            with Image.open(image_path) as raw:
                raw = raw.convert("RGB")
                variant_images = _variant_images(raw, variants)
        except Exception as exc:  # pragma: no cover - defensive
            return [f"  Tesseract failed to open image: {exc}"]

        lines: List[str] = []
        for name, variant in variant_images.items():
            try:
                text = pytesseract.image_to_string(variant, config="--psm 6")
            except Exception as exc:  # pragma: no cover - defensive
                lines.append(f"  Tesseract/{name}: error {exc}")
                continue
            cleaned = _normalise_microscope_text(text or "")
            values = _parse_microscope_candidates([cleaned])
            texts = [cleaned] if cleaned else []
            lines.extend(
                self._format_output(
                    f"Tesseract/{name}",
                    image_path,
                    texts,
                    values,
                    [],
                    output_mode,
                )
            )
        return lines

    def _refresh_image_list(self) -> None:
        folder = Path(self.directory_edit.text().strip() or ".")
        if not folder.exists() or not folder.is_dir():
            self.image_list.clear()
            self._last_scanned_folder = None
            return

        existing_selection = {
            str(data)
            for data in (item.data(QtCore.Qt.ItemDataRole.UserRole) for item in self.image_list.selectedItems())
            if data
        }
        self.image_list.clear()

        images = [path.resolve() for path in self._all_images_in_folder(folder)]

        for path in images:
            item = QtWidgets.QListWidgetItem(path.name)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            if str(path) in existing_selection:
                item.setSelected(True)
            self.image_list.addItem(item)

        if self.image_list.count() and not self.image_list.selectedItems():
            self.image_list.item(0).setSelected(True)

        self._last_scanned_folder = folder
        self._update_previews()

    def _selected_images(self, folder: Path) -> List[Path]:
        selected: List[Path] = []
        for item in self.image_list.selectedItems():
            data = item.data(QtCore.Qt.ItemDataRole.UserRole)
            if not data:
                continue
            candidate = Path(data)
            if candidate.exists():
                selected.append(candidate)

        if selected:
            return selected

        # Fall back to all images in the folder when nothing is selected.
        return self._all_images_in_folder(folder)

    def _all_images_in_folder(self, folder: Path) -> List[Path]:
        if not folder.exists() or not folder.is_dir():
            return []
        return sorted(
            [path.resolve() for path in folder.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES],
            key=lambda path: path.name.lower(),
        )

    def _clear_preview_layout(self) -> None:
        while self.preview_layout.count():
            item = self.preview_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _update_previews(self) -> None:
        folder = Path(self.directory_edit.text().strip() or ".")
        images = self._selected_images(folder)
        if not images:
            self.preview_title.setText("Preview: (select an image)")
            self._clear_preview_layout()
            self._add_preview_message("Select one or more microscope images to view preprocessing variants.")
            return

        first = images[0]
        self.preview_title.setText(f"Preview: {first.name}{' (+' + str(len(images) - 1) + ' more)' if len(images) > 1 else ''}")

        try:
            with Image.open(first) as raw:
                pil_image = raw.convert("RGB")
        except Exception as exc:  # pragma: no cover - defensive
            self._clear_preview_layout()
            self._add_preview_message(f"Failed to open {first.name}: {exc}")
            return

        variant_images = _variant_images(pil_image, VARIANT_ORDER)
        states = self._variant_states()

        self._clear_preview_layout()
        for name in VARIANT_ORDER:
            image = variant_images.get(name)
            if image is None:
                continue
            label = self._build_preview_widget(name, image, states.get(name, False))
            self.preview_layout.addWidget(label)
        if not self.preview_layout.count():
            self._add_preview_message("No preprocessing variants are available for preview.")
        else:
            self.preview_layout.addStretch(1)

    def _add_preview_message(self, message: str) -> None:
        label = QtWidgets.QLabel(message)
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        self.preview_layout.addWidget(label)

    def _build_preview_widget(self, name: str, image: Image.Image, enabled: bool) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        container.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        status = "enabled" if enabled else "disabled"
        header = QtWidgets.QLabel(f"{name} ({status})")
        header.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        header.setWordWrap(True)
        layout.addWidget(header)

        try:
            qimage = ImageQt.ImageQt(image)
            pixmap = QtGui.QPixmap.fromImage(qimage)
        except Exception:  # pragma: no cover - defensive
            error_label = QtWidgets.QLabel("Unable to render preview")
            error_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(error_label)
            return container

        preview = PreviewImageLabel(name, image)
        target_width = min(480, max(220, pixmap.width()))
        if pixmap.width() > target_width:
            pixmap = pixmap.scaledToWidth(
                target_width,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
        preview.setPixmap(pixmap)
        layout.addWidget(preview)
        return container

    def _begin_progress(self, total: int) -> None:
        if total <= 0:
            self.progress_bar.hide()
            return
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Processing %p%")
        self.progress_bar.setTextVisible(True)
        self.progress_bar.show()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents)

    def _advance_progress(self) -> None:
        if not self.progress_bar.isVisible():
            return
        self.progress_bar.setValue(self.progress_bar.value() + 1)
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents)

    def _end_progress(self) -> None:
        if not self.progress_bar.isVisible():
            return
        self.progress_bar.setFormat("Completed")
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents)
        self.progress_bar.hide()


def main() -> Optional[QtWidgets.QWidget]:
    app = QtWidgets.QApplication.instance()
    created = False
    if app is None:
        app = QtWidgets.QApplication([])
        created = True
    ensure_app_theme(app)
    widget = OcrDebugWidget()
    widget.show()
    if created:
        app.exec()
        return None
    return widget


if __name__ == "__main__":  # pragma: no cover - manual execution
    import sys

    logging.basicConfig(level=logging.INFO)
    app = QtWidgets.QApplication(sys.argv)
    widget = OcrDebugWidget()
    widget.show()
    sys.exit(app.exec())
