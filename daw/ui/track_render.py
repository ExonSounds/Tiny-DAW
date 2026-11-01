"""Freezing and bouncing tracks (right-click a track's row), with a progress window.

Freeze: the track is rendered (audio/render.py) and from then on plays that rendering instead of
its clips and plugins, which saves the CPU its plugins would use. Its clips and plugins are locked
until it is unfrozen; the rendering is saved with the project.

Bounce: the rendering becomes an ordinary audio clip, either on a new audio track right below
(the original is kept and muted), or in place: the track's clips, instrument and effects are
replaced by it (a MIDI track becomes an audio track).

Rendering calls the track's plugins from the main thread, so playback stops and the engine leaves
plugins alone meanwhile (Engine.plugins_paused_while). A frozen track bounces its rendering as is.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from ..audio.render import render_track
from ..model import AUDIO, STRAIGHT, AudioClip


class TrackRenderer:
    def __init__(self, window, engine, timeline, plugins):
        """window: MainWindow (stop(), statusBar()); plugins: PluginHost (to close plugin windows)."""
        self.window, self.engine, self.timeline, self.plugins = window, engine, timeline, plugins

    @property
    def project(self):
        return self.timeline.project

    def fill_menu(self, menu, track):
        """The track's right-click menu entries."""
        has_audio = bool(track.frozen or any(not c.disabled for c in self.project.clips_on(track)))
        menu.addSeparator()
        if track.frozen:
            menu.addAction("Unfreeze track").triggered.connect(lambda: self.unfreeze(track))
        else:
            freeze = menu.addAction("Freeze track")
            freeze.setEnabled(has_audio)
            freeze.triggered.connect(lambda: self.freeze(track))
        bounce = menu.addAction("Bounce to new audio track")
        bounce.setEnabled(has_audio)
        bounce.triggered.connect(lambda: self.bounce(track, in_place=False))
        in_place = menu.addAction("Bounce in place…")
        in_place.setEnabled(has_audio)
        in_place.triggered.connect(lambda: self.bounce(track, in_place=True))

    def render(self, track):
        """(start frame, stereo audio) of the track, or None (cancelled, failed, or nothing to render)."""
        if track.frozen:  # already rendered
            return track.frozen.start, track.frozen.data
        self.window.stop()
        dialog = QProgressDialog(f"Rendering {track.name}…", "Cancel", 0, 100, self.window)
        dialog.setWindowTitle("Rendering")
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(300)

        def progress(done):
            dialog.setValue(int(done * 100))
            QApplication.processEvents()
            return not dialog.wasCanceled()

        try:
            with self.engine.plugins_paused_while():  # this thread calls the track's plugins now
                result = render_track(self.project, track, self.engine.sr, progress)
        except Exception as e:
            dialog.close()
            QMessageBox.warning(self.window, "Cannot render track", f"{track.name}\n\n{e}")
            return None
        dialog.close()
        return result

    def freeze(self, track):
        result = self.render(track)
        if result is None:
            return
        start, audio = result
        track.frozen = self.audio_clip(f"{track.name} (frozen)", audio, start)
        track.frozen.track = track
        self.timeline.tracks_regrouped()  # its clips are locked now: deselect them; redraw lanes and panel
        self.window.statusBar().showMessage(f"{track.name} is frozen: its plugins rest until you unfreeze it", 5000)

    def unfreeze(self, track):
        track.frozen = None
        self.timeline.tracks_regrouped()

    def bounce(self, track, in_place):
        if in_place and QMessageBox.question(
                self.window, "Bounce in place",
                f"Replace the clips of {track.name}" + (", its instrument and its effects" if track.instruments()
                                                         or track.effects else "") +
                " with one audio clip of how it sounds now?\nThis cannot be undone.") != QMessageBox.Yes:
            return
        result = self.render(track)
        if result is None:
            return
        start, audio = result
        clip = self.audio_clip(f"{track.name} (bounced)", audio, start)
        if in_place:
            slots = [s for s in (track.instrument, *track.rack, *track.effects) if s]
            self.plugins.close_editor(slots)
            self.project.remove_clips(self.project.clips_on(track))
            track.cables = STRAIGHT  # (first: the audio thread never finds cables to effects that are gone)
            track.rack_cables = ()
            track.kind, track.instrument, track.effects, track.frozen = AUDIO, None, (), None
            track.needs_instrument, track.uses_rack, track.rack = False, False, ()
            target = track
        else:
            target = self.project.add_track(AUDIO, track.group, after=track)
            target.name = f"{track.name} bounce"
            track.muted = True  # the bounce plays instead (unmute to hear both)
        self.timeline.place_clip(clip, start, target)
        self.timeline.tracks_regrouped()

    def audio_clip(self, name, audio, start):
        clip = AudioClip(name=name, source=audio, source_sr=self.engine.sr, start=start)
        clip.prepare(self.engine.sr)
        return clip
