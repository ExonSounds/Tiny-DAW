"""Options → Skin → Customize this skin…: every colour of the skin and its fonts, shown live as
you change them. Save keeps them in your own skin file (the program's own skins are saved as a
new one of yours); Export writes the skin anywhere, to share it."""
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QDialog, QDoubleSpinBox, QFileDialog, QFontComboBox, QFormLayout,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
                               QTabWidget, QVBoxLayout, QWidget)

from . import skin as skins, theme

TRANSPARENT = {"selection_fill", "selection_edge", "loop", "loop_off", "frozen_tint", "overview_view", "overview_view_fill",
               "key_sounding", "roll_shade"}  # usually see-through: their picker offers transparency


class ColorButton(QPushButton):
    """A colour swatch with its hex value; click to pick another."""

    def __init__(self, name, label, value, changed):
        super().__init__()
        self.name, self.label, self.changed = name, label, changed
        self.setMinimumWidth(130)
        self.clicked.connect(self.pick)
        self.set_value(value)

    def set_value(self, value):
        self.value = value
        color = theme.parse_color(value)
        text = "#ffffff" if color.lightness() < 128 or color.alpha() < 90 else "#000000"
        self.setText(value)
        self.setStyleSheet(f"QPushButton {{ background: {color.name()}; color: {text}; "
                           f"border: 1px solid {theme.FRAME_EDGE.name()}; padding: 3px; }}")

    def pick(self):
        options = QColorDialog.ShowAlphaChannel if self.name in TRANSPARENT or len(self.value) == 9 else \
            QColorDialog.ColorDialogOption(0)
        color = QColorDialog.getColor(theme.parse_color(self.value), self, self.label, options)
        if color.isValid():
            self.set_value(theme.hex_of(color))
            self.changed(self.name, self.value)


