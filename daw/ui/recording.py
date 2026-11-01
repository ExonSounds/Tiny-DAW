"""Recording audio (● Rec next to Play, R): onto the selected audio track, from the selection
cursor, from the input chosen in its settings (right-click ● Rec), with an optional count-in of
metronome clicks first. Pressing ● Rec again, Play/Pause or Space stops it; the take becomes an
audio clip on the track, replacing what it covers, moved earlier by the device's input and
output delay (and plugin delay compensation) so it lines up with what was heard while playing.

The engine does the recording itself (audio/engine.py: start_recording / finish_recording).
A track is selected by clicking its lane or its row in the panel (the row is outlined).
"""
import numpy as np
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QMessageBox

from ..audio.metronome import render_click
from ..model import AUDIO, AudioClip

COUNT_IN_BARS = (0, 1, 2, 4)


def input_choices(count):
    """[(label, setting)] for a device with `count` input channels: each one alone (mono), then pairs."""
    mono = [(f"In {i + 1} (mono)", f"{i + 1}") for i in range(count)]
    pairs = [(f"In {i + 1}/{i + 2} (stereo)", f"{i + 1}/{i + 2}") for i in range(0, count - 1, 2)]
    return mono + pairs


def input_channels(setting):
    """"1" -> [0]; "3/4" -> [2, 3] (0-based channels)."""
    return [int(n) - 1 for n in setting.split("/")]


def count_in_audio(settings, timing, bars, sr):
    """(frames, stereo audio) of `bars` bars of metronome clicks (the count-in); (0, None) for none."""
    if not bars:
        return 0, None
    beats = bars * timing.beats_per_bar
    beat_frames = timing.beat_s * sr
    frames = round(beats * beat_frames)
    audio = np.zeros(frames, np.float32)
    for beat in range(beats):
        click = render_click(settings.bar if beat % timing.beats_per_bar == 0 else settings.beat, sr)
        at = round(beat * beat_frames)
        n = min(len(click), frames - at)
        audio[at:at + n] += click[:n]
    return frames, np.repeat(audio[:, None], 2, axis=1)


class RecordSettingsDialog(QDialog):
    """Right-click ● Rec: the input to record and the count-in."""

    def __init__(self, parent, prefs, inputs):
        super().__init__(parent)
        self.setWindowTitle("Recording")
        form = QFormLayout(self)
        self.input = QComboBox()
        for label, setting in input_choices(inputs):
            self.input.addItem(label, setting)
        self.input.setCurrentIndex(max(0, self.input.findData(prefs.record_input)))
        self.input.setEnabled(inputs > 0)
        form.addRow("Record from", self.input)
        self.count_in = QComboBox()
        for bars in COUNT_IN_BARS:
            self.count_in.addItem("Off" if not bars else f"{bars} bar{'s' if bars > 1 else ''}", bars)
        self.count_in.setCurrentIndex(max(0, self.count_in.findData(prefs.count_in_bars)))
        form.addRow("Count-in", self.count_in)
        hint = QLabel("The count-in plays the metronome's clicks (its sounds: right-click Metronome) before "
                      "recording starts at the selection cursor." if inputs else
                      "This audio device has no inputs: choose one that has (e.g. your audio interface's "
                      "ASIO driver) in Options → Audio properties.")
        hint.setWordWrap(True)
        hint.setProperty("hint", True)
        form.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class Recording:
    def __init__(self, window, engine, timeline):
        """window: MainWindow (prefs, transport, metronome_settings, statusBar())."""
        self.window, self.engine, self.timeline = window, engine, timeline
        self.track = None  # the track being recorded onto, while recording
        self.takes = 0  # numbering for the takes' names

    @property
    def active(self):
        return self.track is not None

    def show_settings(self):
        prefs = self.window.prefs
        dialog = RecordSettingsDialog(self.window, prefs, self.engine.input_count() if self.engine.stream else 0)
        if dialog.exec():
            if dialog.input.currentData():
                prefs.record_input = dialog.input.currentData()
            prefs.count_in_bars = dialog.count_in.currentData()
            prefs.save()

    def toggle(self):
        """● Rec: start recording, or stop and keep the take."""
        if self.active:
            self.finish()
        else:
            self.start()
        self.window.transport.set_recording(self.active)

    def start(self):
        tl, prefs = self.timeline, self.window.prefs
        track = tl.current_track
        if track is None or track.kind != AUDIO or track.frozen or track not in tl.project.tracks:
            QMessageBox.information(self.window, "Record", "Select the audio track to record onto first: click "
                                    "its lane or its row in the panel." if track is None or track.kind != AUDIO
                                    else f"{track.name} is frozen: unfreeze it to record onto it.")
            return
        if not self.engine.stream:
            QMessageBox.information(self.window, "Record", "No audio device is open (Options → Audio properties).")
            return
        channels = input_channels(prefs.record_input)
        available = self.engine.input_count()
        if max(channels) >= available:
            QMessageBox.information(self.window, "Record", (
                f"This audio device has {available} input{'s' if available != 1 else ''}, so In {prefs.record_input} "
                "can't be recorded. Right-click ● Rec to choose its input." if available else
                "This audio device has no inputs: choose one that has (e.g. your audio interface's ASIO driver) in "
                "Options → Audio properties."))
            return
        self.window.stop()
        start = tl.cursor
        tl.seek(start)
        frames, clicks = count_in_audio(self.window.metronome_settings, tl.project.timing, prefs.count_in_bars,
                                        self.engine.sr)
        try:
            self.engine.start_recording(channels, frames, clicks)
        except Exception as e:
            QMessageBox.warning(self.window, "Cannot record", str(e))
            return
        self.track = track
        tl.recording = (track, start)
        self.window.transport.set_playing(True)
        self.window.statusBar().showMessage(f"Recording In {prefs.record_input} onto {track.name}"
                                            + (" after the count-in" if frames else "") + "…")

    def finish(self):
        """Stop, and place the take on the track as an audio clip."""
        recorder = self.engine.finish_recording()
        track, self.track = self.track, None
        self.timeline.recording = None
        self.window.transport.set_playing(False)
        self.window.transport.set_recording(False)
        self.timeline.update()
        if recorder is None or track not in self.timeline.project.tracks:
            return None
        start, audio = recorder.audio()
        if start < 0:  # it began right at 0 s: what came in before then is left out
            audio, start = audio[-start:], 0
        if not len(audio):
            self.window.statusBar().showMessage("Nothing was recorded", 5000)
            return None
        if audio.shape[1] == 1:
            audio = np.repeat(audio, 2, axis=1)  # a mono input plays on both sides
        self.takes += 1
        clip = AudioClip(name=f"Recording {self.takes}", source=np.ascontiguousarray(audio, np.float32),
                         source_sr=self.engine.sr)
        self.timeline.place_clip(clip, start, track)
        self.window.statusBar().showMessage(f"Recorded {len(audio) / self.engine.sr:.1f} s onto {track.name}", 5000)
        return clip
