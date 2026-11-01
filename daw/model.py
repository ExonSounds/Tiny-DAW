"""The project data: tracks and the clips placed on them. No audio-device or GUI code.

Tuples are replaced, never mutated, so the audio thread always iterates a
consistent snapshot without locking.

There are two kinds of track, AUDIO and MIDI, and each only holds clips of
its own kind. Both clip kinds expose `data`: audio at the engine rate that the
mixer plays. A MIDI clip gets its `data` by rendering its notes with the
built-in synth (audio/synth.py); on a MIDI track with a VST3 instrument the
notes are played live through the plugin instead.

Tracks of both kinds can be put in a Group: shown together, folded away together, muted
and soloed together.

Any track (and the master) can hold VST3 effects, placed in its patch space (the FX window) and
wired by hand with cables: `cables` is a tuple of (from, to), from = PATCH_IN (the track's sound
before its effects), an effect, or another track (TrackSource: its sound after its effects and
fader, only ever into a Sidechain), to = an effect, an effect's Sidechain input (if it has one),
or PATCH_OUT (on to the track's fader). An output
can feed several inputs, and an input adds up all that comes in (audio/patch.py). A new track has
IN wired straight to OUT; an effect that isn't wired in is placed but not heard. A PluginSlot keeps
what a project saves about a plugin; the loaded plugin itself is `instance`.

A MIDI track can also play several VST instruments at once, from its instrument patch panel
(`uses_rack`): `rack` holds them, each with its own volume, and `rack_cables` wires them the same
way, only ever PATCH_IN (the track's notes) → an instrument, and an instrument → PATCH_OUT (on to
its effects' IN). Each heard instrument's sound is added up; a newly placed one isn't wired.
"""
from dataclasses import dataclass, field

import numpy as np

from . import config
from .timing import Timing
from .audio import synth
from .audio.dsp import resample, waveform_peaks

AUDIO, MIDI = "audio", "midi"
GROUP = "group"  # key for numbering groups in Project.tracks_made
PATCH_IN, PATCH_OUT = "in", "out"  # the two ends of a patch space (see the top)
STRAIGHT = ((PATCH_IN, PATCH_OUT),)  # cables of a patch without effects in the way


@dataclass(frozen=True)
class Sidechain:
    """The sidechain input of an effect, as the end of a cable (the same effect: the same input)."""
    slot: object


@dataclass(frozen=True)
class TrackSource:
    """Another track's sound (after its effects and fader), as where a cable into a sidechain comes from."""
    track: object


def chain(slots):
    """Cables wiring `slots` one after the other: IN → first → … → last → OUT."""
    ends = (PATCH_IN, *slots, PATCH_OUT)
    return tuple(zip(ends, ends[1:]))


@dataclass(eq=False)
class PluginSlot:
    """A VST3 plugin on a track or the master."""
    path: str  # the plugin file (see audio/plugins.py)
    name: str
    kind: str  # "instrument" or "effect"
    plugin_name: str = None  # which plugin, for files that hold several
    bypass: bool = False
    state: bytes = None  # its settings as last saved or loaded (VST3 state)
    instance: object = None  # the loaded plugin; None while not loaded (or missing)
    error: str = None  # set by the audio thread if the plugin failed; it is then skipped
    recent: tuple = ()  # names of the parameters tweaked last in its window, latest first (not saved)
    at: tuple = None  # (x, y) of its unit in the patch space (FX window, instrument panel); None = not placed yet
    volume_db: float = 0.0  # instruments in an instrument patch panel: their own volume (MIN_DB = silent)

    def current_state(self):
        from .audio.plugins import state_of  # (the host is only needed once plugins are)
        return state_of(self.instance) if self.instance is not None else self.state


@dataclass(eq=False)
class Group:
    """Tracks (audio and MIDI alike) kept together under a header lane, which can fold them away.
    Muting or soloing the group mutes or solos all its tracks. Its tracks are always next to each
    other in Project.tracks (Project keeps them so)."""
    name: str
    folded: bool = False  # member lanes hidden; the header lane shows their clips in miniature
    muted: bool = False
    soloed: bool = False
    color: str = None  # "#rrggbb" for its header row; None = the standard look
    tone: str = None  # as Track.tone


@dataclass(eq=False)
class Automation:
    """One parameter of one of a track's plugins, following a curve over time (a lane under the
    track; audio/automation.py applies it)."""
    slot: PluginSlot  # the track's instrument or one of its effects
    param: str  # the parameter's name in the plugin
    points: tuple = ()  # ((seconds, value 0…1), …) by time; replaced, never changed (the audio thread reads it)
    height: int = config.AUTOMATION_H
    target: object = None  # (plugin instance, its parameter), looked up by audio/automation.resolve()
    last: float = None  # the value last given to the plugin (audio thread)

    def set_points(self, points):
        """Replace the curve: kept in time order, values within 0…1."""
        self.points = tuple(sorted((max(0.0, float(t)), min(1.0, max(0.0, float(v)))) for t, v in points))


