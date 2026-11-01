"""Choosing a MIDI track's instrument, and opening its window to configure it.

The menu belongs to the "Choose instrument" button that fills a new MIDI track's row, and to the
▾ of the instrument button (🎹) on its row once it has one: the built-in synth, or a VST3
instrument found on this computer (audio/plugins.py), loaded with the usual "please wait" window
(plugin_host.py). The track is silent until its first instrument is chosen and loaded. Clicking
🎹 itself opens the instrument's own window (edit_instrument).

"Instrument patch panel…" makes the track play several instruments at once instead, from its
instrument patch panel (plugin_dialog.InstrumentPanelDialog; 🎹 then opens it). Its one instrument
goes in it first, wired, so it keeps sounding. Choosing a single instrument again leaves the panel
as it is (not heard), to go back to."""
from PySide6.QtWidgets import QMessageBox

from ..audio.plugins import INSTRUMENT, usable
from ..model import PATCH_IN, PATCH_OUT
from .plugin_dialog import slot_label

BUILTIN = "Built-in synth"
RACK = "Instrument patch panel…"


def same_plugin(info, slot):
    return slot is not None and (info.path, info.plugin_name) == (slot.path, slot.plugin_name)


def fill_instrument_menu(menu, parent, host, track, chosen):
    """Fill `menu` with the instruments to choose from (the track's own ticked) and, once it has a
    VST3 instrument, a way to open its window. chosen() is called after the track's instrument changed."""
    menu.clear()
    if track.frozen:
        menu.addAction(f"{track.name} is frozen: unfreeze it to change its instrument").setEnabled(False)
        return menu
    current = None if track.needs_instrument or track.uses_rack else track.instrument
    if current is not None:
        edit = menu.addAction(f"Open the {current.name} window")
        edit.setEnabled(usable(current))
        edit.triggered.connect(lambda: edit_instrument(parent, host, track))
        menu.addSeparator()
    builtin = menu.addAction(BUILTIN)
    builtin.setCheckable(True)
    builtin.setChecked(current is None and not track.needs_instrument and not track.uses_rack)
    builtin.triggered.connect(lambda: set_instrument(parent, host, track, None, chosen))
    count = len(track.rack)
    rack = menu.addAction(f"Instrument patch panel ({count} instrument{'' if count == 1 else 's'})…" if count else RACK)
    rack.setCheckable(True)
    rack.setChecked(track.uses_rack and not track.needs_instrument)
    rack.setToolTip("Several instruments on this track at once, each with its own volume")
    rack.triggered.connect(lambda: use_rack(parent, host, track, chosen))
    menu.addSeparator()
    found = host.library.of_kind(INSTRUMENT)
    if current is not None and not any(same_plugin(info, current) for info in found):
        missing = menu.addAction(slot_label(current))  # e.g. not installed any more
        missing.setCheckable(True)
        missing.setChecked(True)
        missing.setEnabled(False)
    known = [info for info in found if info.kind]
    untried = [info for info in found if not info.kind]  # never loaded, so they may be effects
    add_entries(menu, known, parent, host, track, current, chosen)
    if known and untried:  # out of the way, in a submenu
        add_entries(menu.addMenu(f"Not loaded before ({len(untried)})"), untried, parent, host, track, current,
                    chosen, labels=False)
    else:
        add_entries(menu, untried, parent, host, track, current, chosen)
    if not found:
        menu.addAction("(no VST3 instruments found)").setEnabled(False)
    return menu


def add_entries(menu, infos, parent, host, track, current, chosen, labels=True):
    """One entry per plugin (labels: with "type not known yet" for those never loaded)."""
    for info in infos:
        action = menu.addAction(info.label if labels else info.name)
        action.setCheckable(True)
        action.setChecked(same_plugin(info, current))
        action.triggered.connect(lambda _=False, i=info: set_instrument(parent, host, track, i, chosen))


def set_instrument(parent, host, track, info, chosen):
    """Load `info` (None: the built-in synth) as the track's instrument. Returns False if it couldn't
    be loaded (the track keeps what it had)."""
    old = None if track.needs_instrument else track.instrument
    if info is None:
        slot = None
    elif same_plugin(info, old) and old.instance is not None:
        slot = old  # the one it has already
    else:
        slot = host.load(info)
        if slot is None:
            return False
        if slot.kind != INSTRUMENT:
            QMessageBox.information(parent, "Not an instrument",
                                    f"{slot.name} is an effect. Add it with the track's FX button instead.")
            return False
    if old is not None and slot is not old:
        host.close_editor([old])
    track.instrument = slot  # loaded already, so the audio thread can use it at once
    track.needs_instrument = False
    if track.uses_rack:  # (its instrument patch panel stays as it is, not heard)
        track.uses_rack = False
        host.close_editor(list(track.rack))
    chosen()
    return True


def use_rack(parent, host, track, chosen):
    """Make the track play its instrument patch panel, and open it. The first time, the instrument it
    has goes in it, wired, so it keeps sounding."""
    if not track.uses_rack or track.needs_instrument:
        single = None if track.needs_instrument else track.instrument
        if not track.rack and single is not None:
            single.at = None
            track.rack_cables = ((PATCH_IN, single), (single, PATCH_OUT))
            track.rack = (single,)
        track.uses_rack = True  # (the panel is ready first: the audio thread never finds half of it)
        track.needs_instrument = False
        if track.instrument in track.rack:
            track.instrument = None
        chosen()
    parent.open_instrument_panel(track)


def edit_instrument(parent, host, track):
    """Open the track's VST3 instrument's own window, to configure it (it stays open while you keep
    working; plugin_host.py). The built-in synth has no settings."""
    slot = track.instrument
    if track.frozen:
        QMessageBox.information(parent, "Track frozen", f"{track.name} is frozen: it plays its rendering, so changes "
                                "to its instrument wouldn't be heard.\n"
                                "Unfreeze it first (right-click its row).")
    elif track.uses_rack and not track.needs_instrument:
        parent.open_instrument_panel(track)
    elif slot is None or track.needs_instrument:
        QMessageBox.information(parent, "Built-in synth", f"{track.name} plays the built-in synth, which has no "
                                "settings. Choose a VST3 instrument with ▾ next to 🎹 to configure one.")
    elif not usable(slot):
        QMessageBox.information(parent, "Instrument not loaded", f"{slot.name} isn't loaded, so its window can't "
                                "open. Choose it again with ▾ next to 🎹.")
    else:
        host.edit(slot, track.name)
