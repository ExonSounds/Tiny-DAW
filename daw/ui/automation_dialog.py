"""Choosing a parameter to automate: one of the track's plugins (its instrument or an effect), then
one of that plugin's parameters, found by typing part of its name (big synths have hundreds). The
ones tweaked last in the plugin's window come first (automation.TweakWatcher), so turning a knob
there and then choosing it here takes no searching."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QVBoxLayout)

from ..audio import automation


class ParameterDialog(QDialog):
    """After exec(): `chosen` is (slot, parameter name), or None."""

    def __init__(self, parent, track):
        super().__init__(parent)
        self.setWindowTitle(f"Automate a parameter: {track.name}")
        self.resize(420, 520)
        self.track, self.chosen = track, None
        taken = {(id(a.slot), a.param) for a in track.automation}
        self.taken = taken
        col = QVBoxLayout(self)
        form = QFormLayout()
        self.plugins = QComboBox()
        for slot in automation.plugins_of(track):
            kind = "Instrument" if slot in track.instruments() else "Effect"
            self.plugins.addItem(f"{kind}: {slot.name}" + ("" if slot.instance is not None else "  (not loaded)"), slot)
        self.plugins.currentIndexChanged.connect(lambda _: self.fill())
        form.addRow("Plugin", self.plugins)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Type part of a parameter's name…")
        self.search.textChanged.connect(lambda _: self.fill())
        form.addRow("Find", self.search)
        col.addLayout(form)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _: self.accept())
        col.addWidget(self.list, 1)
        self.count = QLabel()
        self.count.setProperty("hint", True)
        col.addWidget(self.count)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        col.addWidget(buttons)
        self.params = {}  # id(slot) -> its parameters, listed once
        self.fill()
        self.search.setFocus()

    def fill(self):
        slot = self.plugins.currentData()
        self.list.clear()
        if slot is None:
            self.count.setText("This track has no plugins to automate: add an instrument or effects first.")
            return
        if id(slot) not in self.params:
            self.params[id(slot)] = automation.parameters(slot)
        words = self.search.text().lower().split()
        matches = lambda name: all(w in name.lower() for w in words)
        params = dict(self.params[id(slot)])
        recent = [(name, params[name]) for name in slot.recent if name in params and matches(name)]
        found = [(name, param) for name, param in self.params[id(slot)] if matches(name)]
        if recent:  # the ones tweaked last in its window first, under headings
            self.add_heading("Recently tweaked")
            self.add_items(slot, recent)
            self.add_heading("All parameters")
        self.add_items(slot, found)
        total = len(self.params[id(slot)])
        self.count.setText(f"{len(found)} of {total} parameters" if slot.instance is not None
                           else f"{slot.name} isn't loaded, so its parameters can't be listed.")
        enabled = [i for i in range(self.list.count()) if self.list.item(i).flags() & Qt.ItemIsEnabled]
        if enabled:
            self.list.setCurrentRow(enabled[0])

    def add_heading(self, title):
        heading = QListWidgetItem(title)
        heading.setFlags(Qt.NoItemFlags)
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        self.list.addItem(heading)

    def add_items(self, slot, found):
        for name, param in found:
            try:
                value = param.string_value.strip()
            except Exception:
                value = ""
            item = QListWidgetItem(f"{name}   ({value})" if value else name)
            item.setData(Qt.UserRole, name)
            if (id(slot), name) in self.taken:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                item.setText(item.text() + "   — automated already")
            self.list.addItem(item)

    def accept(self):
        item = self.list.currentItem()
        if item is None or not item.flags() & Qt.ItemIsEnabled:
            return
        self.chosen = (self.plugins.currentData(), item.data(Qt.UserRole))
        super().accept()
