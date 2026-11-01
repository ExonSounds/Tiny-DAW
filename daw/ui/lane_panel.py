"""The right-hand panel: one header per track (name, effects, mute, solo, remove, output pair, level
meters) and the master strip.

A track's Auto button (under its outputs) shows or hides its automation lanes; each lane has a row
here: its plugin and parameter, + to automate another parameter, ✕ to remove the lane.

Each track row has a volume slider under its meter (after its effects; double-click: 0 dB), with
its setting in dB next to it. A MIDI track has an instrument button (🎹): click it to open its
instrument's window, ▾ to choose another; a new one, until it has an instrument, shows only its
name and a "Choose instrument" button across its row (instrument_menu.py), and is silent.
Each track row's meter has a bar for each output it plays on (its pair); a group's for its
tracks' outputs; the master's for every output any track plays on. Rows line up with the timeline
lanes, each as tall as its track (drag a row's bottom edge, here or in the timeline, to
resize it); main_window.py keeps their vertical scrolling in sync. Right-click a row to give
the track a colour, with the same dialog and automatic contrast as clips, or to group tracks.

A group has a header row above its tracks (fold ▾/▸, mute, solo, ungroup, and a meter showing
its tracks together); its tracks' rows have a bar in the group's colour down their left side.
The rows follow the same lanes as the timeline (lanes.py).
"""
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QSizePolicy,
                               QToolButton, QVBoxLayout, QWidget)

from .. import config
from ..audio import automation
from ..model import MIDI, Automation
from . import theme
from .color_picker import GROUP as GROUP_SUBJECT, TRACK, ColorPickerDialog
from .automation_dialog import ParameterDialog
from .colors import default_color, track_colors
from .lanes import layout
from .level_meter import LevelMeter
from .loudness_display import LoudnessDisplay
from .timeline import group_fill
from .widgets import (DbReading, NameLabel, VolumeSlider, fx_button, icon_button, instrument_button, lane_frame,
                      output_selector, toggle_button)

GROUP_BAR_W = 5  # px: bar down the left of a group's tracks' rows


class EdgeGrip(QWidget):
    """Invisible strip along the bottom of a track's (or an automation lane's) row: drag it to
    resize it, double-click it for the default height."""
    HEIGHT = 5  # the row's bottom margin, so it never covers the controls

    def __init__(self, frame, track, timeline):
        super().__init__(frame)
        self.track, self.timeline = track, timeline
        self.start = None  # (global mouse y, track height) while dragging
        self.setCursor(Qt.SizeVerCursor)
        self.setToolTip("Drag to resize the track; double-click for the default height")
        frame.installEventFilter(self)  # follow the row's size
        self.place()

    def place(self):
        row = self.parentWidget()
        self.setGeometry(0, row.height() - self.HEIGHT, row.width(), self.HEIGHT)
        self.raise_()

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Resize:
            self.place()
        return False

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.start = (e.globalPosition().y(), self.track.height)

    def mouseMoveEvent(self, e):
        if self.start:
            y, height = self.start
            self.timeline.set_track_height(self.track, height + e.globalPosition().y() - y)

    def mouseReleaseEvent(self, e):
        self.start = None

    def mouseDoubleClickEvent(self, e):
        self.timeline.set_track_height(self.track, self.timeline.default_height(self.track))


def with_menu(frame, fill_menu):
    """Right-clicking the row opens a menu that fill_menu(menu) fills."""
    frame.setContextMenuPolicy(Qt.CustomContextMenu)

    def show(pos):
        menu = QMenu(frame)
        fill_menu(menu)
        menu.exec(frame.mapToGlobal(pos))

    frame.customContextMenuRequested.connect(show)


def group_bar_color(group):
    return group_fill(group) if group.color else theme.GROUP_ACCENT


def track_outputs(track):
    """The device outputs (0-based) a track plays on: its pair."""
    return [track.output, track.output + 1]


