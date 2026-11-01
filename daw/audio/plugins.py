"""VST3 and VST2 plugins (instruments and effects), hosted by Tiny DAW's own plugin host: the
native module vst3host (native/vst3host.cpp on Steinberg's VST3 SDK, and native/vst2plugin.cpp;
see native/build.ps1). Both kinds have the same methods, so the rest of the program treats them alike.

How the program uses it:
  - Plugins are loaded, and their windows (plugin_editor.py), state, parameter names and texts
    used, on the main thread; the audio thread processes them. Loading one never holds up the
    others. A render processes plugins on the main thread, so meanwhile the audio thread leaves
    them alone (Engine.plugins_paused_while).
  - Effects get a stereo input, plus their sidechain input when they have one (has_sidechain;
    audio/patch.py feeds it from its cables). Instruments get the notes of each buffer, timed to
    the frame; hanging notes are ended with note-offs for every note still held (notes_off()).
  - A plugin with look-ahead reports how late it is (latency, in frames); its output is that much
    behind its input, and the mixer delays every other track to match (plugin delay compensation).
  - Plugins are processed continuously while the audio device is open, playing or not (as DAWs
    do): otherwise synths like Serum 2 show "DAW suspended audio processing", can't be played
    from their own keyboard, and effect tails freeze. So the main thread never processes or
    resets a plugin while a device is open.
  - A plugin's state is its processor's and its window's settings (the VST3 way), kept as
    "TDV3" + the length of the first + both. States saved by versions up to 0.3.1 (pedalboard,
    JUCE's format) are read too (juce_state.py).

Finding plugins is instant: the VST3 folders are listed, and each plugin's kind (instrument or
effect) comes from the bundle's moduleinfo.json or, failing that, from a cache. The kinds still
unknown are found out in the background when the program starts (plugin_probe.py, in a process of
its own; learn()), once per plugin file (again only if the file changes), and otherwise the first
time a plugin is loaded. VST2 plugins are DLLs in the folders chosen in Options → VST2 plugin
folders: a 64-bit DLL whose export table has "VSTPluginMain" (or "main") is one (read from the
file, never run; vst2_entry()), and the answer is cached by the file's date. Their names end in
"(VST2)", as many plugins come in both kinds (plugin_cache.json in %APPDATA%\\TinyDAW). The cache also keeps how long
each plugin took to load last time, for the "please wait" message.
"""
import glob
import json
import os
import re
import struct
import time
from dataclasses import dataclass

import numpy as np
from mido import Message

from . import juce_state
from ..model import PATCH_IN, PATCH_OUT

try:
    from . import vst3host
except ImportError as e:  # not built for this Python (native/build.ps1): no plugins
    vst3host, HOST_MISSING = None, str(e)
else:
    HOST_MISSING = None

DEFAULT_FOLDERS = (r"C:\Program Files\Common Files\VST3",
                   os.path.expandvars(r"%LOCALAPPDATA%\Programs\Common\VST3"))
# where VST2 plugins usually are (Options → VST2 plugin folders offers those that exist)
VST2_USUAL_FOLDERS = (r"C:\Program Files\VstPlugins", r"C:\Program Files\Steinberg\VstPlugins",
                      r"C:\Program Files\Common Files\VST2", r"C:\Program Files\Common Files\Steinberg\VST2")
VST2_SUFFIX = " (VST2)"
CACHE_PATH = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "TinyDAW", "plugin_cache.json")
INSTRUMENT, EFFECT = "instrument", "effect"
LOAD_TIMES = "load seconds"  # key in the cache: {"path|plugin name": seconds the last load took}
STATE_MAGIC = b"TDV3"

# sent to an instrument when playback starts or jumps: "all sound off" and "all notes off" on every channel
ALL_NOTES_OFF = [Message("control_change", channel=ch, control=cc, value=0) for ch in range(16) for cc in (120, 123)]


@dataclass(frozen=True)
class PluginInfo:
    path: str  # the file to load (the one inside a bundle folder, if it is a bundle)
    name: str
    kind: str = None  # INSTRUMENT, EFFECT, or None until found out (plugin_probe.py) or loaded once
    plugin_name: str = None  # which plugin, for files that hold several (e.g. "Serum 2" / "Serum 2 FX")

    @property
    def label(self):
        return self.name if self.kind else f"{self.name}  (type not known yet)"


