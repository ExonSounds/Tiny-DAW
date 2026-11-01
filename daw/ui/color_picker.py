"""Colour dialog for clips and track rows: a colour wheel (hue around, saturation outward), a
brightness slider, a hex field, recently used colours, and a preview with automatic contrast.

Recent colours are remembered between runs (QSettings), shared by clips and tracks.
"""
import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import (QButtonGroup, QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSlider, QToolButton, QWidget)

from . import theme
from .colors import clip_colors, readable_text, tone_available, track_colors

RECENT_MAX = 10
_SETTINGS = ("TinyDAW", "TinyDAW")


def load_recent():
    stored = QSettings(*_SETTINGS).value("recent_clip_colors") or []
    if isinstance(stored, str):  # a one-item list comes back from the registry as a plain string
        stored = [stored]
    return [c for c in stored if QColor(c).isValid()]


def remember(hex_color):
    recent = [hex_color] + [c for c in load_recent() if c != hex_color]
    QSettings(*_SETTINGS).setValue("recent_clip_colors", recent[:RECENT_MAX])


def hsv_to_rgb(h, s, v):
    """Vectorised HSV -> RGB, all arrays in 0..1."""
    i = np.floor(h * 6).astype(int) % 6
    f = h * 6 - np.floor(h * 6)
    p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return r, g, b


class ColorWheel(QWidget):
    """Hue goes around the circle, saturation from the centre (white/grey) to the rim."""
    changed = Signal()

    def __init__(self, size=200):
        super().__init__()
        self.setFixedSize(size, size)
        self.h, self.s, self.v = 0.0, 0.0, 1.0
        self._image, self._image_v = None, None

    def color(self):
        return QColor.fromHsvF(self.h, self.s, self.v)

    def set_color(self, color):
        h, s, v, _ = color.getHsvF()
        self.h, self.s, self.v = max(h, 0.0), s, v  # grey has no hue (-1)
        self.update()

    def set_value(self, v):
        self.v = v
        self.update()

    def radius(self):
        return self.width() / 2 - 2

    def wheel_image(self):
        if self._image is None or self._image_v != self.v:
            n, r = self.width(), self.radius()
            y, x = np.mgrid[0:n, 0:n] + 0.5 - n / 2
            dist = np.hypot(x, y)
            hue = (np.arctan2(-y, x) / (2 * np.pi)) % 1.0
            red, green, blue = hsv_to_rgb(hue, np.clip(dist / r, 0, 1), np.full_like(dist, self.v))
            alpha = np.clip(r - dist + 0.5, 0, 1)  # soft 1 px rim
            rgba = (np.dstack([red * alpha, green * alpha, blue * alpha, alpha]) * 255).astype(np.uint8)
            self._buffer = np.ascontiguousarray(rgba)  # QImage does not copy; keep the bytes alive
            self._image = QImage(self._buffer.data, n, n, 4 * n, QImage.Format_RGBA8888_Premultiplied)
            self._image_v = self.v
        return self._image

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawImage(0, 0, self.wheel_image())
        angle, c = 2 * np.pi * self.h, self.width() / 2
        marker = QPointF(c + np.cos(angle) * self.s * self.radius(), c - np.sin(angle) * self.s * self.radius())
        p.setPen(QPen(readable_text(self.color()), 2))
        p.setBrush(self.color())
        p.drawEllipse(marker, 6, 6)

    def pick(self, pos):
        c = self.width() / 2
        x, y = pos.x() - c, pos.y() - c
        self.h = (np.arctan2(-y, x) / (2 * np.pi)) % 1.0
        self.s = min(1.0, np.hypot(x, y) / self.radius())
        self.update()
        self.changed.emit()

    def mousePressEvent(self, e):
        self.pick(e.position())

    def mouseMoveEvent(self, e):
        self.pick(e.position())


