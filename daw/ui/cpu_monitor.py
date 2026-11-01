"""Audio CPU monitor in the top-right corner: current and average callback load, and a red
warning with the number of late audio buffers (likely clicks) since Play was pressed."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from .. import config
from . import theme


def load_color(load):
    return theme.METER_HOT if load >= 0.8 else theme.METER_WARN if load >= 0.5 else theme.METER_OK


class CpuMonitor(QWidget):
    def __init__(self):
        super().__init__()
        self.load = None  # (current, average) as fractions, or None when no audio is running
        self.late = 0
        self.setFixedSize(config.PANEL_W, config.RULER_H)
        self.show_load(None)

    def show_load(self, load, late=0):
        self.load, self.late = load, late
        self.setToolTip(
            "Audio CPU: share of each buffer's time spent computing audio.\n"
            f"Current = last {config.CPU_REFRESH_MS} ms, avg = rolling ~{config.CPU_AVERAGE_SECONDS:g} s.\n"
            "Near 100 % you will hear dropouts.\n"
            f"⚠ = late audio buffers since Play (likely clicks): {late}")
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), theme.RULER_BG)
        p.fillRect(0, 0, 1, self.height(), theme.METER_BG)  # separator from the ruler
        text_rect = self.rect().adjusted(8, 0, -8, -3)
        p.setPen(theme.RULER_TEXT)
        if self.load is None:
            p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, "Audio CPU  –")
            return
        current, average = self.load
        p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, f"Audio CPU {current * 100:4.1f} %")
        if self.late:
            p.drawText(text_rect.adjusted(0, 0, -50, 0), Qt.AlignVCenter | Qt.AlignRight, f"avg {average * 100:4.1f} %")
            p.setPen(theme.METER_HOT)
            p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignRight, f"⚠ {self.late}")
        else:
            p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignRight, f"avg {average * 100:4.1f} %")
        # thin bar along the bottom: current load, full width = 100 %
        bar_w = int((self.width() - 1) * min(1.0, current))
        p.fillRect(1, self.height() - 3, self.width() - 1, 3, theme.METER_BG)
        p.fillRect(1, self.height() - 3, bar_w, 3, load_color(current))
