"""Rendering offline, no Qt: a track, for freezing and bouncing it (render_track), and the whole
mix, for File → Render selection / Render project (render_mix, below).

A track renders the way it plays: its clips, through its VST3 instrument on a MIDI track (else
the built-in synth's audio), then its effects, with their automation. Not included: mute, solo, its group, the master
volume and master effects. The rendering runs from the track's first clip to its last clip's
end, plus up to RENDER_TAIL_S while plugins may still ring out (cut once the sound has died away).

Plugins with look-ahead make the track come out late (plugins.latency): the rendering is moved
back by that much, so it plays in time without them.

Its sidechains hear the other tracks they listen to as they play (their effects and fader; a
frozen one its rendering), even muted: those are rendered first (sidechain_sources).

It calls the track's plugins from this (the main) thread, so the audio thread must leave them
alone meanwhile (Engine.plugins_paused_while; see plugins.py). They are reset first, so a
rendering never starts with what they were playing live.
"""
import numpy as np

from .. import config
from . import automation
from .dsp import db_to_gain
from .mixer import add_clip, mix_clips
from ..model import TrackSource
from .mixer import listened_to
from .patch import play_instruments, run_patch
from .plugins import instrument_latency, note_messages, plays_plugins, track_latency, usable


def sidechain_sources(project, track, sr, seen):
    """{TrackSource: (start frame, its sound (frames, 2))} for the other tracks the track's sidechains
    listen to, rendered as they play, in time (their own delay already taken out). seen: tracks being
    rendered already (a loop counts once)."""
    found = {}
    for other in listened_to(track.cables) & set(project.tracks):
        if other in seen:
            continue
        if other.frozen is not None:
            f = other.frozen
            sound = np.zeros((f.frames, 2), np.float32)
            add_clip(sound, f, f.start)
            rendered = (f.start, sound)
        else:
            rendered = render_track(project, other, sr, seen=seen | {other})
        if rendered is not None:
            at, sound = rendered
            if other.volume_db != 0:  # (its fader, as it plays)
                sound = sound * db_to_gain(other.volume_db, config.MIN_DB)
            found[TrackSource(other)] = (at, sound)
    return found


def window(sound, at, pos, n):
    """Frames [pos, pos + n) of a sound starting at frame `at`, (2, n), silence outside it."""
    part = np.zeros((2, n), np.float32)
    lo, hi = max(pos, at), min(pos + n, at + len(sound))
    if hi > lo:
        part[:, lo - pos:hi - pos] = sound[lo - at:hi - at].T
    return part


def render_track(project, track, sr, progress=None, seen=None):
    """(start frame, stereo float32 audio (frames, 2)) of the track, or None if it has no clips
    that play (disabled clips are left out).
    progress(fraction done) is called now and then; if it returns False, rendering stops and
    None is returned. Raises if its instrument fails."""
    clips = [c for c in project.clips_on(track) if not c.disabled]
    if not clips:
        return None
    instrument = plays_plugins(track)  # its instrument plugins play the notes (else the built-in synth)
    instruments = [s for s in track.instruments() if usable(s)] if instrument else []
    effects = [s for s in track.effects if usable(s) and not s.bypass]
    plugins = instruments + effects
    # EDGE_FADE_FRAMES of silence on both sides: a clip made of the rendering fades in and out over
    # them (mixer.add_clip), so it plays exactly like the track did (unless it starts at 0 s)
    pad = config.EDGE_FADE_FRAMES
    start = max(0, min(c.start for c in clips) - pad)
    last_note = max(c.end for c in clips)
    late = track_latency(track)  # its plugins hold the sound back up to this long: render that much more, drop it
    end = last_note + (round(config.RENDER_TAIL_S * sr) if plugins else 0) + pad + late
    block = config.RENDER_BLOCK
    for slot in plugins:
        slot.instance.reset()
    sources = sidechain_sources(project, track, sr, seen or {track})
    in_late = instrument_latency(track)
    out = np.zeros((end - start, 2), np.float32)
    state, rack_state = {}, {}  # (its own: the audio thread keeps the track's)
    try:
        for i, pos in enumerate(range(start, end, block)):
            n = min(block, end - pos)
            buf = out[pos - start:pos - start + n]  # a view: written in place
            automation.apply(track, pos, sr)
            if instrument:
                play_instruments(track, note_messages(clips, pos, n, sr, restart=pos == start), buf, sr, rack_state)
                for slot in instruments:
                    if slot.error:
                        raise RuntimeError(f"{slot.name} stopped: {slot.error}")
            else:
                for clip in clips:
                    add_clip(buf, clip, pos, 0)
            heard = {source: (window(sound, at, pos, n), 0) for source, (at, sound) in sources.items()}  # (in time: 0 late)
            late = run_patch(track.cables, buf, sr, state, heard, in_late)
            if progress and i % 32 == 0 and progress((pos - start) / (end - start)) is False:
                return None
    finally:
        track.patch_state.clear()  # (the audio thread starts afresh with its cables)
        track.rack_state.clear()
    out = out[late:]  # (as late as its cables said) in time with the clips, as delay compensation plays it
    # cut the tail where it has died away (keeping the silent pad)
    loud = np.flatnonzero(np.abs(out[last_note - start:]).max(axis=1) > config.RENDER_SILENCE)
    out = out[:last_note - start + (loud[-1] + 1 if len(loud) else 0) + pad]
    return start, out


