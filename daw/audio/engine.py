"""The audio output stream and the transport (play/pause, position).

The stream can use any Windows driver type: ASIO, WASAPI (shared or exclusive),
WDM-KS, DirectSound or MME (see devices.py).
_callback runs on the driver's thread; everything else on the GUI thread.
The stream uses every output channel the device has; each track plays on its own pair.
The callback mixes, runs VST3 instruments and effects (see plugins.py), and for the meters
hands copies of what played to `metering`, which measures on the GUI thread (metering.py).
While looping (`loop`), playback jumps from the loop's end back to its start, exactly at that
frame (a buffer that crosses it is mixed in two parts).

Recording (start_recording / finish_recording): with ASIO the stream also carries the device's
inputs (one stream per ASIO device, so inputs and outputs come together), and the callback keeps
the input channels being recorded (a Recorder) for every buffer that plays. Other driver types
have separate input devices: the driver type's default input is opened for the take only. A
count-in first plays clicks while the transport waits, then playback and recording start on
the same frame. While recording, the loop is ignored (a take is one stretch of time).
It also plays the Explorer's sample preview (preview()), on outputs 1/2, playing or not, and
the metronome while playing (set_metronome(), metronome.py), after the master meters so it never
counts towards the master's level or LUFS.
"""
import gc
import threading
from contextlib import contextmanager
from time import perf_counter, sleep

import numpy as np
import sounddevice as sd

from .. import config
from . import devices
from .cpu import CpuMeter
from .live import LiveNotes
from .metering import MASTER, Metering
from .metronome import add_clicks, render_clicks
from .mixer import mix_clips

LATE_FACTOR = 1.5  # falling this many buffers behind real time (at least) counts as a likely dropout
CLOCK_REANCHOR_S = 10.0


class Recorder:
    """A take being recorded: the input `channels` (0-based) of the device, in `chunks` as they
    come (audio thread). `start` is the timeline frame it began at; `late` how many frames the
    input arrives after what was heard (the device's input and output delay plus plugin delay
    compensation), so the take is moved that much earlier."""

    def __init__(self, channels, io_latency_s, sr):
        self.channels = list(channels)
        self.chunks = []
        self.start = None
        self.io_frames = round(io_latency_s * sr)
        self.late = self.io_frames

    def begin(self, start, latency):
        """Playback reached the take's first frame (audio thread)."""
        if self.start is None:
            self.start, self.late = start, self.io_frames + latency

    def take(self, block):
        self.chunks.append(block[:, self.channels])  # (a copy: fancy indexing)

    def audio(self):
        """(timeline frame, float32 audio (frames, channels)) of the take so far."""
        audio = np.concatenate(self.chunks) if self.chunks else np.zeros((0, len(self.channels)), np.float32)
        return (self.start or 0) - self.late, audio


class PreviewVoice:
    """A sample being previewed: stereo audio at the engine rate and how far it has played; with
    `loop` (which the GUI may switch while it plays) it starts over at the end."""

    def __init__(self, data, pos=0, loop=False):
        self.data, self.pos, self.loop = data, pos, loop


