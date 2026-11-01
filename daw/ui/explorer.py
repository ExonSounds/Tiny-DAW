"""The explorer on the left: browse your sample folders or search them by name, listen to a sample by
clicking it, then drag samples onto the timeline (or double-click one to add it at the selection cursor).

The folders are the ones you add with "+" (remembered between runs). Browsing shows one folder at
a time as a tree, with only audio and MIDI files. Searching looks through all folders and their
subfolders for files whose names contain every word typed. The list of files to search is built
in a background thread (index_folders), when the explorer opens and whenever the folders change
or you press ⟳, so typing stays instant even for big libraries.

Clicking a sample, or moving to it with the arrow keys, shows its waveform at the top
(sample_waveform.py; loaded in a background thread) and, with 🔊 Preview on (remembered), plays it
through the engine (Engine.preview, outputs 1/2): once, or over and over with ⟲ Loop on (remembered);
■ stops it. Click the waveform to play from that point.
"""
import os
import threading
import time

from PySide6.QtCore import QDir, QMimeData, QObject, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFileSystemModel, QHBoxLayout, QLabel,
                               QLineEdit, QStackedWidget, QToolButton, QTreeView, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import config
from ..audio.file_io import AUDIO_EXTENSIONS, MIDI_EXTENSIONS, is_sample, load_playable
from . import preferences
from .sample_waveform import SampleWaveform

FOLDERS_KEY = "explorer/folders"
PREVIEW_KEY = "explorer/preview"
LOOP_KEY = "explorer/loop"
REPEAT_GAP_S = 0.3  # a press and its click both ask to preview the same sample: play it once


def load_folders():
    stored = QSettings(*preferences.SETTINGS).value(FOLDERS_KEY)
    if stored is None:  # first run: the Music folder, if there is one
        music = os.path.join(os.path.expanduser("~"), "Music")
        return [music] if os.path.isdir(music) else []
    if isinstance(stored, str):  # a one-item list comes back from the registry as a plain string
        stored = [stored]
    return [f for f in stored if os.path.isdir(f)]


def save_folders(folders):
    QSettings(*preferences.SETTINGS).setValue(FOLDERS_KEY, list(folders))


def index_folders(folders, limit=config.EXPLORER_MAX_FILES):
    """[(lower-case name, path)] of every sample in `folders` and their subfolders (at most `limit`)."""
    found = []
    for folder in folders:
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            found += [(f.lower(), os.path.join(root, f)) for f in files if is_sample(f)]
            if len(found) >= limit:
                return found[:limit]
    return found


def search(index, text, limit=config.EXPLORER_MAX_RESULTS):
    """Paths whose file name contains every word of `text` (any case), shortest names first."""
    words = text.lower().split()
    hits = [(name, path) for name, path in index if all(w in name for w in words)]
    hits.sort(key=lambda hit: (len(hit[0]), hit[0]))
    return [path for _, path in hits[:limit]], len(hits)


def file_urls(paths):
    """Drag data for files: the same as dragging them from Windows Explorer."""
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(p) for p in paths])
    return data


class ResultList(QTreeWidget):
    """Search results (name, folder); drags out as files."""

    def __init__(self):
        super().__init__()
        self.setColumnCount(2)
        self.setHeaderLabels(["Name", "Folder"])
        self.setRootIsDecorated(False)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)

    def mimeData(self, items):
        return file_urls([item.data(0, Qt.UserRole) for item in items])


class _Indexed(QObject):
    done = Signal(int, list)  # (generation, index): from the indexing thread to the GUI thread
    loaded = Signal(int, str, object)  # (generation, path, audio or None): a sample loaded in a thread


