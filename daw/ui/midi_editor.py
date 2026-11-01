"""The MIDI editor (double-click a MIDI clip): a piano roll with the notes' velocities under it.

    +------+--------------------------------------------+--+
    |      | ruler: bars and beats, both cursors          |  |
    | keys | notes: time across, pitch up                |sb|
    |      |                                             |  |
    +------+--------------------------------------------+--+
    | vel. | one bar per note: its velocity               |  |
    |      | horizontal scroll bar                        |  |
    +------+--------------------------------------------+--+

Editing (a change goes into the clip when the mouse is let go, so it is rendered once):
    click an empty spot      a new note there, one grid step long (drag to make it longer)
    drag a note              move it in time and pitch (all selected notes together)
    drag a note's right end  change its length
    click / Ctrl+click       select / add to the selection; Delete deletes the selected notes
    Ctrl+D                   duplicate the selected notes right after them (the copies are selected)
    right-click a note       delete it
    velocity lane            click or drag across the bars to set velocities
    keys on the left         press to play a pitch; drag over them to play every key passed
    wheel / Shift+wheel / Ctrl+wheel / Alt+wheel: scroll up and down / sideways / zoom / key height
    ⤢ Expand                 the editor fills the screen (Keys − / + change the key height)
The ruler shows the timeline's two cursors, as on the timeline: the selection cursor (blue,
where Play starts) and the playback cursor (red, what is heard). Click or drag on the ruler to
move the selection cursor (while playing, playback jumps there too).
Notes sound through the track itself: its VST3 instrument (or the built-in synth), effects and
volume (audio/live.py). A pressed key, a new note or a grabbed note sounds while the mouse is held;
moving a note to another pitch plays the pitches it passes.
In bars and beats, times snap to the project's grid at this zoom (hold Alt to place freely).

Note times count from the start of the clip's notes, which is (clip.start - clip.offset) on the
timeline. The part outside the clip's window (after splitting it) is shaded: those notes don't play.
"""
import math

import numpy as np
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollBar, QToolButton, QVBoxLayout, QWidget

from .. import config
from ..audio.plugins import plays_plugins
from . import theme
from .colors import clip_color, clip_colors
from ..timing import BAR
from .formatting import fmt_time, ruler_step, step_decimals
from .timeline import paint_cursor

KEYS_W = 56  # px: the keyboard on the left (and the velocity lane's label)
ROW_H = 12  # px per pitch, to start with
MIN_ROW_H, MAX_ROW_H = 6, 32  # px: the key height can be changed between these (Keys − / +, Alt+wheel)
VELOCITY_H = 90  # px: the velocity lane
RULER_H = 22  # px: the editor's ruler
CURSOR_MS = 33  # how often the cursors are checked for having moved
EDGE_PX = 5  # px: grabbing this close to a note's right end changes its length
DEFAULT_VELOCITY = 100
GLIDE_S = 0.12  # a key passed while dragging over the keys sounds this long
BLACK_KEYS = {1, 3, 6, 8, 10}  # pitch % 12
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def note_name(pitch):
    return f"{NOTE_NAMES[pitch % 12]}{pitch // 12 - 1}"  # 60 = C4


