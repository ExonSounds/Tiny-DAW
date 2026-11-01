"""Saving and loading projects. No GUI code.

A saved project is a JSON file plus a folder next to it named "<name>_audio":

    MySong.json
    MySong_audio/
        drums.wav
        vocals.wav

The folder holds the audio of every audio clip as 32-bit float WAV, exactly what the
program plays from (whatever format it was imported from). Clips with the same audio share one
file: split clips, and the same sample used several times (identical audio is recognised by its
content). Saving again reuses files already in the folder and never overwrites or deletes any.
MIDI notes are stored in the JSON itself. Times are in seconds, so a project opens at
any sample rate. The tempo, time signature, grid view and loop range are stored under "timing".

VST3 plugins are stored by their file path plus their settings (base64 of the plugin's own
state); the plugins themselves stay where they are installed. load_project() only restores
these PluginSlots: loading the plugins is the GUI's job, on the main thread (audio/plugins.py).
Effects also keep their place in the patch space ("at"), and "cables" / "master_cables" list how
they are wired: [from, to] with "in", "out", an effect's number, ["side", number] for an
effect's sidechain input, or ["track", number] for another track (its place in "tracks") heard by
a sidechain. Projects saved before effects
were wired by hand have no cables: their effects are wired one after the other, as they played.
"""
import base64
import hashlib
import json
import os
import re
import weakref
from dataclasses import asdict, fields

import numpy as np
import soundfile as sf

from . import config
from .audio.file_io import load_audio
from .model import (AUDIO, GROUP, MIDI, PATCH_IN, PATCH_OUT, AudioClip, Automation, Group, MidiClip, PluginSlot,
                    Sidechain, Track, TrackSource, chain)
from .timing import Timing

FORMAT, VERSION = "tinydaw-project", 1
TRACK_FIELDS = ("name", "kind", "muted", "soloed", "output", "volume_db", "height", "color", "tone",
                "needs_instrument", "show_automation", "uses_rack")
SLOT_FIELDS = ("path", "name", "kind", "plugin_name", "bypass", "volume_db")
GROUP_FIELDS = ("name", "folded", "muted", "soloed", "color", "tone")


def describe_slot(slot, states=True):
    """states=False leaves out the plugin's settings: some plugins describe the same settings with
    different bytes each time, so they can't tell whether anything changed (see describe())."""
    state = slot.current_state() if states else None
    entry = {**{f: getattr(slot, f) for f in SLOT_FIELDS},
             "state": base64.b64encode(state).decode("ascii") if state else None}
    if slot.at is not None:
        entry["at"] = list(slot.at)
    return entry


def read_slot(entry):
    state = entry.get("state")
    at = entry.get("at")
    return PluginSlot(**{f: entry[f] for f in SLOT_FIELDS if f in entry},
                      state=base64.b64decode(state) if state else None,
                      at=(int(at[0]), int(at[1])) if isinstance(at, list) and len(at) == 2 else None)


def describe_cables(cables, effects, tracks):
    """[[from, to], …]: "in", "out", the effect's number in `effects`, ["side", its number], or ["track",
    its number in `tracks`] (cables from tracks that are gone are left out)."""
    def name(end):
        if isinstance(end, TrackSource):
            return ["track", tracks.index(end.track)]
        if isinstance(end, Sidechain):
            return ["side", name(end.slot)]
        return end if end in (PATCH_IN, PATCH_OUT) else next(i for i, s in enumerate(effects) if s is end)
    return [[name(a), name(b)] for a, b in cables if not (isinstance(a, TrackSource) and a.track not in tracks)]