def track_header(track, color, timeline, channels, open_plugins, instruments, fill_menu, rename, on_output,
                 toggle_automation=None):
    """One track's row in the panel, in `color` or the track's own colour; the stripe on its left
    shows audio (blue) or MIDI (green), and a group's tracks also have a bar in the group's colour.
    open_plugins(track) opens its effects window (FX button); instruments = (fill(menu, track): the
    instruments to choose from, edit(track): opens its instrument's window), for MIDI tracks;
    fill_menu(menu, track) fills its right-click menu;
    rename(track) asks for a new name (double-click the name); on_output() is called when its
    outputs change; toggle_automation(track, on) shows or hides its automation lanes (Auto).

    Returns (row widget, its LevelMeter, its LoudnessDisplay); a MIDI track still waiting for its
    instrument has neither: (row widget, None, None)."""
    text = None
    if track.color:
        color, text = track_colors(track.color, track.tone)
    frame = lane_frame(color, theme.TRACK_ACCENT[track.kind], track.height, text)
    with_menu(frame, lambda menu: fill_menu(menu, track))
    frame.mousePressEvent = lambda e: timeline.set_current_track(track)  # clicking the row selects the track
    EdgeGrip(frame, track, timeline)
    outer = QHBoxLayout(frame)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    if track.group:
        bar = QFrame()
        bar.setFixedWidth(GROUP_BAR_W)
        bar.setStyleSheet(f"background: {group_bar_color(track.group).name()};")
        bar.setToolTip(f"In {track.group.name}")
        outer.addWidget(bar)
    col = QVBoxLayout()
    col.setContentsMargins(8, 5, 8, 5)
    col.setSpacing(4)
    outer.addLayout(col)
    row = QHBoxLayout()
    name = NameLabel(f"❄ {track.name}" if track.frozen else track.name)
    name.double_clicked.connect(lambda: rename(track))
    name.setToolTip("Frozen: it plays its rendering; its clips and plugins are locked until you unfreeze it "
                    "(right-click)" if track.frozen else "Double-click to rename. Right-click the row for the "
                    "track colour, groups, freezing and bouncing")
    row.addWidget(name, 1)
    remove = icon_button("✕", lambda: timeline.remove_track(track), f"Remove {track.name}")
    if track.kind == MIDI and track.needs_instrument:  # nothing else until it has an instrument
        row.addWidget(remove)
        col.addLayout(row)
        choose = QPushButton("Choose instrument")
        choose.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        choose.setFocusPolicy(Qt.NoFocus)
        choose.setToolTip("The track is silent until it has an instrument: the built-in synth or a VST3 instrument")
        menu = QMenu(choose)
        menu.aboutToShow.connect(lambda: instruments[0](menu, track))
        choose.setMenu(menu)  # (clicking shows it)
        col.addWidget(choose, 1)
        return frame, None, None

    def set_flag(attr, on):
        setattr(track, attr, on)
        timeline.update()  # silent clips are drawn grey

    if track.kind == MIDI:
        row.addWidget(instrument_button(track.instrument, lambda: instruments[1](track),
                                        lambda menu: instruments[0](menu, track),
                                        rack=len(track.rack) if track.uses_rack else None))
    row.addWidget(fx_button(track.effects, lambda: open_plugins(track)))
    row.addWidget(toggle_button("M", "mute", track.muted, lambda on: set_flag("muted", on), "Mute"))
    row.addWidget(toggle_button("S", "solo", track.soloed, lambda on: set_flag("soloed", on), "Solo"))
    row.addWidget(remove)
    col.addLayout(row)

    meter = LevelMeter(channels)
    meter.show_channels(track_outputs(track))

    def set_output(first):
        track.output = first
        meter.show_channels(track_outputs(track))
        on_output()  # the master's and its group's meters show the outputs in use

    def set_volume(db):
        track.volume_db = db  # the mixer reads it every buffer

    loudness = LoudnessDisplay()
    slider = VolumeSlider(track.volume_db, set_volume)
    bottom = QGridLayout()  # outputs | the meter, the volume slider under it | LUFS, the volume's dB under it
    bottom.setHorizontalSpacing(6)
    bottom.setVerticalSpacing(2)
    bottom.addWidget(output_selector(channels, track.output, set_output), 0, 0, Qt.AlignTop)
    bottom.addWidget(meter, 0, 1)
    bottom.addWidget(loudness, 0, 2, Qt.AlignTop | Qt.AlignRight)
    bottom.addWidget(slider, 1, 1)
    bottom.addWidget(DbReading(slider), 1, 2, Qt.AlignRight)
    if toggle_automation is not None:
        auto = QToolButton()
        auto.setText("Auto")
        auto.setCheckable(True)
        auto.setChecked(track.show_automation)
        auto.setFixedSize(58, 16)
        auto.setFocusPolicy(Qt.NoFocus)
        auto.setProperty("accent", True)  # lit while its lanes show
        count = len(track.automation)
        auto.setToolTip(f"Show or hide this track's automation ({count} lane{'s' if count != 1 else ''})" if count
                        else "Automate a parameter of this track's instrument or effects")
        auto.toggled.connect(lambda on: toggle_automation(track, on))
        bottom.addWidget(auto, 1, 0)
    bottom.setColumnStretch(1, 1)
    bottom.setRowStretch(0, 1)
    col.addLayout(bottom, 1)
    return frame, meter, loudness


