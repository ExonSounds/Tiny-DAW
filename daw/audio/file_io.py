"""Reading audio and MIDI files from disk."""
import os

import mido
import numpy as np
import soundfile as sf

from . import synth
from .dsp import resample, to_stereo

AUDIO_EXTENSIONS = (".wav", ".flac", ".ogg", ".mp3", ".aif", ".aiff")
MIDI_EXTENSIONS = (".mid", ".midi")


def is_midi(path):
    return os.path.splitext(path)[1].lower() in MIDI_EXTENSIONS


def is_sample(path):
    """An audio or MIDI file the program can load (by its extension)."""
    return os.path.splitext(path)[1].lower() in AUDIO_EXTENSIONS + MIDI_EXTENSIONS


def load_audio(path):
    """Return (stereo float32 data, sample rate). Raises on unreadable files."""
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    return to_stereo(data), sr


def load_midi(path):
    """Return (notes, length in seconds); notes is (n, 4) float: start s, end s, pitch, velocity.

    All tracks and channels of the file are merged, with tempo changes applied.
    """
    notes, held, now = [], {}, 0.0
    for msg in mido.MidiFile(path):  # merged tracks; msg.time is seconds since the previous message
        now += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            held.setdefault((msg.channel, msg.note), []).append((now, msg.velocity))
        elif msg.type in ("note_on", "note_off") and held.get((msg.channel, msg.note)):
            start, velocity = held[(msg.channel, msg.note)].pop(0)
            notes.append((start, now, msg.note, velocity))
    for (_, pitch), starts in held.items():  # notes never released end with the file
        notes.extend((start, now, pitch, velocity) for start, velocity in starts)
    notes.sort()
    return np.array(notes, dtype=np.float64).reshape(-1, 4), now


def load_playable(path, sr):
    """Stereo audio at rate sr for any sample file, to preview it: audio files resampled,
    MIDI files rendered with the built-in synth."""
    if is_midi(path):
        notes, length_s = load_midi(path)
        return synth.render(notes, sr, length_s)
    data, source_sr = load_audio(path)
    return resample(data, source_sr, sr)