def read_cables(entries, effects, tracks=()):
    """Cables from describe_cables(); cables to effects or from tracks that aren't there (or the wrong
    way round) are left out. None (a project from before cables): the effects one after the other."""
    if entries is None:
        return chain(effects)

    def end(name, ends):
        if isinstance(name, list) and len(name) == 2 and name[0] == "track":  # (only ever where a cable comes from)
            ok = PATCH_IN in ends and isinstance(name[1], int) and 0 <= name[1] < len(tracks)
            return TrackSource(tracks[name[1]]) if ok else None
        if isinstance(name, list):  # ["side", n]: only ever where a cable goes to
            target = end(name[1], ()) if len(name) == 2 and name[0] == "side" and PATCH_OUT in ends else None
            return Sidechain(target) if target is not None else None
        if name in ends:
            return name
        return effects[name] if isinstance(name, int) and 0 <= name < len(effects) else None

    cables = []
    for entry in entries:
        if isinstance(entry, list) and len(entry) == 2:
            a, b = end(entry[0], (PATCH_IN,)), end(entry[1], (PATCH_OUT,))
            if (a is not None and b is not None and a is not b and (a, b) not in cables and Sidechain(a) != b
                    and (not isinstance(a, TrackSource) or isinstance(b, Sidechain))):
                cables.append((a, b))
    return tuple(cables)


def describe_track(track, groups, sr, audio_file, states=True, tracks=()):
    """groups: the project's groups in order; the track names its group by index. A frozen track
    names the file of its rendering (audio_file, as for clips). tracks: all of them (its cables name
    the other tracks its sidechains listen to by their place)."""
    frozen = track.frozen
    return {**{f: getattr(track, f) for f in TRACK_FIELDS},
            "group": groups.index(track.group) if track.group else None,
            "frozen": {"file": audio_file(frozen), "start": frozen.start / sr} if frozen else None,
            "instrument": describe_slot(track.instrument, states) if track.instrument else None,
            "effects": [describe_slot(s, states) for s in track.effects],
            "cables": describe_cables(track.cables, track.effects, tracks),
            "rack": [describe_slot(s, states) for s in track.rack],
            "rack_cables": describe_cables(track.rack_cables, track.rack, ()),
            "automation": [describe_automation(track, a) for a in track.automation]}


def describe_automation(track, lane):
    """An automation lane: its plugin ("instrument", ["rack", its place in the instrument patch panel], or
    the effect's place in the chain), parameter and points."""
    rack = [i for i, s in enumerate(track.rack) if s is lane.slot]
    plugin = "instrument" if lane.slot is track.instrument else ["rack", rack[0]] if rack else next(
        i for i, s in enumerate(track.effects) if s is lane.slot)
    return {"plugin": plugin, "param": lane.param, "height": lane.height, "points": [list(p) for p in lane.points]}


def read_automation(track, entries):
    """The automation lanes of a track (its plugins already read); lanes of missing plugins are left out."""
    lanes = []
    for entry in entries:
        plugin = entry.get("plugin")
        if isinstance(plugin, list) and len(plugin) == 2 and plugin[0] == "rack":
            slot = track.rack[plugin[1]] if isinstance(plugin[1], int) and 0 <= plugin[1] < len(track.rack) else None
        else:
            slot = track.instrument if plugin == "instrument" else (
                track.effects[plugin] if isinstance(plugin, int) and 0 <= plugin < len(track.effects) else None)
        if slot is None or not entry.get("param"):
            continue
        lane = Automation(slot, entry["param"], height=int(entry.get("height", config.AUTOMATION_H)))
        lane.set_points(entry.get("points", ()))
        lanes.append(lane)
    return tuple(lanes)


def audio_folder(json_path):
    stem = os.path.splitext(os.path.basename(json_path))[0]
    return os.path.join(os.path.dirname(os.path.abspath(json_path)), f"{stem}_audio")