class PluginChoiceNeeded(Exception):
    """The file holds several plugins; `names` lists them."""

    def __init__(self, names):
        super().__init__(", ".join(names))
        self.names = names


def loadable_file(path):
    """For a bundle folder (X.vst3/Contents/x86_64-win/X.vst3) the inner file (projects name plugins by it)."""
    inner = os.path.join(path, "Contents", "x86_64-win", os.path.basename(path))
    return inner if os.path.isdir(path) and os.path.isfile(inner) else path


def is_vst2(path):
    return path.lower().endswith(".dll")


def vst2_entry(path):
    """True if the file is a 64-bit Windows DLL exporting a VST2 plugin's entry point ("VSTPluginMain" or
    "main"), read from its headers and export table without loading it."""
    import struct
    try:
        with open(path, "rb") as f:
            def at(offset, size):
                f.seek(offset)
                data = f.read(size)
                if len(data) < size:
                    raise ValueError
                return data
            if at(0, 2) != b"MZ":
                return False
            pe = struct.unpack("<I", at(0x3C, 4))[0]
            if at(pe, 4) != b"PE\0\0":
                return False
            machine, count = struct.unpack("<HH", at(pe + 4, 4))
            if machine != 0x8664:  # (32-bit plugins can't run in this 64-bit program)
                return False
            optional_size = struct.unpack("<H", at(pe + 20, 2))[0]
            optional = pe + 24
            if struct.unpack("<H", at(optional, 2))[0] != 0x20B:
                return False
            export_rva = struct.unpack("<I", at(optional + 112, 4))[0]
            sections = [struct.unpack("<8sIIII", at(optional + optional_size + 40 * i, 24)) for i in range(count)]

            def offset_of(rva):
                for _, vsize, vaddr, raw_size, raw_ptr in sections:
                    if vaddr <= rva < vaddr + max(vsize, raw_size):
                        return rva - vaddr + raw_ptr
                raise ValueError

            if not export_rva:
                return False
            directory = offset_of(export_rva)
            names, names_rva = struct.unpack("<I", at(directory + 24, 4))[0], struct.unpack("<I", at(directory + 32, 4))[0]
            table = offset_of(names_rva)
            for i in range(min(names, 4096)):
                name = at(offset_of(struct.unpack("<I", at(table + 4 * i, 4))[0]), 14)
                if name.startswith((b"VSTPluginMain\0", b"main\0")):
                    return True
    except (OSError, ValueError, struct.error):
        pass
    return False


def read_moduleinfo(bundle):
    """[(name, kind)] from a bundle's moduleinfo.json (JSON5-ish: comments, trailing commas), or None."""
    info = os.path.join(bundle, "Contents", "Resources", "moduleinfo.json")
    if not os.path.isfile(info):
        return None
    try:
        text = open(info, encoding="utf-8-sig").read()
        text = re.sub(r"(?m)^\s*//[^\n]*", "", text)
        text = re.sub(r",(\s*[}\]])", r"\1", text)
        classes = json.loads(text, strict=False).get("Classes", [])
    except (OSError, ValueError):
        return None
    found = [(c["Name"], INSTRUMENT if any("Instrument" in s for s in c.get("Sub Categories", [])) else EFFECT)
             for c in classes if c.get("Category") == "Audio Module Class"]
    return found or None


