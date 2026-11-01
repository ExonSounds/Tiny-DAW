"""The sound through a patch space: a track's (or the master's) effects, wired with cables (model.py).

The sound comes in at PATCH_IN and goes wherever the cables take it. An effect's input adds up
everything wired into it (nothing wired in: silence), and its output can feed several inputs; what
reaches PATCH_OUT goes on to the fader (nothing: the track is silent). An effect with a sidechain
input gets what is wired into its Sidechain the same way (nothing: silence); besides IN and effects,
other tracks can be wired into a sidechain (TrackSource: their sound after their effects and fader,
handed over by the mixer, which processes them first). Only effects whose
output reaches OUT are processed; a bypassed effect, or one that isn't loaded or has stopped after
an error, passes its input on unchanged.

Effects with look-ahead hold their sound back (plugins.latency). Where paths of different delays
meet (an input's cables, or an effect's input and sidechain), the quicker ones are held back to
match (a DelayLine per cable), so parallel paths stay in time, and patch_latency() is how late OUT
is: the track's share of the delay compensation. Everything is counted from the timeline: IN may
already be late (an instrument with look-ahead; for the master, the latest track), and another
track's sound is as late as that track (plugins.track_latency).
Runs on the audio thread (and in renders); the plan of a set of cables is worked out once.

A MIDI track's instrument patch panel is simpler: its notes go from IN to each instrument wired from
it, and the sound of every instrument wired to OUT is added up, each at its own volume and held back
to line up with the latest of them (play_instruments()).
"""
import numpy as np

from .. import config
from ..model import PATCH_IN, PATCH_OUT, STRAIGHT, Sidechain, TrackSource
from .dsp import db_to_gain
from .plugins import (fed_instruments, heard_instruments, latency, play_instrument, process_effect, track_latency,
                      usable)


class DelayLine:
    """Holds a sound back by `frames` (a track's share of the delay compensation, or a cable's)."""

    def __init__(self, frames, channels):
        self.frames, self.channels = frames, channels
        self.held = np.zeros((frames, channels), np.float32)
        self.sounding = False  # sound still held, to come out in the next buffers

    def process(self, signal):
        """Delay signal (frames, channels: a view) in place."""
        if not self.frames:
            return
        joined = np.concatenate([self.held, signal])
        signal[:] = joined[:len(signal)]
        self.held = joined[len(signal):]
        self.sounding = bool(self.held.any())

    def clear(self):
        self.held[:] = 0
        self.sounding = False


def feeds(node, sources):
    """What is wired into an effect (its input, then its sidechain) or into OUT."""
    if node == PATCH_OUT:
        return sources.get(PATCH_OUT, [])
    return sources.get(node, []) + sources.get(Sidechain(node), [])


def plan(cables):
    """(order, sources): the effects that are heard (their output reaches OUT), each after all that feed
    it; and {effect, Sidechain or PATCH_OUT: what is wired into it}. A cable that would close a loop is
    left out (the patch panel never makes one)."""
    sources = {}
    for a, b in cables:
        sources.setdefault(b, []).append(a)
    order, state = [], {}

    def visit(node):
        state[node] = "open"
        for end in ([node] if node == PATCH_OUT else [node, Sidechain(node)]):
            kept = []
            for a in sources.get(end, ()):
                if a == PATCH_IN or isinstance(a, TrackSource) or state.get(a) == "done":
                    kept.append(a)
                elif a not in state:
                    visit(a)
                    kept.append(a)
                # (else: `a` is still open, so a → node would close a loop)
            if end in sources:
                sources[end] = kept
        state[node] = "done"
        if node != PATCH_OUT:
            order.append(node)

    visit(PATCH_OUT)
    return order, sources


def active(slot):
    return usable(slot) and not slot.bypass


def leaves(sources):
    """The other tracks wired in."""
    return {a for wired in sources.values() for a in wired if isinstance(a, TrackSource)}


def delays_of(order, sources, in_late=0, seen=frozenset()):
    """{node: frames its output is late}, OUT included (see the top)."""
    late = {PATCH_IN: in_late}
    for source in leaves(sources):
        late[source] = track_latency(source.track, seen)
    for node in order + [PATCH_OUT]:
        came = max((late[a] for a in feeds(node, sources)), default=0)
        late[node] = came + (latency(node) if node != PATCH_OUT and active(node) else 0)
    return late