def describe(project, sr, master, audio_file=lambda clip: id(clip.source), states=True):
    """The project as a JSON-ready dict. `audio_file(clip)` names each audio clip's file;
    by default it is the identity of its audio, which is enough to tell whether anything changed.
    states=False leaves out plugin settings (for telling whether anything changed)."""
    rows, groups = project.rows(), project.groups()
    clips = []
    for clip in project.clips:
        entry = {"kind": clip.kind, "name": clip.name, "track": rows[clip.track],
                 "start": clip.start / sr, "offset": clip.offset / sr,
                 "length": None if clip.length is None else clip.length / sr,
                 "color": clip.color, "tone": clip.tone, "disabled": clip.disabled}
        if clip.kind == AUDIO:
            entry["file"] = audio_file(clip)
        else:
            entry["notes"] = clip.notes.tolist()  # [start s, end s, pitch, velocity]
            entry["midi_length"] = clip.length_s
        clips.append(entry)
    return {"format": FORMAT, "version": VERSION, "master": master, "tracks_made": dict(project.tracks_made),
            "timing": asdict(project.timing),
            "groups": [{f: getattr(g, f) for f in GROUP_FIELDS} for g in groups],
            "tracks": [describe_track(t, groups, sr, audio_file, states, project.tracks) for t in project.tracks],
            "clips": clips,
            "master_effects": [describe_slot(s, states) for s in project.master_effects],
            "master_cables": describe_cables(project.master_cables, project.master_effects, project.tracks)}


def save_project(path, project, sr, master):
    """Write the JSON and any audio not yet in the project's audio folder."""
    folder = audio_folder(path)
    os.makedirs(folder, exist_ok=True)
    folder_name = os.path.basename(folder)

    # Audio clips, and the renderings of frozen tracks
    audio = [c for c in project.clips if c.kind == AUDIO] + [t.frozen for t in project.tracks if t.frozen]
    files = {}  # id(source) -> file name in the folder
    by_sound = {}  # fingerprint of the audio -> file name: the same audio is kept in one file
    # Audio already saved in this folder (from an earlier save or load) is reused as is.
    for clip in audio:
        p = clip.source_path
        if p and os.path.dirname(os.path.abspath(p)) == folder and os.path.exists(p):
            files[id(clip.source)] = by_sound.setdefault(fingerprint(clip.source, clip.source_sr), os.path.basename(p))
    # Everything else is written as a new file, never over an existing one (once per sound).
    for clip in audio:
        if id(clip.source) not in files:
            sound = fingerprint(clip.source, clip.source_sr)
            if sound not in by_sound:
                by_sound[sound] = unique_file_name(folder, clip.name)
                sf.write(os.path.join(folder, by_sound[sound]), clip.source, clip.source_sr, subtype="FLOAT")
            files[id(clip.source)] = by_sound[sound]
    for clip in audio:
        clip.source_path = os.path.join(folder, files[id(clip.source)])

    data = describe(project, sr, master, lambda clip: f"{folder_name}/{files[id(clip.source)]}")
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(temp, path)  # a crash mid-save never leaves a half-written project


_fingerprints = {}  # (id(audio), rate) -> its fingerprint, while that audio exists (audio is never changed)


def fingerprint(audio, sr):
    """A fingerprint of a clip's audio: equal only for identical audio at the same rate. Each piece
    of audio is fingerprinted once, so saving stays quick."""
    key = (id(audio), sr)
    if key not in _fingerprints:
        digest = hashlib.sha1(memoryview(np.ascontiguousarray(audio)).cast("B"))
        digest.update(f"{audio.shape} {audio.dtype} {sr}".encode())
        _fingerprints[key] = digest.hexdigest()
        weakref.finalize(audio, _fingerprints.pop, key, None)  # forgotten with the audio (its id may be reused)
    return _fingerprints[key]


def unique_file_name(folder, clip_name):
    stem = re.sub(r"[^\w\- ]+", "_", os.path.splitext(clip_name)[0]).strip() or "audio"
    name, n = f"{stem}.wav", 2
    while os.path.exists(os.path.join(folder, name)):
        name, n = f"{stem} ({n}).wav", n + 1
    return name


