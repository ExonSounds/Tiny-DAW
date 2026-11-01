"""Loudness in LUFS, following ITU-R BS.1770 / EBU R128.

The signal is K-weighted (a high shelf and a high-pass that model how loud we hear
frequencies), then its power is collected in 100 ms blocks:
  momentary   = last 400 ms
  short-term  = last 3 s
  integrated  = everything since reset, over 400 ms blocks, gated: blocks below -70 LUFS
                are ignored, then blocks more than 10 LU below the average of the rest.
Every channel is weighted 1.0 (BS.1770's weights for left/right/centre).
"""
import threading
from collections import deque

import numpy as np
from scipy.signal import sosfilt

BLOCK_S = 0.1
MOMENTARY_BLOCKS, SHORT_TERM_BLOCKS = 4, 30
ABSOLUTE_GATE_LUFS, RELATIVE_GATE_LU = -70.0, -10.0


def k_weighting(sr):
    """Second-order sections of the K-weighting filter at sample rate sr."""
    def biquad(kind, gain_db, q, fc):
        a = 10 ** (gain_db / 40)
        w0 = 2 * np.pi * fc / sr
        alpha, cos = np.sin(w0) / (2 * q), np.cos(w0)
        if kind == "high_shelf":
            b = [a * ((a + 1) + (a - 1) * cos + 2 * np.sqrt(a) * alpha), -2 * a * ((a - 1) + (a + 1) * cos),
                 a * ((a + 1) + (a - 1) * cos - 2 * np.sqrt(a) * alpha)]
            den = [(a + 1) - (a - 1) * cos + 2 * np.sqrt(a) * alpha, 2 * ((a - 1) - (a + 1) * cos),
                   (a + 1) - (a - 1) * cos - 2 * np.sqrt(a) * alpha]
        else:  # high-pass
            b = [(1 + cos) / 2, -(1 + cos), (1 + cos) / 2]
            den = [1 + alpha, -2 * cos, 1 - alpha]
        return [*(np.array(b) / den[0]), 1.0, den[1] / den[0], den[2] / den[0]]
    return np.array([biquad("high_shelf", 4.0, 1 / np.sqrt(2), 1500.0), biquad("high_pass", 0.0, 0.5, 38.0)])


def to_lufs(power):
    return -0.691 + 10 * np.log10(power) if power > 0 else float("-inf")


class LoudnessMeter:
    """Measures one signal. add()/add_silence() run on the audio thread, read() on the GUI thread."""

    def __init__(self, sr, channels):
        self.channels = channels
        self.sos = k_weighting(sr)
        self.zi = np.zeros((len(self.sos), 2, channels))  # filter state carried between buffers
        self.hop = int(sr * BLOCK_S)
        self._sum, self._count = 0.0, 0  # the 100 ms block being filled
        self._blocks = deque(maxlen=SHORT_TERM_BLOCKS)  # mean power of recent 100 ms blocks
        self._gates = []  # power of every 400 ms block (75 % overlap) since reset
        self._lock = threading.Lock()

    def add(self, x):
        """x: (frames, channels) float audio."""
        y, self.zi = sosfilt(self.sos, x, axis=0, zi=self.zi)
        self._collect(np.einsum("ij,ij->i", y, y))  # per-frame power summed over channels

    def add_silence(self, frames):
        self.zi[:] = 0.0
        self._collect(None, frames)

    def _collect(self, power, frames=None):
        n = len(power) if power is not None else frames
        pos = 0
        while pos < n:
            take = min(self.hop - self._count, n - pos)
            if power is not None:
                self._sum += float(power[pos:pos + take].sum())
            self._count += take
            pos += take
            if self._count == self.hop:
                with self._lock:
                    self._blocks.append(self._sum / self.hop)
                    if len(self._blocks) >= MOMENTARY_BLOCKS:
                        self._gates.append(sum(list(self._blocks)[-MOMENTARY_BLOCKS:]) / MOMENTARY_BLOCKS)
                self._sum, self._count = 0.0, 0

    def read(self):
        """(momentary, short-term, integrated) in LUFS; -inf when silent or nothing measured yet."""
        with self._lock:
            blocks, gates = list(self._blocks), np.array(self._gates)
        momentary = to_lufs(np.mean(blocks[-MOMENTARY_BLOCKS:])) if blocks else float("-inf")
        short_term = to_lufs(np.mean(blocks)) if blocks else float("-inf")
        return momentary, short_term, integrated(gates)


def integrated(gates):
    """Gated mean loudness of 400 ms block powers."""
    gates = gates[gates > 10 ** ((ABSOLUTE_GATE_LUFS + 0.691) / 10)]
    if not len(gates):
        return float("-inf")
    relative = to_lufs(gates.mean()) + RELATIVE_GATE_LU
    gates = gates[gates > 10 ** ((relative + 0.691) / 10)]
    return to_lufs(gates.mean())
