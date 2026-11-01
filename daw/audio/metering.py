"""Level and loudness metering, done off the audio thread.

The audio callback only hands over a copy of what each track and the master played
(tap / tap_silence: a quick memory copy). The GUI thread then
  - every frame, takes per-channel peaks for the level meters (take_peaks), and
  - a few times a second, K-filters everything that arrived since for the LUFS meters
    in one batch per meter (update_loudness), much cheaper than filtering each buffer.
Either can be switched off; then the callback does not even copy for it.
"""
from collections import deque

import numpy as np

from .loudness import LoudnessMeter

MASTER = "master"  # key of the master signal; tracks are keyed by their Track


class Metering:
    def __init__(self, sr, channels):
        self.sr, self.channels = sr, channels
        self.levels_on = True  # peak meters
        self.loudness_on = True  # LUFS
        self._inbox = deque()  # (key, block or silent frame count, first output channel); audio thread appends
        self.reset_loudness()

    @property
    def active(self):
        return self.levels_on or self.loudness_on

    # --- audio thread ---------------------------------------------------------------

    def tap(self, key, block, first=0):
        """block: what `key` played, on outputs [first, first + its width). Must be a copy."""
        self._inbox.append((key, block, first))

    def tap_silence(self, key, frames):
        self._inbox.append((key, frames, 0))

    # --- GUI thread -------------------------------------------------------------------

    def reset_loudness(self):
        """Start the LUFS measurements afresh (e.g. when playback starts)."""
        self.loudness = {MASTER: LoudnessMeter(self.sr, self.channels)}  # key -> LoudnessMeter
        self._pending = {}  # key -> blocks / silent frame counts not yet measured, in order

    def take_peaks(self):
        """Drain what was played since the last call: (master peaks, {track: peaks}), one value per
        output channel. Also queues the audio for the next update_loudness()."""
        master, tracks = np.zeros(self.channels), {}
        while self._inbox:
            key, block, first = self._inbox.popleft()
            if self.loudness_on:
                self._pending.setdefault(key, []).append(block)
            if isinstance(block, int) or not self.levels_on:
                continue
            peaks = np.abs(block).max(axis=0)
            if key == MASTER:
                master = np.maximum(master, peaks)
            else:
                full = tracks.setdefault(key, np.zeros(self.channels))
                np.maximum(full[first:first + len(peaks)], peaks, out=full[first:first + len(peaks)])
        return master, tracks

    def update_loudness(self):
        """Measure everything queued since the last call: one filter run per meter."""
        pending, self._pending = self._pending, {}
        for key, items in pending.items():
            meter = self.loudness.get(key)
            run = []  # consecutive audio blocks, measured together
            for item in items + [None]:
                if isinstance(item, np.ndarray):
                    if meter is None or meter.channels != item.shape[1]:
                        meter = self.loudness[key] = LoudnessMeter(self.sr, item.shape[1])
                    run.append(item)
                    continue
                if run:
                    meter.add(np.concatenate(run))
                    run = []
                if isinstance(item, int) and meter is not None:
                    meter.add_silence(item)

    def read(self, key):
        """(momentary, short-term, integrated) LUFS of a track or MASTER; -inf if never heard."""
        meter = self.loudness.get(key)
        return meter.read() if meter else (float("-inf"),) * 3
