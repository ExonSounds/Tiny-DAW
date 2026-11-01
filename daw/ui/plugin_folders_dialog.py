"""Options → VST2 plugin folders: where the program looks for VST2 plugins (DLLs), as well as the
standard VST3 folder. Subfolders are searched too. Remembered between runs (preferences.py).
VST2 plugins then appear wherever plugins are chosen, named "… (VST2)".
"""
import os

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QPushButton,
                               QVBoxLayout)

from ..audio.plugins import VST2_USUAL_FOLDERS


class PluginFoldersDialog(QDialog):
    def __init__(self, parent, folders):
        super().__init__(parent)
        self.setWindowTitle("VST2 plugin folders")
        self.resize(560, 360)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Folders with VST2 plugins (their subfolders are searched too):"))
        self.list = QListWidget()
        self.list.addItems(folders)
        self.list.currentRowChanged.connect(self.update_buttons)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        add = QPushButton("Add folder…")
        add.clicked.connect(self.add_folder)
        self.remove = QPushButton("Remove")
        self.remove.clicked.connect(self.remove_folder)
        self.usual = QPushButton("Add the usual folders")
        self.usual.setToolTip("Adds those of these that are on this computer:\n" + "\n".join(VST2_USUAL_FOLDERS))
        self.usual.clicked.connect(self.add_usual)
        for button in (add, self.remove, self.usual):
            button.setAutoDefault(False)
            row.addWidget(button)
        row.addStretch(1)
        lay.addLayout(row)
        hint = QLabel("VST3 plugins are always found in C:\\Program Files\\Common Files\\VST3. Only 64-bit VST2 "
                      "plugins can be used; plugins that hold several (shells, such as Waves) aren't supported yet.")
        hint.setProperty("hint", True)
        hint.setWordWrap(True)
        lay.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.update_buttons()

    def folders(self):
        return [self.list.item(i).text() for i in range(self.list.count())]

    def add(self, folder):
        folder = os.path.normpath(folder)
        if folder not in self.folders():
            self.list.addItem(folder)
        self.update_buttons()

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Folder with VST2 plugins",
                                                  next((f for f in VST2_USUAL_FOLDERS if os.path.isdir(f)), ""))
        if folder:
            self.add(folder)

    def remove_folder(self):
        row = self.list.currentRow()
        if row >= 0:
            self.list.takeItem(row)
        self.update_buttons()

    def add_usual(self):
        for folder in self.usual_folders():
            self.add(folder)

    def usual_folders(self):
        """The usual VST2 folders that are on this computer and not listed yet."""
        return [f for f in VST2_USUAL_FOLDERS if os.path.isdir(f) and os.path.normpath(f) not in self.folders()]

    def update_buttons(self):
        self.remove.setEnabled(self.list.currentRow() >= 0)
        self.usual.setEnabled(bool(self.usual_folders()))
