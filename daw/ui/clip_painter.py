"""Drawing one clip: its box, its contents (waveform or MIDI notes) and its name.

Qt often repaints only a strip of the timeline (playhead moves, scrolling), so
everything here draws only what falls inside `visible` and gives identical
pixels whether the clip is painted whole or strip by strip. The contents come
as ready-made images from waveform_cache.py.
"""
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPen

from .. import config
from . import theme
from .colors import clip_color, clip_colors
from .waveform_cache import TILE_W, TILES


def paint_clip(p, clip, rect, visible, view_left, pps, sr, selected, audible, frozen=False):
    """Box, darker handle strip on top (drag to move), contents below (select / cut), name.

    Colours come from the clip's colour (or its kind's default) with automatic contrast.
    A frozen track's clips get an icy tint (they are locked). A disabled clip is only its outline
    and name, in its colour."""
    color, tone = clip_color(clip)  # its own, its track's, or its kind's default
    colors = clip_colors(color, audible, tone)
    if clip.disabled:
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(colors.selected_border, 2) if selected else QPen(colors.fill, 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRect(rect.adjusted(0.75, 0.75, -0.75, -0.75))  # the whole line inside the clip's box
        p.setRenderHint(QPainter.Antialiasing, False)
        paint_name(p, clip, rect, view_left, colors.fill)
        return
    p.fillRect(rect, colors.fill)
    p.fillRect(QRectF(rect.left(), rect.top(), rect.width(), config.CLIP_HANDLE_H), colors.handle)
    body = rect.adjusted(0, config.CLIP_HANDLE_H, 0, 0)
    if body.bottom() >= visible.top() and body.top() <= visible.bottom():  # not just the name strip
        paint_contents(p, clip, body, visible, pps, sr, colors.content)
    if frozen:
        p.fillRect(rect, theme.FROZEN_TINT)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(QPen(colors.selected_border, 2) if selected else QPen(colors.border, 1.5))
    p.setBrush(Qt.NoBrush)
    p.drawRect(rect)  # square corners
    p.setRenderHint(QPainter.Antialiasing, False)
    paint_name(p, clip, rect, view_left, colors.text)


def paint_name(p, clip, rect, view_left, color):
    """Name sticks to the view's left edge, but stays inside the clip."""
    text_w = p.fontMetrics().horizontalAdvance(clip.name)
    x = max(rect.left() + 6, min(max(rect.left(), view_left) + 6, rect.right() - text_w - 6))
    p.save()
    p.setClipRect(rect.adjusted(1, 1, -1, -1))
    p.setPen(color)
    p.drawText(QPointF(x, rect.top() + 3 + p.fontMetrics().ascent()), clip.name)
    p.restore()


def paint_contents(p, clip, body, visible, pps, sr, color):
    """The waveform or MIDI notes: copies of ready-made tiles (waveform_cache.py), only the
    tiles that overlap `visible`. Tiles are counted from the clip's left pixel, so moving the
    clip reuses them."""
    base, top = round(body.left()), int(body.top())
    total = int(body.right()) - base  # contents width in pixels
    height = int(body.height())
    first = max(0, visible.left() - base)
    last = min(total, visible.left() + visible.width() - base)  # exclusive
    if last <= first or height <= 0:
        return
    for index in range(first // TILE_W, (last - 1) // TILE_W + 1):
        width = min(TILE_W, total - index * TILE_W)
        p.drawImage(base + index * TILE_W, top, TILES.tile(clip, index, width, height, pps, sr, color))


def paint_range(p, time_range, spans, x_of, visible):
    """Translucent box over the selected time range on each selected lane.

    spans: {track: (lane top y, lane height)} for the range's tracks."""
    x0, x1 = x_of(time_range.start), x_of(time_range.end)
    if x1 + 2 < visible.left() or x0 - 2 > visible.right():  # +2: edge lines round to nearby pixels
        return
    for y, h in spans.values():
        p.fillRect(QRectF(x0, y, x1 - x0, h), theme.SELECTION_FILL)
    top = min(y for y, _ in spans.values())
    bottom = max(y + h for y, h in spans.values())
    p.setPen(theme.SELECTION_EDGE)
    p.drawLine(QLineF(x0, top, x0, bottom))
    p.drawLine(QLineF(x1, top, x1, bottom))
