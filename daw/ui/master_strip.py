"""The master lane's controls: LUFS, effects (FX), mute, a level meter (a bar per output in use), and volume.
Its row stays at the bottom of the screen, over the tracks (main_window.py pins it there).
Under the controls, slightly dimmed: the program's version, and the update notice (update_notice.py)."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QVBoxLayout

from .. import config
from ..audio.dsp import db_to_gain
from ..version import VERSION
from . import theme
from .level_meter import LevelMeter
from .loudness_display import LoudnessDisplay
from .update_notice import notice_text
from .widgets import DbReading, fx_button, lane_frame, lane_style, toggle_button


class MasterStrip:
    """Builds the master lane frame; `frame` is the widget to place, `meter` gets fed peaks
    and `loudness` LUFS values."""

    def __init__(self, engine, open_plugins):
        self.engine = engine
        self.open_plugins = open_plugins  # opens the master's plugins window (FX button)
        self.frame = lane_frame(theme.MASTER_LANE, height=config.MASTER_H)
        col = QVBoxLayout(self.frame)
        col.setContentsMargins(8, 3, 8, 3)
        col.setSpacing(2)

        self.top = QHBoxLayout()
        title = QLabel("Master")
        title.setStyleSheet("font-weight: bold;")
        self.top.addWidget(title, 1)
        self.loudness = LoudnessDisplay()
        self.top.addWidget(self.loudness)
        self.fx = None
        self.refresh_fx(())
        self.mute_button = toggle_button("M", "mute", False, self.set_mute, "Mute the master output")
        self.top.addWidget(self.mute_button)
        col.addLayout(self.top)

        self.meter = LevelMeter(engine.channels)
        col.addWidget(self.meter, 1)

        bottom = QHBoxLayout()
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(config.MIN_DB, config.MAX_DB)
        self.volume.setValue(0)
        self.volume.setFocusPolicy(Qt.NoFocus)
        self.volume.setToolTip("Master volume (double-click the dB value to reset)")
        self.volume.valueChanged.connect(self.set_volume)
        self.db_label = DbReading(self.volume)
        bottom.addWidget(self.volume, 1)
        bottom.addWidget(self.db_label)
        col.addLayout(bottom)

        self.version_label = QLabel(f"Version: {VERSION}")
        self.version_label.setProperty("dimmed", True)  # the skin's dimmed text (theme.stylesheet)
        self.notice = QLabel(notice_text(None))
        self.notice.setProperty("dimmed", True)
        self.update_result = None
        self.notice.setOpenExternalLinks(True)  # its link opens the GitHub page
        self.notice.setToolTip("Checked on GitHub when the program starts")
        col.addSpacing(2)
        col.addWidget(self.version_label)
        col.addWidget(self.notice)
        self.set_volume(0)

    def show_update(self, result):
        """What the update check found (update_check.check())."""
        self.update_result = result
        self.notice.setText(notice_text(result))
        if result[0] == "failed":
            self.notice.setToolTip(f"Checked on GitHub when the program starts ({result[1]})")

    def restyle(self):
        """A new skin: the colours this row was built with."""
        self.frame.setStyleSheet(lane_style(theme.MASTER_LANE))
        self.notice.setText(notice_text(self.update_result))  # (its link's colour)

    def refresh_fx(self, effects):
        """(Re)make the FX button for the master's effects (lit while there are any)."""
        button = fx_button(effects, self.open_plugins)
        button.setToolTip(button.toolTip() + "\nMaster effects work on outputs 1/2.")
        if self.fx is None:
            self.top.insertWidget(2, button)
        else:
            self.top.replaceWidget(self.fx, button)
            self.fx.hide()  # deleteLater() only acts on the next event loop pass
            self.fx.deleteLater()
        self.fx = button

    def settings(self):
        """What a saved project keeps of the master."""
        return {"volume_db": self.volume.value(), "mute": self.mute_button.isChecked()}

    def apply(self, settings):
        self.volume.setValue(int(settings.get("volume_db", 0)))
        self.mute_button.setChecked(bool(settings.get("mute", False)))

    def set_mute(self, on):
        self.engine.master_mute = on

    def set_volume(self, db):
        self.engine.master_gain = db_to_gain(db, config.MIN_DB)