@dataclass(eq=False)
class Track:
    name: str
    kind: str = AUDIO
    muted: bool = False
    soloed: bool = False
    output: int = 0  # first device output (0-based) of the pair the track plays on: 0 = Out 1/2, 2 = Out 3/4
    volume_db: float = 0.0  # its fader, after its effects (MIN_DB = silent); not part of freezes and bounces
    height: int = config.TRACK_H  # lane height in pixels, dragged by its bottom edge
    instrument: PluginSlot = None  # MIDI tracks: a VST3 instrument; None = the built-in synth
    needs_instrument: bool = False  # a new MIDI track: silent, its row saying "Choose instrument", until one is
    #                                 chosen (instrument_menu.py)
    effects: tuple = ()  # PluginSlots in its patch space; replaced, never mutated
    cables: tuple = STRAIGHT  # how they are wired (see the top); replaced, never mutated
    color: str = None  # "#rrggbb" for its row in the panel; None = the standard look
    tone: str = None  # "light" / "dark": fixed text colour on its row (if readable); None = automatic
    group: Group = None  # the group it belongs to, if any
    frozen: object = None  # an AudioClip of the track rendered (audio/render.py), played instead of its clips
    #                        and plugins while frozen; None = not frozen
    automation: tuple = ()  # Automation lanes, top to bottom; replaced, never changed in place
    show_automation: bool = False  # its automation lanes are shown under it
    patch_state: dict = field(default_factory=dict)  # the audio thread's working state for its cables (patch.py)
    uses_rack: bool = False  # MIDI tracks: it plays the instruments of its instrument patch panel (`instrument` rests)
    rack: tuple = ()  # PluginSlots of instruments in its instrument patch panel; replaced, never mutated
    rack_cables: tuple = ()  # how they are wired: (PATCH_IN, instrument) and (instrument, PATCH_OUT) only
    rack_state: dict = field(default_factory=dict)  # the audio thread's delay lines lining up its instruments

    def instruments(self):
        """The instrument plugins it plays: those of its instrument patch panel, its one instrument, or none
        (the built-in synth; audio tracks)."""
        if self.kind != MIDI or self.needs_instrument:
            return ()
        if self.uses_rack:
            return self.rack
        return (self.instrument,) if self.instrument is not None else ()


@dataclass(eq=False, kw_only=True)
class Clip:
    """A window onto `data`: frames [offset, offset + frames) play from timeline frame `start`.

    Splitting a clip makes copies that share `data` with different windows (see edits.py).
    """
    kind = None  # AUDIO or MIDI, set by the subclasses
    name: str
    track: Track = None
    start: int = 0  # timeline position in engine frames
    offset: int = 0  # first frame of `data` that plays
    length: int = None  # frames that play; None = to the end of `data`
    color: str = None  # "#rrggbb" picked by the user; None = the default for its kind
    tone: str = None  # waveform/notes and name: "light" (white), "dark" (dark grey) or None = automatic
    disabled: bool = False  # stays on the timeline, drawn as an outline, but doesn't play (nor render)
    data: np.ndarray = None  # stereo audio at the engine rate (set by prepare)

    def prepare(self, sr):
        """(Re)build `data` for the engine's sample rate."""
        raise NotImplementedError

    @property
    def frames(self):
        return self.length if self.length is not None else len(self.data) - self.offset

    @property
    def end(self):
        return self.start + self.frames


@dataclass(eq=False, kw_only=True)
class AudioClip(Clip):
    kind = AUDIO
    source: np.ndarray  # original stereo audio
    source_sr: int
    source_path: str = None  # WAV in a saved project's audio folder holding exactly `source`, if any
    peaks: np.ndarray = None  # waveform min/max for drawing (set by prepare)

    def prepare(self, sr):
        self.data = resample(self.source, self.source_sr, sr)
        self.peaks = waveform_peaks(self.data, config.PEAK_BLOCK)


@dataclass(eq=False, kw_only=True)
class MidiClip(Clip):
    kind = MIDI
    notes: np.ndarray  # (n, 4) float: start s, end s, pitch, velocity; times relative to the clip start
    length_s: float  # clip length without the synth's release tail
    events: np.ndarray = None  # (n, 4) int: on frame, off frame, pitch, velocity, for VST3 instruments

    def prepare(self, sr):
        self.data = synth.render(self.notes, sr, self.length_s)
        self.events = np.column_stack([np.round(self.notes[:, :2] * sr), self.notes[:, 2:]]).astype(np.int64)


