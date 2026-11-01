"""Notes played live into a track (the MIDI editor's keys and notes), heard through the track
itself: its VST3 instrument or the built-in synth, then its effects, volume and outputs.

The GUI thread calls note_on / note_off; the audio thread (mixer.py) takes what arrived once per
buffer. A deque hands them over: appending and popping are atomic, so no lock is needed. For the
built-in synth the GUI thread renders the note (synth.render_note) and the audio thread only
copies it in, fading it out quickly when the note is let go.
"""
from collections import deque

import numpy as np
from mido import Message

from . import synth

HOLD_S = 4.0  # a built-in synth note held down sounds this long at most


class LiveVoice:
    """A built-in synth note being played live (audio thread)."""

    def __init__(self, pitch, data):
        self.pitch, self.data, self.pos = pitch, data, 0
        self.released_at = None  # position where it was let go: it fades out from there

    def add_to(self, signal, fade):
        """Add the next len(signal) frames; False once it has finished."""
        n = len(signal)
        chunk = self.data[self.pos:self.pos + n]
        if self.released_at is not None:
            gain = np.clip(1.0 - (np.arange(self.pos, self.pos + len(chunk)) - self.released_at) / fade, 0.0, 1.0)
            chunk = chunk * gain
            if len(gain) and gain[-1] <= 0:
                self.pos = len(self.data)  # faded out: finished
        signal[:len(chunk)] += chunk[:, None]
        self.pos += n
        return self.pos < len(self.data)


class LiveNotes:
    """Live notes for one track. GUI thread: note_on, note_off. Audio thread: take, add_voices."""

    def __init__(self):
        self.inbox = deque()  # ("on", pitch, velocity, audio or None, frames or None) / ("off", pitch)
        self.voices = []  # LiveVoice (built-in synth), audio thread only
        self.timed_offs = []  # [frames left, pitch]: notes that stop by themselves, audio thread only
        self.held = {}  # pitch -> notes of it sent to a VST3 instrument and not let go yet, audio thread only

    # --- GUI thread ---

    def note_on(self, pitch, velocity, sr, builtin, length_s=None):
        """Start a note; it stops at note_off, or after length_s if given. builtin: the track plays
        the built-in synth (else its VST3 instrument)."""
        audio = synth.render_note(pitch, velocity, length_s or HOLD_S, sr) if builtin else None
        self.inbox.append(("on", int(pitch), int(velocity), audio, None if length_s is None else int(length_s * sr)))

    def note_off(self, pitch):
        self.inbox.append(("off", int(pitch)))

    # --- audio thread ---

    def active(self):
        return bool(self.inbox or self.voices or self.timed_offs)

    def holding(self):
        """Is a note being played live into the instrument (held, or to stop by itself)? (audio thread)"""
        return bool(self.inbox or self.timed_offs or self.held)

    def clear(self):
        self.inbox.clear()
        self.voices, self.timed_offs, self.held = [], [], {}

    def take(self, frames, sr):
        """What arrived since the last buffer: MIDI messages for a VST3 instrument (built-in synth
        notes become voices instead). Notes that stop by themselves stop within this buffer."""
        messages = []
        while self.inbox:
            item = self.inbox.popleft()
            if item[0] == "on":
                _, pitch, velocity, audio, length = item
                if audio is not None:
                    self.voices.append(LiveVoice(pitch, audio))
                else:
                    messages.append(Message("note_on", note=pitch, velocity=max(1, velocity), time=0))
                    if length is None:
                        self.held[pitch] = self.held.get(pitch, 0) + 1
                if length is not None:
                    self.timed_offs.append([length, pitch])
            else:
                messages.append(self.release(item[1], 0, sr))
                if self.held.get(item[1], 0) > 1:
                    self.held[item[1]] -= 1
                else:
                    self.held.pop(item[1], None)
        due = []
        for entry in self.timed_offs:
            entry[0] -= frames
            if entry[0] <= 0:
                due.append(entry)
                messages.append(self.release(entry[1], max(0, frames + entry[0]), sr))
        if due:
            self.timed_offs = [e for e in self.timed_offs if e not in due]
        return [m for m in messages if m is not None]

    def release(self, pitch, at_frame, sr):
        """Let a note go: a built-in voice fades out; a VST3 instrument gets a note-off."""
        for voice in self.voices:
            if voice.pitch == pitch and voice.released_at is None:
                voice.released_at = voice.pos + at_frame
        return Message("note_off", note=pitch, velocity=0, time=at_frame / sr)

    def add_voices(self, signal, sr):
        """Mix the built-in synth's live notes into signal (frames, channels)."""
        fade = max(1, int(synth.RELEASE_S * sr))
        self.voices = [v for v in self.voices if v.add_to(signal, fade)]
