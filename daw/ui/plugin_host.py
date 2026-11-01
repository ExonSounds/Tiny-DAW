"""Loading VST3 plugins and opening their windows, for the GUI.

Both happen on the main thread (audio/plugins.py). Most plugins load in well under a second;
meanwhile a small "please wait" window shows, saying how long the plugin took last time. Other
plugins keep playing meanwhile. A plugin's own window (plugin_editor.py) doesn't block anything:
several can be open, and the program keeps running as usual. It opens titled with the plugin's
and track's names, over the main window or where it was last, fully on screen. While it is open,
the parameters changed in it are noted (automation.TweakWatcher) and it is photographed now and
then, for the patch panel in the effects window (plugin_pictures.py); photograph() takes a
picture without showing the window.

KindFinder finds out in the background whether the plugins not known yet are instruments or effects
(audio/plugin_probe.py, in a process of its own, one plugin after the other); the menus and lists
show what it found as soon as they open again.
"""
import json
import os
import sys
import tempfile
import time
import weakref

from PySide6.QtCore import QEventLoop, QObject, QProcess, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox, QProgressDialog

from ..audio import automation
from ..audio.plugin_probe import MARK
from ..audio.plugins import PluginChoiceNeeded, PluginInfo, PluginLibrary, set_state, usable
from ..model import PluginSlot
from . import plugin_pictures, plugin_windows
from .plugin_editor import EditorWindow

TWEAK_MS = 150  # how often to ask an open window which parameters were changed in it
PHOTO_EVERY_S = 1.5  # how often an open window is photographed (the last picture is kept when it closes)
PHOTO_WAIT_S = 0.8  # photograph(): how long the invisible window gets to draw itself
PHOTO_GIVE_UP_S = 4  # …and how long to keep trying while it shows nothing yet
PHOTO_TRY_MS = 100


PROBE_TIMEOUT_S = 30  # KindFinder: a plugin taking longer than this to be looked at is given up on
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # (holds daw/)


class PictureSignals(QObject):
    taken = Signal(object)  # a new picture of a plugin was kept: its PluginSlot


class KindFinder(QObject):
    """Finds the kinds of a library's plugins not known yet (library.unknown_files()) with
    audio/plugin_probe.py in a process of its own, and tells the library (learn / probe_failed). A plugin
    that crashes the process, or takes over PROBE_TIMEOUT_S, is given up on and the process goes on with
    the next ones. `progress(done, total)` after each plugin; `finished(found)` at the end."""
    progress = Signal(int, int)
    finished = Signal(int)  # plugins whose kind was found
    COMMAND = ("-m", "daw.audio.plugin_probe")  # (python's arguments, before the list file)

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.todo = library.unknown_files()
        self.total, self.found = len(self.todo), 0
        self.current, self.buffer, self.process = None, "", None
        self.list_file = os.path.join(tempfile.gettempdir(), f"tinydaw-probe-{os.getpid()}-{id(self)}.json")
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.too_long)

    def start(self):
        """Look at the next plugins (in a new process); finished() at once if there are none."""
        if not self.todo:
            self.stop_process()
            try:
                os.remove(self.list_file)
            except OSError:
                pass
            self.finished.emit(self.found)
            return
        with open(self.list_file, "w", encoding="utf-8") as f:
            json.dump(self.todo, f)
        self.process = QProcess(self)
        self.process.setWorkingDirectory(PROJECT_DIR)
        self.process.setProcessChannelMode(QProcess.SeparateChannels)
        self.process.setStandardErrorFile(QProcess.nullDevice())
        self.process.readyReadStandardOutput.connect(self.read)
        self.process.finished.connect(self.ended)
        self.current, self.buffer = None, ""
        self.process.start(sys.executable, [*self.COMMAND, self.list_file])

    def read(self):
        self.buffer += bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
        *lines, self.buffer = self.buffer.split("\n")
        for line in lines:
            if MARK in line:
                try:
                    self.note(json.loads(line.split(MARK, 1)[1]))
                except ValueError:
                    pass

    def note(self, entry):
        if "start" in entry:
            self.current = entry["start"]
            self.timer.start(PROBE_TIMEOUT_S * 1000)
            return
        path = entry.get("path")
        if path not in self.todo:
            return
        if entry.get("classes"):
            self.library.learn(path, [tuple(c) for c in entry["classes"]])
            self.found += 1
        else:
            self.library.probe_failed(path, entry.get("error") or "unknown")
        self.forget(path)

    def forget(self, path):
        self.todo.remove(path)
        self.current = None
        self.timer.stop()
        self.progress.emit(self.total - len(self.todo), self.total)

    def too_long(self):
        if self.current in self.todo:
            self.library.probe_failed(self.current, f"took over {PROBE_TIMEOUT_S} s")
            self.forget(self.current)
        self.stop_process()  # (ended() goes on with the rest)

    def ended(self, *_):
        """The process ended: done, crashed or stopped. A plugin it was looking at is to blame."""
        self.read()
        if self.current in self.todo:
            self.library.probe_failed(self.current, "crashed while being looked at")
            self.forget(self.current)
        process, self.process = self.process, None
        if process is not None:
            process.deleteLater()
        QTimer.singleShot(0, self.start)

    def stop_process(self):
        process = self.process
        if process is not None and process.state() != QProcess.NotRunning:
            process.kill()

    def cancel(self):
        """Stop at once (the library is being replaced, or the program closes)."""
        self.todo = []
        self.timer.stop()
        if self.process is not None:
            self.process.finished.disconnect(self.ended)
            self.stop_process()
            self.process.waitForFinished(2000)
            self.process = None


