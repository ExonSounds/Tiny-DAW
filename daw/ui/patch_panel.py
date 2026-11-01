"""The effects window's patch space: the effects as units on a patch panel, wired by hand.

Between rails with screws, the sound comes in at the IN jack on the left and leaves at the OUT
jack on the right edge. Each effect is a unit placed anywhere in the space: a picture of the
plugin's own window (plugin_pictures.py; an empty faceplate until there is one), its name, its
input jack (left), its sidechain jack ("SC", next to the input) if the plugin has a sidechain
input, its output jack (right), and a light: green = on and wired, amber = placed but
not wired in (its sound isn't heard), dark = bypassed (click the light), red = not loaded or
stopped after an error. Patch cables run from outputs (IN, a unit's right jack) to inputs (a
unit's input or sidechain jack, OUT); an output can feed several inputs and an input adds up all
its cables (audio/patch.py). What goes into a sidechain only steers the effect (a compressor or
limiter listening to it): it isn't heard itself. Along the bottom, above the rail, every other track
has an output jack too ("OTHER TRACKS"): its sound after its effects and fader, for sidechains only
(e.g. the kick track into the bass compressor's SC). A track that already listens to this one,
directly or through others, can't be plugged in (a loop): its jack is dimmed. A cable that would close a loop can't be plugged in.

Mouse: drag from a jack to another to plug a cable in (outputs to inputs, either way round); drag
a cable's plug off an input to move it to another input, or drop it anywhere else to unplug it;
right-click a cable to remove it. Drag a unit to move it; the space grows as units go further
right or down (drag one past the edge), and the window can be made bigger both ways. Click a
unit to select it, double-click it to open its window, right-click it for its menu; double-click
an empty spot to place an effect there, right-click it for the space's menu.
Keys: Left / Right select, Enter opens, Delete removes. Space, Home and the transport keys still
reach the transport (they are not used here).
Colours come from the skin when painting; the cables have their own colours, like real ones.

A MIDI track's instrument patch panel uses the same panel with instruments=True: IN is the track's
notes and OUT goes on to its effects; cables only run from IN to an instrument's input and from its
output to OUT (no sidechains, no other tracks), and each unit has a volume fader under its name
(drag it or turn the mouse wheel over it; double-click: 0 dB). Its light mutes it.
"""
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QToolTip, QWidget

from .. import config
from . import theme

RAIL = 16  # px, the rails at the top and bottom (with the screws)
PAD = 14  # px, between the rails and the units
END_W = 54  # px, the IN and OUT ends
CARD_W, CARD_H = 236, 194  # px, a unit
PIC = QRect(8, 8, 220, 136)  # where the picture is, in a unit
NAME_Y = 150  # px, top of the name line, in a unit
JACK_Y = 176  # px, centre of the jacks and the light, in a unit
JACK_R = 7  # px
LED_R = 5  # px
GAP = 34  # px, between units placed side by side
ROW = CARD_H + 64  # px, from one row of placed units to the next (room for hanging cables)
SAG = 38  # px, a short cable hangs this far below its plugs (longer ones more)
SNAP = 8  # px, units are placed on this grid
MARGIN = 24  # px, room right of and below the last units (moving a unit past the edge makes the space bigger)
CABLES = ("#e0533d", "#e8b23a", "#4aa3df", "#5bbf6a", "#b07cd8", "#e57fb0")  # patch cable colours
SCREW_EVERY = 150  # px
SOURCE, TARGET, SIDE = "source", "target", "side"  # a jack sound leaves by (IN, a unit's output) / comes
#                                                    in by (a unit's input, OUT) / a unit's sidechain input
IN, OUT = "in", "out"  # the ends' nodes; a unit's node is its index; a cable into a sidechain goes to ("side", index)
#                        and one from another track comes from ("track", its index in `sources`)
SIDE_DX = 24  # px from a unit's input jack to its sidechain jack
SOURCES_H = 52  # px, the row of other tracks' jacks along the bottom
SOURCE_W = 132  # px from one track's jack to the next
SOURCES_X = 118  # px, the first track's jack
VOLUME_H = 26  # px, an instrument's volume fader, under its name (instrument patch panels)
VOLUME_TEXT_W = 58  # px, its value, right of the fader


@dataclass
class Unit:
    name: str
    picture: QImage = None
    bypass: bool = False
    status: str = "On"  # shown next to the light
    problem: bool = False  # not loaded or stopped: the light is red
    idle: bool = False  # not wired in (its sound isn't heard): the light is amber
    sidechain: bool = False  # it has a sidechain input (a jack next to its input)
    tooltip: str = ""
    pos: QPoint = None  # top-left in the space; None: the panel finds it a free spot
    volume_db: float = None  # instruments: its own volume (a fader on the unit); None: no fader


