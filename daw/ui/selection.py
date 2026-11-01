"""What is selected on the timeline: a set of clips and, optionally, a time range over some lanes.

Dragging over empty lane space makes a range; every clip it touches is selected too.
Clicking clips selects them (Ctrl+click adds or removes one).
"""
from dataclasses import dataclass


@dataclass
class TimeRange:
    start: int  # frames
    end: int
    tracks: tuple  # lanes the range covers, top to bottom


class Selection:
    def __init__(self):
        self.clips = set()
        self.range = None

    def clear(self):
        self.clips = set()
        self.range = None

    def select_only(self, clip):
        self.clips = {clip}
        self.range = None

    def toggle(self, clip):
        self.clips ^= {clip}
        self.range = None

    def set_range(self, time_range, clips):
        self.range = time_range
        self.clips = set(clips)

    def forget(self, clips=(), track=None):
        """Drop clips (and a track) that no longer exist."""
        self.clips -= set(clips)
        if track is not None and self.range:
            tracks = tuple(t for t in self.range.tracks if t is not track)
            self.range = TimeRange(self.range.start, self.range.end, tracks) if tracks else None
