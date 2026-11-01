"""Small numpy helpers for audio data. Arrays are float32, shaped (frames, channels)."""
import numpy as np


def to_stereo(data):
    """Duplicate mono to two channels, or keep the first two of a multichannel file."""
    stereo = np.repeat(data, 2, axis=1) if data.shape[1] == 1 else data[:, :2]
    return np.ascontiguousarray(stereo, dtype=np.float32)


def resample(data, src_sr, dst_sr):
    """Linear-interpolation resampling. Good enough until a proper resampler is needed."""
    if src_sr == dst_sr:
        return data
    n = int(round(len(data) * dst_sr / src_sr))
    old, new = np.arange(len(data)), np.linspace(0, len(data) - 1, n)
    return np.stack([np.interp(new, old, data[:, c]) for c in range(data.shape[1])], axis=1).astype(np.float32)


def waveform_peaks(data, block):
    """(blocks, 2) array of min/max of the mono mix per `block` samples, for drawing."""
    mono = data.mean(axis=1)
    blocks = np.pad(mono, (0, -len(mono) % block)).reshape(-1, block)
    return np.stack([blocks.min(axis=1), blocks.max(axis=1)], axis=1).clip(-1, 1)


def db_to_gain(db, min_db):
    """Decibels to a linear factor; anything at or below min_db is silence."""
    return 0.0 if db <= min_db else 10 ** (db / 20)


def gain_to_db(gain):
    return 20 * np.log10(max(gain, 1e-6))