def patch_latency(cables, in_late=0, seen=frozenset()):
    """Frames OUT is late, IN being `in_late` frames late (the slowest path to OUT). seen: tracks whose
    latency is being worked out already (a loop of tracks feeding each other's sidechains counts once)."""
    if cables == STRAIGHT:
        return in_late
    return delays_of(*plan(cables), in_late, seen)[PATCH_OUT]


def play_instruments(track, messages, out, sr, state):
    """Render one buffer of the instruments a track plays and add them into out (frames, channels): its
    one instrument, or each heard instrument of its instrument patch panel (at its own volume, held back by
    a DelayLine in `state` to line up with the latest). Instruments of the panel that don't get the notes
    any more, or aren't heard, let go of the notes they hold; all get "all notes off"."""
    heard = heard_instruments(track)
    if not track.uses_rack:
        for slot in heard:
            play_instrument(slot, messages, out, sr)
        return
    fed = fed_instruments(track)
    resets = [m for m in messages if m.type == "control_change"]
    for slot in track.rack:
        if usable(slot) and (slot not in heard or slot not in fed) and slot.instance.holding():
            slot.instance.notes_off()
    latest = max((latency(s) for s in heard), default=0)
    channels = out.shape[1]
    sound = np.empty_like(out)
    for slot in heard:
        sound.fill(0)
        play_instrument(slot, messages if slot in fed else resets, sound, sr)
        wanted = latest - latency(slot)
        line = state.get(slot)
        if line is None or (line.frames, line.channels) != (wanted, channels):
            line = state[slot] = DelayLine(wanted, channels)
        line.process(sound)
        if slot.volume_db != 0:
            sound *= db_to_gain(slot.volume_db, config.MIN_DB)
        out += sound
    for gone in [s for s in state if s not in heard]:
        del state[gone]


def run_patch(cables, signal, sr, state, tracks=None, in_late=0):
    """Process signal (frames, channels: a view) through the cables' effects, in place. state: a dict
    kept by the caller between buffers (the plan and the cables' delay lines). tracks: {TrackSource:
    (its sound (2, frames), frames it is late)} for the other tracks wired in (missing: silence).
    in_late: frames the signal is late already. Returns how late the result is."""
    if cables == STRAIGHT:
        return in_late
    if state.get("cables") is not cables:  # wired differently: plan afresh
        state.clear()
        state["cables"], (state["order"], state["sources"]) = cables, plan(cables)
        state["lines"] = {}
    order, sources, lines = state["order"], state["sources"], state["lines"]
    frames, channels = signal.shape
    sound = {PATCH_IN: np.ascontiguousarray(signal.T if channels == 2 else np.repeat(signal[:, :1].T, 2, axis=0))}
    late = {PATCH_IN: in_late}
    for source in leaves(sources):
        sound[source], late[source] = (tracks or {}).get(source) or (np.zeros((2, frames), np.float32), 0)
    for node in order:
        came = max((late[a] for a in feeds(node, sources)), default=0)
        x = gather(node, sources.get(node, ()), sound, late, came, lines, frames)
        late[node] = came
        if active(node):
            side_wired = sources.get(Sidechain(node))
            side = (gather(Sidechain(node), side_wired, sound, late, came, lines, frames)
                    if side_wired and node.instance.has_sidechain else None)
            try:
                x = process_effect(node, x, side, sr)
                late[node] = came + latency(node)
            except Exception as e:  # a failing plugin is switched off, never the whole audio stream
                node.error = str(e) or type(e).__name__
        sound[node] = x
    wired = sources.get(PATCH_OUT, ())
    latest = max((late[a] for a in wired), default=in_late)
    out = gather(PATCH_OUT, wired, sound, late, latest, lines, frames)
    signal[:] = out[:channels].T
    return latest


def gather(end, wired, sound, late, latest, lines, frames):
    """What comes into `end` (an effect's input, a Sidechain, OUT): its cables added up, (2, frames),
    each held back to be `latest` frames late."""
    if not wired:
        return np.zeros((2, frames), np.float32)
    total = None
    for a in wired:
        y = sound[a]
        if latest != late[a]:
            line = lines.get((a, end))
            if line is None or line.frames != latest - late[a]:
                line = lines[(a, end)] = DelayLine(latest - late[a], 2)
            y = y.T.copy()  # (the same sound may go on elsewhere undelayed)
            line.process(y)
            y = y.T
        total = y if total is None else total + y
    return np.ascontiguousarray(total)
