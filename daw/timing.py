"""Bars and beats: the project's tempo, and the grid the ruler shows and clicks snap to. No Qt.

Bar 1 starts at 0 s and the tempo is constant. Beats are quarter notes; a bar has
`beats_per_bar` of them (the time signature is beats_per_bar / 4).

The grid is dynamic: its lines are always at least GRID_MIN_PX apart, so zooming in makes
it finer (bars → beats → 8ths → 16ths → 32nds) and zooming out coarser (2, 4, 8… bars).
In thirds view (triplets) the steps within a bar divide beats in three instead: half-note,
quarter, 8th, 16th and 32nd triplets. Grid positions are counted in beats as Fractions, so
thirds stay exact; only steps that fit a whole number of times in a bar are used, so the
grid always lines up with the bars.

TapTempo works out a tempo from taps (the Tap button next to the BPM box).
"""
import math
from dataclasses import dataclass
from fractions import Fraction

from . import config

STRAIGHT_STEPS = tuple(Fraction(n, d) for n, d in ((1, 8), (1, 4), (1, 2), (1, 1), (2, 1)))  # in beats
THIRDS_STEPS = tuple(Fraction(n, d) for n, d in ((1, 12), (1, 6), (1, 3), (2, 3), (4, 3)))
BAR, BEAT, FINE = 2, 1, 0  # how strong a grid line is
EPSILON = 1e-4  # beats; a position rounded to whole frames still counts as on its beat


@dataclass
class Timing:
    bpm: float = config.DEFAULT_BPM
    beats_per_bar: int = config.DEFAULT_BEATS_PER_BAR
    beats: bool = True  # edit in bars and beats (ruler, grid, snapping); False = minutes and seconds
    thirds: bool = False  # the grid divides beats in thirds (triplets)
    loop_start: float = 0.0  # the loop range, in seconds (end <= start: none chosen yet)
    loop_end: float = 0.0
    looping: bool = False  # playback jumps from the loop's end back to its start

    def loop_range(self):
        """(start, end) seconds of the loop range, or None if there is none."""
        return (self.loop_start, self.loop_end) if self.loop_end - self.loop_start >= config.LOOP_MIN_S else None

    @property
    def beat_s(self):
        return 60.0 / self.bpm

    def grid_step(self, pps):
        """Beats between grid lines at zoom `pps` (pixels per second)."""
        beat_px = pps * self.beat_s
        bar = Fraction(self.beats_per_bar)
        for step in THIRDS_STEPS if self.thirds else STRAIGHT_STEPS:
            if step < bar and (bar / step).denominator == 1 and step * beat_px >= config.GRID_MIN_PX:
                return step
        step = bar
        while step * beat_px < config.GRID_MIN_PX:
            step *= 2
        return step

    def snap(self, seconds, pps):
        """`seconds` moved to the nearest grid line at zoom `pps`."""
        step = float(self.grid_step(pps)) * self.beat_s
        return max(0.0, round(seconds / step) * step)

    def lines(self, t0, t1, pps):
        """(seconds, strength, label) for every grid line from t0 to t1 seconds. Strength is BAR,
        BEAT or FINE; label is the ruler text ("5" for bar 5, "5.3" for its third beat) or None.
        Bars are labelled every 1, 2, 4… bars and beats once there is room (BAR/BEAT_LABEL_MIN_PX)."""
        step, bar = self.grid_step(pps), self.beats_per_bar
        beat_px = pps * self.beat_s
        every = 1  # label every this many bars
        while every * bar * beat_px < config.BAR_LABEL_MIN_PX:
            every *= 2
        label_beats = beat_px >= config.BEAT_LABEL_MIN_PX and (1 / step).denominator == 1
        found = []
        for k in range(max(0, math.floor(t0 / self.beat_s / step)), math.ceil(t1 / self.beat_s / step) + 1):
            pos = k * step
            bar_index, beat = divmod(pos, bar)
            label = None
            if beat == 0:
                strength = BAR
                if bar_index % every == 0:
                    label = str(int(bar_index) + 1)
            elif beat.denominator == 1:
                strength = BEAT
                if label_beats:
                    label = f"{int(bar_index) + 1}.{int(beat) + 1}"
            else:
                strength = FINE
            found.append((float(pos) * self.beat_s, strength, label))
        return found

    def position(self, seconds):
        """(bar, beat, part) counted from 1: part is the 16th within the beat, or the third in thirds view."""
        bar, beat = divmod(seconds / self.beat_s + EPSILON, self.beats_per_bar)
        return int(bar) + 1, int(beat) + 1, int(beat % 1 * (3 if self.thirds else 4)) + 1


class TapTempo:
    """Works out a tempo from taps in time with the music (the Tap button): the average interval
    of the last TAP_COUNT taps. A pause longer than TAP_RESET_S starts over."""

    def __init__(self):
        self.taps = []  # times in seconds

    def tap(self, now):
        """Count a tap at time `now` (seconds). Returns the tempo in BPM once there are two taps, else None."""
        if self.taps and now - self.taps[-1] > config.TAP_RESET_S:
            self.taps = []
        self.taps = (self.taps + [now])[-config.TAP_COUNT:]
        if len(self.taps) < 2 or self.taps[-1] <= self.taps[0]:
            return None
        bpm = 60.0 * (len(self.taps) - 1) / (self.taps[-1] - self.taps[0])
        return round(min(max(bpm, config.MIN_BPM), config.MAX_BPM), 2)
