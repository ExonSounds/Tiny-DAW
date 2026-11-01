"""A tiny built-in instrument so MIDI lanes make sound.

Notes are rendered to audio ahead of time (when a MIDI clip is loaded or the
sample rate changes), so playing them costs the same as an audio clip. Swap
`render` for something better, or for a real-time instrument in the engine,
when the DAW grows one.
"""
import numpy as np

ATTACK_S = 0.005
RELEASE_S = 0.08
HARMONICS = ((1, 1.0), (2, 0.35), (3, 0.15))  # (multiple of the note frequency, amplitude)
NOTE_GAIN = 0.15  # level of one note at full velocity; chords add up


def note_frequency(pitch):
    return 440.0 * 2 ** ((pitch - 69) / 12)


def render_note(pitch, velocity, duration_s, sr):
    """One note including its release tail, as mono float32."""
    hold = max(1, int(duration_s * sr))
    n = hold + int(RELEASE_S * sr)
    t = np.arange(n) / sr
    f = note_frequency(pitch)
    wave = sum(amp * np.sin(2 * np.pi * f * h * t) for h, amp in HARMONICS if f * h < sr / 2)
    envelope = np.minimum(1.0, t / ATTACK_S)
    envelope[hold:] *= np.linspace(1.0, 0.0, n - hold)
    return (NOTE_GAIN * velocity / 127 * wave * envelope).astype(np.float32)


def render(notes, sr, length_s):
    """Stereo float32 audio of all notes; notes is (n, 4): start s, end s, pitch, velocity."""
    out = np.zeros(int((length_s + RELEASE_S) * sr) + 1, np.float32)
    for start, end, pitch, velocity in notes:
        a = int(start * sr)
        if a >= len(out):
            continue
        note = render_note(pitch, velocity, end - start, sr)[:len(out) - a]
        out[a:a + len(note)] += note
    return np.repeat(out[:, None], 2, axis=1)
