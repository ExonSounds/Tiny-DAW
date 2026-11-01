"""The effects window of a track (FX button on its row) or of the master: its patch space
(patch_panel.py), with a picture of each plugin (plugin_pictures.py). Effects are placed in it
and wired by hand (model.py, audio/patch.py): a newly chosen effect is only placed, so it isn't
heard until it is wired in. Changes apply immediately, while playing too. (A MIDI track's
instrument is chosen with the instrument button on its row: instrument_menu.py.)

InstrumentPanelDialog is the same window for a MIDI track's instrument patch panel (several
instruments on one track, each with its own volume): its notes go from IN to the instruments wired
from it, and those wired to OUT are heard, added up, on to the track's effects.

It is an ordinary window, not a modal one: the rest of the program keeps working while it is
open (play, stop, edit, open other tracks' windows). main_window.py keeps one per track. It can
be made bigger both ways; the patch space scrolls when it is bigger than the window.
Effects without a picture yet are photographed when the window opens (invisibly, about a second
each; then never again, unless their window is opened or "Take a new picture" is chosen)."""
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QInputDialog, QLabel,
                               QMenu, QMessageBox, QPushButton, QScrollArea, QVBoxLayout)

from ..audio.patch import plan
from ..audio.plugins import EFFECT, INSTRUMENT, usable
from ..audio.mixer import listened_to
from ..model import PATCH_IN, PATCH_OUT, STRAIGHT, Sidechain, TrackSource, chain
from . import plugin_pictures
from .patch_panel import CARD_H, CARD_W, END_W, GAP, IN, OUT, PatchPanel, Unit

CABLES_OF = {"effects": "cables", "master_effects": "master_cables", "rack": "rack_cables"}  # where each
#                                                                                          owner keeps its cables


def slot_label(slot):
    if slot.instance is None:
        return f"{slot.name}  (not loaded)"
    if slot.error:
        return f"{slot.name}  (stopped after an error: {slot.error})"
    return slot.name


def touches(cable, slot):
    """Is the cable plugged into this effect (its input, sidechain or output)?"""
    return slot in cable or Sidechain(slot) in cable


def unit_of(slot, heard, fed, sidechain=False, instrument=False):
    """How an effect (or an instrument of an instrument patch panel) shows on the patch panel. heard: its
    output reaches OUT; fed: something is wired into it."""
    idle = False
    if slot.instance is None:
        status, tip = "Not loaded", f"{slot.name} isn't loaded (not installed here any more?). Its settings are kept."
    elif slot.error:
        status, tip = "Stopped", f"{slot.name} stopped after an error: {slot.error}"
    elif instrument:
        if slot.bypass:
            status, tip = "Muted", f"{slot.name} is muted. Click the light to hear it again."
        elif not heard:
            status, idle = "Not wired", True
            tip = f"{slot.name} isn't heard: wire its output (right jack) to OUT."
        elif not fed:
            status, idle = "No notes", True
            tip = f"{slot.name} gets no notes: wire IN to its input (left jack)."
        else:
            status, tip = "On", (f"{slot.name}: double-click to open its window. Click the light to mute it; "
                                 "its fader sets its volume.")
    elif slot.bypass:
        status, tip = "Bypassed", f"{slot.name} is bypassed: its input passes through unchanged. Click the light to switch it on."
    elif not heard:
        status, idle = "Not wired", True
        tip = f"{slot.name} isn't heard: wire its output (right jack) to OUT or to another effect that is."
    elif not fed:
        status, idle = "No input", True
        tip = f"Nothing is wired into {slot.name}: wire IN (or another effect's output) to its input (left jack)."
    else:
        status, tip = "On", f"{slot.name}: double-click to open its window. Click the light to bypass it."
    pos = QPoint(*slot.at) if slot.at is not None else None
    has_sidechain = sidechain or (usable(slot) and slot.instance.has_sidechain)
    if has_sidechain:
        tip += "\nIt has a sidechain input (SC, next to its input jack)."
    return Unit(slot.name, plugin_pictures.picture(slot), slot.bypass, status, problem=slot.instance is None or bool(slot.error),
                idle=idle, sidechain=has_sidechain and not instrument, tooltip=f"{tip}\n{slot.path}", pos=pos,
                volume_db=slot.volume_db if instrument else None)


