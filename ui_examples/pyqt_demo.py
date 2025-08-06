"""Minimal PyQt6 UI demo.

Shows a window with a button that updates a label.
Run with:
    python ui_examples/pyqt_demo.py
"""

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget
import sys

def main() -> None:
    app = QApplication(sys.argv)
    window = QWidget()
    window.setWindowTitle("PyQt6 Demo")

    layout = QVBoxLayout(window)

    label = QLabel("Button not pressed")
    button = QPushButton("Press me")

    def handle_click() -> None:
        label.setText("Button pressed!")

    button.clicked.connect(handle_click)
    layout.addWidget(label)
    layout.addWidget(button)

    window.show()
    app.exec()

if __name__ == "__main__":
    main()