class Engine:
    def __init__(self, project):
        self.project = project
        self.sr = config.FALLBACK_SAMPLE_RATE
        self.channels = 2  # output channels of the open device
        self.blocksize = 0  # frames per buffer asked of the driver; 0 = the driver's own setting
        self.driver_type = None  # e.g. "ASIO", "Windows WASAPI" (see devices.DRIVER_TYPES), once open
        self.exclusive = False  # WASAPI exclusive mode in use
        self.pos = 0  # playhead, in frames
        self.playing = False
        self.loop = None  # (start, end) frames while looping; replaced, never changed, by the GUI thread
        self.inputs = 0  # input channels on the stream (ASIO); 0: none (other drivers record from their own input)
        self._input = None  # the input buffer of the current callback (duplex stream)
        self.recorder = None  # Recorder while recording; set and cleared by the GUI thread
        self.count_in = 0  # frames the transport still waits (count-in) after play(); then it starts
        self.input_stream = None  # other drivers: their default input device, open while recording
        self.master_gain = 1.0
        self.master_mute = False
        self.metering = Metering(self.sr, self.channels)
        self.cpu = CpuMeter()
        self.stream = None
        self._scratch = None  # one track's sum, reused every callback
        self._clock = None  # [perf_counter() reference, frames produced since], for dropout detection
        self.last_error = None  # the latest error inside the audio callback, if any (it then played silence)
        self._restart = True  # next buffer starts playback afresh (VST3 instruments: all notes off)
        self._next_pos = 0  # where the next buffer continues, if the playhead is not moved
        self._was_playing = False  # transport state at the previous buffer (a change means all notes off)
        self.plugins_paused = False  # set while the main thread processes plugins (see plugins_paused_while)
        self.preview_voice = None  # PreviewVoice playing, or None; replaced, never changed, by the GUI thread
        self.metronome = None  # MetronomeSettings while the metronome is on, else None
        self._clicks = None  # (bar click, beat click) rendered for self.sr, while it is on; replaced, never changed
        self._lock = threading.Lock()  # guards pos between the GUI and audio threads
        self._delays = {}  # track -> DelayLine: plugin delay compensation (mixer.py)
        self.latency = 0  # frames the sound lags behind pos (plugin delay compensation), set by the callback
        self._play_from = 0  # where playback last started or jumped to
        self.live = {}  # track -> LiveNotes (live.py); replaced, never changed in place, by the GUI thread

    def open(self, device, blocksize=None, exclusive=False):
        """Open an output device at its own sample rate with all its outputs.
        blocksize: frames per buffer (bigger = less CPU, more delay); None keeps the current choice.
        exclusive: for WASAPI devices, take the device for this program alone (lower delay).
        Returns (name, sample rate, latency in s)."""
        self.close()
        if blocksize is not None:
            self.blocksize = blocksize
        info = devices.device_info(device)
        self.driver_type = devices.driver_type_of(device)
        extra = sd.WasapiSettings(exclusive=True) if exclusive and self.driver_type == "Windows WASAPI" else None
        sr = int(info["default_samplerate"])
        if sr != self.sr:
            self.pos = self.pos * sr // self.sr
            self.project.change_sample_rate(self.sr, sr)
            self.sr = sr
            self.set_metronome(self.metronome)  # its clicks at the new rate
        self.channels = info["max_output_channels"]
        levels, loudness = self.metering.levels_on, self.metering.loudness_on
        self.metering = Metering(sr, self.channels)
        self.metering.levels_on, self.metering.loudness_on = levels, loudness
        # PortAudio sizes the driver's real buffer from the latency asked for; asking for exactly one buffer's
        # worth makes the driver use that size (otherwise it keeps its own and just cuts it into pieces).
        latency = self.blocksize / sr if self.blocksize else "low"
        if self.blocksize and self.driver_type in devices.MIN_LATENCY:
            latency = max(latency, devices.MIN_LATENCY[self.driver_type])  # these drivers starve on less
        self.inputs = info["max_input_channels"] if self.driver_type == "ASIO" else 0
        self.stream = None
        if self.inputs:  # its inputs come with the outputs (for recording)
            try:
                self.stream = sd.Stream(device=(device, device), samplerate=sr, dtype="float32",
                                        channels=(self.inputs, self.channels), blocksize=self.blocksize,
                                        latency=latency, callback=self._duplex_callback)
            except sd.PortAudioError:
                self.inputs = 0  # (the driver won't open its inputs: play without them)
        if self.stream is None:
            self.stream = sd.OutputStream(device=device, samplerate=sr, dtype="float32", channels=self.channels,
                                          blocksize=self.blocksize, latency=latency, callback=self._callback,
                                          extra_settings=extra)
        self.exclusive = extra is not None
        self.cpu.reset()
        self._clock = None
        self.stream.start()
        return info["name"], sr, self.output_latency()

    def output_latency(self):
        latency = self.stream.latency
        return latency[1] if isinstance(latency, tuple) else latency

    def input_count(self):
        """How many input channels can be recorded: the stream's (ASIO), else the driver type's
        default input device's (0 if there is none)."""
        if self.inputs:
            return self.inputs
        index = devices.default_input(self.driver_type) if self.driver_type else None
        return devices.device_info(index)["max_input_channels"] if index is not None else 0

    def start_recording(self, channels, count_in=0, clicks=None):
        """Play from pos and record the input `channels` (0-based) from there, after `count_in` frames
        during which `clicks` (the count-in, stereo) play. Raises if the input can't be opened."""
        if not self.stream or self.playing:
            raise RuntimeError("The audio device isn't ready")
        if self.inputs:
            latency = self.stream.latency
            io_latency = latency[0] + latency[1]
        else:  # other drivers: their default input device, for this take only
            index = devices.default_input(self.driver_type)
            if index is None:
                raise RuntimeError(f"{devices.label(self.driver_type)} has no input device")
            self.input_stream = sd.InputStream(device=index, samplerate=self.sr, dtype="float32",
                                               channels=max(channels) + 1, blocksize=self.blocksize,
                                               latency="low", callback=self._input_callback)
            self.input_stream.start()
            io_latency = self.input_stream.latency + self.output_latency()
        self.recorder = Recorder(channels, io_latency, self.sr)
        if clicks is not None and count_in:
            self.preview(clicks / config.PREVIEW_GAIN)  # (as loud as the metronome: previews are played quieter)
        self.count_in = count_in
        self.play()

    def finish_recording(self):
        """Stop playback and recording; returns the Recorder (None if not recording)."""
        recorder = self.recorder
        self.stop()
        self.count_in = 0
        if self.input_stream is not None:
            self.input_stream.close()
            self.input_stream = None
        sleep(0.05)  # a callback that is running may still be adding its buffer
        self.recorder = None
        return recorder

    def _duplex_callback(self, indata, outdata, frames, time, status):
        self._input = indata
        self._callback(outdata, frames, time, status)

    def _input_callback(self, indata, frames, time, status):
        """The separate input device (drivers other than ASIO): keep it once the take has begun."""
        recorder = self.recorder
        if recorder is not None and recorder.start is not None and self.playing:
            recorder.take(indata)

    def close(self):
        self.stop()
        if self.input_stream is not None:
            self.input_stream.close()
            self.input_stream = None
        if self.stream:
            self.stream.close()
            self.stream = None

    def seek(self, frame):
        with self._lock:
            self.pos = self._play_from = max(0, frame)

    def heard_pos(self):
        """Where what is heard now is on the timeline: plugins with look-ahead hold the sound back
        (`latency`), so while playing it lags behind pos. The playback cursor shows this."""
        with self._lock:
            return max(self._play_from, self.pos - self.latency) if self.playing else self.pos

    def play(self):
        """Start playback. Python's full garbage collection pauses every thread for ~30 ms,
        which delays the audio callback and makes the sound hiccup; so while playing, every
        object that already exists is frozen out of it, and new garbage is cheap to scan."""
        if self.playing or not self.stream:
            return
        self.metering.reset_loudness()  # integrated LUFS covers this playback
        self.cpu.late = 0
        self._clock = None
        # (Plugins are not reset here: the audio thread keeps using them even while stopped, and a
        #  reset from this thread could clash with that. Instruments get "all notes off" instead.)
        gc.freeze()
        self._play_from = self.pos
        self.playing = True

    def preview(self, data, start=0, loop=False):
        """Play `data` (stereo, engine rate) on outputs 1/2 from frame `start`, over whatever else
        plays: once, or over and over (loop)."""
        self.preview_voice = PreviewVoice(np.ascontiguousarray(data, np.float32) * config.PREVIEW_GAIN, start, loop)

    def stop_preview(self):
        self.preview_voice = None

    def live_notes(self, track):
        """The LiveNotes that play into `track` (made the first time)."""
        notes = self.live.get(track)
        if notes is None:
            notes = LiveNotes()
            self.live = {**{t: n for t, n in self.live.items() if t in self.project.tracks}, track: notes}
        return notes

    def set_metronome(self, settings):
        """Switch the metronome on with these sounds (MetronomeSettings), or off (None)."""
        self.metronome = settings
        self._clicks = render_clicks(settings, self.sr) if settings else None

    def stop(self):
        """Stop playback, and let the garbage collector catch up now that a pause is harmless."""
        self.count_in = 0
        if not self.playing:
            return
        self.pos = self.heard_pos()  # the playback cursor stays where the sound stopped
        self.playing = False
        gc.unfreeze()
        gc.collect()

    def _callback(self, out, frames, time, status):
        started = perf_counter()
        budget = frames / self.sr
        if self.playing and self.stream is not None and status is not None:  # not when called without a device
            self._check_dropout(started, frames, budget, status)
        out.fill(0)
        playing = self.playing
        plugins = not self.plugins_paused
        # VST3 plugins keep running while stopped too, like in any DAW: synths stay playable from their
        # own keyboard, reverb tails ring out, and plugins such as Serum 2 don't report "suspended".
        voice = self.preview_voice
        live = self.live
        playing_live = any(n.active() for n in live.values())
        if playing or voice is not None or playing_live or (plugins and self._has_plugins()):
            if playing:
                with self._lock:
                    parts = self._advance(frames)
            else:
                parts = ((0, frames, None),)  # transport stopped: no clips, only the plugins
            if self._scratch is None or self._scratch.shape != out.shape:
                self._scratch = np.zeros_like(out)
            metering = self.metering
            recorder = self.recorder
            for offset, n, start in parts:  # (more parts where the loop jumps back, or a count-in ends)
                moving = start is not None  # (not while counting in)
                # all notes off when playback starts, stops, the playhead is moved or loops, or plugins were paused
                restart = self._restart or moving != self._was_playing or (moving and start != self._next_pos)
                if plugins:
                    self._restart = False
                self._was_playing = moving
                self._next_pos = start + n if moving else None
                if recorder is not None and moving:
                    recorder.begin(start, self.latency)
                    if self.inputs and self._input is not None:
                        recorder.take(self._input[offset:offset + n])
                self._frames = n
                part = out[offset:offset + n]
                try:
                    self.latency = mix_clips(self.project, part, start, self._scratch[offset:offset + n],
                                             self._tap_track if metering.active else _ignore, self.sr, restart,
                                             plugins, self._delays, live)
                except Exception as e:  # never let an error stop the audio stream: play silence, remember why
                    part.fill(0)
                    self.last_error = f"{type(e).__name__}: {e}"
            if voice is not None:
                self._play_preview(out, voice)
            gain = 0.0 if self.master_mute else self.master_gain
            out *= gain
            if metering.active:
                metering.tap(MASTER, out.copy())
            clicks = self._clicks
            if playing and clicks is not None:
                # in time with what is heard (delay compensation), never before where playback started
                for offset, n, start in parts:
                    if start is not None:
                        add_clicks(out[offset:offset + n], start - self.latency, clicks, self.project.timing, self.sr,
                                   gain, self._play_from)
        self.cpu.add(perf_counter() - started, budget)

    def _advance(self, frames):
        """Move the playhead on by a buffer (audio thread, under the lock). Returns its parts as
        (offset in the buffer, frames, timeline frame they start at): one, or more where the loop
        jumps back, or a count-in ends (its part's timeline frame is None: the transport waits).
        Playback that starts after the loop's end just carries on; while recording, it doesn't loop."""
        parts, offset, pos, loop = [], 0, self.pos, None if self.recorder else self.loop
        if self.count_in:
            offset = min(self.count_in, frames)
            self.count_in -= offset
            parts.append((0, offset, None))
            if offset == frames:
                return parts
        # (<=: a loop's end right at the end of this buffer jumps too; otherwise the next buffer would start
        # at the end, "after" the loop, and play on: loops whose length is a whole number of buffers)
        while loop and pos < loop[1] <= pos + frames - offset:
            n = loop[1] - pos
            parts.append((offset, n, pos))
            offset, pos = offset + n, loop[0]
            self._play_from = pos  # (the playback cursor never shows the time before the jump)
        if offset < frames:
            parts.append((offset, frames - offset, pos))
        self.pos = pos + frames - offset
        return parts

    def _play_preview(self, out, voice):
        """Add the next buffer of the preview to outputs 1/2 (audio thread); forget it once done
        (a looping one starts over instead)."""
        width, done, data = min(2, out.shape[1]), 0, voice.data
        while done < len(out):
            chunk = data[voice.pos:voice.pos + len(out) - done]
            out[done:done + len(chunk), :width] += chunk[:, :width]
            done += len(chunk)
            voice.pos += len(chunk)
            if voice.pos < len(data):
                continue
            if voice.loop and len(data):
                voice.pos = 0
            else:
                if self.preview_voice is voice:
                    self.preview_voice = None
                break

    @contextmanager
    def plugins_paused_while(self):
        """Use around processing plugins on the main thread (renders, freezing, bouncing): a plugin must
        never be processed by two threads at once, so the audio thread leaves plugins alone for that
        time; audio tracks keep playing. Afterwards instruments get "all notes off", as their notes
        were missed."""
        self.plugins_paused = True
        sleep(0.05)  # let a callback that is already running finish with its plugins
        try:
            yield
        finally:
            self._restart = True
            self.plugins_paused = False

    def _has_plugins(self):
        return any(slot.instance is not None for slot in self.project.plugin_slots())

    def _check_dropout(self, now, frames, budget, status):
        """Count likely clicks for the CPU monitor: PortAudio reported an underflow, or the audio
        produced has fallen behind real time by more than the device had queued, so it ran dry.
        Comparing totals rather than the gap between two callbacks copes with drivers that call
        in bursts (WASAPI exclusive) or with uneven buffer sizes (DirectSound)."""
        if self._clock is None or now - self._clock[0] > CLOCK_REANCHOR_S:
            # (time, frames produced) since a reference point; renewed now and then so the slight
            # drift between the audio device's clock and the PC's never adds up to a false alarm
            self._clock = [now, 0]
        start, produced = self._clock
        behind = (now - start) - produced / self.sr
        if status.output_underflow or behind > max(LATE_FACTOR * budget, self.output_latency()):
            self.cpu.late += 1
            self._clock = [now, 0]  # the device has re-synchronised; measure from here
        self._clock[1] += frames

    def _tap_track(self, track, block, first, channels):
        """Called by mix_clips for every track: hand a copy of its own outputs to the meters."""
        if block is None:  # silent in this buffer
            self.metering.tap_silence(track, self._frames)
        else:
            self.metering.tap(track, block[:, first:first + channels].copy(), first)


def _ignore(*args):
    pass
