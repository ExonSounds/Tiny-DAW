"""Peak meter with one horizontal bar per output channel in use (top to bottom: lowest output first).

Used on every track row (its output pair), group row (its tracks' outputs) and the master row
(every output a track plays on). A bar is never thicker than a tenth of the meter's width;
the bars sit in the middle of the meter's height.
"""
import numpy as np
from PySide6.QtWidgets import QSizePolicy, QWidget
from PySide6.QtGui import QPainter

from .. import config
from ..audio.dsp import gain_to_db
from . import theme

WARN_DB, HOT_DB = -12, -0.1  # bar turns yellow, then red, above these
MAX_BAR_FRACTION = 0.1  # of the meter's width: the thickest a bar may be
GAP = 1  # px between bars


def db_fraction(db):
    """-60 dB (MIN_DB) -> 0, 0 dB -> 1."""
    return min(1.0, max(0.0, (db - config.MIN_DB) / -config.MIN_DB))


def output_label(channels):
    """[4, 5] -> "outputs 5 and 6", [0] -> "output 1"."""
    names = [str(c + 1) for c in channels]
    return f"output {names[0]}" if len(names) == 1 else f"outputs {', '.join(names[:-1])} and {names[-1]}"


class LevelMeter(QWidget):
    def __init__(self, channels=2):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(4)
        self.shown, self.levels = [], np.zeros(0)
        self.wanted = [0, 1]  # outputs asked for, kept while a device lacks some of them
        self.set_channels(channels)

    def set_channels(self, channels):
        """The open device has this many outputs; outputs it lacks are not shown."""
        self.channels = channels
        self.show_channels(self.wanted)

    def show_channels(self, indexes):
        """Show bars for these outputs (0-based) only; if none of them exist: output 1."""
        self.wanted = list(indexes)
        shown = sorted({i for i in self.wanted if 0 <= i < self.channels}) or [0]
        if shown != self.shown:
            self.shown = shown
            self.levels = np.zeros(len(shown))
            self.setToolTip(f"Peak level of {output_label(shown)}, top to bottom")
            self.update()

    def push(self, peaks):
        """Show new peaks (one per output of the device); bars fall back slowly instead of jumping."""
        new = np.zeros_like(self.levels)
        if peaks is not None:
            new = np.array([peaks[i] if i < len(peaks) else 0.0 for i in self.shown])
        new = np.maximum(new, self.levels * config.METER_DECAY)
        new[new < 1e-4] = 0.0  # let idle meters settle so they stop repainting
        if not np.array_equal(new, self.levels):
            self.levels = new
            self.update()

    def bar_rects(self):
        """(y, thickness) of each bar."""
        n, w, h = len(self.levels), self.width(), self.height()
        gap = GAP if n * (1 + GAP) - GAP <= h else 0  # no room for gaps: bars touch
        thickness = max(1, min(int(w * MAX_BAR_FRACTION), (h - gap * (n - 1)) // n))
        total = n * thickness + gap * (n - 1)
        if total > h:  # not even 1 px each: spread over the height (neighbours may share a pixel)
            return [(min(h - 1, i * h // n), 1) for i in range(n)]
        top = (h - total) // 2
        return [(top + i * (thickness + gap), thickness) for i in range(n)]

    def paintEvent(self, event):
        p = QPainter(self)
        w = self.width()
        warn_w, hot_w = int(w * db_fraction(WARN_DB)), int(w * db_fraction(HOT_DB))
        for level, (y, thickness) in zip(self.levels, self.bar_rects()):
            p.fillRect(0, y, w, thickness, theme.METER_BG)
            bar_w = int(w * db_fraction(gain_to_db(level)))
            # green up to -12 dB, yellow up to 0 dB, red at the right end
            for right, left, color in ((min(bar_w, warn_w), 0, theme.METER_OK),
                                       (min(bar_w, hot_w), warn_w, theme.METER_WARN),
                                       (bar_w, hot_w, theme.METER_HOT)):
                if right > left:
                    p.fillRect(left, y, right - left, thickness, color)
