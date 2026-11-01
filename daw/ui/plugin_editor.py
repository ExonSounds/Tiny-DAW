"""A plugin's own window: an ordinary Qt window the plugin draws in (its VST3 view is attached to
the window's native handle; native/vst3host.cpp). Several can be open at once and nothing waits
for them: the program keeps running as usual.

The plugin decides the size (it may change it, e.g. when its own window grows a panel); plugins
that allow it can be resized by dragging the window's edge. Sizes from the plugin are in
physical pixels, the window's in Qt's (logical) ones: devicePixelRatio converts. The window stays
above the main window and is named after the plugin and its track.

invisible=True: the window opens fully transparent, off the taskbar and without taking the focus,
for a picture of it (plugin_host.photograph).
"""
from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import QWidget

IDLE_MS = 30  # how often the plugin is given time for its window (values from playback, size changes)


class EditorWindow(QWidget):
    closed = Signal(object)  # the slot, once the window is closed

    def __init__(self, parent, slot, title, invisible=False):
        flags = Qt.Window | (Qt.Tool if invisible else Qt.Window)
        super().__init__(parent, flags)
        self.slot = slot
        self.setWindowTitle(title)
        self.setAttribute(Qt.WA_NativeWindow)
        self.setAttribute(Qt.WA_DeleteOnClose)
        if invisible:
            self.setWindowOpacity(0.0)
            self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resizable = False
        self.fitting = False  # (resizing to the plugin's own size: not passed back to it)
        self.timer = QTimer(self, interval=IDLE_MS)
        self.timer.timeout.connect(self.idle)

    def attach(self):
        """Let the plugin draw in this window. False if it has no window (or not one for Windows)."""
        size = self.slot.instance.open_editor(int(self.winId()), self.devicePixelRatioF())
        if size is None:
            return False
        self.resizable = self.slot.instance.editor_resizable()
        self.fit(*size)
        self.timer.start()
        return True

    def fit(self, width, height):
        """Take the plugin's size (physical pixels)."""
        dpr = self.devicePixelRatioF()
        w, h = max(1, round(width / dpr)), max(1, round(height / dpr))
        self.fitting = True
        if self.resizable:
            self.setMinimumSize(1, 1)
            self.setMaximumSize(16777215, 16777215)
            self.resize(w, h)
        else:
            self.setFixedSize(w, h)
        self.fitting = False

    def idle(self):
        instance = self.slot.instance
        if instance is None:
            self.close()
            return
        instance.idle()
        wanted = instance.requested_size()
        if wanted is not None:
            self.fit(*wanted)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.resizable and not self.fitting and self.slot.instance is not None:
            dpr = self.devicePixelRatioF()
            w, h = self.slot.instance.editor_resized(round(self.width() * dpr), round(self.height() * dpr))
            if (round(w / dpr), round(h / dpr)) != (self.width(), self.height()):
                self.fit(w, h)  # (the plugin only takes certain sizes)

    def place(self, main, last=None):
        """Over the main window (a third of the way down), or where it was last (`last`, a QPoint), fully
        on the screen it is on."""
        frame = self.frameGeometry()
        if last is None:
            centre = main.frameGeometry()
            last = QPoint(centre.x() + (centre.width() - frame.width()) // 2,
                          centre.y() + (centre.height() - frame.height()) // 3)
        screen = (main.screen().virtualSiblingAt(last + QPoint(frame.width() // 2, 16)) or main.screen())
        area = screen.availableGeometry()
        x = max(area.left(), min(last.x(), area.right() - frame.width()))
        y = max(area.top(), min(last.y(), area.bottom() - frame.height()))
        self.move(x, y)

    def closeEvent(self, e):
        self.timer.stop()
        if self.slot.instance is not None:
            self.slot.instance.close_editor()
        self.closed.emit(self.slot)
        super().closeEvent(e)