class Project:
    def __init__(self):
        self.clear()

    def clear(self):
        self.tracks = ()  # in display order
        self.clips = ()
        self.master_effects = ()  # PluginSlots on the master (outputs 1/2)
        self.master_cables = STRAIGHT  # how they are wired
        self.master_patch_state = {}
        self.tracks_made = {AUDIO: 0, MIDI: 0, GROUP: 0}  # numbering for default names; never reused
        self.timing = Timing()  # tempo, time signature, and whether to edit in bars and beats or seconds

    def forget_gone_tracks(self):
        """Unplug the sidechain cables from tracks that are gone (removed)."""
        tracks = set(self.tracks)
        for owner, attr in [(t, "cables") for t in self.tracks] + [(self, "master_cables")]:
            cables = getattr(owner, attr)
            kept = tuple(c for c in cables if not (isinstance(c[0], TrackSource) and c[0].track not in tracks))
            if len(kept) != len(cables):
                setattr(owner, attr, kept)

    def plugin_slots(self):
        """Every plugin in the project: instruments, track effects, master effects."""
        for track in self.tracks:
            if track.instrument is not None:
                yield track.instrument
            yield from track.rack
            yield from track.effects
        yield from self.master_effects

    # --- tracks ---

    def add_track(self, kind=AUDIO, group=None, after=None):
        """A new track at the bottom, or right below track `after`, or at the end of `group`."""
        self.tracks_made[kind] += 1
        track = Track(f"{'MIDI' if kind == MIDI else 'Audio'} {self.tracks_made[kind]}", kind, group=group,
                      needs_instrument=kind == MIDI)
        at = self.tracks.index(after) + 1 if after in self.tracks else len(self.tracks)
        self.tracks = self.tracks[:at] + (track,) + self.tracks[at:]
        self.gather_groups()
        return track

    # --- groups ---

    def groups(self):
        """The groups in use, top to bottom (a group with no tracks left is gone)."""
        return list(dict.fromkeys(t.group for t in self.tracks if t.group is not None))

    def members(self, group):
        return [t for t in self.tracks if t.group is group]

    def group_tracks(self, tracks):
        """Put `tracks` in a new group, gathered where the topmost of them is. Returns the group."""
        self.tracks_made[GROUP] += 1
        group = Group(f"Group {self.tracks_made[GROUP]}")
        for track in tracks:
            track.group = group
        self.gather_groups()
        return group

    def ungroup(self, group):
        """The group goes; its tracks stay where they are."""
        for track in self.members(group):
            track.group = None

    def gather_groups(self):
        """Keep each group's tracks next to each other, where its topmost track is
        (a track that joined or left a group may have split it)."""
        order, done = [], set()
        for track in self.tracks:
            if track.group is None:
                order.append(track)
            elif track.group not in done:
                done.add(track.group)
                order += self.members(track.group)
        self.tracks = tuple(order)

    def remove_track(self, track):
        """Removes the track and every clip on it."""
        self.clips = tuple(c for c in self.clips if c.track is not track)
        self.tracks = tuple(t for t in self.tracks if t is not track)

    def rows(self):
        """{track: lane index}"""
        return {t: i for i, t in enumerate(self.tracks)}

    def first_empty_track(self, kind):
        """An empty track of that kind that can be seen (not in a folded group), or None."""
        used = {c.track for c in self.clips}
        return next((t for t in self.tracks if t.kind == kind and t not in used and not t.frozen
                     and not (t.group and t.group.folded)), None)

    def audible(self, track):
        """False when it or its group is muted, when other tracks or groups are soloed and
        neither it nor its group is, or when it is a MIDI track still waiting for its instrument."""
        group = track.group
        if track.muted or (group and group.muted) or track.needs_instrument:
            return False
        if track.soloed or (group and group.soloed):
            return True
        return not any(t.soloed or (t.group and t.group.soloed) for t in self.tracks)

    # --- clips ---

    def add_clip(self, clip):
        self.clips += (clip,)

    def remove_clips(self, clips):
        self.clips = tuple(c for c in self.clips if c not in clips)

    def clips_on(self, track):
        return [c for c in self.clips if c.track is track]

    def clips_in(self, tracks, start, end):
        """Clips on any of `tracks` that overlap frames [start, end)."""
        return [c for c in self.clips if c.track in tracks and c.start < end and c.end > start]

    def end_frame(self):
        return max((c.end for c in self.clips), default=0)

    def change_sample_rate(self, old_sr, new_sr):
        for clip in self.clips + tuple(t.frozen for t in self.tracks if t.frozen):
            clip.start = clip.start * new_sr // old_sr
            clip.offset = clip.offset * new_sr // old_sr
            if clip.length is not None:
                clip.length = clip.length * new_sr // old_sr
            clip.prepare(new_sr)
