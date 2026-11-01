"""Audio CPU load: how much of each buffer's time budget the callback uses.

100 % means the callback took as long as the audio it produced lasts; above
that the driver runs dry and you hear dropouts.
"""
import threading


class CpuMeter:
    def __init__(self):
        self._lock = threading.Lock()
        self.late = 0  # late callbacks (likely dropouts) since playback started; counted by the engine
        self.reset()

    def reset(self):
        with self._lock:
            self._busy = 0.0  # seconds spent in the callback since the last read()
            self._budget = 0.0  # seconds of audio produced in that time
        self.average = None

    def add(self, busy, budget):
        """Called by the audio thread after every callback."""
        with self._lock:
            self._busy += busy
            self._budget += budget

    def read(self, average_seconds):
        """(current, average) load as fractions, or None if no audio ran since the last read.

        current: load over the time since the previous read().
        average: exponential average with a time constant of about average_seconds.
        """
        with self._lock:
            busy, budget = self._busy, self._budget
            self._busy = self._budget = 0.0
        if budget == 0:
            return None
        current = busy / budget
        weight = min(1.0, budget / average_seconds)
        self.average = current if self.average is None else self.average + weight * (current - self.average)
        return current, self.average