def automation_header(lane, timeline, add, remove):
    """An automation lane's row: its plugin and parameter, + (automate another parameter of the
    track) and ✕ (remove the lane)."""
    a, track = lane.automation, lane.owner
    frame = lane_frame(theme.AUTOMATION_LANE, theme.TRACK_ACCENT[track.kind], lane.height)
    # (a line above it, as in the timeline: stacked lanes stay apart)
    frame.setStyleSheet(frame.styleSheet() + f" #lane {{ border-top: 1px solid {theme.GRID_BY_STRENGTH[2].name()}; }}")
    frame.mousePressEvent = lambda e: timeline.set_current_track(track)
    EdgeGrip(frame, a, timeline)
    col = QVBoxLayout(frame)
    col.setContentsMargins(14, 4, 8, 4)
    col.setSpacing(2)
    row = QHBoxLayout()
    name = QLabel(a.param)
    name.setToolTip(f"{a.slot.name}: {a.param}")
    name.setMinimumWidth(10)
    row.addWidget(name, 1)
    if not track.frozen:
        row.addWidget(icon_button("+", lambda: add(track), "Automate another parameter of this track"))
        row.addWidget(icon_button("✕", lambda: remove(track, a), "Remove this automation lane"))
    col.addLayout(row)
    detail = QLabel(a.slot.name + ("" if a.target is None or a.target[1] is not None else "  (no such parameter now)"))
    detail.setProperty("hint", True)
    col.addWidget(detail)
    col.addStretch(1)
    return frame


def group_header(group, timeline, channels, fill_menu, rename):
    """A group's row: fold ▾/▸, name, mute, solo and ungroup (its tracks stay), above a level meter
    of all its tracks together. Returns (row widget, its LevelMeter)."""
    text = track_colors(group.color, group.tone)[1] if group.color else None
    frame = lane_frame(group_fill(group), group_bar_color(group), config.GROUP_H, text)
    with_menu(frame, lambda menu: fill_menu(menu, group))
    col = QVBoxLayout(frame)
    col.setContentsMargins(4, 4, 8, 4)
    col.setSpacing(3)
    row = QHBoxLayout()
    fold = icon_button("▸" if group.folded else "▾", lambda: timeline.toggle_fold(group),
                       "Show its tracks" if group.folded else "Fold its tracks away")
    row.addWidget(fold)
    name = NameLabel(f"<b>{group.name}</b>")
    name.double_clicked.connect(lambda: rename(group))
    name.setToolTip("Double-click to rename. Right-click for its colour; double-click its lane to fold it")
    row.addWidget(name, 1)

    def set_flag(attr, on):
        setattr(group, attr, on)
        timeline.update()  # silent clips are drawn grey

    row.addWidget(toggle_button("M", "mute", group.muted, lambda on: set_flag("muted", on), "Mute the group"))
    row.addWidget(toggle_button("S", "solo", group.soloed, lambda on: set_flag("soloed", on), "Solo the group"))
    row.addWidget(icon_button("✕", lambda: timeline.ungroup(group), "Ungroup (the tracks stay)"))
    col.addLayout(row)
    meter = LevelMeter(channels)
    meter_row = QHBoxLayout()
    meter_row.setContentsMargins(26, 0, 0, 0)  # under the name, past the fold button
    meter_row.addWidget(meter)
    col.addLayout(meter_row, 1)
    return frame, meter


def choose_tracks(parent, tracks, checked):
    """A small dialog to tick tracks (those in `checked` already ticked). The ticked ones, or None."""
    dialog = QDialog(parent)
    dialog.setWindowTitle("Group tracks")
    col = QVBoxLayout(dialog)
    col.addWidget(QLabel("Tracks to put in a new group:"))
    box = QListWidget()
    for track in tracks:
        item = QListWidgetItem(track.name + (f"   (now in {track.group.name})" if track.group else ""))
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if track in checked else Qt.Unchecked)
        box.addItem(item)
    col.addWidget(box)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    col.addWidget(buttons)
    if not dialog.exec():
        return None
    return [t for i, t in enumerate(tracks) if box.item(i).checkState() == Qt.Checked]


