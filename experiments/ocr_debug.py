"""Interactive microscope OCR playground for PaddleOCR and Tesseract."""

from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from PyQt6 import QtCore, QtWidgets

from microwire_data_builder.core import _extract_microscope_diameters, _normalise_microscope_text, _parse_microscope_candidates
from microwire_data_builder.ocr import get_paddle_ocr

try:  # pragma: no cover - optional dependency
    import pytesseract  # type: ignore[import-not-found]
    from pytesseract import TesseractNotFoundError  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency
    pytesseract = None
    TesseractNotFoundError = RuntimeError


VARIANT_ORDER: Tuple[str, ...] = (
    "base",
    "grayscale",
    "contrast",
    "sharpen",
    "autocontrast",
    "invert",
    "binary",
    "binary_invert",
    "fourier",
)


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
    mapping = {
        "base": base,
        "grayscale": grayscale,
        "contrast": enhanced,
        "sharpen": sharpened,
        "autocontrast": autocontrasted,
        "invert": inverted,
        "binary": binary_gray,
        "binary_invert": ImageOps.invert(binary_gray),
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


class OcrDebugWidget(QtWidgets.QWidget):
    """Simple playground for microscope OCR preprocessing experiments."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Microscope OCR Debug")
        self.resize(960, 640)
        self._logger = logging.getLogger("microwire_data_builder.ocr_debug")

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

        controls_row = QtWidgets.QHBoxLayout()
        controls_row.setSpacing(12)

        self.engine_combo = QtWidgets.QComboBox()
        self.engine_combo.addItems(["PaddleOCR", "Tesseract", "Both"])
        controls_row.addWidget(QtWidgets.QLabel("Engine:"))
        controls_row.addWidget(self.engine_combo)

        self.variant_list = QtWidgets.QListWidget()
        self.variant_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        for variant in VARIANT_ORDER:
            item = QtWidgets.QListWidgetItem(variant)
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.CheckState.Checked)
            self.variant_list.addItem(item)
        variant_container = QtWidgets.QVBoxLayout()
        variant_container.addWidget(QtWidgets.QLabel("Tesseract variants:"))
        variant_container.addWidget(self.variant_list)
        variant_widget = QtWidgets.QWidget()
        variant_widget.setLayout(variant_container)
        controls_row.addWidget(variant_widget, 1)

        run_button = QtWidgets.QPushButton("Run OCR")
        run_button.clicked.connect(self.run_analysis)
        controls_row.addWidget(run_button)
        controls_row.addStretch(1)
        layout.addLayout(controls_row)

        self.output_edit = QtWidgets.QPlainTextEdit()
        self.output_edit.setReadOnly(True)
        layout.addWidget(self.output_edit, 1)

        note = QtWidgets.QLabel(
            "PaddleOCR always runs with the microwire data builder pipeline and emits per-variant"
            " logs when OCR debug mode is enabled. Tesseract executes only the selected"
            " preprocessing variants."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

    # UI helpers -----------------------------------------------------------------
    def _browse_for_directory(self) -> None:
        current = Path(self.directory_edit.text().strip() or ".").resolve()
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Select microscope image folder", str(current))
        if directory:
            self.directory_edit.setText(directory)

    def _selected_variants(self) -> List[str]:
        variants: List[str] = []
        for index in range(self.variant_list.count()):
            item = self.variant_list.item(index)
            if item.checkState() == QtCore.Qt.CheckState.Checked:
                variants.append(item.text())
        return variants or list(VARIANT_ORDER)

    # OCR execution ---------------------------------------------------------------
    def run_analysis(self) -> None:
        folder = Path(self.directory_edit.text().strip() or ".")
        if not folder.exists():
            QtWidgets.QMessageBox.warning(self, "OCR Debug", f"Folder {folder} does not exist.")
            return
        images = sorted(
            [
                path
                for path in folder.iterdir()
                if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
            ]
        )
        if not images:
            QtWidgets.QMessageBox.information(self, "OCR Debug", "No microscope images found in the selected folder.")
            return

        engine_choice = self.engine_combo.currentText()
        run_paddle = engine_choice in {"PaddleOCR", "Both"}
        run_tesseract = engine_choice in {"Tesseract", "Both"}
        selected_variants = self._selected_variants()

        output_lines: List[str] = []
        for image_path in images:
            output_lines.append(f"Image: {image_path.name}")
            if run_paddle:
                paddle_lines = self._run_paddle(image_path)
                output_lines.extend(paddle_lines)
            if run_tesseract:
                tesseract_lines = self._run_tesseract(image_path, selected_variants)
                output_lines.extend(tesseract_lines)
            output_lines.append("")
        self.output_edit.setPlainText("\n".join(output_lines).rstrip())

    def _run_paddle(self, image_path: Path) -> List[str]:
        ocr = get_paddle_ocr(self._logger)
        if ocr is None:
            return ["  PaddleOCR: unavailable (install paddlepaddle/paddleocr)" ]
        try:
            result = _extract_microscope_diameters(image_path, self._logger)
        except Exception as exc:  # pragma: no cover - defensive
            return [f"  PaddleOCR error: {exc}"]
        return EngineResult(
            label="PaddleOCR",
            texts=[text for text in getattr(result, "texts", [])],
            values=list(getattr(result, "values", [])),
        ).to_lines()

    def _run_tesseract(self, image_path: Path, variants: Sequence[str]) -> List[str]:
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
            result = EngineResult(label=f"Tesseract/{name}", texts=[cleaned] if cleaned else [], values=values)
            lines.extend(result.to_lines())
        return lines


def main() -> Optional[QtWidgets.QWidget]:
    return OcrDebugWidget()


if __name__ == "__main__":  # pragma: no cover - manual execution
    import sys

    logging.basicConfig(level=logging.INFO)
    app = QtWidgets.QApplication(sys.argv)
    widget = OcrDebugWidget()
    widget.show()
    sys.exit(app.exec())