class MidiEditor(QWidget):
    """Edits clip.notes; on_change() is called after each change that went into the clip."""
    closed = Signal()

    def __init__(self, parent, clip, project, engine, on_change, timeline=None):
        """timeline: whose selection cursor the ruler shows and moves (None: only the playback cursor)."""
        super().__init__(parent, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.clip, self.project, self.engine, self.on_change = clip, project, engine, on_change
        self.timeline = timeline
        self.cursor_xs = (None, None)  # (selection, playback) cursor x in the views, as last drawn
        self.notes = [list(map(float, n)) for n in clip.notes]  # [start s, end s, pitch, velocity]; the working copy
        self.selected = set()  # indexes into self.notes
        self.drag = None  # ("move" | "resize" | "velocity", ...) while the mouse is down
        self.last_velocity = DEFAULT_VELOCITY
        self.row_h = ROW_H
        self.held = None  # the pitch sounding while the mouse is held
        self.refit = False  # fit the view at the next resize (after expanding)
        self.resize(960, 560)
        self.roll = PianoRoll(self)
        self.velocity = VelocityLane(self)
        self.hbar, self.vbar = QScrollBar(Qt.Horizontal), QScrollBar(Qt.Vertical)
        self.hbar.valueChanged.connect(self.refresh)
        self.vbar.valueChanged.connect(self.refresh)
        self.hint = QLabel("Click to add a note, drag to move, drag its right end to resize, right-click to delete, "
                           "Ctrl+D to duplicate the selected ones. Velocities below. Alt: no snapping.")
        self.hint.setProperty("hint", True)  # the skin's hint colour
        self.hint.setContentsMargins(4, 2, 4, 2)
        tools = QHBoxLayout()
        tools.setContentsMargins(0, 0, 4, 0)
        tools.addWidget(self.hint, 1)
        tools.addWidget(QLabel("Keys"))
        for text, tip, step in (("−", "Shorter keys: more of them in view (Alt+wheel)", -2),
                                ("+", "Taller keys (Alt+wheel)", 2)):
            button = QToolButton()
            button.setText(text)
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False, d=step: self.set_row_height(self.row_h + d))
            tools.addWidget(button)
        self.expand_button = QToolButton()
        self.expand_button.setText("⤢ Expand")
        self.expand_button.setCheckable(True)
        self.expand_button.setToolTip("Let the editor fill the screen (again to make it small)")
        self.expand_button.toggled.connect(self.set_expanded)
        tools.addWidget(self.expand_button)
        grid = QVBoxLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        grid.addLayout(tools)
        self.ruler = EditorRuler(self)
        ruler_row = QHBoxLayout()
        ruler_row.setSpacing(0)
        ruler_row.addWidget(self.ruler, 1)
        ruler_row.addSpacing(self.vbar.sizeHint().width())
        grid.addLayout(ruler_row)
        top = QHBoxLayout()
        top.setSpacing(0)
        top.addWidget(self.roll, 1)
        top.addWidget(self.vbar)
        grid.addLayout(top, 1)
        bottom = QHBoxLayout()
        bottom.setSpacing(0)
        bottom.addWidget(self.velocity, 1)
        bottom.addSpacing(self.vbar.sizeHint().width())
        grid.addLayout(bottom)
        hrow = QHBoxLayout()
        hrow.setContentsMargins(KEYS_W, 0, self.vbar.sizeHint().width(), 0)
        hrow.addWidget(self.hbar)
        grid.addLayout(hrow)
        self.pps = 200.0
        self.update_title()
        self.fit()
        self.cursor_timer = QTimer(self, interval=CURSOR_MS)
        self.cursor_timer.timeout.connect(self.follow_cursors)
        self.cursor_timer.start()

    # --- time and pitch ------------------------------------------------------------------

    @property
    def sr(self):
        return self.engine.sr

    def origin_s(self):
        """Timeline seconds of note time 0."""
        return (self.clip.start - self.clip.offset) / self.sr

    def window_s(self):
        """(from, to) in note time: the part of the notes the clip plays."""
        return self.clip.offset / self.sr, (self.clip.offset + self.clip.frames) / self.sr

    def length_s(self):
        """Note time covered by the view's scroll range."""
        ends = [n[1] for n in self.notes] + [self.window_s()[1], self.clip.length_s]
        return max(ends) + 2 * self.step_s() * 8

    def x_of(self, t):
        return KEYS_W + (t * self.pps - self.hbar.value())

    def t_at(self, x):
        return (x - KEYS_W + self.hbar.value()) / self.pps

    def y_of(self, pitch):
        return (127 - pitch) * self.row_h - self.vbar.value()

    def pitch_at(self, y):
        return min(127, max(0, 127 - int((y + self.vbar.value()) // self.row_h)))

    def snapping(self, modifiers):
        return self.project.timing.beats and not modifiers & Qt.AltModifier

    def step_s(self):
        """The grid step at this zoom, in seconds: new notes are this long."""
        t = self.project.timing
        return float(t.grid_step(self.pps)) * t.beat_s if t.beats else ruler_step(self.pps) / 4

    def snap(self, t, modifiers, floor=False):
        """Note time t on the project's grid (unless Alt is held or editing in seconds)."""
        if not self.snapping(modifiers):
            return max(0.0, t)
        step, origin = self.step_s(), self.origin_s()
        steps = (origin + t) / step
        return max(0.0, (math.floor(steps + 1e-9) if floor else round(steps)) * step - origin)

    def snap_delta(self, dt, modifiers):
        if not self.snapping(modifiers):
            return dt
        step = self.step_s()
        return round(dt / step) * step

    def note_at(self, x, y):
        """(index, "end" or "body") of the note under (x, y) in the roll, or (None, None); later notes on top."""
        t, pitch = self.t_at(x), self.pitch_at(y)
        for i in reversed(range(len(self.notes))):
            start, end, p, _ = self.notes[i]
            if p == pitch and self.x_of(start) <= x <= self.x_of(end) + 1:
                return i, "end" if self.x_of(end) - x <= EDGE_PX else "body"
        return None, None

    # --- view --------------------------------------------------------------------------

    def fit(self):
        """Show the whole clip across, and its notes (or middle C) up and down."""
        width = max(200, self.roll.width() - KEYS_W - 20)
        self.pps = min(2000.0, max(20.0, width / max(0.5, self.window_s()[1] - self.window_s()[0])))
        pitches = [n[2] for n in self.notes] or [60]
        middle = (max(pitches) + min(pitches)) / 2
        self.update_ranges()
        self.hbar.setValue(int(self.window_s()[0] * self.pps))
        self.vbar.setValue(int((127 - middle) * self.row_h - self.roll.height() / 2))
        self.refresh()

    def update_ranges(self):
        self.hbar.setRange(0, max(0, int(self.length_s() * self.pps) - (self.roll.width() - KEYS_W)))
        self.hbar.setPageStep(max(1, self.roll.width() - KEYS_W))
        self.vbar.setRange(0, max(0, 128 * self.row_h - self.roll.height()))
        self.vbar.setPageStep(max(1, self.roll.height()))
        self.vbar.setSingleStep(self.row_h)

    def zoom(self, factor, x):
        t = self.t_at(x)
        self.pps = min(4000.0, max(10.0, self.pps * factor))
        self.update_ranges()
        self.hbar.setValue(int(t * self.pps - (x - KEYS_W)))
        self.refresh()

    def set_row_height(self, height):
        """Taller or shorter keys, keeping the pitch in the middle of the view where it is."""
        height = min(MAX_ROW_H, max(MIN_ROW_H, int(height)))
        middle = self.pitch_at(self.roll.height() / 2)
        self.row_h = height
        self.update_ranges()
        self.vbar.setValue(int((127 - middle) * height + height / 2 - self.roll.height() / 2))
        self.refresh()

    def set_expanded(self, on):
        """Fill the screen with the editor, so the piano roll gets all the room (or back)."""
        if on:
            self.showMaximized()
        else:
            self.showNormal()
        self.expand_button.setChecked(on)
        self.refit = True  # once it has its new size (resizeEvent): the clip across the whole width

    def wheel(self, e, x):
        delta = e.angleDelta().y() or e.angleDelta().x()  # (Alt turns the wheel sideways)
        if e.modifiers() & Qt.AltModifier:
            self.set_row_height(self.row_h + (2 if delta > 0 else -2))
        elif e.modifiers() & Qt.ControlModifier:
            self.zoom(config.ZOOM_STEP if delta > 0 else 1 / config.ZOOM_STEP, x)
        elif e.modifiers() & Qt.ShiftModifier:
            self.hbar.setValue(self.hbar.value() - delta)
        else:
            self.vbar.setValue(self.vbar.value() - delta // 2)

    # --- the cursors ---

    def cursor_x(self):
        """x of the timeline's selection cursor in the views, or None."""
        if self.timeline is None:
            return None
        return self.x_of(self.timeline.cursor / self.sr - self.origin_s())

    def playhead_x(self):
        """x of the playback cursor (what is heard) in the views."""
        return self.x_of(self.engine.heard_pos() / self.sr - self.origin_s())

    def follow_cursors(self):
        """Redraw when a cursor has moved (checked a few dozen times a second)."""
        xs = tuple(None if x is None else int(x) for x in (self.cursor_x(), self.playhead_x()))
        if xs != self.cursor_xs:
            self.cursor_xs = xs
            self.refresh()

    def paint_cursor_lines(self, p, height):
        """Both cursors' lines down a view (the playback cursor on top where they meet)."""
        for x, color in ((self.cursor_x(), theme.CURSOR), (self.playhead_x(), theme.PLAYHEAD)):
            if x is not None and x >= KEYS_W:
                p.setPen(color)
                p.drawLine(int(x), 0, int(x), height)

    def move_cursor(self, x, modifiers):
        """The ruler was clicked at x: the selection cursor goes there (on the grid unless Alt); while
        playing, playback jumps there too, as from the timeline's ruler."""
        if self.timeline is None:
            return
        frame = max(0, round((self.origin_s() + self.snap(self.t_at(x), modifiers)) * self.sr))
        self.timeline.set_cursor(frame)
        if self.engine.playing:
            self.timeline.seek(frame)
        self.follow_cursors()

    def refresh(self):
        self.roll.update()
        self.velocity.update()
        self.ruler.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.update_ranges()
        if self.refit:
            self.refit = False
            QTimer.singleShot(0, self.fit)  # after the roll inside has its new size too

    def closeEvent(self, e):
        self.cursor_timer.stop()
        self.release()
        self.closed.emit()
        super().closeEvent(e)

    def update_title(self):
        self.setWindowTitle(f"Notes: {self.clip.name} — {self.clip.track.name if self.clip.track else ''}")

    # --- editing ------------------------------------------------------------------------

    def commit(self):
        """Put the edited notes into the clip and render it again."""
        clip = self.clip
        if clip not in self.project.clips:
            self.close()
            return
        chosen = [self.notes[i] for i in self.selected if i < len(self.notes)]
        self.notes.sort(key=lambda n: (n[0], n[2]))  # by time; the selection stays on the same notes
        self.selected = {i for i, n in enumerate(self.notes) if any(n is c for c in chosen)}
        clip.notes = np.array(self.notes, dtype=np.float64).reshape(-1, 4)
        if len(self.notes):
            clip.length_s = max(clip.length_s, max(n[1] for n in self.notes))  # a note past the end: the clip grows
        clip.prepare(self.sr)
        self.update_ranges()
        self.refresh()
        self.on_change()

    def duplicate(self, modifiers=Qt.NoModifier):
        """Copy the selected notes to right after them, and select the copies (Ctrl+D again repeats
        them once more). They move on by their span, from the first note's start to the last one's end;
        in bars and beats rounded up to the grid step, so a pattern repeats in time."""
        chosen = [self.notes[i] for i in sorted(self.selected) if i < len(self.notes)]
        if not chosen:
            return
        span = max(n[1] for n in chosen) - min(n[0] for n in chosen)
        if self.snapping(modifiers):
            step = self.step_s()
            span = max(1, math.ceil(span / step - 1e-9)) * step
        copies = [[start + span, end + span, pitch, velocity] for start, end, pitch, velocity in chosen]
        self.notes += copies
        self.selected = set(range(len(self.notes) - len(copies), len(self.notes)))
        self.commit()

    def delete(self, indexes):
        if indexes:
            self.notes = [n for i, n in enumerate(self.notes) if i not in indexes]
            self.selected = set()
            self.commit()

    # --- hearing notes: through the track (audio/live.py) ---

    def live(self):
        return self.engine.live_notes(self.clip.track)

    def builtin(self):
        """True if the track plays the built-in synth (no instrument plugin, nor an instrument patch panel)."""
        return not plays_plugins(self.clip.track)

    def hold(self, pitch):
        """Sound `pitch` through the track until release() (or another pitch is held)."""
        pitch = int(pitch)
        if pitch == self.held:
            return
        self.release()
        self.live().note_on(pitch, self.last_velocity, self.sr, self.builtin())
        self.held = pitch

    def release(self):
        if self.held is not None:
            self.live().note_off(self.held)
            self.held = None

    def glide(self, pitch):
        """The mouse, held, moved to `pitch` (over the keys, or with a note): every pitch passed on the
        way sounds briefly, and the one reached is held."""
        pitch = int(pitch)
        if self.held is not None and pitch != self.held:
            step = 1 if pitch > self.held else -1
            for passed in range(self.held + step, pitch, step):
                self.live().note_on(passed, self.last_velocity, self.sr, self.builtin(), length_s=GLIDE_S)
        self.hold(pitch)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete(set(self.selected))
        elif e.key() == Qt.Key_A and e.modifiers() & Qt.ControlModifier:
            self.selected = set(range(len(self.notes)))
            self.refresh()
        elif e.key() == Qt.Key_D and e.modifiers() & Qt.ControlModifier:
            self.duplicate(e.modifiers())
        elif e.key() == Qt.Key_Escape:
            self.selected = set()
            self.refresh()
        else:
            super().keyPressEvent(e)

    def color(self):
        return clip_colors(clip_color(self.clip)[0]).fill


class PianoRoll(QWidget):
    """The keys (left) and the notes; all its editing goes through the MidiEditor."""

    def __init__(self, editor):
        super().__init__()
        self.ed = editor
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(200)

    def paintEvent(self, event):
        ed, p = self.ed, QPainter(self)
        w, h = self.width(), self.height()
        top, bottom = ed.pitch_at(0), ed.pitch_at(h)
        for pitch in range(bottom, top + 1):  # rows: black keys darker, a line above every C
            y = ed.y_of(pitch)
            p.fillRect(QRectF(KEYS_W, y, w, ed.row_h), theme.LANES[0] if pitch % 12 in BLACK_KEYS else theme.LANES[1])
            if pitch % 12 == 11:
                p.fillRect(QRectF(KEYS_W, y, w, 1), theme.GRID_BY_STRENGTH[2])
        self.paint_grid(p, h)
        lo, hi = ed.window_s()  # shade what the clip doesn't play
        p.fillRect(QRectF(KEYS_W, 0, max(0.0, ed.x_of(lo) - KEYS_W), h), theme.ROLL_SHADE)
        p.fillRect(QRectF(ed.x_of(hi), 0, w, h), theme.ROLL_SHADE)
        color = ed.color()
        p.setRenderHint(QPainter.Antialiasing, True)
        for i, (start, end, pitch, velocity) in enumerate(ed.notes):
            rect = QRectF(ed.x_of(start), ed.y_of(pitch) + 1, max(3.0, (end - start) * ed.pps), ed.row_h - 2)
            if rect.right() < KEYS_W or rect.left() > w or rect.bottom() < 0 or rect.top() > h:
                continue
            fill = QColor(color).lighter(100 + int(velocity / 127 * 60))
            p.setBrush(fill)
            p.setPen(QPen(theme.SELECTED_NOTE, 2) if i in ed.selected else QPen(fill.darker(160), 1))
            p.drawRect(rect)
        p.setRenderHint(QPainter.Antialiasing, False)
        ed.paint_cursor_lines(p, h)
        self.paint_keys(p, h, bottom, top)

    def paint_grid(self, p, h):
        ed, t0, t1 = self.ed, self.ed.t_at(KEYS_W), self.ed.t_at(self.width())
        timing = ed.project.timing
        if timing.beats:
            origin = ed.origin_s()
            for seconds, strength, _ in timing.lines(max(0.0, origin + t0), origin + t1, ed.pps):
                x = int(ed.x_of(seconds - origin))
                p.setPen(theme.GRID_BY_STRENGTH[strength])
                p.drawLine(x, 0, x, h)
        else:
            step = ruler_step(ed.pps)
            for k in range(int(t0 / step), int(t1 / step) + 2):
                x = int(ed.x_of(k * step))
                p.setPen(theme.GRID)
                p.drawLine(x, 0, x, h)

    def paint_keys(self, p, h, bottom, top):
        ed = self.ed
        p.fillRect(0, 0, KEYS_W, h, theme.KEYS_WHITE)
        for pitch in range(bottom, top + 1):
            y = ed.y_of(pitch)
            if pitch % 12 in BLACK_KEYS:
                p.fillRect(QRectF(0, y, KEYS_W * 0.62, ed.row_h), theme.KEYS_BLACK)
            else:
                p.fillRect(QRectF(0, y + ed.row_h - 1, KEYS_W, 1), theme.KEYS_LINE)
            if pitch == ed.held:  # the key sounding now
                p.fillRect(QRectF(0, y, KEYS_W, ed.row_h), theme.KEY_SOUNDING)
            if pitch % 12 == 0 and ed.row_h >= 9:
                p.setPen(theme.KEYS_LABEL)
                p.drawText(QRectF(0, y, KEYS_W - 4, ed.row_h), Qt.AlignRight | Qt.AlignVCenter, note_name(pitch))
        p.fillRect(KEYS_W - 1, 0, 1, h, theme.RULER_BG)

    # --- mouse ---

    def mousePressEvent(self, e):
        ed, pos = self.ed, e.position()
        if pos.x() < KEYS_W:  # a key: it sounds while held; dragging over the keys plays each one passed
            if e.button() == Qt.LeftButton:
                ed.hold(ed.pitch_at(pos.y()))
                ed.drag = ("keys",)
                ed.refresh()
            return
        index, zone = ed.note_at(pos.x(), pos.y())
        ctrl = bool(e.modifiers() & Qt.ControlModifier)
        if e.button() == Qt.RightButton:
            if index is not None:
                ed.delete({index})
            return
        if e.button() != Qt.LeftButton:
            return
        if index is None:  # an empty spot: a new note, one grid step long; dragging makes it longer
            if not ctrl:
                ed.selected = set()
            start = ed.snap(ed.t_at(pos.x()), e.modifiers(), floor=True)
            pitch = ed.pitch_at(pos.y())
            ed.notes.append([start, start + ed.step_s(), float(pitch), float(ed.last_velocity)])
            index, zone = len(ed.notes) - 1, "end"
            ed.selected.add(index)
            changed = True
        else:
            if ctrl:
                ed.selected ^= {index}
                ed.refresh()
                return
            if index not in ed.selected:
                ed.selected = {index}
            ed.last_velocity = int(ed.notes[index][3])
            changed = False
        origin = {i: list(ed.notes[i]) for i in ed.selected}
        ed.drag = ("resize" if zone == "end" else "move", pos, index, origin, changed)
        ed.hold(ed.notes[index][2])  # the grabbed (or new) note sounds while held
        ed.refresh()

    def mouseMoveEvent(self, e):
        ed, pos = self.ed, e.position()
        if ed.drag is None:
            index, zone = ed.note_at(pos.x(), pos.y()) if pos.x() >= KEYS_W else (None, None)
            self.setCursor(Qt.SizeHorCursor if zone == "end" else Qt.OpenHandCursor if zone else Qt.ArrowCursor)
            return
        if ed.drag[0] == "keys":
            ed.glide(ed.pitch_at(pos.y()))
            ed.refresh()
            return
        kind, start_pos, index, origin, _ = ed.drag
        if kind == "move":
            dt = ed.snap_delta(ed.t_at(pos.x()) - ed.t_at(start_pos.x()), e.modifiers())
            dt = max(dt, -min(n[0] for n in origin.values()))  # nothing before the clip's start
            dp = ed.pitch_at(pos.y()) - ed.pitch_at(start_pos.y())
            dp = min(max(dp, -min(n[2] for n in origin.values())), 127 - max(n[2] for n in origin.values()))
            for i, (s, en, pt, v) in origin.items():
                ed.notes[i] = [s + dt, en + dt, pt + dp, v]
            ed.glide(ed.notes[index][2])  # hear the pitch it is moved to
        else:
            grabbed = origin[index]
            new_end = ed.snap(ed.t_at(pos.x()), e.modifiers())
            shortest = ed.step_s() if ed.snapping(e.modifiers()) else 0.01
            delta = max(new_end, grabbed[0] + shortest) - grabbed[1]
            for i, (s, en, pt, v) in origin.items():
                ed.notes[i] = [s, max(en + delta, s + shortest), pt, v]
        ed.drag = (kind, start_pos, index, origin, True)
        ed.refresh()

    def mouseReleaseEvent(self, e):
        drag, self.ed.drag = self.ed.drag, None
        self.ed.release()
        if drag and drag[0] != "keys" and drag[4]:  # something changed: into the clip
            self.ed.commit()
        self.ed.refresh()

    def wheelEvent(self, e):
        self.ed.wheel(e, e.position().x())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.ed.update_ranges()


class VelocityLane(QWidget):
    """A bar per note at its start, as tall as its velocity; click or drag across to set them."""

    def __init__(self, editor):
        super().__init__()
        self.ed = editor
        self.setFixedHeight(VELOCITY_H)

    def velocity_at(self, y):
        return int(min(127, max(1, round((self.height() - 3 - y) / (self.height() - 6) * 127))))

    def paintEvent(self, event):
        ed, p = self.ed, QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), theme.METER_BG)
        p.fillRect(0, 0, w, 1, theme.RULER_BG)
        for level in (32, 64, 96, 127):
            y = int(h - 3 - level / 127 * (h - 6))
            p.fillRect(KEYS_W, y, w, 1, theme.GRID if level != 127 else theme.GRID_BY_STRENGTH[0])
        color = ed.color()
        for i, (start, end, pitch, velocity) in enumerate(ed.notes):
            x = ed.x_of(start)
            if x < KEYS_W - 4 or x > w:
                continue
            bar = velocity / 127 * (h - 6)
            fill = theme.SELECTED_NOTE if i in ed.selected else QColor(color).lighter(130)
            p.fillRect(QRectF(x, h - 3 - bar, 4, bar), fill)
            p.fillRect(QRectF(x - 2, h - 5 - bar, 8, 3), fill)
        ed.paint_cursor_lines(p, h)
        p.fillRect(0, 0, KEYS_W, h, theme.RULER_BG)
        p.setPen(theme.RULER_TEXT)
        p.drawText(QRectF(4, 0, KEYS_W - 6, h), Qt.AlignLeft | Qt.AlignVCenter, "Velocity")

    def set_under(self, pos):
        """Set the velocity of the notes whose bar is under (or nearest to, within a few px) the mouse."""
        ed = self.ed
        near = [(abs(ed.x_of(n[0]) + 2 - pos.x()), i) for i, n in enumerate(ed.notes)]
        near = [(d, i) for d, i in near if d <= 6]
        if not near:
            return False
        closest = min(d for d, _ in near)
        value = self.velocity_at(pos.y())
        for d, i in near:
            if d == closest:
                ed.notes[i][3] = float(value)
        ed.last_velocity = value
        ed.refresh()
        return True

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.position().x() >= KEYS_W:
            self.ed.drag = ("velocity", self.set_under(e.position()))

    def mouseMoveEvent(self, e):
        if self.ed.drag and self.ed.drag[0] == "velocity":
            changed = self.set_under(e.position())
            self.ed.drag = ("velocity", self.ed.drag[1] or changed)

    def mouseReleaseEvent(self, e):
        drag, self.ed.drag = self.ed.drag, None
        if drag and drag[0] == "velocity" and drag[1]:
            self.ed.commit()

    def wheelEvent(self, e):
        self.ed.wheel(e, e.position().x())


class EditorRuler(QWidget):
    """Bars and beats (or seconds) over the piano roll, with both cursors and their arrows. Click or
    drag to move the selection cursor."""

    def __init__(self, editor):
        super().__init__()
        self.ed = editor
        self.setFixedHeight(RULER_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click or drag to move the selection cursor (where Play starts)")

    def paintEvent(self, event):
        ed, p = self.ed, QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), theme.RULER_BG)
        t0, t1 = ed.t_at(KEYS_W), ed.t_at(w)
        timing, origin = ed.project.timing, ed.origin_s()
        p.setPen(theme.RULER_TEXT)
        if timing.beats:
            for seconds, strength, label in timing.lines(max(0.0, origin + t0 - 1), origin + t1, ed.pps):
                x = int(ed.x_of(seconds - origin))
                if x < KEYS_W - 40:
                    continue
                p.drawLine(x, 0 if label else h - (8 if strength == BAR else 4), x, h)
                if label:
                    p.drawText(x + 4, 15, label)
        else:
            step = ruler_step(ed.pps)
            for k in range(int(max(0.0, origin + t0) / step), int((origin + t1) / step) + 2):
                x = int(ed.x_of(k * step - origin))
                p.drawLine(x, h - 6, x, h)
                p.drawText(x + 4, 15, fmt_time(k * step, step_decimals(step)))
        for x, color in ((ed.cursor_x(), theme.CURSOR), (ed.playhead_x(), theme.PLAYHEAD)):
            if x is not None and x >= KEYS_W:
                paint_cursor(p, int(x), color, h)
        p.fillRect(0, 0, KEYS_W, h, theme.RULER_BG)  # (labels left of the notes area stay hidden)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.position().x() >= KEYS_W:
            self.ed.move_cursor(e.position().x(), e.modifiers())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton and e.position().x() >= KEYS_W:
            self.ed.move_cursor(e.position().x(), e.modifiers())

    def wheelEvent(self, e):
        self.ed.wheel(e, e.position().x())