class PluginLibrary:
    """The VST3 plugins on this computer. `plugins` is filled instantly; kinds become known as described above."""

    def __init__(self, folders=DEFAULT_FOLDERS, cache_path=None, vst2_folders=()):
        cache_path = cache_path or CACHE_PATH  # (looked up now, so tests can move it)
        self.folders, self.cache_path, self.vst2_folders = folders, cache_path, tuple(vst2_folders)
        try:
            self.cache = json.load(open(cache_path, encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}
        self.plugins = self.scan()

    def scan(self):
        found = []
        for folder in self.folders:
            for path in sorted(glob.glob(os.path.join(folder, "**", "*.vst3"), recursive=True)):
                if os.sep + "Contents" + os.sep in path:
                    continue  # the inner file of a bundle: listed through the bundle
                file = loadable_file(path)
                stem = os.path.splitext(os.path.basename(path))[0]
                cached = self.cache.get(file)
                if cached and cached.get("mtime") == os.path.getmtime(file):
                    found += [PluginInfo(file, e["name"], e["kind"], e["plugin_name"]) for e in cached["plugins"]]
                    continue
                classes = read_moduleinfo(path) if os.path.isdir(path) else None
                if classes and len(classes) > 1:
                    found += [PluginInfo(file, name, kind, name) for name, kind in classes]
                elif classes:
                    found.append(PluginInfo(file, classes[0][0], classes[0][1]))
                else:
                    found.append(PluginInfo(file, stem))
        found += self.scan_vst2()
        return sorted(found, key=lambda p: p.name.lower())

    def scan_vst2(self):
        """The VST2 plugins in the chosen folders (and their subfolders)."""
        found, changed = [], False
        for folder in self.vst2_folders:
            for path in sorted(glob.glob(os.path.join(folder, "**", "*.dll"), recursive=True)):
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                cached = self.cache.get(path)
                if not (cached and cached.get("mtime") == mtime):
                    stem = os.path.splitext(os.path.basename(path))[0]
                    plugins = [{"name": stem + VST2_SUFFIX, "kind": None, "plugin_name": None}] if vst2_entry(path) else []
                    self.cache[path] = cached = {"mtime": mtime, "plugins": plugins}  # (not a plugin: remembered too)
                    changed = True
                found += [PluginInfo(path, e["name"], e["kind"], e["plugin_name"]) for e in cached["plugins"]]
        if changed:
            self.save_cache()
        return found

    def unknown_files(self):
        """The plugin files with a plugin whose kind isn't known yet, and that weren't looked at in vain before
        (for kind finding: plugin_probe.py)."""
        files = dict.fromkeys(p.path for p in self.plugins if p.kind is None)
        return [f for f in files if not self.probe_failed_before(f)]

    def probe_failed_before(self, file):
        cached = self.cache.get(file)
        try:
            return bool(cached and cached.get("probe_failed") and cached.get("mtime") == os.path.getmtime(file))
        except OSError:
            return True

    def learn(self, file, classes):
        """The kinds plugin_probe.py found for a file: classes = [[name, kind], ...]. One plugin keeps the name
        it is listed by; a file holding several is listed once per plugin (by their names)."""
        listed = [p for p in self.plugins if p.path == file]
        if len(classes) == 1 and len(listed) == 1:
            infos = [PluginInfo(file, listed[0].name, classes[0][1], listed[0].plugin_name)]
        else:
            infos = [PluginInfo(file, name, kind, name) for name, kind in classes]
        self.remember(file, infos)

    def probe_failed(self, file, why):
        """plugin_probe.py couldn't tell (the plugin failed, crashed or hung): not tried again unless the
        file changes; its kind is learnt when it is loaded."""
        try:
            mtime = os.path.getmtime(file)
        except OSError:
            return
        listed = [p for p in self.plugins if p.path == file]
        self.cache[file] = {"mtime": mtime, "probe_failed": why,
                            "plugins": [{"name": p.name, "kind": p.kind, "plugin_name": p.plugin_name} for p in listed]}
        self.save_cache()

    def of_kind(self, kind):
        """Plugins of that kind, plus those whose kind is not known yet."""
        return [p for p in self.plugins if p.kind in (kind, None)]

    def load(self, info, sample_rate=48000):
        """Load a plugin (main thread only), ready to process at `sample_rate`. Learns and remembers its
        kind and how long it took. Raises on failure (PluginChoiceNeeded: the file holds several)."""
        if vst3host is None:
            raise RuntimeError(f"The plugin host isn't built for this Python ({HOST_MISSING}); see native/build.ps1.")
        started = time.perf_counter()
        if is_vst2(info.path):
            plugin = vst3host.Vst2Plugin(info.path)
        else:
            if info.plugin_name is None:
                names = [c["name"] for c in vst3host.classes(info.path)]
                if len(names) > 1:
                    self.remember(info.path, [PluginInfo(info.path, n, None, n) for n in names])
                    raise PluginChoiceNeeded(names)
            plugin = vst3host.Plugin(info.path, info.plugin_name or "")
        plugin.prepare(sample_rate)
        kind = INSTRUMENT if plugin.is_instrument else EFFECT
        self.cache.setdefault(LOAD_TIMES, {})[self.load_key(info)] = round(time.perf_counter() - started, 2)
        if info.kind != kind:
            same_file = [p for p in self.plugins if p.path == info.path and p != info]
            self.remember(info.path, same_file + [PluginInfo(info.path, info.name, kind, info.plugin_name)])
        else:
            self.save_cache()
        return plugin

    @staticmethod
    def load_key(info):
        return f"{info.path}|{info.plugin_name or ''}"

    def last_load_s(self, info):
        """How long this plugin took to load last time (seconds), or None if it never loaded here."""
        return self.cache.get(LOAD_TIMES, {}).get(self.load_key(info))

    def save_cache(self):
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            json.dump(self.cache, open(self.cache_path, "w", encoding="utf-8"), indent=1)
        except OSError:
            pass  # only a cache

    def remember(self, file, infos):
        self.cache[file] = {"mtime": os.path.getmtime(file),
                            "plugins": [{"name": p.name, "kind": p.kind, "plugin_name": p.plugin_name} for p in infos]}
        self.save_cache()
        self.plugins = sorted([p for p in self.plugins if p.path != file] + infos, key=lambda p: p.name.lower())


def state_of(plugin):
    """A loaded plugin's settings, to save (see the top)."""
    component, controller = plugin.component_state(), plugin.controller_state()
    return STATE_MAGIC + struct.pack("<I", len(component)) + component + controller


def set_state(plugin, state):
    """Give a loaded plugin saved settings (from state_of(), or from an older version: JUCE's format)."""
    if state.startswith(STATE_MAGIC):
        size = struct.unpack_from("<I", state, 4)[0]
        component, controller = state[8:8 + size], state[8 + size:]
    elif juce_state.is_juce_state(state):
        component, controller = juce_state.split(state)
    else:
        raise ValueError("unknown plugin state")
    plugin.set_state(component, controller)


class Parameter:
    """One of a plugin's parameters, as automation and the GUI use it: its value 0…1 (raw_value; set
    from either thread, read on the main thread), the plugin's text for a value, and its steps (a
    switch or a choice: only so many values; VST2 plugins don't say, so theirs take any value)."""

    def __init__(self, plugin, info):
        self.plugin, self.id, self.name = plugin, info["id"], info["name"]
        self.steps = max(0, int(info.get("steps") or 0))  # 0: any value; n: n + 1 values (k / n), e.g. 1: Off / On

    @property
    def raw_value(self):
        return self.plugin.get_param(self.id)

    @raw_value.setter
    def raw_value(self, value):
        self.plugin.set_param(self.id, value)

    @property
    def string_value(self):
        return self.plugin.param_text(self.id, self.raw_value)

    def get_text_for_raw_value(self, value):
        return self.plugin.param_text(self.id, value)


def sync_state(slot, sr, frames=512):
    """Have the plugin take in the latest changes made in its window before its state is saved: they
    reach its processor with the next buffer it processes. The audio thread processes plugins all
    the time while a device is open; without one, this processes one silent buffer (main thread
    only, never while the audio thread uses the plugin)."""
    if not usable(slot):
        return
    out = np.zeros((2, frames), np.float32)
    ensure_rate(slot, sr)
    if slot.kind == INSTRUMENT:
        slot.instance.render([], out)
    else:
        slot.instance.process(out.copy(), None, out)


# --- audio thread ---------------------------------------------------------------------------

def usable(slot):
    return slot is not None and slot.instance is not None and slot.error is None


def latency(slot):
    """Frames a plugin holds the sound back (look-ahead limiters, linear-phase EQs…), as it reports it."""
    return slot.instance.latency


def ensure_rate(slot, sr):
    """Get the plugin ready for sample rate `sr` if it isn't (the device changed): it starts afresh."""
    if slot.instance.sample_rate != sr:
        slot.instance.prepare(sr)


def process_effect(slot, x, sidechain, sr):
    """An effect's output (2, frames) for its input x (2, frames) and sidechain (or None)."""
    ensure_rate(slot, sr)
    y = np.empty((2, x.shape[1]), np.float32)
    slot.instance.process(x, sidechain, y)
    return y


def track_latency(track, seen=frozenset()):
    """Frames a track's plugins hold its sound back (0 while frozen: its rendering is compensated): its
    instrument, then the slowest path through its effects' cables (other tracks into sidechains
    included: a track listening to a late one is late too)."""
    from .patch import patch_latency  # (patch.py builds on this module)
    if track.frozen is not None or track in seen:
        return 0
    return patch_latency(track.cables, instrument_latency(track), seen | {track})


def plays_plugins(track):
    """Does a MIDI track play instrument plugins (its own, or its instrument patch panel's: even none wired
    in) rather than the built-in synth?"""
    return track.kind == "midi" and not track.needs_instrument and (track.uses_rack or usable(track.instrument))


def heard_instruments(track):
    """The instrument plugins of a track that are heard: its one instrument, or those of its instrument
    patch panel wired to OUT and not muted (bypass). Only loaded ones that haven't failed."""
    if not track.uses_rack:
        return [s for s in track.instruments() if usable(s)]
    return [s for s in track.instruments() if usable(s) and not s.bypass and (s, PATCH_OUT) in track.rack_cables]


def fed_instruments(track):
    """The instruments of a track's instrument patch panel that get its notes (wired from IN)."""
    return {b for a, b in track.rack_cables if a == PATCH_IN}


def instrument_latency(track):
    """Frames its instruments hold the sound back: the latest heard one (the others are held back to match)."""
    if track.frozen is not None:
        return 0
    return max((latency(s) for s in heard_instruments(track)), default=0)


def play_instrument(slot, messages, out, sr):
    """Render one buffer of an instrument plugin and add it into out (frames, channels). messages: mido
    notes timed in seconds from the buffer's start; any control change ends every note held."""
    frames, channels = out.shape
    notes = []
    for m in messages:
        if m.type == "control_change":
            slot.instance.notes_off()
        elif m.type in ("note_on", "note_off"):
            notes.append((min(frames - 1, max(0, round(m.time * sr))), m.type == "note_on" and m.velocity > 0,
                          m.note, m.velocity, m.channel))
    audio = np.empty((2, frames), np.float32)
    try:
        ensure_rate(slot, sr)
        slot.instance.render(notes, audio)
    except Exception as e:  # a failing plugin is switched off, never the whole audio stream
        slot.error = str(e) or type(e).__name__
        return
    out += audio[:channels].T


def note_messages(clips, start, frames, sr, restart=False):
    """MIDI messages for the notes of `clips` that start or end in timeline frames [start, start+frames),
    timed in seconds from the buffer start. Notes are cut at the end of each clip's window.
    start=None (transport stopped): no notes, only "all notes off" on a restart."""
    events = []
    end = start + frames if start is not None else None
    for clip in clips if start is not None else ():
        on, off, pitch, velocity = clip.events.T
        lo, hi = clip.offset, clip.offset + clip.frames
        inside = (on >= lo) & (on < hi)  # notes that start within the clip's window
        t_on = clip.start + on - lo
        t_off = clip.start + np.minimum(off, hi) - lo
        starts = inside & (t_on >= start) & (t_on < end)
        stops = inside & (t_off >= start) & (t_off < end)
        events += [(t - start, 1, int(p), max(1, int(v))) for t, p, v in zip(t_on[starts], pitch[starts], velocity[starts])]
        events += [(t - start, 0, int(p), 0) for t, p in zip(t_off[stops], pitch[stops])]
    events.sort()  # by time; note-offs before note-ons at the same moment
    messages = list(ALL_NOTES_OFF) if restart else []
    messages += [Message("note_on" if is_on else "note_off", note=p, velocity=v, time=t / sr)
                 for t, is_on, p, v in events]
    return messages
