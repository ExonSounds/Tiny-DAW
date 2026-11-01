"""The top bar: file/track buttons, the playback buttons (centred), time display, tempo, metronome and grid.
(The audio driver and device are chosen in Options → Audio properties…, see audio_dialog.py.)

The playback buttons are square, each showing an icon from the DAW_icons folder (next to the daw
folder), drawn in the skin's text colour for the button's state (accent_text when on, disabled_text
when off limits), so they follow skin changes at once; their names are in their tooltips. A
button whose icon file is missing shows a symbol instead (Loop has no icon yet: ⟲).

It only creates the controls; main_window.py connects them to the timeline and engine.
"""
import os

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QKeySequence, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QSizePolicy, QSpinBox, QStyle,
                               QStyleOptionToolButton, QStylePainter, QToolBar, QToolButton, QWidget)

from .. import config
from . import theme
from .formatting import fmt_bars, fmt_time

ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "DAW_icons")
_tinted = {}  # (icon, colour, px) -> QPixmap, or None if its file is missing


def tinted(name, color, px):
    """DAW_icons/<name>.svg drawn `px` pixels square (device pixels) in `color`, or None without the file."""
    key = (name, color.name(QColor.HexArgb), px)
    if key not in _tinted:
        renderer = QSvgRenderer(os.path.join(ICON_DIR, f"{name}.svg"))
        if not renderer.isValid():
            _tinted[key] = None
        else:
            image = QImage(px, px, QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            p = QPainter(image)
            renderer.render(p, QRectF(0, 0, px, px))
            p.setCompositionMode(QPainter.CompositionMode_SourceIn)  # its shape, in the colour
            p.fillRect(image.rect(), color)
            p.end()
            _tinted[key] = QPixmap.fromImage(image)
    return _tinted[key]


class IconButton(QToolButton):
    """A square playback button showing DAW_icons/<icon>.svg (or `glyph` without the file) in the skin's
    text colour for its state; its action's text and shortcut are only in its tooltip."""

    def __init__(self, icon, glyph):
        super().__init__()
        self.icon_name, self.glyph = icon, glyph
        self.setFixedSize(config.TRANSPORT_BUTTON_PX, config.TRANSPORT_BUTTON_PX)

    def set_icon(self, icon, glyph):
        self.icon_name, self.glyph = icon, glyph
        self.update()

    def paintEvent(self, _):
        opt = QStyleOptionToolButton()
        self.initStyleOption(opt)
        opt.text, opt.icon = "", QIcon()  # the skin's button only: the icon is drawn below
        p = QStylePainter(self)
        p.drawComplexControl(QStyle.CC_ToolButton, opt)
        role = "disabled_text" if not self.isEnabled() else "accent_text" if self.isChecked() else "button_text"
        color = theme.current[role]
        dpr = self.devicePixelRatioF()
        px = config.TRANSPORT_ICON_PX
        pixmap = tinted(self.icon_name, color, round(px * dpr))
        if pixmap is not None:
            pixmap.setDevicePixelRatio(dpr)
            p.drawPixmap((self.width() - px) // 2, (self.height() - px) // 2, pixmap)
        else:
            font = self.font()
            font.setPointSizeF(font.pointSizeF() * 1.7)
            p.setFont(font)
            p.setPen(color)
            p.drawText(QRect(0, 0, self.width(), self.height()), Qt.AlignCenter, self.glyph)


def separator():
    """A plain upright line between groups of controls (coloured by the skin's stylesheet)."""
    line = QFrame()
    line.setObjectName("separator")
    line.setFixedWidth(7)  # (the line is 1 px: the stylesheet's margins take the rest)
    return line


class TransportBar(QToolBar):
    """Three parts: the file and track buttons on the left, the playback buttons in the middle of the
    window (kept there whatever the window's width, as long as everything fits), and the time,
    tempo, metronome and grid on the right."""

    def __init__(self):
        super().__init__("Transport")
        self.setMovable(False)
        bar = QWidget()
        bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        # the left and right parts have the same stretch, so they are equally wide: the middle is centred
        self.left, self.middle, self.right = QHBoxLayout(), QHBoxLayout(), QHBoxLayout()
        for part, stretch in ((self.left, 1), (self.middle, 0), (self.right, 1)):
            holder = QWidget()
            holder.setLayout(part)
            part.setContentsMargins(0, 0, 0, 0)
            part.setSpacing(2)
            row.addWidget(holder, stretch)
        self.addWidget(bar)

        self.add_file_action = self.button(self.left, "Add file…")
        self.add_file_action.setToolTip("Add audio or MIDI files (Ctrl+I)")
        self.add_file_action.setShortcut(QKeySequence("Ctrl+I"))
        self.add_audio_track_action = self.button(self.left, "+ Audio")
        self.add_audio_track_action.setShortcut(QKeySequence("Ctrl+T"))
        self.add_audio_track_action.setToolTip("Add an audio track (Ctrl+T)")
        self.add_midi_track_action = self.button(self.left, "+ MIDI")
        self.add_midi_track_action.setShortcut(QKeySequence("Ctrl+Shift+T"))
        self.add_midi_track_action.setToolTip("Add a MIDI track (Ctrl+Shift+T)")
        self.left.addStretch(1)

        self.play_action = self.icon_button("Play", "play", "▶")
        self.play_action.setShortcut(QKeySequence(Qt.Key_Space))
        self.play_action.setToolTip("Play (Space), from the selection cursor")
        self.record_action = self.icon_button("Rec", "record", "●")
        self.record_action.setShortcut(QKeySequence(Qt.Key_R))
        self.record_action.setToolTip("Record (R) onto the selected audio track, from the selection cursor; again to "
                                      "stop. Right-click for the input and a count-in")
        self.record_button = self.middle.itemAt(self.middle.count() - 1).widget()
        self.record_button.setProperty("toggle", "record")  # red while recording (theme.stylesheet)
        self.record_button.setContextMenuPolicy(Qt.CustomContextMenu)  # main_window opens its settings
        self.start_action = self.icon_button("Start", "return_to_start", "⏮")
        self.start_action.setShortcut(QKeySequence(Qt.Key_Home))
        self.start_action.setToolTip("Back to the start (Home): both cursors to 0 s")
        self.loop_action = self.icon_button("Loop", "loop", "⟲")
        self.loop_action.setCheckable(True)
        self.loop_action.setShortcut(QKeySequence(Qt.Key_L))
        self.loop_action.setToolTip("Loop playback (L): from the loop range's end back to its start. Turning it on "
                                    "with a time range selected loops that range; drag the ends of the band on the "
                                    "ruler to change it")
        for action in (self.play_action, self.start_action):  # also from the plugins (FX) windows
            action.setShortcutContext(Qt.ApplicationShortcut)
        self.follow_action = self.icon_button("Follow", "follow", "⇥")
        self.follow_action.setCheckable(True)
        self.follow_action.setChecked(True)
        self.follow_action.setShortcut(QKeySequence(Qt.Key_F))
        self.follow_action.setToolTip("Keep the playhead in view during playback (F)")
        self.middle_buttons = [self.middle.itemAt(i).widget() for i in range(self.middle.count())]
        self.play_button = self.middle_buttons[0]

        self.right.addStretch(1)
        self.time_label = QLabel()
        self.time_label.setObjectName("time")  # the skin's numbers font (theme.stylesheet)
        self.time_label.setMinimumWidth(200)
        self.time_label.setAlignment(Qt.AlignCenter)
        self.right.addWidget(self.time_label)
        self.right.addWidget(separator())
        self.build_timing_controls()

    def button(self, part, text):
        """A toolbar-style button in `part` for a new action (its shortcut works while the window is active)."""
        action = QAction(text, self)
        button = QToolButton()
        button.setDefaultAction(action)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.NoFocus)
        part.addWidget(button)
        return action

    def icon_button(self, text, icon, glyph):
        """A square playback button in the middle for a new action, showing DAW_icons/<icon>.svg (IconButton)."""
        action = QAction(text, self)
        button = IconButton(icon, glyph)
        button.setDefaultAction(action)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.NoFocus)
        self.middle.addWidget(button)
        return action

    def build_timing_controls(self):
        self.bpm_box = QDoubleSpinBox()
        self.bpm_box.setRange(config.MIN_BPM, config.MAX_BPM)
        self.bpm_box.setDecimals(2)
        self.bpm_box.setSuffix(" BPM")
        self.bpm_box.setKeyboardTracking(False)  # apply typed tempos on Enter, not at every digit
        self.bpm_box.setToolTip("Tempo in beats (quarter notes) per minute")
        self.right.addWidget(self.bpm_box)
        self.tap_button = QToolButton()
        self.tap_button.setText("Tap")
        self.tap_button.setAutoRaise(True)
        self.tap_button.setShortcut(QKeySequence(Qt.Key_T))
        self.tap_button.setToolTip("Tap tempo (T): click in time with the music, at least twice; the BPM follows "
                                   f"the average of your last {config.TAP_COUNT} taps. A pause of "
                                   f"{config.TAP_RESET_S:g} s starts over")
        self.right.addWidget(self.tap_button)
        self.metronome_button = QToolButton()
        self.metronome_button.setText("Metronome")
        self.metronome_button.setAutoRaise(True)
        self.metronome_button.setCheckable(True)
        self.metronome_button.setShortcut(QKeySequence("Ctrl+M"))
        self.metronome_button.setToolTip("Metronome on/off while playing (Ctrl+M). Right-click for its sounds")
        self.metronome_button.setContextMenuPolicy(Qt.CustomContextMenu)  # main_window opens its settings
        self.metronome_button.setProperty("accent", True)  # lit in the skin's accent colour when on
        self.right.addWidget(self.metronome_button)
        self.signature_box = QSpinBox()
        self.signature_box.setRange(1, config.MAX_BEATS_PER_BAR)
        self.signature_box.setSuffix(" / 4")
        self.signature_box.setKeyboardTracking(False)
        self.signature_box.setToolTip("Time signature: beats per bar")
        self.right.addWidget(self.signature_box)
        for box in (self.bpm_box, self.signature_box):  # only take the keyboard when clicked
            box.setFocusPolicy(Qt.ClickFocus)
        self.beats_action = self.button(self.right, "♩ Beats")
        self.beats_action.setCheckable(True)
        self.beats_action.setShortcut(QKeySequence(Qt.Key_B))
        self.beats_action.setToolTip("Edit in bars and beats (B): bar ruler and grid, clicks snap to the grid "
                                     "(hold Alt to place freely). Off: minutes and seconds")
        self.thirds_action = self.button(self.right, "⅓ Thirds")
        self.thirds_action.setCheckable(True)
        self.thirds_action.setShortcut(QKeySequence(Qt.Key_3))
        self.thirds_action.setToolTip("Divide the grid in thirds: triplets (3)")

    def show_timing(self, timing):
        """Show the project's tempo and grid settings (without sending change signals)."""
        for widget, value in ((self.bpm_box, timing.bpm), (self.signature_box, timing.beats_per_bar),
                              (self.beats_action, timing.beats), (self.thirds_action, timing.thirds),
                              (self.loop_action, timing.looping)):
            widget.blockSignals(True)
            if widget in (self.beats_action, self.thirds_action, self.loop_action):
                widget.setChecked(value)
            else:
                widget.setValue(value)
            widget.blockSignals(False)
        self.thirds_action.setEnabled(timing.beats)

    def set_recording(self, on):
        """● Rec lit (red) while recording."""
        self.record_action.setCheckable(True)
        self.record_action.setChecked(on)
        self.record_action.setCheckable(on)  # (not checkable otherwise: a click only asks to record)

    def set_playing(self, playing):
        self.play_action.setText("Pause" if playing else "Play")
        self.play_action.setToolTip("Pause (Space)" if playing else "Play (Space), from the selection cursor")
        self.play_button.set_icon(*(("pause", "⏸") if playing else ("play", "▶")))

    def set_time(self, seconds, timing):
        text = fmt_time(seconds)
        self.time_label.setText(f"{fmt_bars(seconds, timing)}   {text}" if timing.beats else text)