class PluginsDialog(QDialog):
    """owner: a Track (effects in `effects`, cables in `cables`) or the Project (`master_effects`,
    `master_cables`). on_change() is called after every change."""
    INSTRUMENTS = False  # an instrument patch panel (InstrumentPanelDialog)
    KIND, NOUN = EFFECT, "effect"  # what is placed in it
    HEADER = "<b>Effects</b> — the sound goes from IN, through the cables, to OUT"
    HINT = ("Drag from a jack to another to plug in a cable: from IN or an effect's output (right) to an "
            "effect's input (left), its sidechain (SC) or OUT. Drag a plug off an input to move or unplug it; right-click a "
            "cable to remove it; the other tracks' jacks along the bottom go into sidechains.\n"
            "Drag an effect to move it; double-click it to open its window; click its "
            "light to bypass it. Double-click an empty spot to place an effect there; right-click for more.")
    BYPASS = "Bypass"

    def __init__(self, parent, host, title, owner, effects_attr, on_change):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(Qt.NonModal)  # the main window stays usable: play, stop, edit…
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.host, self.owner, self.attr, self.on_change = host, owner, effects_attr, on_change
        self.cables_attr = CABLES_OF[effects_attr]
        self.where = getattr(owner, "name", "Master")  # for the plugin windows' titles
        self.project = host.window.project
        self.others = []  # the other tracks, as the panel lists them (sidechain sources)
        lay = QVBoxLayout(self)

        lay.addWidget(QLabel(self.HEADER))
        self.panel = PatchPanel(instruments=self.INSTRUMENTS)
        self.panel.opened.connect(lambda i: self.open_window(self.effects[i]))
        self.panel.bypass_toggled.connect(self.toggle_bypass)
        self.panel.unit_moved.connect(self.unit_moved)
        self.panel.patched.connect(self.patch)
        self.panel.add_requested.connect(self.add_effect)
        self.panel.remove_requested.connect(lambda i: self.remove_effect())
        self.panel.menu_requested.connect(self.unit_menu)
        self.panel.space_menu_requested.connect(self.space_menu)
        self.panel.volume_changed.connect(self.volume_changed)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.panel)
        self.scroll.setWidgetResizable(True)  # (the space fills the window; it scrolls when bigger)
        self.scroll.setFrameShape(QFrame.NoFrame)
        lay.addWidget(self.scroll, 1)
        buttons = QHBoxLayout()
        for text, slot in (("Add…", lambda: self.add_effect(None)), ("Remove", self.remove_effect),
                           ("Open window", lambda: self.open_window(self.selected()))):
            button = QPushButton(text)
            button.setAutoDefault(False)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        hint = QLabel(self.HINT)
        hint.setProperty("hint", True)  # the skin's hint colour
        hint.setWordWrap(True)
        lay.addWidget(hint)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.close)
        lay.addWidget(close)
        self.host.pictures.taken.connect(self.picture_taken)
        self.resize(2 * END_W + 3 * (CARD_W + GAP) + GAP + 40, self.panel.card_h * 2 + 260)
        self.refresh()
        QTimer.singleShot(0, self.photograph_missing)

    # --- effects and cables ---

    @property
    def effects(self):
        return getattr(self.owner, self.attr)

    @property
    def cables(self):
        return getattr(self.owner, self.cables_attr)

    def set_patch(self, effects=None, cables=None, current=None):
        """New effects and/or cables (new tuples: the audio thread sees old or new, never half). Cables go
        first when effects are removed, last when they are added: the audio thread never finds a cable to
        an effect that is gone."""
        if cables is not None:
            cables = tuple(cables)
            if effects is not None and len(effects) < len(self.effects):
                setattr(self.owner, self.cables_attr, cables)
        if effects is not None:
            setattr(self.owner, self.attr, tuple(effects))
        if cables is not None:
            setattr(self.owner, self.cables_attr, cables)
        self.refresh(current)
        self.on_change()

    def refresh(self, current=None):
        effects, cables = self.effects, self.cables
        order, sources = plan(cables)
        heard = set(order)
        index = {slot: i for i, slot in enumerate(effects)}

        project = self.project
        self.others = [] if self.INSTRUMENTS else [t for t in project.tracks if t is not self.owner]
        others = {t: k for k, t in enumerate(self.others)}

        def node(end):
            if isinstance(end, TrackSource):
                return ("track", others[end.track]) if end.track in others else None
            if isinstance(end, Sidechain):
                return ("side", index[end.slot]) if end.slot in index else None
            return IN if end == PATCH_IN else OUT if end == PATCH_OUT else index.get(end)

        # (a plugin not loaded here keeps the sidechain jack its cables are plugged into)
        units = [unit_of(s, s in heard, bool(sources.get(s)), Sidechain(s) in sources, self.INSTRUMENTS) for s in effects]
        if all(u.pos is None for u in units):  # (none placed yet, e.g. from an older project: in a row, as they were wired)
            for i, unit in enumerate(units):
                unit.pos = QPoint(END_W + GAP + i * (CARD_W + GAP), self.panel.top())
        self.panel.set_units(units, [(node(a), node(b)) for a, b in cables if node(a) is not None and node(b) is not None],
                             current, [(t.name, self.listens_to_me(t)) for t in self.others])

    def listens_to_me(self, track):
        """Does `track` listen (through sidechains, maybe via other tracks) to this window's owner? Then
        wiring it in here would make a loop. (Nothing listens to the master.)"""
        todo, seen = [track], set()
        while todo:
            t = todo.pop()
            if t is self.owner:
                return True
            for source in listened_to(t.cables) - seen:
                seen.add(source)
                todo.append(source)
        return False

    def picture_taken(self, slot):
        if any(s.path == slot.path and s.plugin_name == slot.plugin_name for s in self.effects):
            self.refresh()

    def selected(self):
        row = self.panel.current
        return self.effects[row] if 0 <= row < len(self.effects) else None

    def open_window(self, slot):
        if slot is not None:
            self.host.edit(slot, self.where)

    def end_of(self, node):
        if isinstance(node, tuple) and node[0] == "track":
            return TrackSource(self.others[node[1]])
        if isinstance(node, tuple):
            return Sidechain(self.effects[node[1]])
        return PATCH_IN if node == IN else PATCH_OUT if node == OUT else self.effects[node]

    def patch(self, remove, add):
        """A cable plugged in, moved or unplugged on the panel ((from, to) nodes, or None)."""
        cables = list(self.cables)
        if remove is not None:
            old = (self.end_of(remove[0]), self.end_of(remove[1]))
            cables = [c for c in cables if c != old]
        if add is not None:
            new = (self.end_of(add[0]), self.end_of(add[1]))
            if new not in cables:
                cables.append(new)
        self.set_patch(cables=cables, current=self.panel.current)

    def keep_places(self):
        """Store where every unit is (also those the panel placed by itself), so they stay there."""
        for slot, unit in zip(self.effects, self.panel.units):
            slot.at = (unit.pos.x(), unit.pos.y())

    def unit_moved(self, row, pos):
        self.keep_places()
        self.effects[row].at = (pos.x(), pos.y())
        self.refresh(row)
        self.on_change()

    def add_effect(self, at=None):
        """Choose an effect (an instrument, in an instrument patch panel) and place it (at `at`, a top-left in
        the space, or on a free spot). It isn't wired."""
        choices = self.host.library.of_kind(self.KIND)
        if not choices:
            QMessageBox.information(self, f"No {self.NOUN}s", f"No VST {self.NOUN}s were found on this computer.")
            return
        label, ok = QInputDialog.getItem(self, f"Add {self.NOUN}", f"{self.NOUN.capitalize()}:",
                                         [c.label for c in choices], 0, False)
        if not ok:
            return
        slot = self.host.load(choices[[c.label for c in choices].index(label)])
        if slot is None:
            return
        if slot.kind != self.KIND:
            QMessageBox.information(self, f"Not an {self.NOUN}", self.wrong_kind(slot))
            return
        self.keep_places()
        spot = at if at is not None else self.panel.free_spot(self.panel.units)
        slot.at = (spot.x(), spot.y())
        self.set_patch(effects=[*self.effects, slot], current=len(self.effects))
        self.panel.ensure_visible(self.panel.unit_rect(self.panel.current))
        self.photograph_missing()

    def wrong_kind(self, slot):
        return f"{slot.name} is an instrument. Choose it with a MIDI track's instrument button (🎹) instead."

    def volume_changed(self, row, db):
        """An instrument's fader moved (instrument patch panels): heard at once."""
        self.effects[row].volume_db = db

    def photograph_missing(self):
        """Take a picture of each effect that has none yet (its window opened invisibly for a moment)."""
        missing = [s for s in self.effects if usable(s) and not plugin_pictures.has_picture(s)]
        if not missing:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for slot in missing:
                if self.isVisible() and any(s is slot for s in self.effects) and not plugin_pictures.has_picture(slot):
                    self.host.photograph(slot)
        finally:
            QApplication.restoreOverrideCursor()
        if self.isVisible():
            self.activateWindow()  # (back from the invisible plugin window)

    def remove_effect(self):
        slot = self.selected()
        if slot is not None:
            self.host.close_editor([slot])
            self.set_patch(effects=[s for s in self.effects if s is not slot],
                           cables=[c for c in self.cables if not touches(c, slot)])

    def unplug(self, slot):
        self.set_patch(cables=[c for c in self.cables if not touches(c, slot)], current=self.panel.current)

    def toggle_bypass(self, row):
        slot = self.effects[row]
        slot.bypass = not slot.bypass
        self.refresh(row)
        self.on_change()

    def unit_menu(self, row, where):
        slot = self.effects[row]
        menu = QMenu(self)
        menu.addAction("Open its window", lambda: self.open_window(slot)).setEnabled(usable(slot))
        bypass = menu.addAction(self.BYPASS, lambda: self.toggle_bypass(row))
        bypass.setCheckable(True)
        bypass.setChecked(slot.bypass)
        take = menu.addAction("Take a new picture", lambda: self.host.photograph(slot) and self.activateWindow())
        take.setEnabled(usable(slot))
        menu.addSeparator()
        menu.addAction("Unplug its cables", lambda: self.unplug(slot)).setEnabled(
            any(touches(c, slot) for c in self.cables))
        menu.addAction("Remove", self.remove_effect)
        menu.exec(where)

    def space_menu(self, pos, where):
        menu = QMenu(self)
        menu.addAction("Place an effect here…", lambda: self.add_effect(self.panel.spot_at(pos)))
        menu.addSeparator()
        in_a_row = menu.addAction("Wire every effect in a row (left to right)", self.wire_in_a_row)
        in_a_row.setEnabled(bool(self.effects))
        menu.addAction("Unplug every cable (IN straight to OUT)",
                       lambda: self.set_patch(cables=STRAIGHT, current=self.panel.current)).setEnabled(
            self.cables != STRAIGHT)
        menu.exec(where)

    def wire_in_a_row(self):
        """Replace every cable: IN → the effects from left to right (top to bottom where level) → OUT."""
        self.keep_places()
        ordered = sorted(self.effects, key=lambda s: (s.at[0], s.at[1]))
        self.set_patch(cables=chain(ordered), current=self.panel.current)