class ClipPreview(QWidget):
    """How a clip will look in the chosen colour, after automatic contrast."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(170, 64)
        self.hex_color, self.tone = "#808080", None

    def set_color(self, hex_color, tone):
        self.hex_color, self.tone = hex_color, tone
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), self.palette().window())
        colors = clip_colors(self.hex_color, True, self.tone)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.fillRect(rect, colors.fill)
        p.fillRect(QRectF(rect.left(), rect.top(), rect.width(), 16), colors.handle)
        p.setPen(colors.text)
        p.drawText(rect.adjusted(6, 1, 0, 0), Qt.AlignLeft | Qt.AlignTop, "Clip name")
        p.setPen(colors.content)
        mid = rect.top() + 16 + (rect.height() - 16) / 2
        for i, x in enumerate(range(int(rect.left()) + 4, int(rect.right()) - 4, 2)):
            a = (np.sin(i * 0.7) * 0.5 + 0.5) * (rect.height() - 24) / 2
            p.drawLine(QPointF(x + 0.5, mid - a), QPointF(x + 0.5, mid + a))  # +0.5: crisp 1 px lines
        p.setPen(QPen(colors.border, 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRect(rect)


class TrackPreview(QWidget):
    """How a track's row in the panel will look in the chosen colour, after automatic contrast."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(170, 64)
        self.hex_color, self.tone = "#808080", None

    def set_color(self, hex_color, tone):
        self.hex_color, self.tone = hex_color, tone
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), self.palette().window())
        background, text = track_colors(self.hex_color, self.tone)
        rect = self.rect().adjusted(2, 2, -2, -2)
        p.fillRect(rect, background)
        p.fillRect(rect.left(), rect.top(), 3, rect.height(), theme.TRACK_ACCENT["audio"])
        p.setPen(text)
        p.drawText(rect.adjusted(10, 6, 0, 0), Qt.AlignLeft | Qt.AlignTop, "Track name")
        font = QFont(theme.MONO_FONT, 8)
        p.setFont(font)
        p.drawText(rect.adjusted(0, 0, -6, -6), Qt.AlignRight | Qt.AlignBottom, "S -14.2\nI -16.0")
        p.fillRect(rect.right() - 54, rect.top() + 6, 22, 16, self.palette().button())  # an M / S button
        p.fillRect(rect.right() - 28, rect.top() + 6, 22, 16, self.palette().button())
        p.fillRect(rect.left() + 10, rect.bottom() - 20, 60, 14, theme.METER_BG)  # a level meter


CLIP, TRACK, GROUP = "clip", "track", "group"  # what the dialog colours
SUBJECTS = {
    CLIP: dict(title="Clip colour", preview=ClipPreview, content="Waveform / notes and name:",
               content_tip="waveform/notes and name", default_tip="Back to the standard colour for audio / MIDI clips"),
    TRACK: dict(title="Track colour", preview=TrackPreview, content="Name and readouts:",
                content_tip="name and LUFS readout", default_tip="Back to the standard look of track rows"),
    GROUP: dict(title="Group colour", preview=TrackPreview, content="Name:",
                content_tip="name", default_tip="Back to the standard look of group rows"),
}


def swatch(hex_color, slot):
    button = QToolButton()
    button.setFixedSize(22, 22)
    button.setToolTip(hex_color)
    button.setStyleSheet(f"QToolButton {{ background: {hex_color}; border: 1px solid {theme.FRAME_EDGE.name()}; "
                         f"border-radius: {theme.radius()}px; }}")
    button.clicked.connect(lambda: slot(QColor(hex_color)))
    return button


TONE_CHOICES = ((None, "Auto", "Picked automatically for the best contrast"),
                ("light", "White", "White {}"),
                ("dark", "Dark grey", "Dark grey {}"))