def load_project(path, project, sr):
    """Fill `project` from the file at `path`, preparing clips for sample rate sr.

    Returns the master settings. Everything is read before `project` is touched, so a
    broken or incomplete project raises and leaves the current one as it was.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("format") != FORMAT:
        raise ValueError("This is not a Tiny DAW project file.")
    base = os.path.dirname(os.path.abspath(path))
    groups = [Group(**{f: g[f] for f in GROUP_FIELDS if f in g}) for g in data.get("groups", ())]
    tracks = [Track(**{f: t[f] for f in TRACK_FIELDS if f in t},
                    group=groups[t["group"]] if t.get("group") is not None else None,
                    instrument=read_slot(t["instrument"]) if t.get("instrument") else None,
                    effects=tuple(read_slot(s) for s in t.get("effects", ())),
                    rack=tuple(read_slot(s) for s in t.get("rack", ())))
              for t in data["tracks"]]
    for track, entry in zip(tracks, data["tracks"]):
        track.cables = read_cables(entry.get("cables"), track.effects, tracks)
        track.rack_cables = tuple(c for c in read_cables(entry.get("rack_cables") or [], track.rack)
                                  if (c[0] == PATCH_IN) != (c[1] == PATCH_OUT))  # (notes in, sound out: nothing else)
        track.automation = read_automation(track, entry.get("automation", ()))
        frozen = entry.get("frozen")
        file = os.path.normpath(os.path.join(base, frozen["file"])) if frozen else None
        if file and os.path.exists(file):  # (its file missing: the track opens unfrozen, playing live)
            source, source_sr = load_audio(file)
            track.frozen = AudioClip(name=f"{track.name} (frozen)", track=track, start=round(frozen["start"] * sr),
                                     source=source, source_sr=source_sr, source_path=file)
            track.frozen.prepare(sr)

    sources, missing, clips = {}, [], []  # sources: file path -> (audio, rate), loaded once and shared
    for entry in data["clips"]:
        common = dict(name=entry["name"], track=tracks[entry["track"]], start=round(entry["start"] * sr),
                      offset=round(entry["offset"] * sr),
                      length=None if entry["length"] is None else round(entry["length"] * sr),
                      color=entry.get("color"), tone=entry.get("tone"), disabled=entry.get("disabled", False))
        if entry["kind"] == AUDIO:
            file = os.path.normpath(os.path.join(base, entry["file"]))
            if not os.path.exists(file):
                missing.append(entry["file"])
                continue
            if file not in sources:
                sources[file] = load_audio(file)
            source, source_sr = sources[file]
            clip = AudioClip(source=source, source_sr=source_sr, source_path=file, **common)
        else:
            notes = np.array(entry["notes"], dtype=np.float64).reshape(-1, 4)
            clip = MidiClip(notes=notes, length_s=entry["midi_length"], **common)
        clip.prepare(sr)
        clips.append(clip)
    if missing:
        raise FileNotFoundError("Audio files are missing:\n" + "\n".join(missing))
    timing = read_timing(data.get("timing", {}))

    master_effects = tuple(read_slot(s) for s in data.get("master_effects", ()))
    master_cables = read_cables(data.get("master_cables"), master_effects, tracks)

    project.tracks, project.clips = tuple(tracks), tuple(clips)
    project.master_effects, project.master_cables = master_effects, master_cables
    project.master_patch_state.clear()
    made = data.get("tracks_made", {})
    project.tracks_made = {AUDIO: made.get(AUDIO, 0), MIDI: made.get(MIDI, 0), GROUP: made.get(GROUP, len(groups))}
    project.gather_groups()
    project.timing = timing
    return data.get("master", {})


def read_timing(entry):
    """A Timing from a project's "timing" entry; missing or out-of-range values get the defaults."""
    timing = Timing(**{f.name: entry[f.name] for f in fields(Timing) if f.name in entry})
    if not config.MIN_BPM <= timing.bpm <= config.MAX_BPM:
        timing.bpm = config.DEFAULT_BPM
    if not 1 <= timing.beats_per_bar <= config.MAX_BEATS_PER_BAR:
        timing.beats_per_bar = config.DEFAULT_BEATS_PER_BAR
    if timing.loop_start < 0 or timing.loop_range() is None:
        timing.loop_start, timing.loop_end, timing.looping = 0.0, 0.0, False
    return timing
