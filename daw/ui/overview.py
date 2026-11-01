"""The project overview: a thin bar above the editor showing the whole project at once.

Every lane is a thin row (as tall, relative to the others, as in the editor) and every clip a
block in its own colour, across the project's whole length; a group's header row shows all its
tracks' clips in miniature (also while it is folded). The part the editor shows is outlined and
the playback cursor is a red line, the selection cursor a blue one. Click or drag on the bar to move the editor there; drag the outline
to slide the view. Drag its bottom edge down to make it taller (more room for each lane), up
to make it smaller again; double-click the edge for the standard height.

The tracks and clips are drawn into an image only when they change: MainWindow calls refresh()
every frame, which compares a cheap summary of the project, so playback and scrolling only redraw
the outline and the playhead over that image.
"""
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .. import config
from . import theme
from .colors import clip_color, clip_colors
from .lanes import layout
from .timeline import group_fill

PAD = 3  # px above and below the rows
EDGE = 5  # px along the bottom that resize the bar


class Overview(QWidget):
    resized = Signal(int)  # its new height, after dragging its bottom edge

    def __init__(self, project, engine, timeline, scroll, height=config.OVERVIEW_H):
        super().__init__()
        self.project, self.engine, self.timeline, self.scroll = project, engine, timeline, scroll
        self.set_height(height)
        self.setMouseTracking(True)  # (the cursor shows where the bottom edge can be dragged)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Project overview: click or drag to move the view. Drag its bottom edge to make it "
                        "taller or smaller; double-click the edge for the standard height")
        self._content_key = self._marks = None
        self._image = None
        self._grab = None  # while dragging: where in the view outline it was grabbed (seconds, px)
        self._sizing = None  # while dragging the bottom edge: (global mouse y, height) at the start

    def set_height(self, height):
        height = max(config.OVERVIEW_H, min(config.OVERVIEW_MAX_H, int(height)))
        self.setFixedHeight(height)
        return height

    def on_edge(self, y):
        return y >= self.height() - EDGE
        for bar in (scroll.horizontalScrollBar(), scroll.verticalScrollBar()):
            bar.valueChanged.connect(self.refresh)

    # --- what to show ---------------------------------------------------------------

    def span(self):
        """Seconds shown across the bar: the whole project, the playhead, and the editor's view."""
        sr = self.engine.sr
        view_end = (self.scroll.horizontalScrollBar().value() + self.scroll.viewport().width()) / self.timeline.pps
        return max(self.project.end_frame() / sr, self.engine.pos / sr, self.timeline.cursor / sr, view_end,
                   config.OVERVIEW_MIN_S)

    def lanes(self):
        """The editor's lanes without the master lane (lanes.py), from y = 0."""
        return layout(self.project)[:-1]

    def row_scale(self):
        """Overview pixels per pixel of lane height."""
        total = sum(lane.height for lane in self.lanes())
        return (self.height() - 2 * PAD) / total if total else 0

    def content_key(self, span):
        """Everything the tracks-and-clips image depends on."""
        p, rows = self.project, self.project.rows()
        return (self.width(), self.height(), self.devicePixelRatioF(), round(span, 3),
                tuple((lane.height, id(lane.track or lane.group or lane.automation), lane.group and lane.group.color)
                      for lane in self.lanes()),
                tuple(p.audible(t) for t in p.tracks),
                tuple((id(c), c.start, c.frames, rows[c.track], clip_color(c)[0]) for c in p.clips))

    def view_rect(self, span):
        """The editor's visible area, in overview pixels."""
        hbar, vbar, vp = self.scroll.horizontalScrollBar(), self.scroll.verticalScrollBar(), self.scroll.viewport()
        x_scale = self.width() / span / self.timeline.pps
        scale = self.row_scale()
        lanes_h = sum(lane.height for lane in self.lanes())
        top = min(max(vbar.value() - config.RULER_H, 0), lanes_h)
        bottom = min(max(vbar.value() + vp.height() - config.RULER_H, 0), lanes_h)
        y0, y1 = (PAD + top * scale, PAD + bottom * scale) if scale else (PAD, self.height() - PAD)
        return QRectF(hbar.value() * x_scale, y0, vp.width() * x_scale, max(y1 - y0, 2))

    def refresh(self):
        """Redraw only what changed (called every frame and when the editor scrolls)."""
        span = self.span()
        key = self.content_key(span)
        if key != self._content_key:
            self._content_key = key
            self._image = self.render_content(span)
            self._marks = None
        x_of = lambda frame: int(frame / self.engine.sr / span * self.width())
        marks = (self.view_rect(span).getRect(), x_of(self.engine.heard_pos()), x_of(self.timeline.cursor))
        if marks != self._marks:
            self._marks = marks
            self.update()

    # --- painting -------------------------------------------------------------------

    def render_content(self, span):
        """The rows and clip blocks, as an image the size of the bar."""
        dpr = self.devicePixelRatioF()
        image = QImage(max(1, round(self.width() * dpr)), max(1, round(self.height() * dpr)),
                       QImage.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(theme.OVERVIEW_BG)
        p = QPainter(image)
        scale, x_scale = self.row_scale(), self.width() / span / self.engine.sr
        rows, in_header, row = {}, {}, 0  # track -> (y, height) of its own row / of its row in its group's header
        for lane in self.lanes():
            y, h = PAD + lane.top * scale, lane.height * scale
            if lane.automation:
                p.fillRect(QRectF(0, y, self.width(), h), theme.AUTOMATION_LANE)
            elif lane.track:
                p.fillRect(QRectF(0, y, self.width(), h), theme.LANES[row % 2])
                rows[lane.track], row = (y, h), row + 1
            else:  # a group's header: its tracks' clips, each track a thinner row
                p.fillRect(QRectF(0, y, self.width(), h), group_fill(lane.group))
                members = self.project.members(lane.group)
                for i, track in enumerate(members):
                    in_header[track] = (y + i * h / len(members), h / len(members))
        for clip in self.project.clips:
            color = clip_colors(clip_color(clip)[0], self.project.audible(clip.track)).fill
            for place in (rows.get(clip.track), in_header.get(clip.track)):
                if place is not None:
                    y, h = place
                    block = QRectF(clip.start * x_scale, y + min(1, h / 4),
                                   max(1.0, clip.frames * x_scale), max(1.0, h - min(2, h / 2)))
                    if clip.disabled:  # outline only, as on the timeline
                        p.setPen(color)
                        p.drawRect(block.adjusted(0.5, 0.5, -0.5, -0.5))
                    else:
                        p.fillRect(block, color)
        p.end()
        return image

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.refresh()

    def paintEvent(self, event):
        if self._image is None:
            self.refresh()
        p = QPainter(self)
        p.drawImage(0, 0, self._image)
        rect, playhead_x, cursor_x = self._marks
        view = QRectF(*rect)
        p.fillRect(view, theme.OVERVIEW_VIEW_FILL)
        p.setPen(QPen(theme.OVERVIEW_VIEW, 1))
        p.drawRect(view.adjusted(0.5, 0.5, -0.5, -0.5))
        p.setPen(theme.CURSOR)
        p.drawLine(cursor_x, 0, cursor_x, self.height())
        p.setPen(theme.PLAYHEAD)
        p.drawLine(playhead_x, 0, playhead_x, self.height())
        p.setPen(theme.OVERVIEW_EDGE)
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)

    # --- mouse: move the editor's view -------------------------------------------------

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self.on_edge(e.position().y()):
            self._sizing = (e.globalPosition().y(), self.height())
            return
        span, pos = self.span(), e.position()
        view = self.view_rect(span)
        # grabbing the outline keeps the grabbed spot under the mouse; elsewhere the view centres there
        if view.left() <= pos.x() <= view.right():
            self._grab = (pos.x() - view.left(), pos.y() - view.top())
        else:
            self._grab = (view.width() / 2, view.height() / 2)
        self.move_view(pos, span)

    def mouseMoveEvent(self, e):
        if self._sizing is not None:
            y, height = self._sizing
            self.resized.emit(self.set_height(height + e.globalPosition().y() - y))
        elif self._grab is not None:
            self.move_view(e.position(), self.span())
        else:
            self.setCursor(Qt.SizeVerCursor if self.on_edge(e.position().y()) else Qt.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        self._grab = self._sizing = None

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton and self.on_edge(e.position().y()):
            self.resized.emit(self.set_height(config.OVERVIEW_H))

    def move_view(self, pos, span):
        hbar, vbar = self.scroll.horizontalScrollBar(), self.scroll.verticalScrollBar()
        hbar.setValue(round((pos.x() - self._grab[0]) * span / self.width() * self.timeline.pps))
        scale = self.row_scale()
        if scale:
            vbar.setValue(round((pos.y() - self._grab[1] - PAD) / scale + config.RULER_H))