class PluginHost:
    def __init__(self, window, engine):
        self.window, self.engine = window, engine
        self._library = None
        self.editors = {}  # slot -> its open EditorWindow
        self.edited = False  # a plugin window was opened, so its settings may have changed since saving
        self.window_positions = weakref.WeakKeyDictionary()  # slot -> where its window was last (QPoint)
        self.pictures = PictureSignals()  # .taken: the effects windows show the new picture
        self.finder = None  # the KindFinder at work, if any

    @property
    def library(self):
        if self._library is None:  # listing the plugin folders is quick, but only done when needed
            self._library = PluginLibrary(vst2_folders=self.window.prefs.vst2_list())
        return self._library

    def rescan(self):
        """List the plugins afresh (the VST2 folders changed), and find out the kinds of new ones. Returns
        the library."""
        running = self.finder is not None
        self.stop_finding_kinds()
        self._library = None
        if running or self.library.unknown_files():
            self.find_kinds()
        return self.library

    def find_kinds(self):
        """Find out in the background whether the plugins not known yet are instruments or effects
        (KindFinder); the status bar says how it goes."""
        if self.finder is not None:
            return
        finder = self.finder = KindFinder(self.library, self.window)
        status = self.window.statusBar()
        if finder.total:
            status.showMessage(f"Finding out the types of {finder.total} plugin{'' if finder.total == 1 else 's'}…", 4000)
        finder.progress.connect(lambda done, total: status.showMessage(
            f"Finding out the types of plugins: {done} of {total}…", 4000))

        def finished(found):
            self.finder = None
            if finder.total:
                status.showMessage(f"Found the types of {found} of {finder.total} plugins", 5000)
            finder.deleteLater()
        finder.finished.connect(finished)
        finder.start()

    def stop_finding_kinds(self):
        if self.finder is not None:
            self.finder.cancel()
            self.finder.deleteLater()
            self.finder = None

    def busy(self, text):
        """A 'please wait' window, painted before the main thread blocks."""
        dialog = QProgressDialog(text, None, 0, 0, self.window)
        dialog.setWindowTitle("Loading plugin")
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.show()
        for _ in range(5):
            QApplication.processEvents()
        return dialog

    def expected(self, info):
        """How long loading this plugin should take, from last time."""
        seconds = self.library.last_load_s(info)
        if seconds is None:
            return "Loading it for the first time can take a little longer."
        return f"Last time it took {seconds:.1f} s." if seconds >= 0.95 else "Last time it took under a second."

    def load(self, info):
        """A new PluginSlot with the plugin loaded, or None (after saying why)."""
        dialog = self.busy(f"Loading {info.name}…\n{self.expected(info)}")
        try:
            instance = self.library.load(info, self.engine.sr)
        except PluginChoiceNeeded as choice:
            dialog.close()
            name, ok = QInputDialog.getItem(self.window, "Choose a plugin", f"{info.name} holds several plugins:",
                                            choice.names, 0, False)
            return self.load(PluginInfo(info.path, name, None, name)) if ok else None
        except Exception as e:
            dialog.close()
            QMessageBox.warning(self.window, "Cannot load plugin", f"{info.name}\n{info.path}\n\n{e}")
            return None
        dialog.close()
        kind = "instrument" if instance.is_instrument else "effect"
        return PluginSlot(info.path, info.name, kind, info.plugin_name, instance=instance)

    def restore(self, slots):
        """Load the plugins of an opened project, with their saved settings. Returns the names of
        those that could not be loaded (they keep their settings, so saving again loses nothing)."""
        failed = []
        dialog = self.busy("Loading plugins of the project…")
        for i, slot in enumerate(slots, 1):
            info = PluginInfo(slot.path, slot.name, slot.kind, slot.plugin_name)
            dialog.setLabelText(f"Loading plugins of the project ({i} of {len(slots)}): {slot.name}…\n"
                                f"{self.expected(info)}")
            QApplication.processEvents()
            try:
                instance = self.library.load(info, self.engine.sr)
                if slot.state:
                    set_state(instance, slot.state)
                slot.instance = instance  # only now can the audio thread use it
            except Exception:
                slot.instance = None
                failed.append(slot.name)
        dialog.close()
        return failed

    def idle(self, slots):
        """Give loaded plugins time on the main thread (call often): values on their way to their
        windows, latency changes, reloads they ask for. Plugins with a window open get it from there."""
        for slot in slots:
            if slot.instance is not None and slot not in self.editors:
                slot.instance.idle()

    # --- windows ---

    def edit(self, slot, where=None):
        """Open the plugin's own window (`where`: the track's name, for its title), or bring it forward."""
        if not usable(slot):
            return
        window = self.editors.get(slot)
        if window is not None:
            window.showNormal()
            window.raise_()
            window.activateWindow()
            return
        window = EditorWindow(self.window, slot, slot.name if where is None else f"{slot.name} — {where}")
        if not window.attach():
            window.deleteLater()
            QMessageBox.information(self.window, "No plugin window", f"{slot.name} has no window of its own.")
            return
        window.place(self.window, self.window_positions.get(slot))
        self.editors[slot] = window
        self.edited = True
        window.watcher = automation.TweakWatcher(slot, self.window.project)
        window.picture, window.photographed = None, time.monotonic()
        tick = QTimer(window, interval=TWEAK_MS)
        tick.timeout.connect(lambda: self.watch(window))
        tick.start()
        window.closed.connect(self.editor_closed)
        window.show()
        window.raise_()
        window.activateWindow()

    def watch(self, window):
        """Now and then while a window is open: the parameters changed in it, and its picture."""
        window.watcher.poll()
        if time.monotonic() - window.photographed >= PHOTO_EVERY_S and window.isVisible():
            window.photographed = time.monotonic()
            window.picture = plugin_pictures.from_capture(plugin_windows.capture(int(window.winId()))) or window.picture
        self.window_positions[window.slot] = window.pos()

    def editor_closed(self, slot):
        window = self.editors.pop(slot, None)
        if window is not None:
            self.window_positions[slot] = window.pos()
            self.keep_picture(slot, window.picture)

    def close_editor(self, slots=None):
        """Close the open plugin windows (of `slots`, when given)."""
        for slot, window in list(self.editors.items()):
            if slots is None or slot in slots:
                window.close()

    def keep_picture(self, slot, image):
        if image is not None and plugin_pictures.save(slot, image):
            self.pictures.taken.emit(slot)

    def photograph(self, slot):
        """Take a picture of the plugin's window without showing it: opened invisibly, photographed once it
        has drawn itself (about a second), closed again. Returns True if a picture was kept."""
        if not plugin_windows.available() or not usable(slot) or slot in self.editors:
            return False
        window = EditorWindow(self.window, slot, slot.name, invisible=True)
        if not window.attach():
            window.deleteLater()
            return False
        window.move(self.window.frameGeometry().topLeft())
        window.show()
        shown, taken, wait = time.monotonic(), [], QEventLoop()

        def look():
            if time.monotonic() - shown < PHOTO_WAIT_S:
                return
            image = plugin_pictures.from_capture(plugin_windows.capture(int(window.winId())))
            if image is not None or time.monotonic() - shown > PHOTO_GIVE_UP_S:
                taken.append(image)
                wait.quit()

        timer = QTimer(interval=PHOTO_TRY_MS)
        timer.timeout.connect(look)
        timer.start()
        wait.exec()
        timer.stop()
        window.close()
        image = taken[0] if taken else None
        self.keep_picture(slot, image)
        return image is not None
