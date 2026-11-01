"""The metronome's settings window (right-click the Metronome button), and remembering them.

Two click editors side by side: the click at the start of each bar, and the click on the other
beats. Each has an envelope graph to drag (the attack, sustain-end and release-end points; up and
down for the level) with the same values as numbers below it, plus pitch, waveform and a Test
button. Changes apply at once (so you can adjust while it plays) and are remembered between runs.
"""
import json
from dataclasses import asdict, fields

from PySide6.QtCore import QPointF, QRectF, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QPushButton,
                               QVBoxLayout, QWidget)

from ..audio import metronome as m
from . import preferences, theme

SETTINGS_KEY = "metronome/sounds"
GRAPH_MS = m.MAX_ATTACK_MS + m.MAX_SUSTAIN_MS + m.MAX_RELEASE_MS  # time shown across the envelope graph
HANDLE_R = 5  # px


def load_settings():
    """The remembered MetronomeSettings (defaults for anything missing or invalid)."""
    settings = m.MetronomeSettings()
    try:
        stored = json.loads(QSettings(*preferences.SETTINGS).value(SETTINGS_KEY, "{}"))
        for which in ("bar", "beat"):
            click = getattr(settings, which)
            for f in fields(m.Click):
                if f.name in stored.get(which, {}):
                    setattr(click, f.name, type(getattr(click, f.name))(stored[which][f.name]))
            limit(click)
    except (ValueError, TypeError, AttributeError):
        settings = m.MetronomeSettings()
    return settings


def save_settings(settings):
    QSettings(*preferences.SETTINGS).setValue(SETTINGS_KEY, json.dumps(asdict(settings)))


def limit(click):
    """Keep a click's values in their ranges."""
    click.attack_ms = min(max(click.attack_ms, 0.0), m.MAX_ATTACK_MS)
    click.sustain_ms = min(max(click.sustain_ms, 0.0), m.MAX_SUSTAIN_MS)
    click.release_ms = min(max(click.release_ms, 0.0), m.MAX_RELEASE_MS)
    click.level_db = min(max(click.level_db, m.MIN_DB), m.MAX_DB)
    click.pitch = min(max(click.pitch, m.MIN_PITCH), m.MAX_PITCH)
    if click.waveform not in m.WAVEFORMS:
        click.waveform = "sine"


