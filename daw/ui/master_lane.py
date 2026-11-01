"""The master lane: always at the bottom of the editor's view, drawn over the tracks, which scroll
underneath it (main_window.py pins it there, and the master's row in the panel next to it).

It shows the grid and both cursors, scrolling sideways with the timeline. Clicking it moves the
selection cursor; files dropped on it go onto new tracks, as on the master lane before.
"""
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget


class MasterLaneView(QWidget):
    def __init__(self, timeline):
        super().__init__()
        self.timeline = timeline
        self.setAcceptDrops(True)

    def offset(self):
        """Timeline x at this widget's left edge."""
        return self.timeline.scroll.horizontalScrollBar().value()

    def paintEvent(self, event):
        p = QPainter(self)
        dx = self.offset()
        p.translate(-dx, 0)  # draw in timeline coordinates
        self.timeline.paint_master(p, event.rect().translated(dx, 0), 0, self.height())

    def update_line(self, x):
        """Repaint the strip where a cursor line at timeline x is (or was)."""
        self.update(QRect(x - self.offset() - 1, 0, 3, self.height()))

    def timeline_x(self, e):
        return e.position().x() + self.offset()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        tl = self.timeline
        tl.set_cursor(tl.snapped_frame_at(self.timeline_x(e), e.modifiers()))
        if not e.modifiers() & Qt.ControlModifier:
            tl.clear_selection()
        tl.setFocus()

    def contextMenuEvent(self, e):
        self.timeline.show_menu(e.globalPos())  # (not over a track lane)

    def wheelEvent(self, e):
        self.timeline.wheel(e.angleDelta().y(), e.modifiers(), self.timeline_x(e))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        tl = self.timeline
        tl.drop_paths([u.toLocalFile() for u in e.mimeData().urls()],
                      tl.snapped_frame_at(self.timeline_x(e), e.modifiers()), None)
        e.acceptProposedAction()