def into(node):
    """The unit (or OUT) a cable's far end goes into: ("side", i) is unit i too."""
    return node[1] if isinstance(node, tuple) else node


def color(name):
    return theme.current[name]


def mixed(a, b, t):
    return QColor(theme.mix(color(a) if isinstance(a, str) else a, color(b) if isinstance(b, str) else b, t))


class PatchPanel(QWidget):
    selected = Signal(int)  # a unit was selected (its index)
    opened = Signal(int)  # double-click / Enter: open its window
    bypass_toggled = Signal(int)
    unit_moved = Signal(int, QPoint)  # dragged to a new top-left
    patched = Signal(object, object)  # (cable to remove or None, cable to add or None); a cable: (from, to) nodes
    add_requested = Signal(object)  # place an effect: at this top-left (QPoint), or None: a free spot
    remove_requested = Signal(int)
    menu_requested = Signal(int, QPoint)  # right-click on a unit (global position)
    space_menu_requested = Signal(QPoint, QPoint)  # right-click on an empty spot (there, global position)
    volume_changed = Signal(int, float)  # an instrument's fader moved (its index, dB)

    def __init__(self, parent=None, instruments=False):
        super().__init__(parent)
        self.instruments = instruments  # an instrument patch panel (see the top)
        self.card_h = CARD_H + (VOLUME_H if instruments else 0)
        self.jack_y = JACK_Y + (VOLUME_H if instruments else 0)
        self.row = self.card_h + 64  # px, from one row of placed units to the next (room for hanging cables)
        self.volume_drag = None  # the unit whose fader is being dragged
        self.units, self.pixmaps, self.cables = [], [], []
        self.sources = []  # other tracks: [(name, blocked: wiring it in would make a loop)]
        self.current = -1
        self.hover = None  # what the mouse is over: ("unit", i), ("led", i), ("jack", jack) or None
        self.press = None  # (unit, where the mouse went down, its offset in the unit) while a button is down
        self.moving = False  # a unit is being dragged
        self.wiring = None  # while a cable is being plugged: {"from": jack, "lifted": cable or None, "to": QPointF}
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

    # --- content ---

    def set_units(self, units, cables, current=None, sources=()):
        """units: Unit per effect; cables: [(from, to)], from = IN, a unit's index or ("track", k), to = a
        unit's index, ("side", index) or OUT; sources: the other tracks, [(name, blocked)]."""
        self.units, self.cables, self.sources = list(units), list(cables), list(sources)
        self.pixmaps = [self.scaled(u.picture) for u in self.units]
        placed = []
        for unit in self.units:
            if unit.pos is None:
                unit.pos = self.free_spot(placed + [u for u in self.units if u.pos is not None])
            placed.append(unit)
        if current is not None:
            self.current = current
        self.current = min(self.current, len(self.units) - 1)
        if self.current < 0 and self.units:
            self.current = 0
        self.fit()
        self.update()

    def scaled(self, image):
        """The picture made to fit a unit's picture area (sharp on high-DPI screens), or None."""
        if image is None or image.isNull():
            return None
        dpr = self.devicePixelRatioF()
        fitted = image.scaled(PIC.size() * dpr, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pixmap = QPixmap.fromImage(fitted)
        pixmap.setDevicePixelRatio(dpr)
        return pixmap

    def set_current(self, index):
        if 0 <= index < len(self.units) and index != self.current:
            self.current = index
            self.update()
            self.selected.emit(index)
        self.ensure_visible(self.unit_rect(self.current) if 0 <= self.current < len(self.units) else None)

    def scroll_area(self):
        area = self.parent().parent() if self.parent() is not None else None  # (viewport, then the scroll area)
        return area if hasattr(area, "ensureVisible") else None

    def ensure_visible(self, rect):
        area = self.scroll_area()
        if area is not None and rect is not None:
            area.ensureVisible(rect.center().x(), rect.center().y(), rect.width() // 2 + 8, rect.height() // 2 + 8)

    # --- geometry ---

    def top(self):
        return RAIL + PAD

    def content_size(self):
        """Room for every unit (the space grows as units are moved further)."""
        right = max((u.pos.x() + CARD_W for u in self.units if u.pos is not None), default=END_W + GAP + CARD_W)
        bottom = max((u.pos.y() + self.card_h for u in self.units if u.pos is not None), default=self.top() + self.card_h)
        return QSize(max(right + MARGIN + END_W, END_W * 2 + 2 * (CARD_W + GAP) + GAP,
                         SOURCES_X + len(self.sources) * SOURCE_W),
                     bottom + SAG + MARGIN + (SOURCES_H if self.sources else 0) + RAIL)

    def sources_top(self):
        return self.height() - RAIL - SOURCES_H

    def fit(self):
        self.setMinimumSize(self.content_size())  # (a scroll area around it scrolls when it is bigger than the view)

    def sizeHint(self):
        return self.content_size()

    def unit_rect(self, index):
        return QRect(self.units[index].pos, QSize(CARD_W, self.card_h))

    def visible_width(self):
        area = self.scroll_area()
        return area.viewport().width() if area is not None else self.width()

    def free_spot(self, taken):
        """Top-left of the first free place on the grid of rows, left to right, top to bottom, among the
        `taken` units' places, within the visible width."""
        columns = max(1, (self.visible_width() - 2 * END_W - GAP) // (CARD_W + GAP))
        for n in range(1000):
            spot = QPoint(END_W + GAP + (n % columns) * (CARD_W + GAP), self.top() + (n // columns) * self.row)
            area = QRect(spot, QSize(CARD_W, self.card_h)).adjusted(-GAP // 2, -GAP // 2, GAP // 2, GAP // 2)
            if not any(area.intersects(QRect(u.pos, QSize(CARD_W, self.card_h))) for u in taken if u.pos is not None):
                return spot
        return QPoint(END_W + GAP, self.top())

    def spot_at(self, point):
        """Top-left of a unit centred on `point`, on the grid and inside the space."""
        x = max(END_W + SNAP, point.x() - CARD_W // 2)
        y = max(self.top(), point.y() - self.card_h // 2)
        return QPoint(round(x / SNAP) * SNAP, round(y / SNAP) * SNAP)

    def jack_point(self, jack):
        side, node = jack
        y = self.top() + self.jack_y
        if node == IN:
            return QPointF(END_W / 2, y)
        if node == OUT:
            return QPointF(self.width() - END_W / 2, y)
        if isinstance(node, tuple):  # ("track", k)
            return QPointF(SOURCES_X + node[1] * SOURCE_W, self.sources_top() + SOURCES_H / 2)
        r = self.unit_rect(node)
        if side == SOURCE:
            return QPointF(r.right() - 15, r.top() + self.jack_y)
        return QPointF(r.left() + 16 + (SIDE_DX if side == SIDE else 0), r.top() + self.jack_y)

    def jacks(self):
        yield SOURCE, IN
        yield TARGET, OUT
        for k in range(len(self.sources)):
            yield SOURCE, ("track", k)
        for i, unit in enumerate(self.units):
            yield TARGET, i
            if unit.sidechain:
                yield SIDE, i
            yield SOURCE, i

    def jack_at(self, pos):
        for jack in self.jacks():
            p = self.jack_point(jack)
            if (p.x() - pos.x()) ** 2 + (p.y() - pos.y()) ** 2 <= (JACK_R + 4) ** 2:
                return jack
        return None

    def led_rect(self, index):
        """The light and its word (click to bypass)."""
        r = self.unit_rect(index)
        left = 34 + (SIDE_DX + 20 if self.units[index].sidechain else 0)
        return QRect(r.left() + left, r.top() + self.jack_y - 10, CARD_W - left - 34, 20)

    def volume_rect(self, index):
        """An instrument's volume fader, with its value (None: the unit has none)."""
        if self.units[index].volume_db is None:
            return None
        r = self.unit_rect(index)
        return QRect(r.left() + 8, r.top() + NAME_Y + 20, CARD_W - 16, VOLUME_H - 4)

    def groove(self, index):
        """Left and right end (x) of a fader's travel: MIN_DB to MAX_DB."""
        v = self.volume_rect(index)
        return v.left() + 8, v.right() - VOLUME_TEXT_W

    def volume_at(self, index, x):
        """The volume for the fader's knob at x: in half dB, caught at 0 dB."""
        x0, x1 = self.groove(index)
        db = config.MIN_DB + min(1.0, max(0.0, (x - x0) / (x1 - x0))) * (config.MAX_DB - config.MIN_DB)
        return 0.0 if abs(db) < 0.75 else round(db * 2) / 2

    def volume_x(self, index, db):
        x0, x1 = self.groove(index)
        return x0 + (db - config.MIN_DB) / (config.MAX_DB - config.MIN_DB) * (x1 - x0)

    def volume_under(self, pos):
        index = self.unit_at(pos)
        rect = self.volume_rect(index) if index is not None else None
        return index if rect is not None and rect.contains(pos) else None

    def set_volume(self, index, db):
        db = min(float(config.MAX_DB), max(float(config.MIN_DB), db))
        if db != self.units[index].volume_db:
            self.units[index].volume_db = db
            self.update()
            self.volume_changed.emit(index, db)

    def unit_at(self, pos):
        order = self.paint_order()
        for index in reversed(order):
            if self.unit_rect(index).contains(pos):
                return index
        return None

    def paint_order(self):
        """Units from bottom to top: the selected one (and one being moved) on top."""
        order = [i for i in range(len(self.units)) if i != self.current]
        return order + [self.current] if 0 <= self.current < len(self.units) else order

    def cable_path(self, a, b):
        path = QPainterPath(a)
        if a.y() > b.y() + 30:  # (up from the other tracks' row: rises, then hangs into the jack)
            path.cubicTo(QPointF(a.x(), a.y() - 70), QPointF(b.x(), b.y() + 80), b)
            return path
        sag = SAG + min(90.0, ((b.x() - a.x()) ** 2 + (b.y() - a.y()) ** 2) ** 0.5 * 0.12)
        path.cubicTo(QPointF(a.x() + 14, a.y() + sag), QPointF(b.x() - 14, b.y() + sag), b)
        return path

    def cable_ends(self, cable):
        return self.jack_point((SOURCE, cable[0])), self.jack_point(self.target_jack(cable[1]))

    @staticmethod
    def target_jack(node):
        """The jack a cable's far end is plugged into."""
        return (SIDE, node[1]) if isinstance(node, tuple) else (TARGET, node)

    @staticmethod
    def target_node(jack):
        """A cable's far end, for the jack it is plugged into (an input, a sidechain or OUT)."""
        return ("side", jack[1]) if jack[0] == SIDE else jack[1]

    def cable_at(self, pos):
        stroker = QPainterPathStroker()
        stroker.setWidth(10)
        for cable in reversed(self.cables):
            if stroker.createStroke(self.cable_path(*self.cable_ends(cable))).contains(QPointF(pos)):
                return cable
        return None

    # --- wiring rules ---

    def reaches(self, start, goal, cables):
        """Does sound from `start`'s output reach `goal` through `cables`?"""
        todo, seen = [start], set()
        while todo:
            node = todo.pop()
            if node == goal:
                return True
            for a, b in cables:
                b = into(b)
                if a == node and b not in seen:
                    seen.add(b)
                    todo.append(b)
        return False

    def can_plug(self, cable, without=None):
        """Can `cable` (from, to) be plugged in (`without` the one being moved)? Not twice, not a unit into
        itself, never a loop."""
        a, b = cable[0], into(cable[1])
        others = [c for c in self.cables if c != without]
        if self.instruments:  # notes from IN into an instrument, its sound to OUT: nothing else
            return cable not in others and ((a == IN and isinstance(b, int)) or (isinstance(a, int) and b == OUT))
        if isinstance(a, tuple):  # another track: only into a sidechain, never one that would make a loop
            return (isinstance(cable[1], tuple) and not self.sources[a[1]][1]
                    and cable not in others)
        return cable not in others and a != b and not (a != IN and b != OUT and self.reaches(b, a, others))

    def wiring_target(self, jack):
        """The cable plugging the cable being dragged into `jack` would make, or None if it can't go there."""
        if jack is None or self.wiring is None:
            return None
        held = self.wiring["from"]
        if (held[0] == SOURCE) == (jack[0] == SOURCE):  # (outputs go to inputs, and the other way round)
            return None
        cable = (held[1], self.target_node(jack)) if held[0] == SOURCE else (jack[1], self.target_node(held))
        return cable if self.can_plug(cable, self.wiring["lifted"]) else None

    # --- painting ---

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self.paint_panel(p)
        self.paint_end(p, IN)
        self.paint_end(p, OUT)
        if self.sources:
            self.paint_sources(p)
        for index in self.paint_order():
            self.paint_unit(p, index)
        if not self.units:
            p.setPen(color("hint_text"))
            p.setFont(self.small_font(size=0))
            p.drawText(QRect(END_W, self.top() + 20, self.width() - 2 * END_W, self.card_h - 40), Qt.AlignCenter,
                       "Double-click here (or use Add…) to place an instrument.\n"
                       "Then wire it: drag a cable from IN (the notes) to its input, and from its output to OUT."
                       if self.instruments else
                       "Double-click here (or use Add…) to place an effect.\n"
                       "Then wire it: drag a cable from IN to its input, and from its output to OUT.")
        lifted = self.wiring["lifted"] if self.wiring else None
        for cable in self.cables:
            if cable != lifted:
                self.paint_cable(p, *self.cable_ends(cable), self.cable_color(cable))
        if self.wiring is not None:
            self.paint_wiring(p)

    def paint_panel(self, p):
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), mixed("base", "window", 0.5))
        for y in (0, h - RAIL):
            p.fillRect(QRect(0, y, w, RAIL), color("button"))
            for x in range(SCREW_EVERY // 2, w, SCREW_EVERY):
                self.paint_screw(p, QPointF(x, y + RAIL / 2))
        p.fillRect(QRect(0, RAIL, w, 1), color("frame_edge"))
        p.fillRect(QRect(0, h - RAIL - 1, w, 1), color("frame_edge"))

    def paint_sources(self, p):
        """The other tracks' row: a jack and a name each."""
        top = self.sources_top()
        p.fillRect(QRect(0, top, self.width(), SOURCES_H), mixed("base", "window", 0.25))
        p.fillRect(QRect(0, top, self.width(), 1), mixed("base", "frame_edge", 0.5))
        p.setPen(color("hint_text"))
        p.setFont(self.small_font(bold=True, size=-1.5))
        p.drawText(QRect(8, top, SOURCES_X - 24, SOURCES_H), Qt.AlignVCenter | Qt.AlignLeft | Qt.TextWordWrap,
                   "OTHER TRACKS\n(sidechains)")
        for k, (name, blocked) in enumerate(self.sources):
            p.setOpacity(0.35 if blocked else 1)
            self.paint_jack(p, (SOURCE, ("track", k)))
            c = self.jack_point((SOURCE, ("track", k)))
            p.setPen(color("button_text"))
            p.setFont(self.small_font())
            text = p.fontMetrics().elidedText(name, Qt.ElideRight, SOURCE_W - JACK_R * 2 - 14)
            p.drawText(QRectF(c.x() + JACK_R + 6, c.y() - 9, SOURCE_W - JACK_R * 2 - 12, 18),
                       Qt.AlignLeft | Qt.AlignVCenter, text)
            p.setOpacity(1)

    def paint_screw(self, p, c):
        p.setPen(Qt.NoPen)
        p.setBrush(mixed("button", "window_text", 0.28))
        p.drawEllipse(c, 4, 4)
        p.setPen(QPen(mixed("button", "frame_edge", 0.6), 1.4))
        p.drawLine(QPointF(c.x() - 2.6, c.y() + 1.5), QPointF(c.x() + 2.6, c.y() - 1.5))

    def paint_jack(self, p, jack):
        c = self.jack_point(jack)
        p.setPen(Qt.NoPen)
        p.setBrush(mixed("button", "window_text", 0.35))
        p.drawEllipse(c, JACK_R, JACK_R)
        p.setBrush(color("frame_edge"))
        p.drawEllipse(c, JACK_R - 3, JACK_R - 3)
        if self.wiring is not None and self.wiring_target(jack) is not None:  # a place the cable can go
            hot = self.hover == ("jack", jack)
            ring = QColor(color("accent"))
            ring.setAlpha(255 if hot else 150)
            p.setPen(QPen(ring, 3 if hot else 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, JACK_R + 4, JACK_R + 4)

    def paint_end(self, p, node):
        c = self.jack_point((SOURCE if node == IN else TARGET, node))
        p.setPen(color("hint_text"))
        p.setFont(self.small_font(bold=True))
        p.drawText(QRectF(c.x() - END_W / 2, self.top() + 8, END_W, 18), Qt.AlignCenter, node.upper())
        p.setFont(self.small_font())
        p.drawText(QRectF(c.x() - END_W / 2, self.top() + 24, END_W, 16), Qt.AlignCenter,
                   ("notes" if node == IN else "to effects") if self.instruments else
                   "from track" if node == IN else "to fader")
        self.paint_jack(p, (SOURCE if node == IN else TARGET, node))

    def small_font(self, bold=False, size=-1.0):
        font = QFont(self.font())
        font.setPointSizeF(max(6.5, self.font().pointSizeF() + size))
        font.setBold(bold)
        return font

    def paint_unit(self, p, index):
        unit, r = self.units[index], self.unit_rect(index)
        radius = theme.radius()
        hovered = self.hover in (("unit", index), ("led", index), ("volume", index)) and self.wiring is None
        p.setPen(Qt.NoPen)
        p.setBrush(mixed("button", "window_text", 0.06) if hovered else color("button"))
        p.drawRoundedRect(QRectF(r), radius, radius)
        # the picture of the plugin
        pic = PIC.translated(r.topLeft())
        p.fillRect(pic, color("base"))
        dim = 0.35 if unit.bypass else 1
        p.setOpacity(dim)
        pixmap = self.pixmaps[index]
        if pixmap is not None:
            size = pixmap.deviceIndependentSize().toSize()
            p.drawPixmap(pic.left() + (pic.width() - size.width()) // 2, pic.top() + (pic.height() - size.height()) // 2,
                         pixmap)
        else:
            self.paint_faceplate(p, pic)
        p.setOpacity(1)
        # the name
        p.setPen(color("disabled_text") if unit.bypass else color("button_text"))
        p.setFont(self.small_font(bold=True, size=0))
        name = p.fontMetrics().elidedText(unit.name, Qt.ElideRight, CARD_W - 16)
        p.drawText(QRect(r.left() + 8, r.top() + NAME_Y, CARD_W - 16, 18), Qt.AlignCenter, name)
        if unit.volume_db is not None:
            self.paint_volume(p, index)
        # jacks, and the light with its state
        self.paint_jack(p, (TARGET, index))
        if unit.sidechain:
            self.paint_jack(p, (SIDE, index))
            c = self.jack_point((SIDE, index))
            p.setPen(color("hint_text"))
            p.setFont(self.small_font(bold=True, size=-2))
            p.drawText(QRectF(c.x() + JACK_R + 2, c.y() - 8, 20, 16), Qt.AlignLeft | Qt.AlignVCenter, "SC")
        self.paint_jack(p, (SOURCE, index))
        led = self.led_rect(index)
        p.setFont(self.small_font())
        width = p.fontMetrics().horizontalAdvance(unit.status) + 2 * LED_R + 6
        centre = QPointF(led.center().x() - width / 2 + LED_R, led.center().y() + 0.5)
        lit = (theme.RECORD if unit.problem else None if unit.bypass else theme.METER_WARN if unit.idle
               else theme.METER_OK)
        p.setPen(QPen(color("frame_edge"), 1))
        p.setBrush(lit if lit is not None else mixed("button", "frame_edge", 0.45))
        p.drawEllipse(centre, LED_R, LED_R)
        p.setPen(color("button_text") if self.hover == ("led", index) else color("hint_text"))
        p.drawText(QRectF(centre.x() + LED_R + 6, led.top(), width, led.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   unit.status)
        if index == self.current:
            p.setPen(QPen(color("accent"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(r).adjusted(1, 1, -1, -1), radius, radius)

    def paint_volume(self, p, index):
        """An instrument's fader: a groove filled up to the knob, a mark at 0 dB, and the value."""
        unit, v = self.units[index], self.volume_rect(index)
        x0, x1 = self.groove(index)
        y, knob = v.center().y() + 0.5, self.volume_x(index, unit.volume_db)
        p.setPen(Qt.NoPen)
        p.setBrush(color("frame_edge"))
        p.drawRoundedRect(QRectF(x0, y - 2, x1 - x0, 4), 2, 2)
        p.setBrush(color("disabled_text") if unit.bypass else mixed("accent", "button", 0.25))
        p.drawRoundedRect(QRectF(x0, y - 2, knob - x0, 4), 2, 2)
        zero = self.volume_x(index, 0.0)
        p.setPen(QPen(color("hint_text"), 1))
        p.drawLine(QPointF(zero, y - 7), QPointF(zero, y - 4))
        p.drawLine(QPointF(zero, y + 4), QPointF(zero, y + 7))
        hot = self.hover == ("volume", index) or self.volume_drag == index
        p.setPen(QPen(color("frame_edge"), 1))
        p.setBrush(color("accent") if hot else mixed("button", "window_text", 0.55))
        p.drawRoundedRect(QRectF(knob - 5, y - 8, 10, 16), 3, 3)
        db = unit.volume_db
        text = "−∞ dB" if db <= config.MIN_DB else f"{db:+.1f} dB".replace("-", "−") if db else "0.0 dB"
        p.setPen(color("button_text") if hot else color("hint_text"))
        p.setFont(self.small_font())
        p.drawText(QRectF(x1 + 8, v.top(), VOLUME_TEXT_W - 8, v.height()), Qt.AlignRight | Qt.AlignVCenter, text)

    def paint_faceplate(self, p, pic):
        """No picture yet: a blank faceplate with a few knobs."""
        p.setPen(QPen(mixed("base", "hint_text", 0.35), 1.6))
        p.setBrush(Qt.NoBrush)
        for i in range(4):
            c = QPointF(pic.left() + pic.width() * (i + 0.5) / 4, pic.top() + pic.height() * 0.42)
            p.drawEllipse(c, 12, 12)
            p.drawLine(c, QPointF(c.x() - 6, c.y() - 8))
        p.setPen(mixed("base", "hint_text", 0.7))
        p.setFont(self.small_font())
        p.drawText(QRect(pic.left(), pic.top() + pic.height() * 0.68, pic.width(), 20), Qt.AlignCenter,
                   "No picture yet")

    def cable_color(self, cable):
        """Cables take the colours in turn, in the order they were plugged in."""
        return QColor(CABLES[self.cables.index(cable) % len(CABLES)])

    def paint_cable(self, p, a, b, cable, plugs=(True, True)):
        path = self.cable_path(a, b)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(cable.darker(150), 6, Qt.SolidLine, Qt.RoundCap))
        p.drawPath(path)
        p.setPen(QPen(cable, 3.6, Qt.SolidLine, Qt.RoundCap))
        p.drawPath(path)
        for end, plugged in zip((a, b), plugs):
            p.setPen(Qt.NoPen)
            p.setBrush(cable.darker(130))
            p.drawEllipse(end, JACK_R - 1, JACK_R - 1)
            p.setBrush(cable if plugged else cable.lighter(120))
            p.drawEllipse(end, JACK_R - 3.5, JACK_R - 3.5)

    def paint_wiring(self, p):
        """The cable in the hand: from the jack it is plugged into to the mouse (or the jack it would go in)."""
        side = self.wiring["from"][0]
        fixed = self.jack_point(self.wiring["from"])
        target = self.hover[1] if self.hover and self.hover[0] == "jack" else None
        loose = self.jack_point(target) if self.wiring_target(target) is not None else self.wiring["to"]
        a, b = (fixed, loose) if side == SOURCE else (loose, fixed)
        lifted = self.wiring["lifted"]
        cable = self.cable_color(lifted) if lifted else QColor(CABLES[len(self.cables) % len(CABLES)])
        self.paint_cable(p, a, b, cable)

    # --- mouse and keys ---

    def mousePressEvent(self, e):
        self.setFocus()
        pos = e.position().toPoint()
        if e.button() == Qt.RightButton:
            self.right_click(pos, e.globalPosition().toPoint())
            return
        if e.button() != Qt.LeftButton:
            return
        jack = self.jack_at(pos)
        if jack is not None:
            self.start_wiring(jack, QPointF(pos))
            return
        index = self.unit_at(pos)
        if index is None:
            return
        if self.led_rect(index).contains(pos):
            self.bypass_toggled.emit(index)
            return
        self.set_current(index)
        if self.volume_under(pos) == index:  # its fader: the knob goes where clicked, then follows the mouse
            self.volume_drag = index
            self.set_volume(index, self.volume_at(index, pos.x()))
            return
        self.press = (index, pos, pos - self.unit_rect(index).topLeft())

    def start_wiring(self, jack, pos):
        """Pick up a cable: the one plugged into this input (to move or unplug it), else a new one from here."""
        lifted = None
        if jack[0] != SOURCE:
            lifted = next((c for c in reversed(self.cables) if c[1] == self.target_node(jack)), None)
        held = (SOURCE, lifted[0]) if lifted else jack
        self.wiring = {"from": held, "lifted": lifted, "to": pos}
        self.hover = ("jack", jack)
        self.update()

    def right_click(self, pos, where):
        index = self.unit_at(pos)
        cable = self.cable_at(pos) if self.jack_at(pos) is None else None  # (cables lie on top of the units)
        if cable is not None:
            menu = QMenu(self)
            menu.addAction(f"Remove the cable {self.node_name(cable[0])} → {self.node_name(cable[1])}",
                           lambda: self.patched.emit(cable, None))
            menu.exec(where)
        elif index is not None:
            self.set_current(index)
            self.menu_requested.emit(index, where)
        else:
            self.space_menu_requested.emit(pos, where)

    def node_name(self, node):
        if isinstance(node, tuple) and node[0] == "track":
            return self.sources[node[1]][0]
        if isinstance(node, tuple):
            return f"{self.units[node[1]].name}'s sidechain"
        return node.upper() if node in (IN, OUT) else self.units[node].name

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self.volume_drag is not None:
            self.set_volume(self.volume_drag, self.volume_at(self.volume_drag, pos.x()))
            return
        if self.wiring is not None:
            self.wiring["to"] = QPointF(pos)
            jack = self.jack_at(pos)
            self.hover = ("jack", jack) if jack is not None else None
            self.ensure_visible(QRect(pos, QSize(1, 1)))
            self.update()
            return
        if self.press is not None:
            index, start, offset = self.press
            if self.moving or (pos - start).manhattanLength() >= QApplication.startDragDistance():
                self.moving = True
                self.units[index].pos = self.spot_at(pos - offset + QPoint(CARD_W // 2, self.card_h // 2))
                self.fit()  # (the space grows if needed)
                self.ensure_visible(self.unit_rect(index))
                self.update()
            return
        self.hover_at(pos)

    def hover_at(self, pos):
        jack = self.jack_at(pos)
        index = self.unit_at(pos)
        hit = (("jack", jack) if jack is not None else ("led", index) if index is not None and self.led_rect(index).contains(pos)
               else ("volume", index) if index is not None and self.volume_under(pos) == index
               else ("unit", index) if index is not None else None)
        if hit != self.hover:
            self.hover = hit
            self.setCursor(Qt.PointingHandCursor if hit and hit[0] in ("jack", "led") else
                           Qt.SizeHorCursor if hit and hit[0] == "volume" else Qt.ArrowCursor)
            self.update()

    def mouseReleaseEvent(self, e):
        pos = e.position().toPoint()
        if self.volume_drag is not None:
            self.volume_drag = None
            self.hover_at(pos)
            self.update()
            return
        if self.wiring is not None:
            cable, lifted = self.wiring_target(self.jack_at(pos)), self.wiring["lifted"]
            self.wiring = None
            if cable is not None:
                if cable != lifted:  # (put back where it was: nothing changes)
                    self.patched.emit(lifted, cable)  # plugged in (moved, if it was picked up off an input)
            elif lifted is not None:
                self.patched.emit(lifted, None)  # dropped anywhere else: unplugged
            self.hover_at(pos)
            self.update()
            return
        press, moving, self.press, self.moving = self.press, self.moving, None, False
        if press is not None and moving:
            self.unit_moved.emit(press[0], self.units[press[0]].pos)

    def mouseDoubleClickEvent(self, e):
        pos = e.position().toPoint()
        if self.jack_at(pos) is not None:
            return
        index = self.unit_at(pos)
        if index is None:
            self.add_requested.emit(self.spot_at(pos))
        elif self.volume_under(pos) == index:
            self.set_volume(index, 0.0)
        elif self.led_rect(index).contains(pos):
            self.bypass_toggled.emit(index)  # (a quick second click on the light counts once more)
        else:
            self.set_current(index)
            self.opened.emit(index)

    def wheelEvent(self, e):
        """Over an instrument's fader: 1 dB a notch; elsewhere the space scrolls."""
        index = self.volume_under(e.position().toPoint())
        steps = e.angleDelta().y() // 120 or (1 if e.angleDelta().y() > 0 else -1 if e.angleDelta().y() < 0 else 0)
        if index is None or not steps:
            super().wheelEvent(e)
            return
        self.set_volume(index, round(self.units[index].volume_db) + steps)
        e.accept()

    def leaveEvent(self, _):
        if self.wiring is None:
            self.hover = None
            self.update()

    def event(self, e):
        if e.type() == QEvent.ToolTip:
            text = self.tooltip_at(e.pos())
            QToolTip.showText(e.globalPos(), text, self) if text else QToolTip.hideText()
            return True
        return super().event(e)

    def tooltip_at(self, pos):
        jack = self.jack_at(pos)
        if jack is not None and self.instruments:
            node = jack[1]
            if node == IN:
                return ("IN: the track's notes (its clips, and keys played in the MIDI editor). Drag a cable from "
                        "here to an instrument's input.")
            if node == OUT:
                return "OUT: on to the track's effects. Every instrument wired in here is heard, added up."
            name = self.units[node].name
            return (f"{name}'s sound: drag a cable from here to OUT." if jack[0] == SOURCE else
                    f"{name}'s input, for the notes: drag a cable from here to IN. With a cable in: drag its plug "
                    "away to move or unplug it.")
        index = self.volume_under(pos)
        if index is not None:
            return (f"{self.units[index].name}'s volume: drag the knob, or turn the mouse wheel over it; "
                    "double-click for 0 dB.")
        if jack is not None:
            side, node = jack
            if node == IN:
                return "IN: the track's sound, before its effects. Drag a cable from here to an effect's input."
            if node == OUT:
                return "OUT: on to the track's fader. What is wired in here is heard (nothing: silence)."
            if isinstance(node, tuple):
                name, blocked = self.sources[node[1]]
                if blocked:
                    return f"{name} already listens to this one (through a sidechain), so it can't be wired in here."
                return (f"{name}: its sound after its effects and fader, for sidechains only (it isn't heard here). "
                        "Drag a cable from here to an effect's SC jack. Even muted, it still steers the sidechain.")
            name = self.units[node].name
            if side == SIDE:
                return (f"{name}'s sidechain input: what is wired in here steers {name} (e.g. a compressor ducking "
                        "to it) without being heard. Drag a cable from here to IN or an effect's output.")
            return (f"{name}'s output: drag a cable from here to another effect's input, a sidechain or OUT."
                    if side == SOURCE else f"{name}'s input: drag a cable from here to IN or an effect's output. "
                                           "With a cable in: drag its plug away to move or unplug it.")
        index = self.unit_at(pos)
        if index is not None:
            return self.units[index].tooltip
        cable = self.cable_at(pos)
        if cable is not None:
            return (f"{self.node_name(cable[0])} → {self.node_name(cable[1])}: right-click to remove it, or drag "
                    "its plug off the input to move or unplug it.")
        return ""

    def keyPressEvent(self, e):
        key = e.key()
        if key in (Qt.Key_Left, Qt.Key_Right) and self.units:
            self.set_current(max(0, min(len(self.units) - 1, self.current + (-1 if key == Qt.Key_Left else 1))))
        elif key in (Qt.Key_Return, Qt.Key_Enter) and 0 <= self.current < len(self.units):
            self.opened.emit(self.current)
        elif key == Qt.Key_Delete and 0 <= self.current < len(self.units):
            self.remove_requested.emit(self.current)
        elif key == Qt.Key_Escape and self.wiring is not None:
            self.wiring = None
            self.update()
        else:
            super().keyPressEvent(e)
