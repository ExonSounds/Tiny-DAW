"""Automation: a plugin parameter following a curve over time (model.Automation, one lane each
under its track). No Qt.

A track's automation lanes belong to its instrument and effects. Parameters are found by name in
the plugin's parameter list (plugins.Parameter), which is quick even for big synths.

While a plugin's window is open, TweakWatcher notes which parameters you change there (the
plugin tells the host about each change made with the mouse; the parameter chooser lists the last
ones first). Several changing at once is a preset being loaded, not a tweak; parameters that
automation is moving don't count either.

While playing (and when rendering), apply() sets each automated parameter to the curve's value
at the start of every buffer, before the plugin processes it.

A parameter with a few values only (a switch: On / Off; a choice of 3 styles…; its `steps`, from
the plugin) has a stepped curve: its points take one of those values (snap()), and it holds each
point's value until the next point, jumping there, rather than sloping through values it can't take. A lane without points leaves its
parameter alone. While stopped, nothing is set, so a plugin's own controls can still be moved.
"""
from bisect import bisect_right
from operator import itemgetter

from .plugins import Parameter, usable

# Placeholders some plugins expose by the thousand.
IGNORED_PREFIXES = ("MIDI CC",)


RECENT = 10  # parameters remembered as recently tweaked, per plugin
PRESET_CHANGE = 4  # more parameters than this changing between two looks: a preset was loaded


def is_placeholder(name):
    return name.startswith(IGNORED_PREFIXES) or (len(name) == 4 and name[0] == "P" and name[1:].isdigit())


def plugins_of(track):
    """The plugins a track's automation can belong to: its instrument (or those of its instrument patch
    panel), then its effects."""
    return [*track.instruments(), *track.effects]


def parameters(slot):
    """[(name, its parameter)] a plugin can automate, in its own order (GUI thread)."""
    if not usable(slot):
        return []
    return [(info["name"], Parameter(slot.instance, info)) for info in slot.instance.parameters()
            if info["automatable"] and not info["read_only"] and not info["hidden"] and not is_placeholder(info["name"])]


class TweakWatcher:
    """Asks a plugin now and then (poll(), GUI thread) which parameters were changed in its window, and
    keeps their names in slot.recent, latest first."""

    def __init__(self, slot, project):
        self.slot, self.project = slot, project
        self.names = {param.id: name for name, param in parameters(slot)}
        slot.instance.edited()  # (forget changes from before)

    def automated(self):
        """Names of this plugin's parameters that automation moves (curves with points)."""
        return {a.param for t in self.project.tracks for a in t.automation if a.slot is self.slot and a.points}

    def poll(self):
        if not usable(self.slot):
            return
        edited = self.slot.instance.edited()
        changed = list(dict.fromkeys(self.names[i] for i in reversed(edited) if i in self.names))  # latest first
        changed = [name for name in changed if name not in self.automated()]
        if 0 < len(changed) <= PRESET_CHANGE:
            self.slot.recent = tuple(dict.fromkeys(changed + list(self.slot.recent)))[:RECENT]


def prune(track):
    """Drop the lanes of plugins the track no longer has (those of an instrument patch panel it doesn't play
    now, or of the instrument it played before, stay). True if any were dropped."""
    slots = [s for s in (track.instrument, *track.rack, *track.effects) if s is not None]
    kept = tuple(a for a in track.automation if any(a.slot is s for s in slots))
    if len(kept) == len(track.automation):
        return False
    track.automation = kept
    return True


def resolve(project):
    """Look up the parameter of every lane whose plugin is (newly) loaded (GUI thread, cheap when
    there is nothing new: called every frame)."""
    for track in project.tracks:
        for lane in track.automation:
            instance = lane.slot.instance
            if instance is not None and (lane.target is None or lane.target[0] is not instance):
                match = next((p for name, p in parameters(lane.slot) if name == lane.param), None)
                lane.target = (instance, match)  # (match None: the plugin has no such parameter now)
                lane.last = None


def steps_of(lane):
    """How many steps the lane's parameter has (0: any value; 1: two values, e.g. Off / On)."""
    target = lane.target
    return getattr(target[1], "steps", 0) if target is not None and target[1] is not None else 0


def snap(value, steps):
    """`value` (0…1) at the nearest of the parameter's values: k / steps (any value when steps is 0)."""
    return round(value * steps) / steps if steps > 0 else value


def value_at(points, seconds, steps=0):
    """The curve's value (0…1) at `seconds`: straight lines between points, flat before the first
    and after the last. A stepped parameter (steps > 0) holds each point's value until the next."""
    i = bisect_right(points, seconds, key=itemgetter(0))
    if i == 0:
        return snap(points[0][1], steps)
    if i == len(points) or steps > 0:
        return snap(points[i - 1][1], steps)
    (t0, v0), (t1, v1) = points[i - 1], points[i]
    return v0 + (v1 - v0) * (seconds - t0) / (t1 - t0) if t1 > t0 else v1


def apply(track, start, sr):
    """Set the track's automated parameters to their values at timeline frame `start` (audio thread,
    or the main thread when rendering)."""
    seconds = start / sr
    for lane in track.automation:
        points, target = lane.points, lane.target
        if not points or target is None or target[1] is None or target[0] is not lane.slot.instance:
            continue
        value = value_at(points, seconds, steps_of(lane))
        if lane.last is None or abs(value - lane.last) > 1e-6:
            try:
                target[1].raw_value = value
                lane.last = value
            except Exception:
                lane.target = (target[0], None)  # (a parameter that refuses: leave it alone from now on)
