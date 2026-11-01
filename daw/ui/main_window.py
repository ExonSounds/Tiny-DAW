"""The main window: creates the project, engine and widgets, and wires them together.
Also the File menu: new, open, save (see ../project_io.py for the file format).

    +---------------------------------------------------------------------+
    | File menu                                                           |
    | TransportBar                                                        |
    +----------+------------------------------------------+---------------+
    | Explorer | Overview (whole project, compressed)     | caption       |
    | (dock,   +------------------------------------------+---------------+
    | View     | Timeline (in a QScrollArea)              | CpuMonitor    |
    | menu)    |   ruler                                  +---------------+
    |          |   track lanes ...                        | LanePanel     |
    |          |                                          |  (scroll-     |
    |          |                                          |   synced)     |
    |          | master lane (pinned over the tracks)     | master row    |
    +----------+------------------------------------------+---------------+
"""
import os
import time

import numpy as np

from PySide6.QtCore import QRect, Qt, QTimer, QUrl
from PySide6.QtGui import QActionGroup, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
                               QMessageBox, QScrollArea, QVBoxLayout, QWidget)

from .. import config, project_io
from ..audio import devices
from ..audio.engine import Engine
from ..audio.metering import MASTER
from ..audio.metronome import render_click
from ..audio import automation
from ..audio.plugins import is_vst2, sync_state
from ..model import AUDIO, MIDI, Project
from ..timing import TapTempo
from .audio_dialog import AudioPropertiesDialog
from .cpu_monitor import CpuMonitor
from .explorer import Explorer
from .instrument_menu import edit_instrument, fill_instrument_menu
from .lane_panel import LanePanel
from .midi_editor import MidiEditor
from .mix_export import MixExporter
from .recording import Recording
from .metronome_dialog import MetronomeDialog, load_settings as load_metronome_settings
from . import colors, skin as skins, theme
from .skin_dialog import SkinDialog
from .master_strip import MasterStrip
from .overview import Overview
from .plugin_dialog import InstrumentPanelDialog, PluginsDialog
from .plugin_folders_dialog import PluginFoldersDialog
from .plugin_host import PluginHost
from .preferences import REFRESH_RATES, Preferences
from .timeline import Timeline
from .track_render import TrackRenderer
from .transport_bar import TransportBar
from .update_notice import UpdateChecker
from .widgets import PinToBottom


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1280, 800)
        self.project = Project()
        self.engine = Engine(self.project)
        self.project_path = None  # JSON file of the open project, once saved or opened
        self.prefs = Preferences.load()
        self.skin = skins.find(self.prefs.skin)  # before anything is built: widgets take its colours
        theme.rounded = self.prefs.rounded_corners
        skins.apply(QApplication.instance(), self.skin)
        self.fx_windows = {}  # id(track or project) -> its open plugins window (show_fx_window)
        self.midi_editors = {}  # id(clip) -> its open MIDI editor
        self.metronome_settings = load_metronome_settings()
        self.engine.blocksize = self.prefs.buffer_size

        self.transport = TransportBar()
        self.addToolBar(self.transport)
        self.scroll = QScrollArea()
        self.timeline = Timeline(self.project, self.engine, self.scroll)
        self.plugins = PluginHost(self, self.engine)
        self.master = MasterStrip(self.engine, self.open_master_plugins)
        self.renderer = TrackRenderer(self, self.engine, self.timeline, self.plugins)
        self.exporter = MixExporter(self, self.engine, self.timeline)
        self.recording = Recording(self, self.engine, self.timeline)
        self.panel = LanePanel(self.project, self.timeline, self.master, self.engine.channels, self.open_track_plugins,
                               self.renderer.fill_menu, (self.fill_instrument_menu, self.edit_instrument))
        self.cpu_monitor = CpuMonitor()
        self.overview = Overview(self.project, self.engine, self.timeline, self.scroll, self.prefs.overview_h)
        self.overview.resized.connect(self.overview_resized)
        self.explorer = Explorer(lambda path: self.timeline.add_file(path, self.timeline.cursor), self.engine)
        self.build_layout()
        self.build_file_menu()
        self.build_view_menu()
        self.build_options_menu()
        self.connect_controls()
        self.start_timers()
        self.apply_meter_options()
        self.updates = UpdateChecker(self)  # the notice under the master row: is there a newer version?
        self.updates.done.connect(self.master.show_update)
        self.updates.start()
        self.open_first_device()
        self.mark_saved()
        self.timeline.setFocus()  # editing keys (Space, S, Delete…) work straight away

    # --- setup ----------------------------------------------------------------------

    def build_layout(self):
        self.scroll.setFrameShape(QFrame.NoFrame)
        # always shown, so the panel's matching bottom padding is always right
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.scroll.setWidget(self.timeline)

        panel_scroll = QScrollArea()
        panel_scroll.setFrameShape(QFrame.NoFrame)
        panel_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        panel_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        panel_scroll.setWidget(self.panel)
        panel_scroll.setFixedWidth(config.PANEL_W)
        # the master lane and its row stay at the bottom of the screen, over the tracks scrolling beneath
        self.master_pins = (PinToBottom(self.timeline.master_view, self.scroll.viewport(), config.MASTER_H),
                            PinToBottom(self.master.frame, panel_scroll.viewport(), config.MASTER_H,
                                        margin=theme.SCROLLBAR_W))
        timeline_bar, panel_bar = self.scroll.verticalScrollBar(), panel_scroll.verticalScrollBar()
        timeline_bar.valueChanged.connect(panel_bar.setValue)
        panel_bar.valueChanged.connect(timeline_bar.setValue)

        left = QVBoxLayout()
        left.setSpacing(0)
        left.addWidget(self.overview)
        left.addWidget(self.scroll, 1)
        overview_caption = self.overview_caption = QLabel("Project overview")  # same height as the overview
        overview_caption.setFixedHeight(self.overview.height())
        overview_caption.setAlignment(Qt.AlignCenter)
        overview_caption.setStyleSheet(f"color: {theme.RULER_TEXT.name()}; background: {theme.OVERVIEW_BG.name()};"
                                       f"border-bottom: 1px solid {theme.OVERVIEW_EDGE.name()};")
        right = QVBoxLayout()
        right.setSpacing(0)
        right.addWidget(overview_caption)
        right.addWidget(self.cpu_monitor)  # same height as the ruler, so the rows still line up
        right.addWidget(panel_scroll, 1)

        central = QWidget()
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addLayout(left, 1)
        row.addLayout(right)
        self.setCentralWidget(central)

        self.explorer_dock = QDockWidget("Explorer")
        self.explorer_dock.setObjectName("explorer")
        self.explorer_dock.setWidget(self.explorer)
        self.explorer_dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        self.explorer_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.explorer_dock)
        self.resizeDocks([self.explorer_dock], [config.EXPLORER_W], Qt.Horizontal)

    def build_file_menu(self):
        menu = self.menuBar().addMenu("&File")
        for text, keys, slot in (("&New project", "Ctrl+N", self.new_project),
                                 ("&Open project…", "Ctrl+O", self.open_project),
                                 ("&Save", "Ctrl+S", self.save_project),
                                 ("Save &as…", "Ctrl+Shift+S", self.save_project_as),
                                 (None, None, None),
                                 ("&Render selection…", "Ctrl+Shift+R", self.exporter.render_selection),
                                 ("Render &project…", "Ctrl+Shift+E", self.exporter.render_project),
                                 (None, None, None),
                                 ("Add audio or MIDI &file…", "Ctrl+I", self.add_files)):
            if text is None:
                menu.addSeparator()
                continue
            action = menu.addAction(text)
            action.triggered.connect(slot)
            if text.startswith("Add"):
                action.setText(f"{text}\t{keys}")  # shown only: the toolbar button owns the shortcut
            else:
                action.setShortcut(QKeySequence(keys))

    def build_view_menu(self):
        menu = self.menuBar().addMenu("&View")
        toggle = self.explorer_dock.toggleViewAction()
        toggle.setText("&Explorer")
        toggle.setShortcut(QKeySequence("Ctrl+E"))
        menu.addAction(toggle)
        menu.addSeparator()
        menu.addAction("Show all automation").triggered.connect(lambda: self.show_all_automation(True))
        menu.addAction("Hide all automation").triggered.connect(lambda: self.show_all_automation(False))

    def show_all_automation(self, on):
        """Every track with automation lanes shows them, or none does."""
        for track in self.project.tracks:
            track.show_automation = on and bool(track.automation)
        self.timeline.lanes_changed()

    def build_options_menu(self):
        """Audio properties, and options that lower CPU use; all remembered between runs."""
        menu = self.menuBar().addMenu("&Options")
        menu.addAction("&Audio properties…").triggered.connect(self.show_audio_properties)
        menu.addAction("&VST2 plugin folders…").triggered.connect(self.choose_vst2_folders)
        menu.addSeparator()

        def radio_submenu(title, choices, current, slot, tip):
            sub = menu.addMenu(title)
            sub.setToolTipsVisible(True)
            group = QActionGroup(sub)
            for value, label in choices:
                action = sub.addAction(label)
                action.setCheckable(True)
                action.setChecked(value == current)
                action.setToolTip(tip)
                action.triggered.connect(lambda _=False, v=value: slot(v))
                group.addAction(action)

        radio_submenu("Screen &refresh",
                      [(fps, f"{fps} per second") for fps in REFRESH_RATES],
                      self.prefs.refresh_fps, self.set_refresh_rate,
                      "How often the playhead and meters move. Lower: less CPU, less smooth")
        menu.addSeparator()
        for text, attr in (("&Level meters", "level_meters"), ("Loudness (&LUFS)", "loudness_meters")):
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(getattr(self.prefs, attr))
            action.toggled.connect(lambda on, a=attr: self.set_meter_option(a, on))
        menu.addSeparator()
        colours = menu.addMenu("&Colours")
        for text, attr, tip in (("&Random colour for new tracks", "random_track_colors",
                                 "Every new track gets a colour of its own, unlike its neighbours'"),
                                ("&Clips take their track's colour", "clips_follow_track",
                                 "Clips without a colour of their own are drawn in their track's colour")):
            action = colours.addAction(text)
            action.setCheckable(True)
            action.setChecked(getattr(self.prefs, attr))
            action.setToolTip(tip)
            action.toggled.connect(lambda on, a=attr: self.set_colour_option(a, on))
        colours.setToolTipsVisible(True)
        colours.addSeparator()
        colours.addAction("&Default clip and row colours…").triggered.connect(
            lambda: self.customize_skin("Tracks and clips"))
        self.apply_colour_options()
        self.skin_menu = menu.addMenu("&Skin")
        self.skin_menu.setToolTipsVisible(True)
        self.skin_menu.aboutToShow.connect(self.fill_skin_menu)
        self.fill_skin_menu()
        self.rounded_action = menu.addAction("Rounded c&orners")
        self.rounded_action.setCheckable(True)
        self.rounded_action.setChecked(self.prefs.rounded_corners)
        self.rounded_action.setToolTip("Off: every corner of buttons, fields, sliders and scroll bars is square")
        self.rounded_action.toggled.connect(self.set_rounded_corners)
        menu.setToolTipsVisible(True)

    # --- skins (skin.py) -----------------------------------------------------------------

    def fill_skin_menu(self):
        """The skins there are (looked up each time the menu opens), and what to do with them."""
        menu = self.skin_menu
        menu.clear()
        group = QActionGroup(menu)
        for skin in skins.library():
            action = menu.addAction(skin.name if skin.builtin else f"{skin.name}  (yours)")
            action.setCheckable(True)
            action.setChecked(skin.name == self.skin.name and skin.builtin == self.skin.builtin)
            action.setToolTip(skin.path or "")
            action.triggered.connect(lambda _=False, s=skin: self.choose_skin(s))
            group.addAction(action)
        menu.addSeparator()
        menu.addAction("&Customize this skin…").triggered.connect(self.customize_skin)
        menu.addAction("&Import skin…").triggered.connect(self.import_skin)
        menu.addAction("&Export this skin…").triggered.connect(self.export_skin)
        menu.addAction("&Open the skins folder").triggered.connect(self.open_skins_folder)

    def choose_skin(self, skin):
        """Use `skin` from now on (remembered)."""
        self.skin = skin
        self.prefs.skin = skin.name
        self.prefs.save()
        self.show_skin(skin)

    def show_skin(self, skin):
        """Put a skin on the whole program at once (also while editing one)."""
        skins.apply(QApplication.instance(), skin)
        self.restyle()

    def restyle(self):
        """Rebuild or repaint what took its colours from the old skin."""
        self.panel.rebuild()
        self.master.restyle()
        self.overview_caption.setStyleSheet(f"color: {theme.RULER_TEXT.name()}; background: {theme.OVERVIEW_BG.name()};"
                                            f"border-bottom: 1px solid {theme.OVERVIEW_EDGE.name()};")
        self.overview._content_key = None  # (its cached image used the old colours)
        self.overview.refresh()
        for widget in QApplication.allWidgets():
            widget.update()

    def set_rounded_corners(self, on):
        """Options → Rounded corners: square corners everywhere when off (remembered)."""
        self.prefs.rounded_corners = on
        self.prefs.save()
        theme.rounded = on
        self.show_skin(self.skin)

    def overview_resized(self, height):
        """The overview's bottom edge was dragged: the caption beside it follows; remembered."""
        self.overview_caption.setFixedHeight(height)
        self.prefs.overview_h = height
        self.prefs.save()

    def set_colour_option(self, attr, on):
        setattr(self.prefs, attr, on)
        self.prefs.save()
        self.apply_colour_options()

    def apply_colour_options(self):
        """Options → Colours: random colours for new tracks; clips taking their track's colour."""
        colors.follow_track = self.prefs.clips_follow_track
        self.timeline.random_track_colors = self.prefs.random_track_colors
        self.restyle()

    def customize_skin(self, tab=None):
        """The skin editor (Options → Skin → Customize, or Colours → Default colours: `tab`)."""
        SkinDialog(self, self.skin, self.show_skin, self.choose_skin, tab).exec()
        self.show_skin(self.skin)  # what was saved, or back to the skin as it was

    def import_skin(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import skin", "", "Tiny DAW skin (*.json)")
        if not path:
            return
        try:
            skin, problems = skins.read(path)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Cannot import skin", f"{path}\n\n{e}")
            return
        target = os.path.join(skins.USER_DIR, skins.file_name(skin.name))
        if os.path.exists(target) and QMessageBox.question(
                self, "Skin exists", f"You already have a skin called {skin.name}. Replace it?") != QMessageBox.Yes:
            return
        skin.path, skin.builtin = target, False
        skins.write(skin, target)
        self.choose_skin(skin)
        if problems:
            QMessageBox.information(self, "Skin imported", f"{skin.name} is in use. These parts of it were left "
                                    "out (they keep the default):\n\n" + "\n".join(problems[:20]))

    def export_skin(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export skin", skins.file_name(self.skin.name),
                                              "Tiny DAW skin (*.json)")
        if path:
            skins.write(self.skin, path if path.lower().endswith(".json") else path + ".json")
            self.statusBar().showMessage(f"Skin saved as {path}", 5000)

    def open_skins_folder(self):
        os.makedirs(skins.USER_DIR, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(skins.USER_DIR))

    def choose_vst2_folders(self):
        """Options → VST2 plugin folders: where VST2 plugins are looked for (remembered)."""
        dialog = PluginFoldersDialog(self, self.prefs.vst2_list())
        if not dialog.exec():
            return
        self.prefs.set_vst2_list(dialog.folders())
        self.prefs.save()
        found = [p for p in self.plugins.rescan().plugins if is_vst2(p.path)]
        self.statusBar().showMessage(f"{len(found)} VST2 plugin{'' if len(found) == 1 else 's'} found", 5000)

    def show_audio_properties(self):
        p = self.prefs
        dialog = AudioPropertiesDialog(self, p.driver_type, self.device, p.buffer_size, p.wasapi_exclusive, self.engine)
        if not dialog.exec():
            return
        choice = (dialog.driver_type, dialog.device, dialog.buffer_size, dialog.exclusive)
        if choice != (p.driver_type, self.device, p.buffer_size, p.wasapi_exclusive):
            self.set_audio_device(*choice)

    def set_audio_device(self, driver_type, device, buffer_size, exclusive=False):
        """Play through `device` (a device index of `driver_type`) with `buffer_size` frames
        (0 = driver default); `exclusive` is WASAPI exclusive mode. Remembered for next time.
        Takes effect by reopening the device, which stops playback."""
        self.device = device
        p = self.prefs
        p.driver_type, p.wasapi_exclusive = driver_type, exclusive
        p.device_name = dict(devices.outputs(driver_type)).get(device, "")
        p.buffer_size = self.engine.blocksize = buffer_size
        p.save()
        if device is not None:
            self.open_device()

    def set_buffer_size(self, frames):
        p = self.prefs
        self.set_audio_device(p.driver_type, self.device, frames, p.wasapi_exclusive)

    def set_refresh_rate(self, fps):
        self.prefs.refresh_fps = fps
        self.prefs.save()
        self.frame_timer.setInterval(round(1000 / fps))

    def set_meter_option(self, attr, on):
        setattr(self.prefs, attr, on)
        self.prefs.save()
        self.apply_meter_options()

    def apply_meter_options(self):
        """Switched-off meters are hidden, and the engine stops measuring for them."""
        levels, loudness = self.prefs.level_meters, self.prefs.loudness_meters
        self.engine.metering.levels_on, self.engine.metering.loudness_on = levels, loudness
        self.panel.set_meters_visible(levels, loudness)

    def connect_controls(self):
        t = self.transport
        t.add_file_action.triggered.connect(self.add_files)
        t.add_audio_track_action.triggered.connect(lambda: self.timeline.add_track(AUDIO))
        t.add_midi_track_action.triggered.connect(lambda: self.timeline.add_track(MIDI))
        t.play_action.triggered.connect(self.toggle_play)
        t.record_action.triggered.connect(self.recording.toggle)
        t.record_button.customContextMenuRequested.connect(self.recording.show_settings)
        t.start_action.triggered.connect(self.go_to_start)
        t.loop_action.toggled.connect(self.set_looping)
        self.timeline.loop_changed.connect(self.sync_loop)
        self.timeline.loop_range_chosen.connect(lambda: self.set_looping(True))
        self.timeline.tracks_changed.connect(self.check_fx_windows)
        self.timeline.edit_notes.connect(self.open_midi_editor)
        t.bpm_box.valueChanged.connect(lambda v: self.set_timing(bpm=v))
        t.signature_box.valueChanged.connect(lambda v: self.set_timing(beats_per_bar=v))
        t.beats_action.toggled.connect(lambda on: self.set_timing(beats=on))
        self.tap_tempo = TapTempo()
        t.tap_button.pressed.connect(self.tap)  # on press, not release: the moment of the tap counts
        t.metronome_button.setChecked(self.prefs.metronome)
        t.metronome_button.toggled.connect(self.set_metronome_on)
        t.metronome_button.customContextMenuRequested.connect(self.show_metronome_settings)
        self.apply_metronome()
        t.thirds_action.toggled.connect(lambda on: self.set_timing(thirds=on))
        for box in (t.bpm_box, t.signature_box):  # after Enter, keys (Space, S, …) go back to the timeline
            box.lineEdit().returnPressed.connect(self.timeline.setFocus)
        t.show_timing(self.project.timing)

    def tap(self):
        """Tap tempo: a tap from the Tap button (or T)."""
        bpm = self.tap_tempo.tap(time.monotonic())
        taps = len(self.tap_tempo.taps)
        if bpm is None:
            self.statusBar().showMessage("Tap tempo: keep tapping in time…", 3000)
            return
        self.transport.bpm_box.setValue(bpm)  # sets the project's tempo
        self.statusBar().showMessage(f"Tap tempo: {bpm:g} BPM from {taps} taps", 3000)

    # --- metronome --------------------------------------------------------------------------

    def set_metronome_on(self, on):
        self.prefs.metronome = on
        self.prefs.save()
        self.apply_metronome()

    def apply_metronome(self, settings=None):
        """Give the engine the metronome's sounds while it is on."""
        self.engine.set_metronome(self.metronome_settings if self.prefs.metronome else None)

    def show_metronome_settings(self):
        MetronomeDialog(self, self.metronome_settings, self.apply_metronome, self.test_click).exec()

    def test_click(self, click):
        self.engine.preview(np.repeat(render_click(click, self.engine.sr)[:, None], 2, axis=1))

    def set_looping(self, on):
        """Loop on or off. Turned on with a time range selected, the loop becomes that range; with no
        loop range yet, the whole arrangement (or 4 bars of an empty project)."""
        t, tl = self.project.timing, self.timeline
        if on:
            if tl.selection.range is not None:
                tl.set_loop(tl.selection.range.start, tl.selection.range.end)
            elif t.loop_range() is None:
                bars = round(4 * t.beats_per_bar * t.beat_s * self.engine.sr)
                tl.set_loop(0, self.project.end_frame() or bars)
        t.looping = on
        self.transport.show_timing(t)
        self.sync_loop()
        tl.update()

    def sync_loop(self):
        """Hand the loop range to the engine while looping (in frames, at its sample rate)."""
        t, sr = self.project.timing, self.engine.sr
        span = t.loop_range() if t.looping else None
        loop = (round(span[0] * sr), round(span[1] * sr)) if span else None
        if loop != self.engine.loop:
            self.engine.loop = loop

    def set_timing(self, **changes):
        """Change the tempo, time signature or grid view. Clips keep their place in time."""
        for name, value in changes.items():
            setattr(self.project.timing, name, value)
        self.transport.show_timing(self.project.timing)
        self.timeline.update()

    def start_timers(self):
        self.frame_timer = QTimer(self)
        self.frame_timer.timeout.connect(self.on_frame)
        self.frame_timer.start(round(1000 / self.prefs.refresh_fps))
        self.cpu_timer = QTimer(self)
        self.cpu_timer.timeout.connect(self.on_cpu_timer)
        self.cpu_timer.start(config.CPU_REFRESH_MS)

    def open_first_device(self):
        """Open the driver type and device used last time (Options → Audio properties). If that
        driver type is gone, use the first available one (ASIO first); if the device is gone,
        that driver type's default device."""
        p = self.prefs
        available = devices.driver_types()
        if p.driver_type not in available:
            p.driver_type = available[0] if available else p.driver_type
        by_name = {name: index for index, name in devices.outputs(p.driver_type)}
        self.device = by_name.get(p.device_name, devices.default_output(p.driver_type))
        if self.device is not None:
            self.open_device()
        else:
            self.statusBar().showMessage("No audio output device found.")

    # --- actions ------------------------------------------------------------------------

    def open_device(self):
        self.stop()
        try:
            name, sr, latency = self.engine.open(self.device, exclusive=self.prefs.wasapi_exclusive)
            kind = devices.label(self.engine.driver_type) + (" exclusive" if self.engine.exclusive else "")
            buffer = "driver buffer" if not self.engine.blocksize else f"{self.engine.blocksize}-frame buffer"
            self.statusBar().showMessage(f"{kind}: {name}  ·  {self.engine.channels} outputs  ·  {sr} Hz  ·  "
                                         f"{buffer}  ·  {latency * 1000:.1f} ms output latency")
        except Exception as e:
            self.statusBar().showMessage(f"Could not open the audio device: {e}  (Options → Audio properties…)")
        self.panel.set_channels(self.engine.channels)
        self.apply_meter_options()  # the engine's metering was recreated for the device
        self.timeline.relayout()

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add audio or MIDI", "", config.FILE_FILTER)
        for path in paths:
            self.timeline.add_file(path, self.timeline.cursor)

    def go_to_start(self):
        """Both cursors to 0 s (Home)."""
        self.timeline.set_cursor(0)
        self.timeline.seek(0)

    def toggle_play(self):
        if not self.engine.stream:
            return
        if self.recording.active:  # stops recording, keeping the take
            self.recording.toggle()
        elif self.engine.playing:
            self.engine.stop()  # the playback cursor stays where it stopped
        else:
            self.timeline.seek(self.timeline.cursor)  # play always starts from the selection cursor
            self.engine.play()
        self.transport.set_playing(self.engine.playing)

    # --- plugins ---------------------------------------------------------------------------

    def open_track_plugins(self, track):
        if track.frozen:
            QMessageBox.information(self, "Track frozen", f"{track.name} is frozen, so its plugins are locked.\n"
                                    "Unfreeze it first (right-click its row).")
            return
        self.show_fx_window(track, lambda: PluginsDialog(self, self.plugins, f"Effects: {track.name}", track,
                                                         "effects", self.panel.rebuild))

    def open_master_plugins(self):
        self.show_fx_window(self.project, lambda: PluginsDialog(
            self, self.plugins, "Effects: Master (outputs 1/2)", self.project, "master_effects",
            lambda: self.master.refresh_fx(self.project.master_effects)))

    def fill_instrument_menu(self, menu, track):
        """A MIDI track's instruments to choose from (instrument_menu.py)."""
        fill_instrument_menu(menu, self, self.plugins, track, lambda: self.instrument_chosen(track))

    def instrument_chosen(self, track):
        """After a track's instrument changed: its row shows it; its instrument patch panel's window closes
        if the panel isn't played any more."""
        self.panel.rebuild()
        window = self.fx_windows.get(("rack", id(track)))
        if window is not None and not track.uses_rack:
            window.close()

    def open_instrument_panel(self, track):
        """A MIDI track's instrument patch panel (several instruments, each with its own volume)."""
        if track.frozen:
            QMessageBox.information(self, "Track frozen", f"{track.name} is frozen, so its plugins are locked.\n"
                                    "Unfreeze it first (right-click its row).")
            return
        self.show_fx_window(track, lambda: InstrumentPanelDialog(self, self.plugins, f"Instruments: {track.name}",
                                                                 track, self.panel.rebuild), key=("rack", id(track)))

    def edit_instrument(self, track):
        """Open a MIDI track's instrument's own window, to configure it."""
        edit_instrument(self, self.plugins, track)

    def show_fx_window(self, owner, make, key=None):
        """Show the plugins window of a track (or the master: owner = the project), one per owner (and
        `key`: a track's instrument patch panel has its own). It doesn't block: the project can be
        played and edited while it is open."""
        key = id(owner) if key is None else key
        window = self.fx_windows.get(key)
        if window is None:
            window = self.fx_windows[key] = make()
            # closed (Close, Esc, or by the program): forget it at once (it is deleted a moment later)
            window.finished.connect(lambda _=None, key=key: self.fx_windows.pop(key, None))
        window.show()
        window.raise_()
        window.activateWindow()

    def check_fx_windows(self):
        """After tracks changed: close the effects (and instrument patch panel) windows of tracks that are
        gone or frozen (or not playing their panel); the others show their plugins afresh."""
        self.project.forget_gone_tracks()  # (sidechains listening to a removed track)
        for window in list(self.fx_windows.values()):
            owner = window.owner
            if owner is self.project:
                window.refresh()
            elif owner not in self.project.tracks or owner.frozen or (window.INSTRUMENTS and not owner.uses_rack):
                window.close()
            else:
                window.refresh()

    def close_fx_windows(self):
        for window in list(self.fx_windows.values()) + list(self.midi_editors.values()):
            window.close()

    def open_midi_editor(self, clip):
        """The MIDI editor of a clip (one per clip); like the FX windows it doesn't block anything."""
        editor = self.midi_editors.get(id(clip))
        if editor is None:
            editor = self.midi_editors[id(clip)] = MidiEditor(self, clip, self.project, self.engine, self.notes_edited,
                                                                     self.timeline)
            editor.closed.connect(lambda key=id(clip): self.midi_editors.pop(key, None))
        editor.show()
        editor.raise_()
        editor.activateWindow()

    def notes_edited(self):
        self.timeline.relayout()  # the clip may have grown

    def check_midi_editors(self):
        """Close the editors of clips that are gone (deleted, split, cut) or locked (frozen track)."""
        for key, editor in list(self.midi_editors.items()):
            clip = editor.clip
            if clip not in self.project.clips or clip.track.frozen:
                self.midi_editors.pop(key, None)
                editor.close()
            else:
                editor.update_title()

    def restore_plugins(self):
        """Load the plugins of a just-opened project; say which could not be loaded."""
        slots = list(self.project.plugin_slots())
        failed = self.plugins.restore(slots) if slots else []
        self.panel.rebuild()
        self.master.refresh_fx(self.project.master_effects)
        if failed:
            QMessageBox.warning(self, "Plugins missing",
                                "These plugins could not be loaded, so they are silent for now:\n\n" + "\n".join(failed) +
                                "\n\nTheir settings are kept: saving the project does not lose them.")

    # --- projects -------------------------------------------------------------------------

    def snapshot(self):
        """The project without plugin settings (see project_io.describe_slot); a plugin window being
        opened counts as a change instead (plugins.edited)."""
        return project_io.describe(self.project, self.engine.sr, self.master.settings(), states=False)

    def mark_saved(self):
        """Remember the project as it is now, to tell later whether it has unsaved changes."""
        self.saved_snapshot = self.snapshot()
        self.plugins.edited = False
        name = os.path.basename(self.project_path) if self.project_path else "Untitled"
        self.setWindowTitle(f"{name} — Tiny DAW")

    def has_unsaved_changes(self):
        return self.plugins.edited or self.snapshot() != self.saved_snapshot

    def confirm_discard(self):
        """True if it is fine to replace or close the current project (saving it first if asked)."""
        if not self.has_unsaved_changes():
            return True
        answer = QMessageBox.question(self, "Unsaved changes", "Save the changes to the current project?",
                                      QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if answer == QMessageBox.Save:
            return self.save_project()
        return answer == QMessageBox.Discard

    def stop(self):
        if self.recording.active:  # (keeping the take)
            self.recording.toggle()
        self.engine.stop()
        self.transport.set_playing(False)

    def reset_view(self):
        """After the project was replaced: empty selection, both cursors at 0, everything redrawn."""
        self.timeline.selection.clear()
        self.timeline.set_cursor(0)
        self.timeline.seek(0)
        self.timeline.relayout()
        self.timeline.tracks_changed.emit()
        self.transport.show_timing(self.project.timing)

    def new_project(self):
        if not self.confirm_discard():
            return
        self.stop()
        self.close_fx_windows()  # they belong to the project being replaced
        self.plugins.close_editor()
        self.project.clear()
        self.master.apply({})
        self.master.refresh_fx(())
        self.project_path = None
        self.reset_view()
        self.mark_saved()

    def open_project(self):
        if not self.confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "Tiny DAW project (*.json)")
        if path:
            self.open_project_file(path)

    def open_project_file(self, path):
        self.stop()
        self.close_fx_windows()  # they belong to the project being replaced
        self.plugins.close_editor()
        try:
            master = project_io.load_project(path, self.project, self.engine.sr)
        except Exception as e:
            QMessageBox.warning(self, "Cannot open project", f"{path}\n\n{e}")
            return False
        self.master.apply(master)
        self.project_path = path
        self.reset_view()
        self.restore_plugins()
        self.mark_saved()
        self.statusBar().showMessage(f"Opened {path}", 5000)
        return True

    def save_project(self):
        """Save to the current file (asks for one the first time). True if saved."""
        if not self.project_path:
            return self.save_project_as()
        return self.save_project_file(self.project_path)

    def save_project_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save project", self.project_path or "Untitled.json",
                                              "Tiny DAW project (*.json)")
        if not path:
            return False
        if not path.lower().endswith(".json"):
            path += ".json"
        return self.save_project_file(path)

    def save_project_file(self, path):
        if self.engine.stream is None:  # with a device open, the audio thread keeps the plugins processing (in sync)
            for slot in self.project.plugin_slots():
                sync_state(slot, self.engine.sr)
        try:
            project_io.save_project(path, self.project, self.engine.sr, self.master.settings())
        except Exception as e:
            QMessageBox.warning(self, "Cannot save project", f"{path}\n\n{e}")
            return False
        self.project_path = path
        self.mark_saved()
        self.statusBar().showMessage(f"Saved {path} (audio in {project_io.audio_folder(path)})", 5000)
        return True

    # --- periodic updates -------------------------------------------------------------

    def on_frame(self):
        """Playhead, time display, level meters and follow-scrolling."""
        tl = self.timeline
        self.sync_loop()  # (also after opening a project or changing the sample rate)
        automation.resolve(self.project)  # (parameters of plugins just loaded)
        self.plugins.idle(self.project.plugin_slots())  # (values to their windows, latency changes)
        heard = self.engine.heard_pos()  # (behind the engine's position while plugins hold sound back)
        tl.set_playhead(heard)
        if tl.recording and tl.recording[0] in tl.track_tops():  # the take grows on its lane
            track = tl.recording[0]
            tl.update(QRect(0, tl.track_tops()[track], tl.width(), track.height))
        self.overview.refresh()
        shown = heard if self.engine.playing else tl.cursor  # stopped: where Play will start
        self.transport.set_time(shown / self.engine.sr, self.project.timing)
        master_peaks, track_peaks = self.engine.metering.take_peaks()  # also queues audio for LUFS
        if self.prefs.level_meters:
            self.master.meter.push(master_peaks)
            for track, meter in self.panel.meters.items():
                meter.push(track_peaks.get(track))  # None (silent) lets the meter fall back
            for group, meter in self.panel.group_meters.items():  # a group's tracks together: the loudest
                peaks = [track_peaks[t] for t in self.project.members(group) if t in track_peaks]
                meter.push(np.max(peaks, axis=0) if peaks else None)
        if self.engine.playing:
            self.follow_playhead()

    def follow_playhead(self):
        tl = self.timeline
        hbar, view_w = self.scroll.horizontalScrollBar(), self.scroll.viewport().width()
        if tl.playhead_x > tl.width() - view_w:
            tl.relayout()  # grow the timeline as playback runs past the end
        if self.transport.follow_action.isChecked():
            # Once the playhead reaches the middle of the view, the view moves with it and keeps it
            # centred; if it is off-screen (e.g. after a seek) the view jumps to centre it.
            left, centre = hbar.value(), view_w // 2
            if tl.playhead_x > left + centre or tl.playhead_x < left:
                hbar.setValue(tl.playhead_x - centre)

    def on_cpu_timer(self):
        """CPU monitor and LUFS readouts, a few times a second (and MIDI editors whose clip went)."""
        if self.midi_editors:
            self.check_midi_editors()
        load = self.engine.cpu.read(config.CPU_AVERAGE_SECONDS) if self.engine.stream else None
        self.cpu_monitor.show_load(load, self.engine.cpu.late)
        if not self.prefs.loudness_meters:
            return
        metering = self.engine.metering
        metering.update_loudness()
        self.master.loudness.show_values(*metering.read(MASTER))
        for track, display in self.panel.loudness.items():
            display.show_values(*metering.read(track))

    # --- Qt events -----------------------------------------------------------------------

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.timeline.relayout()

    def closeEvent(self, e):
        if not self.confirm_discard():
            e.ignore()
            return
        self.plugins.close_editor()
        self.plugins.stop_finding_kinds()
        self.engine.close()
        super().closeEvent(e)