def render_mix(project, start, end, sr, master_db=0.0, tail=False, progress=None):
    """Stereo float32 audio (frames, 2) of the whole mix from frame `start` to `end`, the way it
    plays: every audible track (mute and solo count) through its instrument, effects and volume, with
    plugin delay compensation, then the master effects and the master volume (master_db). Tracks
    playing on other outputs than 1/2 are mixed in too (a stereo file has only 1/2). Not included:
    the metronome and the Explorer's preview.

    It starts PREROLL_S before `start`, so effects such as reverb and delay are already ringing there
    as they would be when playing through. tail=True renders past `end` while plugins still ring out
    (up to RENDER_TAIL_S, cut where the sound has died away).
    progress(fraction done) is called now and then; if it returns False, rendering stops and None
    is returned. The audio thread must leave plugins alone meanwhile (Engine.plugins_paused_while)."""
    slots = [s for s in project.plugin_slots() if usable(s)]
    for slot in slots:  # never start with what they were playing live
        slot.instance.reset()
    clear_patch_states(project)
    first = max(0, start - round(config.RENDER_PREROLL_S * sr))
    extra = round(config.RENDER_TAIL_S * sr) if tail else 0
    block, delays, latency = config.RENDER_BLOCK, {}, 0
    out = np.zeros((end - first + extra, 2), np.float32)
    scratch = np.zeros((block, 2), np.float32)
    pos, total = first, len(out)
    try:
        # until the end, plus the time plugins with look-ahead hold the sound back (known as it plays)
        while pos - first < total + latency:
            n = block
            if pos - first + n > len(out):
                out = np.concatenate([out, np.zeros((n, 2), np.float32)])
            latency = mix_clips(project, out[pos - first:pos - first + n], pos, scratch, _ignore, sr,
                                restart=pos == first, delays=delays)
            pos += n
            if progress and (pos - first) // n % 32 == 0 and progress(min(1.0, (pos - first) / (total + latency))) is False:
                return None
    finally:
        clear_patch_states(project)
    out = out[start - first + latency:]  # what is heard at `start` comes out `latency` later
    out = out[:end - start + extra]
    if tail:  # cut where it has died away
        loud = np.flatnonzero(np.abs(out[end - start:]).max(axis=1) > config.RENDER_SILENCE)
        out = out[:end - start + (loud[-1] + 1 if len(loud) else 0)]
    if master_db != 0:
        out *= db_to_gain(master_db, config.MIN_DB)
    return out


def _ignore(*args):
    pass


def clear_patch_states(project):
    """Forget what the effects' cables held back (patch.py), so neither a render nor the audio thread
    after it hears the other's sound."""
    for track in project.tracks:
        track.patch_state.clear()
    project.master_patch_state.clear()