class Explorer(QWidget):
    def __init__(self, add_file, engine):
        """add_file(path): add a sample at the selection cursor (double-click). engine plays the previews."""
        super().__init__()
        self.add_file, self.engine = add_file, engine
        self._preview_generation, self._last_preview = 0, (None, 0.0)
        self.shown = (None, None, None)  # (path, sample rate, audio) of the sample in the waveform
        self.voice = None  # (the engine's PreviewVoice, first frame it plays) while previewing
        self.waveform = SampleWaveform()
        self.waveform.clicked.connect(self.play_from)
        self.position_timer = QTimer(self, interval=30)  # moves the waveform's line while previewing
        self.position_timer.timeout.connect(self.show_position)
        self.folders = load_folders()
        self.index, self.indexing = [], False
        self._generation = 0
        self._indexed = _Indexed()
        self._indexed.done.connect(self.on_indexed)
        self._indexed.loaded.connect(self.on_preview_loaded)

        self.folder_box = QComboBox()
        self.folder_box.setToolTip("Folder to browse")
        self.folder_box.currentIndexChanged.connect(self.show_folder)
        add = self.small_button("+", "Add a folder with samples", self.add_folder)
        self.remove_button = self.small_button("−", "Remove this folder from the explorer (nothing is deleted)",
                                               self.remove_folder)
        rescan = self.small_button("⟳", "Look for new samples in the folders", self.reindex)
        top = QHBoxLayout()
        top.setSpacing(3)
        top.addWidget(self.folder_box, 1)
        for button in (add, self.remove_button, rescan):
            top.addWidget(button)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search samples…")
        self.search_box.setClearButtonEnabled(True)
        self.search_timer = QTimer(self, singleShot=True, interval=150)  # search once typing pauses
        self.search_timer.timeout.connect(self.run_search)
        self.search_box.textChanged.connect(lambda: self.search_timer.start())

        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.AllDirs | QDir.Files | QDir.NoDotAndDotDot)
        self.model.setNameFilters([f"*{ext}" for ext in AUDIO_EXTENSIONS + MIDI_EXTENSIONS])
        self.model.setNameFilterDisables(False)  # hide other files instead of greying them
        self.tree = QTreeView()
        self.tree.setModel(self.model)
        for column in range(1, self.model.columnCount()):
            self.tree.hideColumn(column)  # name only
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QAbstractItemView.DragOnly)
        self.tree.doubleClicked.connect(lambda index: self.open_path(self.model.filePath(index)))
        self.tree.clicked.connect(lambda index: self.preview(self.model.filePath(index)))
        self.tree.selectionModel().currentChanged.connect(lambda index: self.preview(self.model.filePath(index)))
        self.results = ResultList()
        self.results.itemDoubleClicked.connect(lambda item: self.open_path(item.data(0, Qt.UserRole)))
        self.results.itemClicked.connect(lambda item: self.preview(item.data(0, Qt.UserRole)))
        self.results.currentItemChanged.connect(lambda item: item and self.preview(item.data(0, Qt.UserRole)))
        self.stack = QStackedWidget()
        self.stack.addWidget(self.tree)
        self.stack.addWidget(self.results)
        self.status = QLabel()
        self.status.setWordWrap(True)

        self.preview_button = QToolButton()
        self.preview_button.setText("🔊 Preview")
        self.preview_button.setCheckable(True)
        self.preview_button.setChecked(QSettings(*preferences.SETTINGS).value(PREVIEW_KEY, True, type=bool))
        self.preview_button.setToolTip("Play a sample when you click it (or move to it with the arrow keys)")
        self.preview_button.setProperty("accent", True)  # lit in the skin's accent colour when on
        self.preview_button.toggled.connect(self.set_preview_on)
        stop = self.small_button("■", "Stop the preview", self.stop_preview)
        self.loop_button = QToolButton()
        self.loop_button.setText("⟲ Loop")
        self.loop_button.setCheckable(True)
        self.loop_button.setChecked(QSettings(*preferences.SETTINGS).value(LOOP_KEY, False, type=bool))
        self.loop_button.setToolTip("Play previews over and over, until ■ or another sample")
        self.loop_button.setProperty("accent", True)
        self.loop_button.toggled.connect(self.set_loop_on)
        preview_row = QHBoxLayout()
        preview_row.addWidget(self.preview_button)
        preview_row.addWidget(self.loop_button)
        preview_row.addWidget(stop)
        preview_row.addStretch(1)

        col = QVBoxLayout(self)
        col.setContentsMargins(6, 6, 6, 6)
        col.setSpacing(5)
        col.addWidget(self.waveform)
        col.addLayout(top)
        col.addWidget(self.search_box)
        col.addLayout(preview_row)
        col.addWidget(self.stack, 1)
        col.addWidget(self.status)
        hint = QLabel("Click a sample to hear it. Drag samples onto a track, or double-click to add it at the cursor.")
        hint.setWordWrap(True)
        hint.setEnabled(False)  # dimmed
        col.addWidget(hint)
        self.fill_folder_box()
        self.reindex()

    @staticmethod
    def small_button(text, tip, slot):
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tip)
        button.setFixedSize(24, 24)
        button.clicked.connect(slot)
        return button

    # --- folders ------------------------------------------------------------------------

    def fill_folder_box(self, current=None):
        self.folder_box.blockSignals(True)
        self.folder_box.clear()
        for folder in self.folders:
            self.folder_box.addItem(os.path.basename(folder.rstrip("\\/")) or folder, folder)
            self.folder_box.setItemData(self.folder_box.count() - 1, folder, Qt.ToolTipRole)
        if not self.folders:
            self.folder_box.addItem("No folders yet: add one with +")
        self.folder_box.setCurrentIndex(self.folders.index(current) if current in self.folders else 0)
        self.folder_box.blockSignals(False)
        self.remove_button.setEnabled(bool(self.folders))
        self.show_folder()

    def show_folder(self):
        folder = self.folder_box.currentData()
        if folder:
            self.tree.setRootIndex(self.model.setRootPath(folder))
            self.model.sort(0)
        self.tree.setVisible(bool(folder))

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Add a folder with samples")
        if not folder:
            return
        folder = os.path.normpath(folder)
        if folder not in self.folders:
            self.folders.append(folder)
            save_folders(self.folders)
            self.reindex()
        self.fill_folder_box(folder)

    def remove_folder(self):
        folder = self.folder_box.currentData()
        if folder in self.folders:
            self.folders.remove(folder)
            save_folders(self.folders)
            self.fill_folder_box()
            self.reindex()

    # --- searching ----------------------------------------------------------------------

    def reindex(self):
        """List every sample in the folders, in a background thread; a newer request wins."""
        self._generation += 1
        generation, folders = self._generation, list(self.folders)
        self.indexing = True
        self.show_status()
        threading.Thread(target=lambda: self._indexed.done.emit(generation, index_folders(folders)),
                         daemon=True, name="explorer-index").start()

    def on_indexed(self, generation, index):
        if generation != self._generation:
            return  # the folders changed meanwhile; a newer index is on its way
        self.index, self.indexing = index, False
        self.run_search()

    def run_search(self):
        text = self.search_box.text().strip()
        self.stack.setCurrentWidget(self.results if text else self.tree)
        self.results.clear()
        if text:
            paths, total = search(self.index, text)
            for path in paths:
                folder = os.path.dirname(path)
                root = next((f for f in self.folders
                             if os.path.normcase(path).startswith(os.path.normcase(os.path.join(f, "")))), "")
                item = QTreeWidgetItem([os.path.basename(path), os.path.relpath(folder, root) if root else folder])
                item.setData(0, Qt.UserRole, path)
                item.setToolTip(0, path)
                item.setToolTip(1, folder)
                self.results.addTopLevelItem(item)
            self.results.resizeColumnToContents(0)
            self.show_status(len(paths), total)
        else:
            self.show_status()

    def show_status(self, shown=None, total=None):
        if self.indexing:
            text = "Looking through the folders…"
        elif not self.folders:
            text = "Add a folder with your samples (+)."
        elif shown is None:
            text = f"{len(self.index)} samples in {len(self.folders)} folder{'s' * (len(self.folders) != 1)}"
        elif total == 0:
            text = "No samples found."
        else:
            text = f"{total} found" + (f", showing the first {shown}" if shown < total else "")
        self.status.setText(text)

    # --- previewing ---------------------------------------------------------------------

    def set_preview_on(self, on):
        QSettings(*preferences.SETTINGS).setValue(PREVIEW_KEY, on)
        if not on:
            self.stop_preview()

    def set_loop_on(self, on):
        QSettings(*preferences.SETTINGS).setValue(LOOP_KEY, on)
        voice = self.engine.preview_voice
        if self.voice and voice is self.voice[0]:
            voice.loop = on  # the preview playing now follows at once

    def preview(self, path):
        """A sample was selected: show its waveform and, if previewing is on, play it once. Loading
        happens in a thread; the sample shown last is kept, so selecting it again plays at once."""
        if not (path and is_sample(path) and os.path.isfile(path)):
            return
        now = time.monotonic()
        if self._last_preview[0] == path and now - self._last_preview[1] < REPEAT_GAP_S:
            return  # (a press and its click both ask)
        self._last_preview = (path, now)
        self._preview_generation += 1
        generation, sr = self._preview_generation, self.engine.sr
        if self.shown[:2] == (path, sr):
            self.on_preview_loaded(generation, path, self.shown[2])
            return

        def load():
            try:
                audio = load_playable(path, sr)
            except Exception:
                audio = None  # unreadable: no waveform, stay silent
            self._indexed.loaded.emit(generation, path, audio)

        threading.Thread(target=load, daemon=True, name="explorer-preview").start()

    def on_preview_loaded(self, generation, path, audio):
        if generation != self._preview_generation:
            return  # only the latest one asked for
        if self.shown[0] != path or self.shown[2] is not audio:
            self.shown = (path, self.engine.sr, audio)
            self.waveform.set_audio(audio, os.path.basename(path), self.engine.sr)
        if audio is not None and self.preview_button.isChecked():
            self.play_from(0.0)

    def play_from(self, fraction):
        """Preview the shown sample from `fraction` (0…1) of the way in (also: click the waveform)."""
        audio = self.shown[2]
        if audio is None:
            return
        first = int(fraction * len(audio))
        self.engine.preview(audio, first, self.loop_button.isChecked())  # (a loop starts over from the beginning)
        self.voice = (self.engine.preview_voice, 0)
        self.position_timer.start()
        self.show_position()

    def show_position(self):
        """Move the waveform's line with the preview; hide it once the preview has ended."""
        voice, first = self.voice or (None, 0)
        audio = self.shown[2]
        if voice is None or self.engine.preview_voice is not voice or audio is None or not len(audio):
            self.voice = None
            self.position_timer.stop()
            self.waveform.set_position(None)
            return
        self.waveform.set_position(min(1.0, (first + voice.pos) / len(audio)))

    def stop_preview(self):
        self._preview_generation += 1  # a preview still loading won't start either
        self.engine.stop_preview()
        self.show_position()

    def open_path(self, path):
        if path and is_sample(path) and os.path.isfile(path):
            self.add_file(path)