class EnvelopeGraph(QWidget):
    """Attack-sustain-release of one click: time across (GRAPH_MS), level up (MIN_DB..MAX_DB).
    Drag a point: the attack end (also the level), the sustain end (also the level), the release end."""
    changed = Signal()

    def __init__(self, click, color):
        super().__init__()
        self.click, self.color = click, color
        self.dragging = None  # index of the point being dragged
        self.setMinimumSize(240, 120)
        self.setMouseTracking(True)
        self.setToolTip("Drag the points: attack, sustain and release times; up and down for the level")

    def area(self):
        return QRectF(self.rect()).adjusted(HANDLE_R + 2, HANDLE_R + 2, -HANDLE_R - 2, -HANDLE_R - 2)

    def x_of(self, ms):
        a = self.area()
        return a.left() + ms / GRAPH_MS * a.width()

    def y_of(self, db):
        a = self.area()
        return a.bottom() - (db - m.MIN_DB) / (m.MAX_DB - m.MIN_DB) * a.height()

    def points(self):
        c = self.click
        y = self.y_of(c.level_db)
        return [QPointF(self.x_of(0), self.area().bottom()), QPointF(self.x_of(c.attack_ms), y),
                QPointF(self.x_of(c.attack_ms + c.sustain_ms), y),
                QPointF(self.x_of(c.attack_ms + c.sustain_ms + c.release_ms), self.area().bottom())]

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        a = self.area()
        p.fillRect(self.rect(), theme.METER_BG)
        p.setPen(theme.GRID)
        for ms in range(0, GRAPH_MS + 1, 50):  # a line every 50 ms
            p.drawLine(QPointF(self.x_of(ms), a.top()), QPointF(self.x_of(ms), a.bottom()))
        for db in (-30, -20, -10, 0):
            p.drawLine(QPointF(a.left(), self.y_of(db)), QPointF(a.right(), self.y_of(db)))
        pts = self.points()
        path = QPainterPath(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        fill = QColor(self.color)
        fill.setAlpha(60)
        p.fillPath(path, fill)
        p.setPen(QPen(self.color, 2))
        p.drawPath(path)
        p.setPen(theme.RULER_TEXT)
        p.drawText(a.adjusted(4, 2, -4, -2), Qt.AlignRight | Qt.AlignTop, f"{GRAPH_MS} ms")
        for i, pt in enumerate(pts[1:], 1):
            p.setPen(QPen(theme.ACCENT_TEXT if i == self.dragging else self.color, 2))
            p.setBrush(theme.METER_BG)
            p.drawEllipse(pt, HANDLE_R, HANDLE_R)

    def mousePressEvent(self, e):
        pos = e.position()
        near = [(abs(pt.x() - pos.x()) + abs(pt.y() - pos.y()), i) for i, pt in enumerate(self.points()[1:], 1)]
        distance, index = min(near)
        self.dragging = index if distance < 20 else None
        self.update()

    def mouseMoveEvent(self, e):
        if self.dragging is None:
            return
        a, c, pos = self.area(), self.click, e.position()
        ms = (pos.x() - a.left()) / a.width() * GRAPH_MS
        if self.dragging == 1:
            c.attack_ms = ms
        elif self.dragging == 2:
            c.sustain_ms = ms - c.attack_ms
        else:
            c.release_ms = ms - c.attack_ms - c.sustain_ms
        if self.dragging in (1, 2):
            c.level_db = m.MIN_DB + (a.bottom() - pos.y()) / a.height() * (m.MAX_DB - m.MIN_DB)
        limit(c)
        c.attack_ms, c.sustain_ms, c.release_ms, c.level_db = (round(v, 1) for v in
                                                                (c.attack_ms, c.sustain_ms, c.release_ms, c.level_db))
        self.update()
        self.changed.emit()

    def mouseReleaseEvent(self, e):
        self.dragging = None
        self.update()


class ClickEditor(QGroupBox):
    """One click: its envelope graph, its values as numbers, pitch, waveform, and Test."""
    changed = Signal()

    def __init__(self, title, click, color, test):
        """test(click): play it once."""
        super().__init__(title)
        self.click = click
        self.graph = EnvelopeGraph(click, color)
        self.graph.changed.connect(self.from_graph)
        self.boxes = {}
        form = QFormLayout()
        for attr, label, low, high, suffix, step in (
                ("attack_ms", "Attack", 0, m.MAX_ATTACK_MS, " ms", 0.5),
                ("sustain_ms", "Sustain", 0, m.MAX_SUSTAIN_MS, " ms", 1),
                ("release_ms", "Release", 0, m.MAX_RELEASE_MS, " ms", 1),
                ("level_db", "Level", m.MIN_DB, m.MAX_DB, " dB", 0.5),
                ("pitch", "Pitch", m.MIN_PITCH, m.MAX_PITCH, " Hz", 10)):
            box = QDoubleSpinBox()
            box.setRange(low, high)
            box.setDecimals(1)
            box.setSingleStep(step)
            box.setSuffix(suffix)
            box.setKeyboardTracking(False)
            box.valueChanged.connect(lambda v, a=attr: self.set_value(a, v))
            self.boxes[attr] = box
            form.addRow(label, box)
        self.waveform = QComboBox()
        self.waveform.addItems([w.capitalize() for w in m.WAVEFORMS])
        self.waveform.currentIndexChanged.connect(lambda i: self.set_value("waveform", m.WAVEFORMS[i]))
        form.addRow("Waveform", self.waveform)
        test_button = QPushButton("▶ Test")
        test_button.setToolTip("Play this click once")
        test_button.clicked.connect(lambda: test(self.click))
        col = QVBoxLayout(self)
        col.addWidget(self.graph, 1)
        col.addLayout(form)
        col.addWidget(test_button)
        self.show_values()

    def show_values(self):
        for attr, box in self.boxes.items():
            box.blockSignals(True)
            box.setValue(getattr(self.click, attr))
            box.blockSignals(False)
        self.waveform.blockSignals(True)
        self.waveform.setCurrentIndex(m.WAVEFORMS.index(self.click.waveform))
        self.waveform.blockSignals(False)
        self.boxes["pitch"].setEnabled(self.click.waveform != "noise")
        self.graph.update()

    def set_value(self, attr, value):
        setattr(self.click, attr, value)
        limit(self.click)
        self.show_values()
        self.changed.emit()

    def from_graph(self):
        self.show_values()
        self.changed.emit()


class MetronomeDialog(QDialog):
    """Edits `settings` (MetronomeSettings) in place; apply(settings) is called after every change."""

    def __init__(self, parent, settings, apply, test):
        super().__init__(parent)
        self.setWindowTitle("Metronome")
        self.settings, self.apply = settings, apply
        self.editors = [ClickEditor("Start of the bar", settings.bar, theme.SOLO, test),
                        ClickEditor("Other beats", settings.beat, theme.TRACK_ACCENT["audio"], test)]
        row = QHBoxLayout()  # the two envelopes right next to each other
        for editor in self.editors:
            editor.changed.connect(self.changed)
            row.addWidget(editor)
        defaults, close = QPushButton("Defaults"), QPushButton("Close")
        defaults.setToolTip("Back to the standard metronome sounds")
        defaults.clicked.connect(self.use_defaults)
        close.setDefault(True)
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(defaults)
        buttons.addStretch(1)
        buttons.addWidget(close)
        col = QVBoxLayout(self)
        col.addLayout(row, 1)
        col.addLayout(buttons)

    def changed(self):
        save_settings(self.settings)
        self.apply(self.settings)

    def use_defaults(self):
        fresh = m.MetronomeSettings()
        for editor, click in zip(self.editors, (fresh.bar, fresh.beat)):
            for f in fields(m.Click):
                setattr(editor.click, f.name, getattr(click, f.name))
            editor.show_values()
        self.changed()
