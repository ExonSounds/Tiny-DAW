"""The waveform at the top of the Explorer: the exact audio of the selected sample (a MIDI file:
what the built-in synth plays of it), left channel above, right below (one lane if they are the
same). A line follows the preview while it plays; click to play from that point.
"""
import numpy as np
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme
from .formatting import fmt_time

HEIGHT = 76  # px


class SampleWaveform(QWidget):
    clicked = Signal(float)  # where it was clicked: 0 (start) … 1 (end)

    def __init__(self):
        super().__init__()
        self.setFixedHeight(HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.audio, self.name, self.seconds = None, "", 0.0
        self.position = None  # 0…1 of the preview playing, or None
        self._peaks = None  # (width, [(mins, maxs)] per lane), for drawing

    def set_audio(self, audio, name, sr):
        """Show this audio (stereo float, at rate sr), or nothing (audio None)."""
        self.audio, self.name = audio, name
        self.seconds = len(audio) / sr if audio is not None else 0.0
        self._peaks, self.position = None, None
        self.setToolTip("Click to play from there" if audio is not None else "")
        self.update()

    def set_position(self, fraction):
        if fraction != self.position:
            self.position = fraction
            self.update()

    def lanes(self, width):
        """Per lane (one per channel, or one if both are the same): the lowest and highest sample of
        each pixel column, from every sample of the audio."""
        if self._peaks is None or self._peaks[0] != width:
            audio = self.audio
            channels = [audio[:, 0]] if audio.shape[1] == 1 or np.array_equal(audio[:, 0], audio[:, 1]) else \
                [audio[:, 0], audio[:, 1]]
            edges = np.linspace(0, len(audio), width + 1).astype(np.int64)[:-1]
            edges = np.minimum(edges, len(audio) - 1)
            self._peaks = (width, [(np.minimum.reduceat(c, edges), np.maximum.reduceat(c, edges)) for c in channels])
        return self._peaks[1]

    def paintEvent(self, event):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), theme.METER_BG)
        if self.audio is None or not len(self.audio) or w < 2:
            p.setPen(theme.RULER_TEXT)
            p.drawText(self.rect(), Qt.AlignCenter, "Click a sample to see its waveform")
            return
        lanes = self.lanes(w)
        lane_h = (h - 14) / len(lanes)
        for i, (mins, maxs) in enumerate(lanes):
            top = 14 + i * lane_h
            middle = top + lane_h / 2
            p.fillRect(QRectF(0, middle, w, 1), theme.GRID)
            half = lane_h / 2 - 1
            p.setPen(theme.SAMPLE_WAVE)
            for x, (lo, hi) in enumerate(zip(mins, maxs)):
                y0, y1 = middle - min(1.0, hi) * half, middle - max(-1.0, lo) * half
                p.drawLine(x, int(y0), x, int(max(y1, y0 + 1)))
        p.setPen(theme.RULER_TEXT)
        p.drawText(QRectF(4, 0, w - 8, 14), Qt.AlignLeft | Qt.AlignVCenter, self.name)
        p.drawText(QRectF(4, 0, w - 8, 14), Qt.AlignRight | Qt.AlignVCenter, fmt_time(self.seconds, 2))
        if self.position is not None:
            x = int(self.position * (w - 1))
            p.setPen(theme.PLAYHEAD)
            p.drawLine(x, 14, x, h)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.audio is not None:
            self.clicked.emit(min(1.0, max(0.0, e.position().x() / max(1, self.width()))))
