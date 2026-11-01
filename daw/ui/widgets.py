"""Small building blocks shared by the panel widgets."""
from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import QComboBox, QFrame, QLabel, QMenu, QSlider, QToolButton, QWidget

from .. import config
from . import theme
from .formatting import fmt_db


def lane_style(color, accent=None, text=None):
    """Stylesheet of a lane-coloured frame (lane_frame): background, left stripe, label colour."""
    border = f"3px solid {accent.name()}" if accent else f"1px solid {theme.FRAME_EDGE.name()}"
    labels = f"#lane QLabel {{ color: {text.name()}; }}" if text else ""
    edge = f"1px solid {theme.ACCENT.name()}"  # a selected track's row (its property "current")
    current = f'#lane[current="true"] {{ border-top: {edge}; border-right: {edge}; border-bottom: {edge}; }}'
    return f"#lane {{ background: {color.name()}; border-left: {border}; }} {current} {labels}"


def lane_frame(color, accent=None, height=config.TRACK_H, text=None):
    """A QFrame exactly one lane tall, coloured like the timeline lane next to it (or the track's colour).

    `accent` draws a stripe down the left edge (the track kind's colour); `text` colours its labels."""
    frame = QFrame()
    frame.setObjectName("lane")
    frame.setFixedHeight(height)
    frame.setStyleSheet(lane_style(color, accent, text))
    return frame


class PinToBottom(QObject):
    """Keeps `widget` across the bottom of `viewport` (a scroll area's), `margin` px above its
    bottom edge and on top of the scrolled contents, whatever the scroll position or size."""

    def __init__(self, widget, viewport, height, margin=0):
        super().__init__(viewport)
        self.widget, self.viewport, self.height, self.margin = widget, viewport, height, margin
        widget.setParent(viewport)
        # the margin below it is covered too, or the scrolled contents would show through there
        self.cover = QWidget(viewport)
        self.cover.setAutoFillBackground(True)
        self.cover.setVisible(margin > 0)
        viewport.installEventFilter(self)
        widget.show()
        self.place()

    def eventFilter(self, obj, e):
        if e.type() in (QEvent.Resize, QEvent.ChildAdded):
            self.place()
        return False

    def place(self):
        vp = self.viewport
        self.widget.setGeometry(0, vp.height() - self.margin - self.height, vp.width(), self.height)
        self.cover.setGeometry(0, vp.height() - self.margin, vp.width(), self.margin)
        self.widget.raise_()
        self.cover.raise_()


class NameLabel(QLabel):
    """A track's or group's name; double-click it to rename."""
    double_clicked = Signal()

    def mouseDoubleClickEvent(self, e):
        self.double_clicked.emit()


def toggle_button(text, role, checked, slot, tooltip=""):
    """Small checkable button (M / S) that lights up in the skin's "mute" or "solo" colour (`role`) when on."""
    button = QToolButton()
    button.setText(text)
    button.setToolTip(tooltip)
    button.setCheckable(True)
    button.setChecked(checked)
    button.setFixedSize(26, 22)
    button.setFocusPolicy(Qt.NoFocus)
    button.setProperty("toggle", role)  # coloured by the skin's stylesheet (theme.stylesheet)
    button.toggled.connect(slot)
    return button


class _NoWheelComboBox(QComboBox):
    """Ignores the mouse wheel, so scrolling the panel never changes a value by accident."""

    def wheelEvent(self, e):
        e.ignore()


def output_pairs(channels):
    """[(first output index, label)]: (0, "1/2"), (2, "3/4"), ...; an odd last output stands alone."""
    return [(i, f"{i + 1}/{i + 2}" if i + 1 < channels else f"{i + 1}") for i in range(0, channels, 2)]