class ColorPickerDialog(QDialog):
    """Colour for clips (subject CLIP) or a track's row in the panel (TRACK).

    After exec(): `chosen` is "#rrggbb" or None (the standard look), and `tone` is "light", "dark"
    or None (automatic) for what is drawn on the colour: waveform/notes and name, or a row's text."""

    def __init__(self, parent, initial, tone=None, subject=CLIP):
        super().__init__(parent)
        self.subject = SUBJECTS[subject]
        self.setWindowTitle(self.subject["title"])
        self.tone_tips = {t: tip.format(self.subject["content_tip"]) for t, _, tip in TONE_CHOICES}
        self.chosen, self.tone, self.wanted_tone = initial.name(), tone, tone
        self.wheel = ColorWheel()
        self.brightness = QSlider(Qt.Horizontal)
        self.brightness.setRange(0, 100)
        self.brightness.setToolTip("Brightness")
        self.hex_edit = QLineEdit()
        self.hex_edit.setMaxLength(7)
        self.hex_edit.setFixedWidth(80)
        self.preview = self.subject["preview"]()

        grid = QGridLayout(self)
        grid.addWidget(self.wheel, 0, 0, 3, 1)
        grid.addWidget(QLabel("Preview (contrast is\nadjusted automatically)"), 0, 1)
        grid.addWidget(self.preview, 1, 1)
        hex_row = QHBoxLayout()
        hex_row.addWidget(QLabel("Hex"))
        hex_row.addWidget(self.hex_edit)
        hex_row.addStretch(1)
        grid.addLayout(hex_row, 2, 1)
        grid.addWidget(self.brightness, 3, 0)

        # White / dark grey are only offered when they contrast enough with the chosen colour
        tone_row = QHBoxLayout()
        tone_row.addWidget(QLabel(self.subject["content"]))
        self.tone_buttons = QButtonGroup(self)
        for tone_value, label, _ in TONE_CHOICES:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setToolTip(self.tone_tips[tone_value])
            button.setProperty("accent", True)  # lit in the skin's accent colour when chosen
            button.setProperty("tone", tone_value)
            self.tone_buttons.addButton(button)
            tone_row.addWidget(button)
        tone_row.addStretch(1)
        self.tone_buttons.buttonClicked.connect(lambda b: self.set_tone(b.property("tone")))
        grid.addLayout(tone_row, 4, 0, 1, 2)

        grid.addWidget(QLabel("Recent"), 5, 0, 1, 2)
        recent_row = QHBoxLayout()
        recent = load_recent()
        for hex_color in recent:
            recent_row.addWidget(swatch(hex_color, self.set_color))
        if not recent:
            recent_row.addWidget(QLabel("(colours you use show up here)"))
        recent_row.addStretch(1)
        grid.addLayout(recent_row, 6, 0, 1, 2)

        buttons = QHBoxLayout()
        default = QPushButton("Default")
        default.setToolTip(self.subject["default_tip"])
        default.clicked.connect(self.use_default)
        ok, cancel = QPushButton("OK"), QPushButton("Cancel")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(default)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        grid.addLayout(buttons, 7, 0, 1, 2)

        self.wheel.changed.connect(self.from_wheel)
        self.brightness.valueChanged.connect(lambda v: (self.wheel.set_value(v / 100), self.from_wheel()))
        self.hex_edit.editingFinished.connect(lambda: self.set_color(QColor(self.hex_edit.text())))
        self.set_color(initial)

    def set_color(self, color):
        if not color.isValid():
            self.hex_edit.setText(self.chosen)
            return
        self.wheel.set_color(color)
        self.brightness.blockSignals(True)
        self.brightness.setValue(round(self.wheel.v * 100))
        self.brightness.blockSignals(False)
        self.show_color(color.name())

    def from_wheel(self):
        self.show_color(self.wheel.color().name())

    def show_color(self, hex_color):
        self.chosen = hex_color
        self.hex_edit.setText(hex_color)
        self.refresh_tones()

    def set_tone(self, tone):
        self.wanted_tone = tone
        self.refresh_tones()

    def refresh_tones(self):
        """Enable White / Dark grey only where they contrast enough. A wanted tone that does not fit
        the current colour shows as Auto, and comes back when you move to a colour that fits it."""
        wanted = self.wanted_tone
        self.tone = wanted if wanted and tone_available(self.chosen, wanted) else None
        for button in self.tone_buttons.buttons():
            tone = button.property("tone")
            ok = tone is None or tone_available(self.chosen, tone)
            button.setEnabled(ok)
            button.setChecked(tone == self.tone)
            if tone:
                button.setToolTip(self.tone_tips[tone] if ok else "Not enough contrast on this colour")
        self.preview.set_color(self.chosen, self.tone)

    def use_default(self):
        self.hex_edit.blockSignals(True)  # closing ends its editing, which would set its colour again
        self.chosen, self.tone = None, None
        super().accept()

    def accept(self):
        remember(self.chosen)
        super().accept()
