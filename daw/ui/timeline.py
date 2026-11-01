"""The scrolling timeline: ruler, audio and MIDI lanes, clips, selection and the two cursors.

Two cursors, each a line with an arrow on the ruler: the selection cursor (blue, `cursor`) is set
by clicking or selecting a range; Play always starts from it, and splitting and adding files use
it. The playback cursor (red, the engine's position) moves while playing and stays where playback
stopped. Clicking the ruler while playing also makes playback jump there.

Also owns the edits you make with the mouse and keyboard: selecting, moving clips,
splitting and cutting, seeking, zooming, dropping files, adding and removing tracks.
Drawing a clip itself lives in clip_painter.py; what is selected in selection.py;
the split/cut operations in ../edits.py.

A clip has two zones: its top strip (CLIP_HANDLE_H, with the name) moves it;
the rest behaves like empty lane space, selecting a time range to split or cut. Its ends
(CLIP_EDGE_PX at either side, top to bottom) trim it: drag one inwards to shorten the clip, or
outwards to bring back what was cut off (by splitting, a cut, or a clip placed over it), as far as
its audio goes; a MIDI clip can be made as long as you like. Snapped to the grid like everything
else (Alt: freely); what it then covers is replaced, as when a clip is placed.
A clip that is moved, dropped or added replaces the parts of other clips it covers on its
track (edits.clear_under), so clips on a track never overlap.
A disabled clip stays where it is, drawn as an outline, but doesn't play (0, or right-click).
Double-click a MIDI clip to edit its notes (midi_editor.py); double-click empty space on a MIDI
track (or right-click → New MIDI clip) to make a new, empty MIDI clip there.
Clips are renamed with Ctrl+R, the right-click menu, or a double-click on their name strip.
The clips of a frozen track (track.frozen) are locked: drawn with an icy tint, they can't be
selected, moved, cut or dropped onto until the track is unfrozen (track_render.py).

When editing in bars and beats (project.timing, see ../timing.py) the ruler and grid show
bars and beats, and the playhead, range edges and moved clips (the start of the clip grabbed)
snap to the grid unless Alt is held.

A track's automation lanes (Auto on its row) lie under it: each a parameter's curve, from 0 at the
bottom to 1 at the top (drag its bottom edge to make it taller, for finer editing; double-click
the edge for the standard height). Click to add a point (dragged at once), drag points (in time they snap
like the cursor, Alt: freely), double-click or right-click a point to delete it; right-click the
lane for its menu. A frozen track's lanes are locked, like its clips.

The loop range (project.timing) is a band on the ruler, bright while looping is on; drag its ends
to change it (they snap like the cursor). main_window.py hands it to the engine.
"""
import os
from dataclasses import dataclass, field

import numpy as np

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox, QToolTip, QWidget

from .. import config, edits, timing
from ..audio import automation
from ..audio.file_io import is_midi, load_audio, load_midi
from ..model import AUDIO, MIDI, AudioClip, Automation, MidiClip
from . import theme
from .clip_painter import paint_clip, paint_range
from .color_picker import ColorPickerDialog
from .colors import clip_color, clip_colors, random_color, track_colors
from .formatting import fmt_time, ruler_step, step_decimals
from .lanes import layout
from .master_lane import MasterLaneView
from .selection import Selection, TimeRange


GROUP_PAD = 6  # px above and below the miniature clips in a group's header lane


LOOP_GRAB_PX = 5  # how close to an end of the loop band the mouse must be to drag it
STEP_LINES_MAX = 16  # a stepped parameter with up to this many steps gets a line at each of its values
STEP_LABEL_MIN_PX = 22  # …and each value's name on its line, when the lines are at least this far apart


def group_fill(group):
    """Background of a group's header lane and panel row."""
    return track_colors(group.color, group.tone)[0] if group.color else theme.GROUP_LANE


def paint_cursor(p, x, color, height):
    """A cursor: a line down the timeline with a small arrow on the ruler pointing down at it."""
    p.setPen(color)
    p.drawLine(x, 0, x, height)
    half = config.CURSOR_ARROW_W / 2
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPolygon([QPointF(x + 0.5 - half, 0), QPointF(x + 0.5 + half, 0), QPointF(x + 0.5, config.CURSOR_ARROW_H)])
    p.setBrush(Qt.NoBrush)
    p.setRenderHint(QPainter.Antialiasing, False)


@dataclass
class MoveDrag:
    """Dragging the selected clips; positions are relative to where the drag began."""
    frame: int
    row: int
    origin: dict  # clip -> (start frame, lane index) when the drag began
    anchor: int = 0  # start frame of the clip grabbed: it is the one put on the grid
    rows_moved: int = 0  # last lane offset that suited every clip's kind


@dataclass
class EdgeDrag:
    """Dragging one end of a clip (side "left" or "right") to trim it or lengthen it again."""
    clip: object
    side: str


@dataclass
class HeightDrag:
    """Dragging a lane's bottom edge to resize its track."""
    track: object
    y: float  # mouse y when the drag began
    height: int  # track height when the drag began


@dataclass
class PointDrag:
    """Dragging a point of an automation lane."""
    lane: object  # lanes.Lane of the automation
    index: int  # the point's place in its curve


@dataclass
class LoopDrag:
    """Dragging one end of the loop range on the ruler."""
    end: bool  # True: its end, False: its start


@dataclass
class RangeDrag:
    """Dragging over empty lane space or a clip's body to select a time range."""
    x: float
    frame: int
    row: int
    clip: object = None  # clip whose body was pressed; a click without dragging selects it
    keep: set = field(default_factory=set)  # clips kept selected (Ctrl held)
    active: bool = False  # moved past DRAG_THRESHOLD_PX
    end_row: int = None  # lane (among the tracks shown) where the range ends


