"""Ready-made images of clip contents (waveforms and MIDI notes), so redraws only copy pixels.

A clip's body is cut into tiles TILE_W pixels wide, counted from the clip's left edge.
Each tile is rendered once with numpy straight into an image and kept in an LRU cache
(about CACHE_MB). The cache key holds everything that changes the pixels: the clip's audio
or notes, its window (offset/length), zoom, sample rate, lane height and colour. So zooming,
cutting, recolouring, muting or resizing a lane simply asks for new tiles; moving a clip
reuses its tiles.
"""
from collections import OrderedDict

import numpy as np
from PySide6.QtGui import QImage

from .. import config
from ..model import AUDIO

TILE_W = 256
CACHE_MB = 96


class TileCache:
    def __init__(self, budget_bytes=CACHE_MB * 2 ** 20):
        self.budget = budget_bytes
        self.bytes = 0
        self._tiles = OrderedDict()  # key -> (source array, QImage, pixel buffer)

    def tile(self, clip, index, width, height, pps, sr, color):
        """QImage of tile `index` (width x height) of the clip's contents in `color`."""
        source = clip.peaks if clip.kind == AUDIO else clip.notes
        key = (clip.kind, id(source), clip.offset, clip.frames, pps, sr, height, color.rgba(), index, width)
        hit = self._tiles.get(key)
        if hit is not None and hit[0] is source:  # `is`: a recycled id() must not return a stale tile
            self._tiles.move_to_end(key)
            return hit[1]
        render = render_audio_tile if clip.kind == AUDIO else render_midi_tile
        mask = render(clip, index * TILE_W, width, height, pps, sr)
        pixels = np.zeros((height, width, 4), np.uint8)
        pixels[mask] = (color.blue(), color.green(), color.red(), 255)  # ARGB32 premultiplied, little-endian
        image = QImage(pixels.data, width, height, 4 * width, QImage.Format_ARGB32_Premultiplied)
        self._tiles[key] = (source, image, pixels)  # QImage does not copy: keep the pixels alive
        self.bytes += pixels.nbytes
        while self.bytes > self.budget and len(self._tiles) > 1:
            _, (_, _, old) = self._tiles.popitem(last=False)
            self.bytes -= old.nbytes
        return image

    def clear(self):
        self._tiles.clear()
        self.bytes = 0


TILES = TileCache()  # shared by every clip drawing


def render_audio_tile(clip, first_col, width, height, pps, sr):
    """Boolean (height, width) mask: one vertical min..max bar per pixel column."""
    blocks_per_px = sr / pps / config.PEAK_BLOCK
    first_block = clip.offset / config.PEAK_BLOCK  # a split clip starts partway into the audio
    cols = np.arange(first_col, first_col + width + 1)
    idx = (cols * blocks_per_px + first_block).astype(int).clip(0, len(clip.peaks) - 1)
    seg = clip.peaks[:max(idx[-1], idx[-2] + 1)]  # last column must not reach into the next pixel's blocks
    lo = np.minimum.reduceat(seg[:, 0], idx[:-1])
    hi = np.maximum.reduceat(seg[:, 1], idx[:-1])
    mid, half = height / 2, height / 2 - 3
    top = np.floor(mid - hi * half)
    bottom = np.maximum(top, np.floor(mid - lo * half))
    rows = np.arange(height)[:, None]
    return (rows >= top) & (rows <= bottom)


def render_midi_tile(clip, first_col, width, height, pps, sr):
    """Boolean (height, width) mask: mini piano roll of the notes inside the clip's window; their
    pitch range fills the height (at least an octave)."""
    mask = np.zeros((height, width), bool)
    window_s, frames_s = clip.offset / sr, clip.frames / sr
    notes = clip.notes[(clip.notes[:, 1] > window_s) & (clip.notes[:, 0] < window_s + frames_s)]
    if not len(notes):
        return mask
    lo, hi = notes[:, 2].min(), notes[:, 2].max()
    if hi - lo < 12:
        lo, hi = (lo + hi) / 2 - 6, (lo + hi) / 2 + 6
    area_top, area_bottom = 2, height - 3
    row_h = (area_bottom - area_top) / (hi - lo + 1)
    x0 = (notes[:, 0] - window_s) * pps - first_col
    x1 = np.maximum((notes[:, 1] - window_s) * pps - first_col, x0 + 1)
    shown = (x1 > 0) & (x0 < width)
    for a, b, pitch in zip(x0[shown], x1[shown], notes[shown, 2]):
        y = area_bottom - (pitch - lo + 1) * row_h
        r0, r1 = max(area_top, int(y)), min(area_bottom, int(np.ceil(y + max(2.0, row_h - 1))))
        mask[r0:r1, max(0, int(a)):min(width, int(np.ceil(b)))] = True
    return mask
