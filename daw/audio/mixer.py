"""Summing clips into an output buffer. Runs on the audio thread, so keep it allocation-light.

Plugin delay compensation: plugins with look-ahead (limiters such as Pro-L 2, linear-phase EQs)
make their track come out late. Every track is delayed so that all come out together with the
latest one (DelayLine); outputs 3 and up are also delayed by the master effects' latency, which
only outputs 1/2 go through. mix_clips returns how far the output lags behind the playhead.

Sidechains (audio/patch.py) can listen to other tracks: a track some sidechain listens to is mixed
before the tracks (and the master) that listen to it, and its sound after its effects and fader is
handed to them, as late as it is (before its delay compensation). It is worked out even while it
is muted or not soloed (then only for the sidechains, not heard): a muted "ghost" kick can still
make a compressor duck.
"""
import numpy as np

from .. import config
from .dsp import db_to_gain
from . import automation
from ..model import TrackSource
from .patch import DelayLine, patch_latency, play_instruments, run_patch
from .plugins import instrument_latency, note_messages, plays_plugins, track_latency, usable


def listened_to(cables):
    """The other tracks these cables take into sidechains."""
    return {a.track for a, _ in cables if isinstance(a, TrackSource)}


def feeding_order(project):
    """(The tracks, each after the ones its sidechains listen to (else in their own order); the tracks some
    sidechain listens to.) A loop of tracks listening to each other (never made by the patch panel)
    keeps its own order."""
    tracks = project.tracks
    needs = {t: listened_to(t.cables) & set(tracks) for t in tracks}
    feeders = set().union(*needs.values(), listened_to(project.master_cables) & set(tracks))
    if not any(needs.values()):
        return tracks, feeders
    order, state = [], {}

    def visit(track):
        state[track] = "open"
        for source in sorted(needs[track], key=tracks.index):
            if source not in state:
                visit(source)
        state[track] = "done"
        order.append(track)

    for track in tracks:
        if track not in state:
            visit(track)
    return order, feeders