def output_selector(channels, current, slot):
    """Combo box of the device's output pairs; calls slot(first output index) on change."""
    combo = _NoWheelComboBox()
    combo.setFocusPolicy(Qt.NoFocus)
    combo.setToolTip("Device outputs this track plays on")
    combo.setFixedWidth(58)
    for first, label in output_pairs(channels):
        combo.addItem(label, first)
    combo.setCurrentIndex(max(0, combo.findData(current)))
    combo.currentIndexChanged.connect(lambda i: slot(combo.itemData(i)))
    return combo


class VolumeSlider(QSlider):
    """A track's volume in dB (MIN_DB = silent … MAX_DB); double-click for 0 dB. Ignores the mouse
    wheel, so scrolling the panel never changes a volume by accident."""

    def __init__(self, db, slot):
        super().__init__(Qt.Horizontal)
        self.setRange(config.MIN_DB, config.MAX_DB)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedHeight(14)
        self.setValue(round(db))
        self.valueChanged.connect(lambda v: (self.show_tip(), slot(float(v))))
        self.show_tip()

    def show_tip(self):
        self.setToolTip(f"Volume {fmt_db(self.value(), config.MIN_DB)} (double-click: 0 dB)")

    def mouseDoubleClickEvent(self, e):
        self.setValue(0)

    def wheelEvent(self, e):
        e.ignore()


class DbReading(QLabel):
    """A volume slider's setting in dB, shown next to it; double-click it for 0 dB."""

    def __init__(self, slider):
        super().__init__()
        self.slider = slider
        self.setFixedWidth(44)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setToolTip("Volume (double-click: 0 dB)")
        slider.valueChanged.connect(self.show_value)
        self.show_value(slider.value())

    def show_value(self, db):
        self.setText(fmt_db(db, config.MIN_DB))

    def mouseDoubleClickEvent(self, e):
        self.slider.setValue(0)


def effects_summary(effects):
    """Tooltip text listing a track's (or the master's) effects."""
    if not effects:
        return "No effects yet: click to add some"
    return "Effects: " + ", ".join(s.name + (" (bypassed)" if s.bypass else "") for s in effects)


def fx_button(effects, slot):
    """'FX' button opening the effects window; lit while there are effects."""
    button = QToolButton()
    button.setText("FX")
    button.setFixedSize(28, 22)
    button.setFocusPolicy(Qt.NoFocus)
    button.setToolTip(effects_summary(effects))
    if effects:
        button.setProperty("lit", True)  # the skin's accent colour (theme.stylesheet)
    button.clicked.connect(slot)
    return button


def instrument_button(instrument, edit, fill_menu, rack=None):
    """A MIDI track's instrument button (🎹): clicking it calls edit() (the instrument's window, or the
    instrument patch panel when it plays one: rack = how many instruments it holds), or, with the
    built-in synth (no settings), shows the menu; its ▾ shows the menu, which fill_menu(menu) fills
    each time it opens (the instruments to choose from)."""
    button = QToolButton()
    button.setText("🎹")
    button.setFixedSize(40, 22)
    button.setFocusPolicy(Qt.NoFocus)
    button.setPopupMode(QToolButton.MenuButtonPopup)
    menu = QMenu(button)
    menu.aboutToShow.connect(lambda: fill_menu(menu))
    button.setMenu(menu)
    if rack is not None:
        button.setToolTip(f"Instrument patch panel: {rack} instrument{'' if rack == 1 else 's'}\nClick to open it; "
                          "▾ to choose another instrument")
        button.clicked.connect(edit)
    elif instrument:
        button.setToolTip(f"Instrument: {instrument.name}\nClick to open its window and configure it; "
                          "▾ to choose another instrument")
        button.clicked.connect(edit)
    else:
        button.setToolTip("Instrument: Built-in synth (no settings)\nClick to choose another instrument")
        button.clicked.connect(button.showMenu)
    return button


def icon_button(text, slot, tooltip=""):
    button = QToolButton()
    button.setText(text)
    button.setToolTip(tooltip)
    button.setFixedSize(22, 22)
    button.setFocusPolicy(Qt.NoFocus)
    button.clicked.connect(slot)
    return button