class Timeline(QWidget):
    tracks_changed = Signal()  # tracks added or removed; the lane panel rebuilds
    edit_notes = Signal(object)  # a MIDI clip to open in the MIDI editor (main_window.py)
    heights_changed = Signal()  # a lane was resized; the lane panel resizes its rows
    loop_changed = Signal()  # the loop range was changed here
    automation_edited = Signal()  # an automation curve was edited here
    current_track_changed = Signal()  # another track was selected (recording.py records onto it)
    loop_range_chosen = Signal()  # "Loop this range": looping goes on

    def __init__(self, project, engine, scroll):
        super().__init__()
        self.project, self.engine, self.scroll = project, engine, scroll
        self.pps = config.DEFAULT_PPS  # zoom, pixels per second
        self.selection = Selection()
        self.drag = None  # MoveDrag or RangeDrag while the mouse button is down
        self.playhead_x = 0  # x of the playback cursor (the engine's position)
        self.cursor = 0  # the selection cursor, in frames
        self.cursor_x = 0
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)  # to show which zone of a clip is under the mouse
        self.view_left = 0  # horizontal scroll position, for on_hscroll
        self.random_track_colors = False  # new tracks get a random colour (Options → Colours)
        self.current_track = None  # the selected track: the lane (or panel row) clicked last
        self.recording = None  # (track, start frame) while recording: the take is shown growing
        scroll.horizontalScrollBar().valueChanged.connect(self.on_hscroll)
        self.master_view = MasterLaneView(self)  # main_window pins it to the bottom of the view
        scroll.viewport().installEventFilter(self)  # re-fit when the view changes size
        self.relayout()

    def eventFilter(self, obj, e):
        if obj is self.scroll.viewport() and e.type() == QEvent.Resize:
            self.relayout()
        return False

    def update(self, *area):
        """Repaint (all of it, or `area`); a full repaint includes the pinned master lane."""
        super().update(*area)
        if not area:
            self.master_view.update()

    def on_hscroll(self, value):
        """Qt moves the already-drawn pixels when scrolling. Clip names stick to the view's left
        edge, so only the name strips of clips that start left of the view (before or after this
        scroll) need repainting. Cheap enough to scroll every frame while following playback."""
        old, self.view_left = self.view_left, value
        if old == value:
            return
        self.master_view.update()  # it isn't scrolled by Qt: it is drawn again at the new offset
        lo, hi = min(old, value), max(old, value)
        width = self.scroll.viewport().width()
        tops = self.track_tops()
        for clip in self.project.clips:
            rect = self.clip_rect(clip, tops)
            if rect is not None and rect.left() < hi and rect.right() >= lo:
                self.update(QRect(value, int(rect.top()) - 1, width, config.CLIP_HANDLE_H + 3))

    # --- coordinates ----------------------------------------------------------

    def x_of(self, frame):
        return frame / self.engine.sr * self.pps

    def frame_at(self, x):
        return int(x / self.pps * self.engine.sr)

    def snapped_frame_at(self, x, modifiers):
        """Frame at x for placing the playhead or a range edge: when editing in bars and beats,
        the nearest line of the grid shown at this zoom, unless Alt is held."""
        t = self.project.timing
        if not t.beats or modifiers & Qt.AltModifier:
            return max(0, self.frame_at(x))
        return round(t.snap(x / self.pps, self.pps) * self.engine.sr)

    def lanes(self):
        """The lanes below the ruler, top to bottom: group headers, tracks, master (lanes.py)."""
        return layout(self.project, config.RULER_H)

    def track_tops(self):
        """{track: y of its lane's top}, for the tracks shown (not those of folded groups)."""
        return {lane.track: lane.top for lane in self.lanes() if lane.track}

    def visible_tracks(self):
        """The tracks shown, top to bottom: the rows that clip moves and range selections count in."""
        return [lane.track for lane in self.lanes() if lane.track]

    def lane_at(self, y):
        """The Lane under y (the master lane below everything), or None over the ruler."""
        if y < config.RULER_H:
            return None
        lanes = self.lanes()
        return next((lane for lane in lanes if y < lane.bottom), lanes[-1])

    def track_at(self, y):
        """The track whose lane is under y, or None (ruler, group header or master lane)."""
        lane = self.lane_at(y)
        return lane.track if lane else None

    def lane_edge_at(self, y):
        """The track or automation lane whose bottom edge is within LANE_EDGE_GRAB_PX of y, or None."""
        if y < config.RULER_H:
            return None
        for lane in self.lanes():
            item = lane.track or lane.automation
            if item is not None and abs(y - lane.bottom) <= config.LANE_EDGE_GRAB_PX:
                return item

    def visible_rect(self):
        vp = self.scroll.viewport()
        return QRect(self.scroll.horizontalScrollBar().value(), self.scroll.verticalScrollBar().value(),
                     vp.width(), vp.height())

    def clip_rect(self, clip, tops):
        """The clip's box: its time span, inset 2 px inside its track's lane; None while its
        track is folded away (tops: track_tops())."""
        top = tops.get(clip.track)
        if top is None:
            return None
        x0, x1 = self.x_of(clip.start), self.x_of(clip.end)
        return QRectF(x0, top + 2, x1 - x0, clip.track.height - 4)

    def hit(self, pos):
        """(clip, "handle" or "body") under pos, or (None, None). Later clips are on top."""
        if pos.y() < config.RULER_H:
            return None, None
        tops = self.track_tops()
        for clip in reversed(self.project.clips):
            rect = self.clip_rect(clip, tops)
            if rect is not None and not clip.track.frozen and rect.adjusted(0, -2, 0, 2).contains(pos):
                return clip, "handle" if pos.y() < rect.top() + config.CLIP_HANDLE_H else "body"
        return None, None

    def edge_at(self, pos):
        """(clip, "left" or "right") whose end is under pos (clips wide enough to have ends), or None."""
        if pos.y() < config.RULER_H:
            return None
        tops = self.track_tops()
        grab = config.CLIP_EDGE_PX
        for clip in reversed(self.project.clips):
            rect = self.clip_rect(clip, tops)
            if rect is None or clip.track.frozen or rect.width() < 3 * grab or not rect.adjusted(0, -2, 0, 2).contains(pos):
                continue
            if pos.x() <= rect.left() + grab:
                return clip, "left"
            if pos.x() >= rect.right() - grab:
                return clip, "right"
        return None

    def drag_edge(self, e):
        """Move the grabbed end of a clip to the mouse (snapped): no shorter than CLIP_MIN_S, not past its
        audio (an audio clip), not before its start (offset 0). The clip is changed in place, one value
        at a time in an order the audio thread can always play from (at worst a buffer of the old window)."""
        clip, sr = self.drag.clip, self.engine.sr
        frame = self.snapped_frame_at(e.position().x(), e.modifiers())
        shortest = max(1, round(config.CLIP_MIN_S * sr))
        if self.drag.side == "right":
            end = max(clip.start + shortest, frame)
            if clip.kind == AUDIO:
                end = min(end, clip.start + len(clip.data) - clip.offset)
            clip.length = end - clip.start
        else:
            end = clip.end
            start = max(clip.start - clip.offset, 0, min(frame, end - shortest))
            moved = start - clip.start
            if moved > 0:  # shorter first, then the window and the start
                clip.length = clip.frames - moved
                clip.offset += moved
                clip.start = start
            elif moved < 0:
                clip.offset += moved
                clip.start = start
                clip.length = end - start
        self.relayout()

    def end_edge_drag(self):
        """A MIDI clip made longer than its notes were rendered: render it that long; then the clip
        replaces what it now covers."""
        clip, sr = self.drag.clip, self.engine.sr
        if clip.kind == MIDI and clip.offset + clip.frames > len(clip.data):
            clip.length_s = max(clip.length_s, (clip.offset + clip.frames) / sr)
            clip.prepare(sr)
        edits.clear_under(self.project, [clip])
        self.relayout()

    def relayout(self):
        """Resize to fit all clips (plus room to drag), all lanes, and both cursors."""
        end = max(self.project.end_frame(), self.engine.pos, self.cursor) / self.engine.sr
        width = max(int((end + config.EMPTY_SECONDS_AFTER_END) * self.pps), self.scroll.viewport().width())
        # at least as tall as the view, so the master lane pinned to its bottom covers no gap
        self.setFixedSize(width, max(self.lanes()[-1].bottom, self.scroll.viewport().height()))
        self.playhead_x = int(self.x_of(self.engine.pos))
        self.cursor_x = int(self.x_of(self.cursor))
        self.update()

    # --- tracks -------------------------------------------------------------------

    def add_track(self, kind=AUDIO, group=None):
        track = self.project.add_track(kind, group)
        if self.random_track_colors:  # (Options → Colours)
            track.color = random_color([t.color for t in self.project.tracks])
        self.relayout()
        self.tracks_changed.emit()
        return track

    def tracks_regrouped(self):
        """After groups changed (made, folded, joined, left) or a track was frozen: forget selected
        clips that are now folded away or locked, and redraw the lanes and the panel."""
        shown = {t for t in self.visible_tracks() if not t.frozen}
        self.selection.forget([c for c in self.selection.clips if c.track not in shown])
        r = self.selection.range
        if r:
            r.tracks = tuple(t for t in r.tracks if t in shown)
            if not r.tracks:
                self.selection.range = None
        self.relayout()
        self.tracks_changed.emit()

    def group_tracks(self, tracks):
        """Put `tracks` in a new group. Returns it."""
        group = self.project.group_tracks(tracks)
        self.tracks_regrouped()
        return group

    def group_selected(self):
        """Group the tracks of the selected range, or of the selected clips (Ctrl+G)."""
        r = self.selection.range
        tracks = set(r.tracks) if r else {c.track for c in self.selection.clips}
        if tracks:
            self.group_tracks([t for t in self.project.tracks if t in tracks])

    def set_group(self, track, group):
        """Move a track into `group`, or out of its group (None)."""
        track.group = group
        self.project.gather_groups()
        self.tracks_regrouped()

    def ungroup(self, group):
        self.project.ungroup(group)
        self.tracks_regrouped()

    def toggle_fold(self, group):
        group.folded = not group.folded
        self.tracks_regrouped()

    def set_track_height(self, item, height):
        """Resize a track's lane or an automation lane (item: Track or Automation), within its range."""
        low, high = ((config.AUTOMATION_MIN_H, config.AUTOMATION_MAX_H) if isinstance(item, Automation)
                     else (config.MIN_TRACK_H, config.MAX_TRACK_H))
        item.height = min(high, max(low, int(height)))
        self.relayout()
        self.heights_changed.emit()

    @staticmethod
    def default_height(item):
        return config.AUTOMATION_H if isinstance(item, Automation) else config.TRACK_H

    def remove_track(self, track):
        clips = self.project.clips_on(track)
        if clips and QMessageBox.question(self, "Remove track", f"Remove {track.name} and its {len(clips)} clip(s)?") \
                != QMessageBox.Yes:
            return
        self.selection.forget(clips, track)
        self.project.remove_track(track)
        self.relayout()
        self.tracks_changed.emit()

    # --- clips ----------------------------------------------------------------------

    def load_clip(self, path):
        """A not-yet-placed AudioClip or MidiClip for the file, or None after showing why not."""
        name = os.path.basename(path)
        try:
            if is_midi(path):
                notes, length_s = load_midi(path)
                return MidiClip(name=name, notes=notes, length_s=length_s)
            data, sr = load_audio(path)
            return AudioClip(name=name, source=data, source_sr=sr)
        except Exception as e:
            QMessageBox.warning(self, "Cannot load file", f"{path}\n\n{e}")

    def place_clip(self, clip, frame, track):
        clip.track, clip.start = track, max(0, frame)
        clip.prepare(self.engine.sr)
        self.project.add_clip(clip)
        edits.clear_under(self.project, [clip])  # the new clip replaces what it covers
        self.selection.select_only(clip)
        self.relayout()

    def add_file(self, path, frame=0):
        """Load a file onto the first empty track of its kind, or a new one."""
        clip = self.load_clip(path)
        if clip:
            self.place_clip(clip, frame, self.project.first_empty_track(clip.kind) or self.add_track(clip.kind))

    def delete_selected(self):
        """With a time range: cut that range out of the clips on its lanes.
        Otherwise: remove the selected clips."""
        r = self.selection.range
        if r:
            edits.delete_range(self.project, r.tracks, r.start, r.end)
        elif self.selection.clips:
            self.project.remove_clips(self.selection.clips)
        self.selection.clear()
        self.relayout()

    def duplicate_selected(self):
        """Ctrl+D. With a time range: copy what lies in it to right after it, and select the copy (the
        range moves on, so Ctrl+D again repeats it once more). Otherwise: copy the selected clips to
        right after the last of them, and select the copies. What was in the way is replaced."""
        r, sel = self.selection.range, self.selection
        if r:
            copies = edits.duplicate_range(self.project, r.tracks, r.start, r.end)
            span = r.end - r.start
            sel.set_range(TimeRange(r.end, r.end + span, r.tracks), copies)
        elif sel.clips:
            sel.clips = set(edits.duplicate_clips(self.project, list(sel.clips)))
        else:
            return
        self.relayout()

    def split_selected(self):
        """With a time range: split the clips on its lanes at both edges and select the middle.
        Otherwise: split the selected clips at the selection cursor."""
        r, sel = self.selection.range, self.selection
        if r:
            pieces = edits.split_clips(self.project, self.project.clips_in(r.tracks, r.start, r.end),
                                       (r.start, r.end))
            sel.clips = {p for p in pieces if p.start >= r.start and p.end <= r.end}
        elif sel.clips:
            sel.clips = set(edits.split_clips(self.project, sel.clips, (self.cursor,)))
        self.update()

    def choose_color(self):
        """Colour dialog for the selected clips; starts from one of their current colours."""
        clips = self.selection.clips
        if not clips:
            return
        first = next(iter(clips))
        dialog = ColorPickerDialog(self, QColor(clip_color(first)[0]), first.tone)
        if dialog.exec():
            for clip in clips:
                clip.color = dialog.chosen  # None = back to the default for its kind
                clip.tone = dialog.tone  # None = automatic waveform/name colour
            self.update()

    def new_midi_clip(self, track, frame, frames=None):
        """A new, empty MIDI clip on `track` at `frame`, `frames` long (default: a bar in bars and
        beats, else 2 s). It replaces what it covers, like any placed clip. Returns it."""
        t = self.project.timing
        seconds = frames / self.engine.sr if frames else (t.beat_s * t.beats_per_bar if t.beats else 2.0)
        clip = MidiClip(name="MIDI clip", notes=np.zeros((0, 4)), length_s=seconds)
        self.place_clip(clip, frame, track)
        return clip

    def toggle_disabled(self):
        """Disable the selected clips (they stay, as outlines, but don't play), or enable them again
        if they all are disabled."""
        clips = self.selection.clips
        if clips:
            disable = not all(c.disabled for c in clips)
            for clip in clips:
                clip.disabled = disable
            self.update()

    def rename_selected(self):
        """Give the selected clips a new name (all the same one)."""
        clips = self.selection.clips
        if not clips:
            return
        current = next(iter(clips)).name
        name, ok = QInputDialog.getText(self, "Rename clips" if len(clips) > 1 else "Rename clip", "Name:", text=current)
        if ok and name.strip():
            for clip in clips:
                clip.name = name.strip()
            self.update()

    def select_all(self):
        self.selection.clips = {c for c in self.project.clips if not c.track.frozen}
        self.selection.range = None
        self.update()

    def clear_selection(self):
        self.selection.clear()
        self.update()

    # --- transport and zoom ---------------------------------------------------------

    def seek(self, frame):
        """Move the playback cursor (playback continues from there if playing)."""
        self.engine.seek(frame)
        self.set_playhead(self.engine.pos)

    def set_playhead(self, frame):
        """Draw the playback cursor at `frame`."""
        self.move_line("playhead_x", int(self.x_of(frame)))

    def set_cursor(self, frame):
        """Move the selection cursor (where Play starts)."""
        self.cursor = max(0, frame)
        self.move_line("cursor_x", int(self.x_of(self.cursor)))

    def move_line(self, attr, x):
        """Move a cursor line, repainting only the thin strips (and arrows) it leaves and enters."""
        old = getattr(self, attr)
        if x != old:
            self.update_line(old)
            setattr(self, attr, x)
            self.update_line(x)
            self.master_view.update_line(old)
            self.master_view.update_line(x)

    def update_line(self, x):
        half = config.CURSOR_ARROW_W // 2 + 2
        self.update(QRect(x - 1, 0, 3, self.height()))
        self.update(QRect(x - half, 0, 2 * half + 1, config.CURSOR_ARROW_H + 2))

    def zoom(self, factor, anchor_x):
        """Zoom keeping the time under anchor_x (widget coordinates) in place."""
        hbar = self.scroll.horizontalScrollBar()
        seconds, offset = anchor_x / self.pps, anchor_x - hbar.value()
        self.pps = min(config.MAX_PPS, max(config.MIN_PPS, self.pps * factor))
        self.relayout()
        hbar.setValue(int(seconds * self.pps - offset))

    # --- painting -------------------------------------------------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        for r in event.region():  # often several small strips (name strips, playhead): paint each alone
            p.save()
            p.setClipRect(r)
            self.paint_area(p, r)
            p.restore()

    def paint_area(self, p, r):
        lanes = self.lanes()
        tops = {lane.track: lane.top for lane in lanes if lane.track}
        self.paint_lanes(p, r, lanes)
        self.paint_ruler(p, r)
        for lane in lanes:
            if lane.group and lane.bottom >= r.top() and lane.top <= r.bottom():
                self.paint_group_contents(p, r, lane)
        view_left = self.scroll.horizontalScrollBar().value()
        for clip in self.project.clips:
            rect = self.clip_rect(clip, tops)
            if rect is None:
                continue
            if (rect.right() + 2 >= r.left() and rect.left() - 2 <= r.right()  # +2: antialiased border
                    and rect.bottom() + 2 >= r.top() and rect.top() - 2 <= r.bottom()):
                paint_clip(p, clip, rect, r, view_left, self.pps, self.engine.sr,
                           clip in self.selection.clips, self.project.audible(clip.track), clip.track.frozen is not None)
        if self.selection.range:
            spans = {t: (tops[t], t.height) for t in self.selection.range.tracks if t in tops}
            if spans:
                paint_range(p, self.selection.range, spans, self.x_of, r)
        self.paint_loop(p)
        if self.recording:
            self.paint_recording(p, tops)
        paint_cursor(p, self.cursor_x, theme.CURSOR, self.height())
        paint_cursor(p, self.playhead_x, theme.PLAYHEAD, self.height())  # on top where they meet

    def paint_lanes(self, p, r, lanes):
        row = 0  # track lanes alternate in shade
        for lane in lanes:
            area = QRect(r.left(), lane.top, r.width(), lane.height)
            if lane.track:
                p.fillRect(area, theme.LANES[row % 2])
                row += 1
            elif lane.group:
                p.fillRect(area, group_fill(lane.group))
                p.fillRect(QRect(r.left(), lane.top, r.width(), 1), theme.RULER_BG)
            elif lane.automation:
                self.paint_automation(p, r, lane)
            else:  # the master lane's place: room to scroll the last track above the pinned master lane
                p.fillRect(QRect(r.left(), lane.top, r.width(), self.height() - lane.top), theme.BELOW_LANES)

    # --- automation lanes ----------------------------------------------------------------

    def automation_y(self, lane, value):
        pad = config.AUTOMATION_PAD
        return lane.top + pad + (1 - value) * (lane.height - 2 * pad)

    def automation_value(self, lane, y):
        """The value at height y in the lane; for a stepped parameter (a switch, a choice) the nearest
        of the values it can take."""
        pad = config.AUTOMATION_PAD
        value = min(1.0, max(0.0, 1 - (y - lane.top - pad) / max(1, lane.height - 2 * pad)))
        return automation.snap(value, automation.steps_of(lane.automation))

    def range_labels(self, a):
        """(top, bottom): how the plugin shows the parameter at 1 and at 0, e.g. ("+30.0 dB", "-30.0 dB");
        "max" / "min" if it can't say. Worked out once per plugin (kept on the lane's Automation)."""
        target = a.target
        key = id(target[0]) if target is not None else None
        cached = getattr(a, "range_text", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        labels = ("max", "min")
        if target is not None and target[1] is not None:
            try:
                top, bottom = (target[1].get_text_for_raw_value(v).strip() for v in (1.0, 0.0))
                if top and bottom and top != bottom:
                    labels = (top, bottom)
            except Exception:
                pass
        a.range_text = (key, labels)
        return labels

    def paint_automation(self, p, r, lane):
        """An automation lane: its background, with the curve's range (from 1 at the top to 0 at the
        bottom) as a tinted band between two lines, a dotted line halfway and the parameter's values
        at both ends (at the left of the view), and a line above it, so stacked lanes stay apart; then
        the curve through its points (flat before the first and after the last), and the points.
        Without points, a dashed line at the parameter's value now."""
        a = lane.automation
        p.fillRect(QRect(r.left(), lane.top, r.width(), lane.height), theme.AUTOMATION_LANE)
        color = QColor(theme.AUTOMATION_LINE)
        if lane.owner.frozen or a.target is None or a.target[1] is None:
            color.setAlpha(110)  # locked (frozen), or its plugin or parameter isn't there now
        top, bottom = round(self.automation_y(lane, 1.0)), round(self.automation_y(lane, 0.0))
        band = QColor(theme.AUTOMATION_LINE)
        band.setAlpha(14)
        p.fillRect(QRect(r.left(), top, r.width(), bottom - top + 1), band)
        guide = QColor(theme.AUTOMATION_LINE)
        guide.setAlpha(70)
        p.setPen(QPen(guide, 1))
        p.drawLine(r.left(), top, r.right(), top)
        p.drawLine(r.left(), bottom, r.right(), bottom)
        guide.setAlpha(40)
        p.setPen(QPen(guide, 1, Qt.DotLine))
        steps = automation.steps_of(a)
        between = [k / steps for k in range(1, steps)] if 1 < steps <= STEP_LINES_MAX else [] if steps else [0.5]
        for value in between:  # (a stepped parameter: a line at each value it can take; else halfway)
            y = round(self.automation_y(lane, value))
            p.drawLine(r.left(), y, r.right(), y)
        p.fillRect(QRect(r.left(), lane.top, r.width(), 1), theme.GRID_BY_STRENGTH[2])  # (apart from the lane above)
        if bottom - top >= 30:
            left = self.scroll.horizontalScrollBar().value()
            font = QFont(self.font())
            font.setPointSizeF(max(6.5, font.pointSizeF() - 1.5))
            p.setFont(font)
            label = QColor(theme.AUTOMATION_LINE)
            label.setAlpha(170)
            p.setPen(label)
            high, low = self.range_labels(a)
            p.drawText(QRect(left + 6, top + 2, 220, 14), Qt.AlignLeft | Qt.AlignTop, high)
            p.drawText(QRect(left + 6, bottom - 15, 220, 14), Qt.AlignLeft | Qt.AlignBottom, low)
            if between and steps and (bottom - top) / steps >= STEP_LABEL_MIN_PX:
                # a stepped parameter, with room: the name of each value in between, on its line
                metrics = p.fontMetrics()
                for value in between:
                    y = round(self.automation_y(lane, value))
                    text = self.value_text(a, value)
                    box = QRect(left + 4, y - metrics.height() // 2, metrics.horizontalAdvance(text) + 6, metrics.height())
                    p.fillRect(box, theme.AUTOMATION_LANE)
                    p.drawText(box.adjusted(2, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, text)
            p.setFont(self.font())
        p.setRenderHint(QPainter.Antialiasing, True)
        if a.points:
            xs = [round(t * self.pps) for t, _ in a.points]
            ys = [self.automation_y(lane, automation.snap(v, steps)) for _, v in a.points]
            line = [QPointF(min(r.left(), xs[0]) - 1, ys[0])]
            for i, (x, y) in enumerate(zip(xs, ys)):
                if steps and i:  # (stepped: holds the value until the point, then jumps)
                    line.append(QPointF(x, ys[i - 1]))
                line.append(QPointF(x, y))
            line.append(QPointF(max(r.right(), xs[-1]) + 1, ys[-1]))
            p.setPen(QPen(color, 1.5))
            p.drawPolyline(line)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            half = config.AUTOMATION_GRAB_PX / 2
            for x, y in zip(xs, ys):
                if r.left() - half <= x <= r.right() + half:
                    p.drawRect(QRectF(x - half + 1, y - half + 1, 2 * half - 2, 2 * half - 2))
            p.setBrush(Qt.NoBrush)
        else:
            value = None
            if a.target is not None and a.target[1] is not None:
                try:
                    value = a.target[1].raw_value
                except Exception:
                    pass
            if value is not None:
                p.setPen(QPen(color, 1, Qt.DashLine))
                y = self.automation_y(lane, value)
                p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(theme.RULER_TEXT)
            left = self.scroll.horizontalScrollBar().value()
            p.drawText(left + 8, lane.top + lane.height // 2 + 4, "Click to add points to the curve")
        p.setRenderHint(QPainter.Antialiasing, False)

    def point_at(self, lane, pos):
        """The index of the point of the automation lane near pos, or None."""
        grab = config.AUTOMATION_GRAB_PX
        near = [(abs(t * self.pps - pos.x()) + abs(self.automation_y(lane, v) - pos.y()), i)
                for i, (t, v) in enumerate(lane.automation.points)
                if abs(t * self.pps - pos.x()) <= grab and abs(self.automation_y(lane, v) - pos.y()) <= grab]
        return min(near)[1] if near else None

    def automation_press(self, e, lane):
        """A click in an automation lane: grab the point there, or add one there (and grab it)."""
        a, pos = lane.automation, e.position()
        if lane.owner.frozen:
            return
        index = self.point_at(lane, pos)
        if index is None:
            seconds = self.snapped_frame_at(pos.x(), e.modifiers()) / self.engine.sr
            a.set_points(a.points + ((seconds, self.automation_value(lane, pos.y())),))
            index = next(i for i, (t, v) in enumerate(a.points) if t == seconds)
        self.drag = PointDrag(lane, index)
        self.show_value_tip(lane, a.points[index][1], e.globalPosition().toPoint())
        self.update()
        self.automation_edited.emit()

    def drag_point(self, e):
        """Move the grabbed point: in time between its neighbours, and in value."""
        d, pos = self.drag, e.position()
        a = d.lane.automation
        points = list(a.points)
        seconds = self.snapped_frame_at(pos.x(), e.modifiers()) / self.engine.sr
        before = points[d.index - 1][0] if d.index > 0 else 0.0
        after = points[d.index + 1][0] if d.index + 1 < len(points) else None
        seconds = max(before, seconds if after is None else min(after, seconds))
        value = self.automation_value(d.lane, pos.y())
        points[d.index] = (seconds, value)
        a.points = tuple(points)  # (still in order: it stays between its neighbours)
        self.show_value_tip(d.lane, value, e.globalPosition().toPoint())
        self.update()
        self.automation_edited.emit()

    def value_text(self, a, value):
        """A value of the lane's parameter as the plugin shows it (else as a percentage)."""
        text = f"{value * 100:.0f} %"
        if a.target is not None and a.target[1] is not None:
            try:
                text = a.target[1].get_text_for_raw_value(value).strip() or text
            except Exception:
                pass
        return text

    def show_value_tip(self, lane, value, global_pos):
        """The parameter's value as the plugin shows it, next to the mouse."""
        a = lane.automation
        QToolTip.showText(global_pos, f"{a.param}: {self.value_text(a, value)}", self)

    def delete_point(self, lane, index):
        a = lane.automation
        a.points = a.points[:index] + a.points[index + 1:]
        self.update()
        self.automation_edited.emit()

    def automation_menu(self, global_pos, lane, pos):
        """Right-click an automation lane: delete the point there, clear the curve, or remove the lane."""
        a = lane.automation
        index = self.point_at(lane, pos)
        if index is not None and not lane.owner.frozen:
            self.delete_point(lane, index)
            return
        menu = QMenu(self)
        clear = menu.addAction("Clear the curve")
        clear.setEnabled(bool(a.points) and not lane.owner.frozen)
        remove = menu.addAction(f"Remove this lane ({a.param})")
        remove.setEnabled(not lane.owner.frozen)
        chosen = menu.exec(global_pos)
        if chosen is clear:
            a.points = ()
            self.update()
            self.automation_edited.emit()
        elif chosen is remove:
            self.remove_automation(lane.owner, a)

    def remove_automation(self, track, a):
        track.automation = tuple(x for x in track.automation if x is not a)
        if not track.automation:
            track.show_automation = False
        self.lanes_changed()

    def lanes_changed(self):
        """Automation lanes were added, removed, shown or hidden: lay out again; the panel rebuilds."""
        self.relayout()
        self.update()
        self.tracks_changed.emit()

    def paint_master(self, p, r, top, bottom):
        """The master lane, between y = top and bottom (master_lane.py draws it there): its background,
        the grid and both cursors' lines. r: the part to draw, in timeline x."""
        p.fillRect(QRect(r.left(), top, r.width(), bottom - top), theme.MASTER_LANE)
        self.paint_ruler(p, r, ruler=False, lines=(top, bottom))
        p.fillRect(QRect(r.left(), top, r.width(), 1), theme.MASTER_DIVIDER)
        for x, color in ((self.cursor_x, theme.CURSOR), (self.playhead_x, theme.PLAYHEAD)):
            p.setPen(color)
            p.drawLine(x, top, x, bottom)

    def paint_group_contents(self, p, r, lane):
        """A group's header lane: every clip of its tracks in miniature, one thin row per track."""
        members = self.project.members(lane.group)
        row_h = (lane.height - 2 * GROUP_PAD) / len(members)
        rows = {track: lane.top + GROUP_PAD + i * row_h for i, track in enumerate(members)}
        for clip in self.project.clips:
            y = rows.get(clip.track)
            if y is None:
                continue
            x0, x1 = self.x_of(clip.start), self.x_of(clip.end)
            if x1 < r.left() or x0 > r.right() + 1:
                continue
            color = clip_colors(clip_color(clip)[0], self.project.audible(clip.track)).fill
            block = QRectF(x0, y + 0.5, max(1.0, x1 - x0), max(1.0, row_h - 1))
            if clip.disabled:  # outline only
                p.setPen(color)
                p.drawRect(block.adjusted(0.5, 0.5, -0.5, -0.5))
            else:
                p.fillRect(block, color)

    def loop_xs(self):
        """x of the loop range's start and end, or None if there is none."""
        span = self.project.timing.loop_range()
        return (round(span[0] * self.pps), round(span[1] * self.pps)) if span else None

    def paint_loop(self, p):
        """The loop range: a band across the lower half of the ruler, with its two ends marked."""
        xs = self.loop_xs()
        if xs is None:
            return
        color = theme.LOOP if self.project.timing.looping else theme.LOOP_OFF
        top = config.RULER_H // 2
        p.fillRect(QRect(xs[0], top, xs[1] - xs[0], config.RULER_H - top), color)
        edge = QColor(color)
        edge.setAlpha(255)
        for x in xs:
            p.fillRect(QRect(x - 1, top, 2, config.RULER_H - top), edge)

    def loop_end_at(self, pos):
        """Which end of the loop range is under the mouse on the ruler: True (end), False (start) or None."""
        xs = self.loop_xs()
        if xs is None or pos.y() >= config.RULER_H:
            return None
        near = [(abs(pos.x() - x), i == 1) for i, x in enumerate(xs) if abs(pos.x() - x) <= LOOP_GRAB_PX]
        return min(near)[1] if near else None

    def set_current_track(self, track):
        if track is not self.current_track:
            self.current_track = track
            self.current_track_changed.emit()

    def paint_recording(self, p, tops):
        """The take being recorded, growing on its track's lane up to the playback cursor."""
        track, start = self.recording
        if track in tops:
            color = QColor(theme.RECORD)
            color.setAlpha(70)
            x0 = self.x_of(start)
            p.fillRect(QRect(x0, tops[track], max(0, self.playhead_x - x0), track.height), color)

    def set_loop(self, start, end):
        """Make frames start…end the loop range (at least LOOP_MIN_S long)."""
        sr, t = self.engine.sr, self.project.timing
        start = max(0, min(start, end))
        end = max(start + round(config.LOOP_MIN_S * sr), max(start, end))
        t.loop_start, t.loop_end = start / sr, end / sr
        self.update()
        self.loop_changed.emit()

    def paint_ruler(self, p, r, ruler=True, lines=None):
        """The ruler (unless ruler=False) and the grid lines below it, from y lines[0] to lines[1]
        (default: from the ruler down the whole timeline)."""
        lines = lines or (config.RULER_H, self.height())
        if ruler:
            p.fillRect(QRect(r.left(), 0, r.width(), config.RULER_H), theme.RULER_BG)
        if self.project.timing.beats:
            self.paint_bars(p, r, ruler, lines)
            return
        step = ruler_step(self.pps)
        decimals = step_decimals(step)
        for i in range(int(r.left() / self.pps / step), int(r.right() / self.pps / step) + 2):
            x = int(i * step * self.pps)
            p.setPen(theme.GRID)
            p.drawLine(x, lines[0], x, lines[1])
            if not ruler:
                continue
            p.setPen(theme.RULER_TEXT)
            p.drawLine(x, config.RULER_H - 6, x, config.RULER_H)
            p.drawText(x + 4, 15, fmt_time(i * step, decimals))

    def paint_bars(self, p, r, ruler=True, lines=None):
        """Bars-and-beats ruler and grid (timing.py): the closer the zoom, the finer the grid.
        Labelled lines cross the whole ruler; other bars, beats and finer lines get shorter ticks."""
        ticks = {timing.FINE: 3, timing.BEAT: 6, timing.BAR: 10}
        label_room = 80  # px: a label whose line is left of r can still reach into r
        for seconds, strength, label in self.project.timing.lines(max(0, r.left() - label_room) / self.pps,
                                                                  (r.right() + 1) / self.pps, self.pps):
            x = int(seconds * self.pps)
            p.setPen(theme.GRID_BY_STRENGTH[strength])
            p.drawLine(x, lines[0], x, lines[1])
            if not ruler:
                continue
            p.setPen(theme.RULER_TEXT)
            p.drawLine(x, 0 if label else config.RULER_H - ticks[strength], x, config.RULER_H)
            if label:
                p.drawText(x + 4, 15, label)

    # --- mouse: clicks pick what to do, drags carry it out ------------------------------

    def mousePressEvent(self, e):
        pos, ctrl = e.position(), bool(e.modifiers() & Qt.ControlModifier)
        clip, zone = self.hit(pos)
        if e.button() == Qt.RightButton:  # the context menu acts on what is under the mouse
            if clip and clip not in self.selection.clips:
                self.selection.select_only(clip)
                self.update()
            return
        if e.button() != Qt.LeftButton:
            return
        self.set_current_track(self.track_at(pos.y()) or self.current_track)
        edge = self.lane_edge_at(pos.y())
        if edge:  # bottom edge of a lane: resize the track's height
            self.drag = HeightDrag(edge, pos.y(), edge.height)
            return
        loop_end = self.loop_end_at(pos)
        if loop_end is not None:  # an end of the loop range on the ruler
            self.drag = LoopDrag(loop_end)
            return
        lane = self.lane_at(pos.y())
        if lane is not None and lane.automation is not None:
            self.set_current_track(lane.owner)
            self.automation_press(e, lane)
            return
        end = self.edge_at(pos)
        if end is not None and not ctrl:  # an end of a clip: trim it, or bring back what was cut off
            if end[0] not in self.selection.clips:
                self.selection.select_only(end[0])
            self.drag = EdgeDrag(*end)
            self.update()
            return
        if zone == "handle":
            if ctrl:
                self.selection.toggle(clip)
            else:
                if clip not in self.selection.clips:
                    self.selection.select_only(clip)
                self.start_move(pos, clip)
        else:  # ruler, empty lane space or a clip's body
            frame = self.snapped_frame_at(pos.x(), e.modifiers())
            self.set_cursor(frame)
            if pos.y() < config.RULER_H and self.engine.playing:  # the ruler: playback jumps there too
                self.seek(frame)
            track = self.track_at(pos.y())
            if track:  # not over the ruler, a group's header or the master lane
                keep = set(self.selection.clips) if ctrl else set()
                row = self.visible_tracks().index(track)
                self.drag = RangeDrag(pos.x(), frame, row, clip, keep, end_row=row)
            if not ctrl:
                self.selection.clear()
        self.update()

    def mouseMoveEvent(self, e):
        if isinstance(self.drag, HeightDrag):
            d = self.drag
            self.set_track_height(d.track, d.height + e.position().y() - d.y)
        elif isinstance(self.drag, MoveDrag):
            self.move_selected(e.position(), e.modifiers())
        elif isinstance(self.drag, EdgeDrag):
            self.drag_edge(e)
        elif isinstance(self.drag, PointDrag):
            self.drag_point(e)
        elif isinstance(self.drag, LoopDrag):
            sr, t = self.engine.sr, self.project.timing
            frame = self.snapped_frame_at(e.position().x(), e.modifiers())
            start, end = round(t.loop_start * sr), round(t.loop_end * sr)
            min_len = round(config.LOOP_MIN_S * sr)
            if self.drag.end:
                self.set_loop(start, max(frame, start + min_len))
            else:
                self.set_loop(min(frame, end - min_len), end)
        elif isinstance(self.drag, RangeDrag):
            self.select_range(e.position(), e.modifiers())
        else:
            self.update_cursor(e.position())

    def mouseReleaseEvent(self, e):
        d = self.drag
        if isinstance(d, RangeDrag):
            if d.active and self.selection.range:
                self.set_cursor(self.selection.range.start)  # play from the start of the selection
            elif d.clip:  # a click on a clip's body selects the clip
                if d.keep:
                    self.selection.toggle(d.clip)
                else:
                    self.selection.select_only(d.clip)
                self.update()
        if isinstance(d, MoveDrag):
            edits.clear_under(self.project, list(d.origin))  # moved clips replace what they now cover
            self.relayout()
        if isinstance(d, EdgeDrag):
            self.end_edge_drag()
        self.drag = None
        self.update_cursor(e.position())

    def mouseDoubleClickEvent(self, e):
        edge = self.lane_edge_at(e.position().y())
        lane = self.lane_at(e.position().y())
        if edge:  # double-click a lane's bottom edge: back to the default height
            self.set_track_height(edge, self.default_height(edge))
        elif lane and lane.automation:  # double-click a point: delete it
            index = self.point_at(lane, e.position())
            if index is not None and not lane.owner.frozen:
                self.drag = None
                self.delete_point(lane, index)
        elif lane and lane.group:  # double-click a group's header lane: fold or unfold it
            self.toggle_fold(lane.group)
        elif self.hit(e.position())[1] == "handle":  # double-click a clip's name strip: rename it
            clip = self.hit(e.position())[0]
            self.selection.select_only(clip)
            self.drag = None
            self.rename_selected()
        elif self.hit(e.position())[0] is not None and self.hit(e.position())[0].kind == MIDI:
            clip = self.hit(e.position())[0]  # double-click a MIDI clip: edit its notes
            self.selection.select_only(clip)
            self.drag = None
            self.update()
            self.edit_notes.emit(clip)
        elif e.button() == Qt.LeftButton and lane and lane.track and lane.track.kind == MIDI and not lane.track.frozen:
            self.drag = None  # double-click empty space on a MIDI track: a new clip there, to edit
            clip = self.new_midi_clip(lane.track, self.snapped_frame_at(e.position().x(), e.modifiers()))
            self.edit_notes.emit(clip)
        else:
            self.mousePressEvent(e)

    def update_cursor(self, pos):
        zone = self.hit(pos)[1]
        if isinstance(self.drag, HeightDrag) or self.lane_edge_at(pos.y()):
            self.setCursor(Qt.SizeVerCursor)
        elif isinstance(self.drag, (LoopDrag, EdgeDrag)) or self.loop_end_at(pos) is not None:
            self.setCursor(Qt.SizeHorCursor)
        elif (lane := self.lane_at(pos.y())) is not None and lane.automation is not None:
            grabbed = isinstance(self.drag, PointDrag) or self.point_at(lane, pos) is not None
            self.setCursor(Qt.ClosedHandCursor if isinstance(self.drag, PointDrag) else
                           Qt.OpenHandCursor if grabbed else Qt.CrossCursor)
        elif self.drag is None and self.edge_at(pos) is not None:
            self.setCursor(Qt.SizeHorCursor)  # an end of a clip: drag to trim or lengthen it
        elif zone == "handle":
            self.setCursor(Qt.ClosedHandCursor if isinstance(self.drag, MoveDrag) else Qt.OpenHandCursor)
        elif self.track_at(pos.y()):
            self.setCursor(Qt.IBeamCursor)  # selecting a range
        else:
            self.unsetCursor()

    def contextMenuEvent(self, e):
        lane = self.lane_at(e.pos().y())
        if lane is not None and lane.automation is not None:
            self.automation_menu(e.globalPos(), lane, QPointF(e.pos()))
            return
        self.show_menu(e.globalPos(), e.pos())

    def show_menu(self, global_pos, pos=None):
        """The right-click menu; pos: where it was asked for on the timeline (None: not over a lane)."""
        menu = QMenu(self)
        has_range, has_clips = self.selection.range is not None, bool(self.selection.clips)
        under = self.track_at(pos.y()) if pos is not None else None
        midi_clips = [c for c in self.selection.clips if c.kind == MIDI]
        range_midi = [t for t in self.selection.range.tracks if t.kind == MIDI] if has_range else []
        edit = menu.addAction("Edit notes…\tdouble-click")
        edit.setEnabled(len(midi_clips) == 1)
        new = menu.addAction("New MIDI clip" + (" over the range" if range_midi else ""))
        new.setEnabled(bool(range_midi) or (under is not None and under.kind == MIDI and not under.frozen))
        menu.addSeparator()
        duplicate = menu.addAction("Duplicate range\tCtrl+D" if has_range else "Duplicate clips\tCtrl+D")
        duplicate.setEnabled(has_range or has_clips)
        split = menu.addAction("Split at selection edges\tS" if has_range else "Split at cursor\tS")
        split.setEnabled(has_range or has_clips)
        delete = menu.addAction("Cut out selected range\tDel" if has_range else "Delete clips\tDel")
        delete.setEnabled(has_range or has_clips)
        menu.addSeparator()
        color = menu.addAction("Clip colour…\tC")
        color.setEnabled(has_clips)
        rename = menu.addAction("Rename clip…\tCtrl+R" if len(self.selection.clips) < 2 else "Rename clips…\tCtrl+R")
        rename.setEnabled(has_clips)
        all_disabled = has_clips and all(c.disabled for c in self.selection.clips)
        disable = menu.addAction("Enable clips\t0" if all_disabled else "Disable clips\t0")
        disable.setEnabled(has_clips)
        group = menu.addAction("Group these tracks\tCtrl+G")
        group.setEnabled(has_range or has_clips)
        loop = menu.addAction("Loop this range")
        loop.setEnabled(has_range)
        menu.addSeparator()
        select_all = menu.addAction("Select all clips\tCtrl+A")
        chosen = menu.exec(global_pos)
        if chosen is edit:
            self.edit_notes.emit(midi_clips[0])
        elif chosen is new:
            if range_midi:  # one per MIDI track of the range, as long as the range
                r = self.selection.range
                made = [self.new_midi_clip(t, r.start, r.end - r.start) for t in range_midi]
            else:
                made = [self.new_midi_clip(under, self.snapped_frame_at(pos.x(), Qt.NoModifier))]
            if len(made) == 1:
                self.edit_notes.emit(made[0])
        elif chosen is color:
            self.choose_color()
        elif chosen is duplicate:
            self.duplicate_selected()
        elif chosen is split:
            self.split_selected()
        elif chosen is delete:
            self.delete_selected()
        elif chosen is select_all:
            self.select_all()
        elif chosen is group:
            self.group_selected()
        elif chosen is rename:
            self.rename_selected()
        elif chosen is disable:
            self.toggle_disabled()
        elif chosen is loop:
            self.set_loop(self.selection.range.start, self.selection.range.end)
            self.loop_range_chosen.emit()

    def start_move(self, pos, grabbed):
        self.selection.range = None  # the range stays put in time; only clips move
        rows = {t: i for i, t in enumerate(self.visible_tracks())}  # clips move between the lanes shown
        origin = {c: (c.start, rows[c.track]) for c in self.selection.clips if c.track in rows}
        # moved clips go last: drawn on top of the clips they pass over (and hit first)
        p = self.project
        p.clips = tuple(c for c in p.clips if c not in origin) + tuple(c for c in p.clips if c in origin)
        self.drag = MoveDrag(self.frame_at(pos.x()), rows[self.track_at(pos.y())], origin, grabbed.start)
        self.setCursor(Qt.ClosedHandCursor)

    def move_selected(self, pos, modifiers=Qt.NoModifier):
        """Shift every selected clip by the same time and lane offset.

        In bars and beats the clip grabbed starts on the nearest grid line (Alt: freely); the
        others keep their distance to it. Clips stay at or after 0 s, and only change lanes when
        every clip lands on a lane of its own kind (audio on audio, MIDI on MIDI).
        """
        d, tracks, t = self.drag, self.visible_tracks(), self.project.timing
        df = self.frame_at(pos.x()) - d.frame
        if t.beats and not modifiers & Qt.AltModifier:
            sr = self.engine.sr
            df = round(t.snap((d.anchor + df) / sr, self.pps) * sr) - d.anchor
        df = max(df, -min(start for start, _ in d.origin.values()))
        under = self.track_at(pos.y())  # over a group's header or the master lane: lanes stay as they are
        dr = tracks.index(under) - d.row if under else d.rows_moved
        if all(0 <= row + dr < len(tracks) and tracks[row + dr].kind == clip.kind and not tracks[row + dr].frozen
               for clip, (_, row) in d.origin.items()):
            d.rows_moved = dr
        for clip, (start, row) in d.origin.items():
            clip.start = start + df
            clip.track = tracks[row + d.rows_moved]
        self.update()

    def select_range(self, pos, modifiers):
        d = self.drag
        if not d.active and abs(pos.x() - d.x) < config.DRAG_THRESHOLD_PX:
            return
        d.active = True
        frame = self.snapped_frame_at(pos.x(), modifiers)
        visible = self.visible_tracks()
        lane = self.lane_at(pos.y())
        if lane and (lane.track or lane.owner):  # (an automation lane counts as its track's)
            d.end_row = visible.index(lane.track or lane.owner)
        elif lane and lane.is_master:  # the master lane: down to the last track
            d.end_row = len(visible) - 1
        # over a group's header the range keeps its lanes
        row = d.end_row
        start, end = min(d.frame, frame), max(d.frame, frame)
        tracks = tuple(t for t in visible[min(d.row, row):max(d.row, row) + 1] if not t.frozen)
        if start == end or not tracks:  # both edges on the same grid line, or only frozen lanes: no range
            self.selection.set_range(None, d.keep)
        else:
            self.selection.set_range(TimeRange(start, end, tracks),
                                     d.keep | set(self.project.clips_in(tracks, start, end)))
        self.update()

    def wheelEvent(self, e):
        self.wheel(e.angleDelta().y(), e.modifiers(), e.position().x())

    def wheel(self, delta, modifiers, x):
        """Ctrl+wheel zooms around timeline x; the wheel alone scrolls sideways."""
        if modifiers & Qt.ControlModifier:
            self.zoom(config.ZOOM_STEP if delta > 0 else 1 / config.ZOOM_STEP, x)
        else:
            hbar = self.scroll.horizontalScrollBar()
            hbar.setValue(hbar.value() - delta)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected()
        elif e.key() == Qt.Key_S and not e.modifiers():
            self.split_selected()
        elif e.key() == Qt.Key_C and not e.modifiers():
            self.choose_color()
        elif e.key() == Qt.Key_A and e.modifiers() & Qt.ControlModifier:
            self.select_all()
        elif e.key() == Qt.Key_G and e.modifiers() & Qt.ControlModifier:
            self.group_selected()
        elif e.key() == Qt.Key_D and e.modifiers() & Qt.ControlModifier:
            self.duplicate_selected()
        elif e.key() == Qt.Key_R and e.modifiers() & Qt.ControlModifier:
            self.rename_selected()
        elif e.key() == Qt.Key_0 and not e.modifiers() & ~Qt.KeypadModifier:
            self.toggle_disabled()
        elif e.key() == Qt.Key_Escape:
            self.clear_selection()
        else:
            super().keyPressEvent(e)

    # --- dropping files from Explorer -------------------------------------------------

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        """Each file goes on the lane under the cursor (then the ones below) if the kind
        matches; otherwise, or on the master lane, onto a new track of its kind. Dropped on a
        group's header, files go onto new tracks in that group."""
        pos = e.position()
        paths = [url.toLocalFile() for url in e.mimeData().urls()]
        # on the grid in bars and beats, unless Alt
        self.drop_paths(paths, self.snapped_frame_at(pos.x(), e.modifiers()), self.lane_at(pos.y()))
        e.acceptProposedAction()

    def drop_paths(self, paths, frame, lane):
        """Place dropped files at `frame` on `lane` and the lanes below it (see dropEvent);
        lane None or the master lane: onto new tracks."""
        tracks = self.visible_tracks()
        track = lane and (lane.track or lane.owner)  # (dropped on an automation lane: onto its track)
        row = tracks.index(track) if track else len(tracks)
        group = lane.group if lane else None
        if group and group.folded:
            group.folded = False
            self.tracks_regrouped()
        for i, path in enumerate(p for p in paths if p and not os.path.isdir(p)):
            clip = self.load_clip(path)
            if not clip:
                continue
            target = tracks[row + i] if row + i < len(tracks) else None
            if target and (target.kind != clip.kind or target.frozen):
                target = None
            self.place_clip(clip, frame, target or self.add_track(clip.kind, group))