class InstrumentPanelDialog(PluginsDialog):
    """A MIDI track's instrument patch panel (owner: the track, instruments in `rack`, cables in
    `rack_cables`): instruments placed side by side, each wired from IN (the notes) and to OUT, each
    with its own volume."""
    INSTRUMENTS = True
    KIND, NOUN = INSTRUMENT, "instrument"
    HEADER = ("<b>Instruments</b> — the notes go from IN to every instrument wired from it; what is wired to OUT "
              "is heard, added up, on to the track's effects")
    HINT = ("Drag from a jack to another to plug in a cable: from IN to an instrument's input (left), and from "
            "its output (right) to OUT. Drag a plug off an input to move or unplug it; right-click a cable to remove it.\n"
            "Each instrument's fader sets its volume (drag it, or turn the mouse wheel over it; double-click: 0 dB); "
            "click its light to mute it. Drag an instrument to move it; double-click it to open its window. "
            "Double-click an empty spot to place an instrument there; right-click for more.")
    BYPASS = "Mute"

    def __init__(self, parent, host, title, track, on_change):
        super().__init__(parent, host, title, track, "rack", on_change)

    def wrong_kind(self, slot):
        return f"{slot.name} is an effect. Add it with the track's FX button instead."

    def space_menu(self, pos, where):
        menu = QMenu(self)
        menu.addAction("Place an instrument here…", lambda: self.add_effect(self.panel.spot_at(pos)))
        menu.addSeparator()
        menu.addAction("Wire every instrument in (IN → each → OUT)", self.wire_all).setEnabled(bool(self.effects))
        menu.addAction("Unplug every cable", lambda: self.set_patch(cables=(), current=self.panel.current)).setEnabled(
            bool(self.cables))
        menu.exec(where)

    def wire_all(self):
        """Every instrument gets the notes and is heard (the cables already there stay)."""
        cables = list(self.cables)
        for slot in self.effects:
            cables += [c for c in ((PATCH_IN, slot), (slot, PATCH_OUT)) if c not in cables]
        self.set_patch(cables=cables, current=self.panel.current)
