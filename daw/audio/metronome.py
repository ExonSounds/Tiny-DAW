"""The metronome: its two click sounds and when they play. No Qt.

One click marks the start of every bar, the other every other beat (quarter note) of the bar,
following the project's tempo and time signature (timing.py). Each click is a short tone shaped
by an attack-sustain-release envelope: it rises from silence to its level in `attack_ms`, stays
there for `sustain_ms`, then falls back to silence in `release_ms`.

The clicks are rendered once (render_clicks, on the GUI thread, when the settings or the sample
rate change); the audio thread only copies them in where beats fall (add_clicks). Where they
fall is worked out from the playhead each buffer, so seeking needs nothing special.
"""
import math
from dataclasses import dataclass, field

import numpy as np

WAVEFORMS = ("sine", "triangle", "square", "noise")
MAX_ATTACK_MS, MAX_SUSTAIN_MS, MAX_RELEASE_MS = 100, 200, 200  # (the envelope editor shows 500 ms)
MIN_DB, MAX_DB = -40.0, 0.0
MIN_PITCH, MAX_PITCH = 100.0, 8000.0


@dataclass
class Click:
    pitch: float = 1000.0  # Hz (not used by "noise")
    level_db: float = -8.0  # sustain level
    attack_ms: float = 1.0
    sustain_ms: float = 10.0
    release_ms: float = 50.0
    waveform: str = "sine"  # one of WAVEFORMS


@dataclass
class MetronomeSettings:
    bar: Click = field(default_factory=lambda: Click(pitch=1600.0, level_db=-4.0, sustain_ms=15.0, release_ms=70.0))
    beat: Click = field(default_factory=Click)  # the other beats of the bar


def render_click(click, sr):
    """The click as mono float32 audio at rate sr."""
    a, s, r = (max(0, round(ms / 1000 * sr)) for ms in (click.attack_ms, click.sustain_ms, click.release_ms))
    n = max(1, a + s + r)
    t = np.arange(n) / sr
    phase = (t * click.pitch) % 1.0
    if click.waveform == "triangle":
        wave = 1.0 - 4.0 * np.abs(phase - 0.5)
    elif click.waveform == "square":
        wave = np.where(phase < 0.5, 1.0, -1.0) * 0.6  # a square sounds louder than a sine at the same peak
    elif click.waveform == "noise":
        wave = np.random.default_rng(0).uniform(-1.0, 1.0, n)  # the same noise every time
    else:
        wave = np.sin(2 * np.pi * phase)
    envelope = np.concatenate([np.linspace(0.0, 1.0, a, endpoint=False), np.ones(s),
                               np.linspace(1.0, 0.0, r + 1)[1:]])[:n]
    return (wave * envelope * 10 ** (click.level_db / 20)).astype(np.float32)


def render_clicks(settings, sr):
    """(bar click, beat click), ready for add_clicks."""
    return render_click(settings.bar, sr), render_click(settings.beat, sr)


def add_clicks(out, start, clicks, timing, sr, gain=1.0, earliest=0):
    """Add the clicks of the beats that sound in out (timeline frames [start, start + len(out)))
    to its first two outputs, times gain; none for beats before frame `earliest`. Audio thread:
    no allocation beyond small slices."""
    bar_click, beat_click = clicks
    beat = 60.0 / timing.bpm * sr  # frames per beat
    longest = max(len(bar_click), len(beat_click))
    frames, width = len(out), min(2, out.shape[1])
    for k in range(max(0, math.ceil((start - longest) / beat)), math.floor((start + frames - 1) / beat) + 1):
        at = round(k * beat)
        if at < earliest:
            continue
        click = bar_click if k % timing.beats_per_bar == 0 else beat_click
        a, b = max(start, at), min(start + frames, at + len(click))
        if a < b:
            out[a - start:b - start, :width] += click[a - at:b - at, None] * gain