class LanePanel(QWidget):
    def __init__(self, project, timeline, master_strip, channels, open_plugins, fill_render_menu=None,
                 instruments=(None, None)):
        """fill_render_menu(menu, track): adds freezing and bouncing to a track's right-click menu;
        instruments = (fill(menu, track), edit(track)): a MIDI track's instruments to choose from, and
        opening its instrument's window."""
        super().__init__()
        self.instruments = instruments
        self.fill_render_menu = fill_render_menu
        self.project, self.timeline, self.master_strip = project, timeline, master_strip
        self.open_plugins = open_plugins  # open_plugins(track): the track's plugins window
        self.channels = channels
        self.show_levels, self.show_loudness = True, True  # Options menu
        self.meters = {}  # track -> LevelMeter, fed by main_window
        self.loudness = {}  # track -> LoudnessDisplay, fed by main_window
        self.frames = {}  # track -> its row widget
        self.group_meters = {}  # group -> LevelMeter of its tracks together, fed by main_window
        self.setFixedWidth(config.PANEL_W)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(0)
        lay.addLayout(self.rows)
        lay.addStretch(1)  # (the master row stays at the bottom of the screen: main_window.py pins it there)
        timeline.tracks_changed.connect(self.rebuild)
        timeline.current_track_changed.connect(self.show_current_track)
        timeline.heights_changed.connect(self.update_heights)
        self.rebuild()

    def set_channels(self, channels):
        """A device with a different number of outputs was opened: tracks routed to outputs it
        lacks go back to Out 1/2, and the rows are rebuilt with the new output list."""
        self.channels = channels
        for track in self.project.tracks:
            if track.output >= channels:
                track.output = 0
        self.master_strip.meter.set_channels(channels)
        self.rebuild()

    def rebuild(self):
        """Recreate the rows: one per lane of the timeline (group headers and the tracks shown)."""
        while self.rows.count():
            old = self.rows.takeAt(0).widget()
            old.hide()  # deleteLater() only acts on the next event loop pass
            old.deleteLater()
        self.meters, self.frames, self.loudness, self.group_meters = {}, {}, {}, {}
        self.automation_frames = {}  # Automation -> its row
        if any([automation.prune(t) for t in self.project.tracks]):  # (lanes of plugins that are gone)
            self.timeline.relayout()
        row = 0  # track rows alternate in shade, like their lanes
        for lane in layout(self.project)[:-1]:
            if lane.automation:
                frame = automation_header(lane, self.timeline, self.add_automation, self.timeline.remove_automation)
                self.automation_frames[lane.automation] = frame
            elif lane.group:
                frame, self.group_meters[lane.group] = group_header(lane.group, self.timeline, self.channels,
                                                                     self.fill_group_menu, self.rename)
            else:
                track = lane.track
                frame, meter, loudness = track_header(
                    track, theme.ROWS[row % 2], self.timeline, self.channels, self.open_plugins,
                    self.instruments, self.fill_track_menu, self.rename, self.show_used_outputs,
                    self.toggle_automation)
                if meter is not None:  # (not while it waits for its instrument)
                    self.meters[track], self.loudness[track] = meter, loudness
                self.frames[track] = frame
                row += 1
            self.rows.addWidget(frame)
        self.show_used_outputs()
        self.set_meters_visible(self.show_levels, self.show_loudness)
        self.show_current_track()
        self.fit_height()

    # --- automation --------------------------------------------------------------------

    def toggle_automation(self, track, on):
        """Auto on a track's row: show or hide its lanes; with none yet, choose a parameter first."""
        if on and not track.automation:
            if not self.add_automation(track):
                self.rebuild()  # (Auto goes off again)
            return
        track.show_automation = on
        self.timeline.lanes_changed()

    def add_automation(self, track):
        """Choose a parameter of the track's plugins and add a lane for it. True if one was added."""
        if track.frozen:
            QMessageBox.information(self, "Track frozen", f"{track.name} is frozen: unfreeze it to automate it.")
            return False
        if not automation.plugins_of(track):
            QMessageBox.information(self, "Automation", f"{track.name} has no plugins yet: automation changes the "
                                    "parameters of a track's instrument or effects. Add some first (FX"
                                    + (", or 🎹" if track.kind == MIDI else "") + ").")
            return False
        dialog = ParameterDialog(self, track)
        if not dialog.exec() or dialog.chosen is None:
            return False
        slot, param = dialog.chosen
        track.automation = track.automation + (Automation(slot, param),)
        track.show_automation = True
        automation.resolve(self.project)
        self.timeline.lanes_changed()
        return True

    def show_current_track(self):
        """Outline the selected track's row (the one the timeline records onto)."""
        for track, frame in self.frames.items():
            current = track is self.timeline.current_track
            if frame.property("current") != current:
                frame.setProperty("current", current)
                frame.style().unpolish(frame)
                frame.style().polish(frame)

    def show_used_outputs(self):
        """Group meters show their tracks' outputs, the master meter every output a track plays on."""
        for group, meter in self.group_meters.items():
            meter.show_channels([c for t in self.project.members(group) for c in track_outputs(t)])
        used = [c for t in self.project.tracks for c in track_outputs(t)]
        self.master_strip.meter.show_channels(used or [0, 1])

    # --- right-click menus -------------------------------------------------------------

    def fill_track_menu(self, menu, track):
        menu.addAction("Rename track…").triggered.connect(lambda: self.rename(track))
        menu.addAction("Track colour…").triggered.connect(lambda: self.choose_color(track))
        if track.kind == MIDI and track.uses_rack and not track.needs_instrument:
            menu.addAction("Open its instrument patch panel").triggered.connect(lambda: self.instruments[1](track))
        elif track.kind == MIDI and track.instrument is not None and not track.needs_instrument:
            menu.addAction(f"Open the {track.instrument.name} window").triggered.connect(
                lambda: self.instruments[1](track))
        if not track.frozen:
            menu.addAction("Automate a parameter…").triggered.connect(lambda: self.add_automation(track))
        if track.automation:
            menu.addAction("Hide automation" if track.show_automation else "Show automation").triggered.connect(
                lambda: self.toggle_automation(track, not track.show_automation))
        menu.addSeparator()
        menu.addAction("Group tracks…").triggered.connect(lambda: self.group_with(track))
        others = [g for g in self.project.groups() if g is not track.group]
        if others:
            join = menu.addMenu("Add to group")
            for group in others:
                join.addAction(group.name).triggered.connect(lambda _=False, g=group: self.timeline.set_group(track, g))
        if track.group:
            menu.addAction(f"Remove from {track.group.name}").triggered.connect(
                lambda: self.timeline.set_group(track, None))
        if self.fill_render_menu:
            self.fill_render_menu(menu, track)

    def fill_group_menu(self, menu, group):
        menu.addAction("Rename group…").triggered.connect(lambda: self.rename(group))
        menu.addAction("Group colour…").triggered.connect(lambda: self.choose_color(group))
        menu.addAction("Show its tracks" if group.folded else "Fold its tracks away").triggered.connect(
            lambda: self.timeline.toggle_fold(group))
        menu.addSeparator()
        menu.addAction("Ungroup (the tracks stay)").triggered.connect(lambda: self.timeline.ungroup(group))

    def choose_color(self, item):
        """The colour dialog (the same as for clips) for a track's or a group's row."""
        is_track = item in self.project.tracks
        initial = item.color or (default_color(item.kind) if is_track else theme.GROUP_ACCENT.name())
        dialog = ColorPickerDialog(self, QColor(initial), item.tone, TRACK if is_track else GROUP_SUBJECT)
        if dialog.exec():
            item.color, item.tone = dialog.chosen, dialog.tone  # None: back to the standard look
            self.rebuild()
            self.timeline.update()  # a group's colour shows on its header lane too

    def group_with(self, track):
        chosen = choose_tracks(self, list(self.project.tracks), {track})
        if chosen:
            self.timeline.group_tracks(chosen)

    def rename(self, item):
        """Ask for a new name for a track or a group."""
        what = "track" if item in self.project.tracks else "group"
        name, ok = QInputDialog.getText(self, f"Rename {what}", "Name:", text=item.name)
        if ok and name.strip():
            item.name = name.strip()
            self.rebuild()

    def set_meters_visible(self, levels, loudness):
        """Show or hide the level meters and LUFS readouts on every row, master included."""
        self.show_levels, self.show_loudness = levels, loudness
        for meter in [*self.meters.values(), *self.group_meters.values(), self.master_strip.meter]:
            meter.setVisible(levels)
        for display in [*self.loudness.values(), self.master_strip.loudness]:
            display.setVisible(loudness)

    def update_heights(self):
        """A track or automation lane was resized: resize the rows (not rebuilt, so a drag in progress continues)."""
        for item, frame in [*self.frames.items(), *self.automation_frames.items()]:
            frame.setFixedHeight(item.height)
        self.fit_height()

    def fit_height(self):
        # All lanes + master, plus room matching the timeline's horizontal scrollbar, so both
        # scroll areas have the same range (the timeline's ruler is matched by the CPU monitor).
        self.setFixedHeight(layout(self.project)[-1].bottom + theme.SCROLLBAR_W)
