"""File → Render selection… / Render project…: the mix, rendered to an audio file
(audio/render.py, render_mix), with a progress window.

Render selection renders exactly the selected time range (drag across the lanes); Render project
renders from 0 s to the end of the last clip, plus the tail while effects ring out. Both render
every audible track, as you hear it, not only the lanes the selection covers. Formats: WAV
(24-bit, 16-bit or 32-bit float) or FLAC (24-bit), at the engine's sample rate.

Rendering calls the plugins from the main thread, so playback stops and the engine leaves plugins
alone meanwhile (Engine.plugins_paused_while), as when freezing a track.
"""
import os

import soundfile as sf
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QProgressDialog

from ..audio.render import render_mix

FORMATS = (  # (file dialog filter, extension, soundfile format, subtype)
    ("WAV, 24-bit (*.wav)", ".wav", "WAV", "PCM_24"),
    ("WAV, 16-bit (*.wav)", ".wav", "WAV", "PCM_16"),
    ("WAV, 32-bit float (*.wav)", ".wav", "WAV", "FLOAT"),
    ("FLAC, 24-bit (*.flac)", ".flac", "FLAC", "PCM_24"),
)


class MixExporter:
    def __init__(self, window, engine, timeline):
        """window: MainWindow (stop(), statusBar(), project_path, master)."""
        self.window, self.engine, self.timeline = window, engine, timeline

    @property
    def project(self):
        return self.timeline.project

    def render_selection(self):
        r = self.timeline.selection.range
        if r is None:
            QMessageBox.information(self.window, "Render selection", "Select a time range first: drag across the "
                                    "lanes where it should start and end.\n\nOr use File → Render project… for "
                                    "the whole arrangement.")
            return None
        return self.export(r.start, r.end, tail=False, suffix="selection")

    def render_project(self):
        end = self.project.end_frame()
        if end == 0:
            QMessageBox.information(self.window, "Render project", "There is nothing to render yet: the project "
                                    "has no clips.")
            return None
        return self.export(0, end, tail=True, suffix="mix")

    def ask_file(self, suffix):
        """(path, soundfile format, subtype) chosen by the user, or None."""
        stem = os.path.splitext(os.path.basename(self.window.project_path or "Untitled"))[0]
        folder = os.path.dirname(self.window.project_path) if self.window.project_path else ""
        path, chosen = QFileDialog.getSaveFileName(self.window, "Render to file",
                                                   os.path.join(folder, f"{stem} ({suffix}).wav"),
                                                   ";;".join(f[0] for f in FORMATS), FORMATS[0][0])
        if not path:
            return None
        _, ext, file_format, subtype = next((f for f in FORMATS if f[0] == chosen), FORMATS[0])
        if not path.lower().endswith(ext):
            path = os.path.splitext(path)[0] + ext
        return path, file_format, subtype

    def export(self, start, end, tail, suffix):
        """Render frames start…end of the mix and write it to a file the user picks. Returns its path."""
        target = self.ask_file(suffix)
        if target is None:
            return None
        path, file_format, subtype = target
        self.window.stop()
        dialog = QProgressDialog("Rendering the mix…", "Cancel", 0, 100, self.window)
        dialog.setWindowTitle("Rendering")
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(300)

        def progress(done):
            dialog.setValue(int(done * 100))
            QApplication.processEvents()
            return not dialog.wasCanceled()

        sr = self.engine.sr
        try:
            with self.engine.plugins_paused_while():  # this thread calls the plugins now
                audio = render_mix(self.project, start, end, sr, self.window.master.volume.value(), tail, progress)
            if audio is not None:
                sf.write(path, audio, sr, format=file_format, subtype=subtype)
        except Exception as e:
            dialog.close()
            QMessageBox.warning(self.window, "Cannot render", f"{path}\n\n{e}")
            return None
        dialog.close()
        if audio is None:  # cancelled
            return None
        self.window.statusBar().showMessage(f"Rendered {len(audio) / sr:.1f} s to {path}", 8000)
        return path