class SkinDialog(QDialog):
    """skin: the Skin to start from; apply(Skin) shows a skin on the program; saved(Skin) is told
    about a skin that was saved (to choose it)."""

    def __init__(self, parent, skin, apply, saved, tab=None):
        """tab: the name of the tab to open on (a colour group, or "Fonts")."""
        super().__init__(parent)
        self.setWindowTitle("Customize skin")
        self.resize(560, 620)
        self.original, self.apply, self.saved = skin, apply, saved
        colors, fonts = skin.complete()
        self.working = replace(skin, colors=dict(colors), fonts=dict(fonts))
        self.dirty = False
        self.buttons = {}

        col = QVBoxLayout(self)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Skin name"))
        self.name_edit = QLineEdit(skin.name)
        self.name_edit.textEdited.connect(lambda _: self.mark())
        name_row.addWidget(self.name_edit, 1)
        col.addLayout(name_row)
        hint = QLabel("Changes show at once. Your own skins are kept in " + skins.USER_DIR)
        hint.setProperty("hint", True)
        hint.setWordWrap(True)
        col.addWidget(hint)

        self.tabs = QTabWidget()
        groups = []
        for name, _, label, group in theme.COLORS:
            if group not in groups:
                groups.append(group)
        for group in groups:
            form = QFormLayout()
            for name, _, label, g in theme.COLORS:
                if g == group:
                    self.buttons[name] = ColorButton(name, label, self.working.colors[name], self.set_color)
                    form.addRow(label, self.buttons[name])
            page = QWidget()
            page.setLayout(form)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.tabs.addTab(scroll, group)
        self.tabs.addTab(self.font_page(), "Fonts")
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == tab:
                self.tabs.setCurrentIndex(i)
        col.addWidget(self.tabs, 1)

        buttons = QHBoxLayout()
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.save)
        save_as = QPushButton("Save as new skin…")
        save_as.clicked.connect(self.save_as)
        export = QPushButton("Export…")
        export.setToolTip("Write this skin to a JSON file anywhere, to share it")
        export.clicked.connect(self.export)
        revert = QPushButton("Revert")
        revert.setToolTip("Back to the skin as it was when this window opened")
        revert.clicked.connect(self.revert)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        for b in (self.save_button, save_as, export, revert):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(close)
        col.addLayout(buttons)
        self.update_buttons()

    def font_page(self):
        f = self.working.fonts
        form = QFormLayout()
        self.system_font = QCheckBox("The system's own")
        self.system_font.setChecked(not f["ui"])
        self.ui_font = QFontComboBox()
        if f["ui"]:
            self.ui_font.setCurrentText(f["ui"])
        self.ui_font.setEnabled(bool(f["ui"]))
        self.ui_size = QDoubleSpinBox()
        self.ui_size.setRange(0, 40)
        self.ui_size.setDecimals(1)
        self.ui_size.setSpecialValueText("The system's own")
        self.ui_size.setValue(float(f["ui_size"] or 0))
        self.mono_font = QFontComboBox()
        self.mono_font.setCurrentText(f["mono"] or "Consolas")
        row = QHBoxLayout()
        row.addWidget(self.ui_font, 1)
        row.addWidget(self.system_font)
        form.addRow(theme.FONT_LABELS["ui"], row)
        form.addRow(theme.FONT_LABELS["ui_size"], self.ui_size)
        form.addRow(theme.FONT_LABELS["mono"], self.mono_font)
        self.system_font.toggled.connect(lambda on: (self.ui_font.setEnabled(not on), self.fonts_changed()))
        self.ui_font.currentFontChanged.connect(lambda _: self.fonts_changed())
        self.ui_size.valueChanged.connect(lambda _: self.fonts_changed())
        self.mono_font.currentFontChanged.connect(lambda _: self.fonts_changed())
        page = QWidget()
        page.setLayout(form)
        return page

    # --- changes ---

    def mark(self):
        self.dirty = True
        self.update_buttons()

    def update_buttons(self):
        self.save_button.setEnabled(self.dirty and not self.working.builtin)
        self.save_button.setToolTip("The program's own skins can't be changed: use Save as new skin"
                                    if self.working.builtin else "Save the changes in this skin's file")

    def set_color(self, name, value):
        self.working.colors[name] = value
        self.apply(self.working)
        self.mark()

    def fonts_changed(self):
        self.working.fonts = {"ui": "" if self.system_font.isChecked() else self.ui_font.currentFont().family(),
                              "ui_size": self.ui_size.value() or 0, "mono": self.mono_font.currentFont().family()}
        self.apply(self.working)
        self.mark()

    def revert(self):
        colors, fonts = self.original.complete()
        self.working = replace(self.original, colors=dict(colors), fonts=dict(fonts))
        for name, button in self.buttons.items():
            button.set_value(self.working.colors[name])
        self.name_edit.setText(self.original.name)
        self.apply(self.working)
        self.dirty = False
        self.update_buttons()

    # --- saving ---

    def current(self):
        return replace(self.working, name=self.name_edit.text().strip() or self.working.name)

    def save(self):
        """Into this skin's own file (yours only)."""
        if self.working.builtin or not self.working.path:
            return self.save_as()
        skin = self.current()
        skins.write(skin, skin.path)
        return self.done_saving(skin)

    def save_as(self):
        name, ok = QInputDialog.getText(self, "Save as new skin", "Name of the new skin:",
                                        text=self.current().name + ("" if not self.working.builtin else " (mine)"))
        if not ok or not name.strip():
            return False
        import os
        path = os.path.join(skins.USER_DIR, skins.file_name(name.strip()))
        if os.path.exists(path) and QMessageBox.question(
                self, "Skin exists", f"There is already a skin file {os.path.basename(path)}. Replace it?") \
                != QMessageBox.Yes:
            return False
        skin = replace(self.current(), name=name.strip(), path=path, builtin=False)
        skins.write(skin, path)
        return self.done_saving(skin)

    def done_saving(self, skin):
        self.working = replace(skin, colors=dict(skin.colors), fonts=dict(skin.fonts))
        self.original = skin
        self.name_edit.setText(skin.name)
        self.dirty = False
        self.update_buttons()
        self.saved(skin)
        return True

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export skin", skins.file_name(self.current().name),
                                              "Tiny DAW skin (*.json)")
        if path:
            skins.write(self.current(), path if path.lower().endswith(".json") else path + ".json")

    def reject(self):
        """Close, Esc and the window's own close button all end here (QDialog's closeEvent calls this):
        unsaved changes are kept or dropped as you say, or the window stays open (Cancel)."""
        if self.dirty:
            answer = QMessageBox.question(self, "Unsaved skin", "Keep the changes to this skin?",
                                          QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if answer == QMessageBox.Cancel or (answer == QMessageBox.Save and not self.save()):
                return
            if answer == QMessageBox.Discard:
                self.revert()
        super().reject()
