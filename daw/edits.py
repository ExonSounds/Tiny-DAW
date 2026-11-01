"""Editing operations on a Project: splitting clips, cutting time ranges out of them, making
room for a moved or placed clip, and duplicating a range or clips.

Clips are never changed in place here: pieces are new Clip objects (sharing the
original audio) swapped into project.clips in one assignment, so the audio
thread never sees a half-edited clip.
"""
from dataclasses import replace


def split_clip(clip, frame):
    """(left, right) pieces of clip cut at timeline `frame`, or None if frame is not inside it."""
    cut = frame - clip.start
    if not 0 < cut < clip.frames:
        return None
    left = replace(clip, length=cut)
    right = replace(clip, start=frame, offset=clip.offset + cut, length=clip.frames - cut)
    return left, right


def split_clips(project, clips, frames):
    """Split each of `clips` at every one of `frames` that falls inside it.

    Returns all resulting pieces (clips that were not cut are returned as they are).
    """
    pieces_of = {}
    for clip in clips:
        pieces = [clip]
        for frame in sorted(frames):
            parts = split_clip(pieces[-1], frame)
            if parts:
                pieces[-1:] = parts
        pieces_of[clip] = pieces
    project.clips = tuple(p for c in project.clips for p in pieces_of.get(c, (c,)))
    return [p for pieces in pieces_of.values() for p in pieces]


def clear_under(project, clips):
    """Make room for `clips` (just moved or placed): the parts of other clips on the same track that
    they cover are cut away, as in other DAWs, so clips on a track never play over each other."""
    placed = set(clips)
    for clip in clips:
        covered = [c for c in project.clips_in((clip.track,), clip.start, clip.end) if c not in placed]
        if covered:
            pieces = split_clips(project, covered, (clip.start, clip.end))
            project.remove_clips({p for p in pieces if p.start >= clip.start and p.end <= clip.end})


def delete_range(project, tracks, start, end):
    """Remove frames [start, end) from every clip on `tracks`; clips crossing the edges are split."""
    pieces = split_clips(project, project.clips_in(tracks, start, end), (start, end))
    project.remove_clips({p for p in pieces if p.start >= start and p.end <= end})


def piece_within(clip, start, end):
    """The part of `clip` inside timeline frames [start, end), as a new clip (sharing its audio), or None."""
    lo, hi = max(start, clip.start), min(end, clip.end)
    if hi <= lo:
        return None
    return replace(clip, start=lo, offset=clip.offset + lo - clip.start, length=hi - lo)


def place_copies(project, copies):
    """Add `copies` to the project; what was under them is cut away (clear_under). Returns them."""
    project.clips = project.clips + tuple(copies)
    clear_under(project, copies)
    return copies


def duplicate_range(project, tracks, start, end):
    """Copy what lies in frames [start, end) on `tracks` to right after it, [end, end + (end - start));
    what was there is replaced. Returns the copies."""
    span = end - start
    pieces = [piece_within(c, start, end) for c in project.clips_in(tracks, start, end)]
    return place_copies(project, [replace(p, start=p.start + span) for p in pieces if p is not None])


def duplicate_clips(project, clips):
    """Copy `clips` to right after them: moved on by their span, from the first one's start to the
    last one's end (so several clips keep their spacing). Returns the copies."""
    if not clips:
        return []
    span = max(c.end for c in clips) - min(c.start for c in clips)
    return place_copies(project, [replace(c, start=c.start + span) for c in clips])
