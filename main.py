"""Start Tiny DAW:  python main.py [project.json | audio and MIDI files to add...]"""
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from daw.ui.main_window import MainWindow
from daw.ui.theme import apply_dark_palette


def main():
    app = QApplication(sys.argv)
    apply_dark_palette(app)
    window = MainWindow()
    window.show()
    for path in sys.argv[1:]:
        if path.lower().endswith(".json"):
            window.open_project_file(path)
        else:
            window.timeline.add_file(path)
    QTimer.singleShot(1500, window.plugins.find_kinds)  # plugins whose kind isn't known yet: in the background
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
