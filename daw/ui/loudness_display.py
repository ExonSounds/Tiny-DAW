"""Small two-line LUFS readout used on every track row and the master row:
short-term (S, last 3 s) on top, integrated (I, since Play was pressed) below;
momentary (last 0.4 s) is in the tooltip."""
import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

TIP = ("Loudness in LUFS (EBU R128)\n"
       "S  short-term, last 3 s\n"
       "I  integrated, since Play was pressed\n"
       "M  momentary, last 0.4 s: {m}")


def fmt_lufs(value):
    return "–" if math.isinf(value) else f"{value:.1f}"


class LoudnessDisplay(QLabel):
    def __init__(self):
        super().__init__()
        self.setObjectName("lufs")  # the skin's numbers font (theme.stylesheet)
        self.setFixedWidth(62)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.show_values(float("-inf"), float("-inf"), float("-inf"))

    def show_values(self, momentary, short_term, integrated):
        self.setText(f"S {fmt_lufs(short_term):>5}\nI {fmt_lufs(integrated):>5}")
        self.setToolTip(TIP.format(m=fmt_lufs(momentary)))