def mix_clips(project, out, start, scratch, on_track, sr, restart=False, plugins=True, delays=None, live=None):
    """Add every audible track's sound for frames [start, start + len(out)) into out.

    Each track is summed in `scratch` (same shape as out) on its own output pair
    (track.output): its clips' audio, or, on a MIDI track with a VST3 instrument, the
    instrument playing the clips' notes; then its effects, then its volume. A frozen track plays its rendering
    (track.frozen) instead, without calling its plugins. on_track(track, scratch, first,
    channels) is called so the engine can meter it: the track's sound is in
    scratch[:, first:first + channels]. For a track that is silent in this buffer (no clips
    and no plugins that could still ring, muted, or not soloed) on_track(track, None, 0, 0)
    is called instead. start=None: the transport is stopped, so only the plugins run (no clips).
    `restart`: playback just started, stopped or jumped; instruments get "all notes off" first.
    plugins=False: don't call any plugin (one is being loaded, which would block them): tracks
    with a VST3 instrument are silent and effects are skipped.
    delays: {track: DelayLine}, kept by the caller between buffers, for the delay compensation
    (None: none). Returns the frames the output lags behind `start` (0 without compensation).
    live: {track: LiveNotes}: notes played live into tracks (live.py), heard through each track's
    instrument and effects, playing or not.
    """
    frames = len(out)
    latency = {t: track_latency(t) if plugins else 0 for t in project.tracks}
    latest = max(latency.values(), default=0)
    master = patch_latency(project.master_cables, latest) - latest if plugins else 0
    order, feeders = feeding_order(project)
    heard_by_sidechains = {}  # TrackSource -> (its sound (2, frames), frames late), for the tracks listening
    if delays is not None:
        for gone in [t for t in delays if t not in latency]:
            del delays[gone]
    by_track = {}
    if start is not None:
        end = start + frames
        for clip in project.clips:
            # >= : a note-off can fall exactly on a MIDI clip's end
            if clip.start < end and clip.end >= start and not clip.disabled:
                by_track.setdefault(clip.track, []).append(clip)
    for track in order:
        clips = by_track.get(track, ())
        frozen = track.frozen
        if track.automation and plugins and start is not None and frozen is None:
            automation.apply(track, start, sr)  # its automated parameters, for this buffer
        vst_instrument = plays_plugins(track)
        instrument = vst_instrument and plugins  # its instrument plugins play the notes
        has_plugins = plugins and (instrument or any(usable(s) and not s.bypass for s in track.effects))
        if frozen is not None:  # frozen: its rendering plays instead of its clips and plugins
            clips = (frozen,) if start is not None and frozen.start < start + frames and frozen.end > start else ()
            vst_instrument, instrument, has_plugins = False, False, False
        first = track.output if track.output < out.shape[1] else 0  # device lacks that output: use 1/2
        channels = min(2, out.shape[1] - first)
        line = None
        if delays is not None:  # hold it back to line up with the latest track
            wanted = latest - latency[track] + (master if first >= 2 else 0)
            line = delays.get(track)
            if line is None or (line.frames, line.channels) != (wanted, channels):
                line = delays[track] = DelayLine(wanted, channels)
            if restart:
                line.clear()  # what it held belongs to where playback was
        audible = project.audible(track)
        feeds = track in feeders and plugins  # (a sidechain listens to it: worked out even if not heard)
        if not audible and line is not None:
            line.clear()
        notes = live.get(track) if live else None  # played live (MIDI editor)
        if notes is not None and (not audible or frozen is not None or (vst_instrument and not plugins)):
            notes.clear()  # nothing to hear them through now
        playing_live = notes is not None and notes.active()
        if (not (audible or feeds) or not (clips or has_plugins or playing_live or (line and line.sounding))
                or (vst_instrument and not plugins)):
            on_track(track, None, 0, 0)
            continue
        scratch.fill(0)
        signal = scratch[:, first:first + channels]
        if instrument:  # keeps rendering even without notes, so releases ring out
            messages = note_messages(clips, start, frames, sr, restart)
            if notes is not None:
                messages += notes.take(frames, sr)
            play_instruments(track, messages, signal, sr, track.rack_state)
            if start is None and not (notes is not None and notes.holding()):
                for slot in track.instruments():
                    if usable(slot) and slot.instance.holding():
                        slot.instance.notes_off()  # stopped, nothing played live, yet a note is on: let it go
        else:
            for clip in clips:
                add_clip(scratch, clip, start, first)
            if notes is not None:
                notes.take(frames, sr)
                notes.add_voices(signal, sr)
        if plugins and frozen is None:
            run_patch(track.cables, signal, sr, track.patch_state, heard_by_sidechains, instrument_latency(track))
        if track.volume_db != 0:  # its fader, after its effects
            signal *= db_to_gain(track.volume_db, config.MIN_DB)
        if feeds:
            heard_by_sidechains[TrackSource(track)] = (
                np.ascontiguousarray(signal.T if channels == 2 else np.repeat(signal[:, :1].T, 2, axis=0)), latency[track])
        if not audible:
            on_track(track, None, 0, 0)
            continue
        if line is not None:
            line.process(signal)
        on_track(track, scratch, first, channels)
        out += scratch
    if plugins:
        run_patch(project.master_cables, out[:, :min(2, out.shape[1])], sr, project.master_patch_state,
                  heard_by_sidechains, latest)
    return latest + master if delays is not None else 0


def add_clip(buf, clip, start, first_channel=0):
    """Add the part of clip that falls in buf (which starts at timeline frame `start`).

    Clips are stereo and play on channels first_channel and first_channel + 1 (only the
    left one if the device has no channel after it).
    """
    cs, frames = clip.start, clip.frames
    a, b = max(start, cs), min(start + len(buf), cs + frames)
    if a >= b:
        return
    i0, i1 = a - cs, b - cs  # positions inside the clip window
    channels = min(buf.shape[1] - first_channel, clip.data.shape[1])
    segment = clip.data[clip.offset + i0:clip.offset + i1, :channels]
    fade = config.EDGE_FADE_FRAMES
    if i0 < fade or i1 > frames - fade:  # short fade at the clip's edges, so cuts don't click
        pos = np.arange(i0, i1)
        segment = segment * np.minimum(1.0, np.minimum(pos + 1, frames - pos) / fade)[:, None]
    buf[a - start:b - start, first_channel:first_channel + channels] += segment
