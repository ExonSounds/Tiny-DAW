"""Options from the Options menu, remembered between runs (QSettings).

Most of them trade detail for lower CPU use; see main_window.build_options_menu().
"""
from dataclasses import dataclass, fields

from PySide6.QtCore import QSettings

SETTINGS = ("TinyDAW", "TinyDAW")
BUFFER_SIZES = (0, 128, 256, 512, 1024, 2048)  # frames per audio buffer; 0 = the driver's own setting
REFRESH_RATES = (60, 30, 15)  # screen updates per second for the playhead and meters


@dataclass
class Preferences:
    driver_type: str = "ASIO"  # see audio/devices.py DRIVER_TYPES; a missing type falls back to another
    device_name: str = ""  # output device to open; "" or a missing one = the driver type's default
    wasapi_exclusive: bool = False
    buffer_size: int = 512  # an explicit size: some drivers' own default changes with whatever was used last
    level_meters: bool = True
    loudness_meters: bool = True
    refresh_fps: int = 60
    metronome: bool = False  # on/off (its sounds: metronome_dialog.py)
    skin: str = "Dark"  # the name of the skin in use (skin.py; Options → Skin)
    overview_h: int = 44  # the project overview's height (drag its bottom edge; config.OVERVIEW_H)
    random_track_colors: bool = False  # new tracks get a random colour (Options → Colours)
    clips_follow_track: bool = False  # clips without a colour of their own take their track's
    rounded_corners: bool = True  # False: every corner of buttons, fields, sliders… is square (theme.rounded)
    record_input: str = "1"  # the input recorded: "1" (mono) or "1/2" (a pair; recording.py)
    count_in_bars: int = 0  # bars of metronome clicks before recording starts (0: none)
    vst2_folders: str = ""  # folders with VST2 plugins, separated by "|" (Options → VST2 plugin folders)

    def vst2_list(self):
        return [f for f in self.vst2_folders.split("|") if f]

    def set_vst2_list(self, folders):
        self.vst2_folders = "|".join(dict.fromkeys(f for f in folders if f))  # (no repeats)

    @classmethod
    def load(cls):
        stored = QSettings(*SETTINGS)
        prefs = cls()
        for f in fields(cls):
            setattr(prefs, f.name, stored.value(f"options/{f.name}", getattr(prefs, f.name), type=type(f.default)))
        if prefs.buffer_size not in BUFFER_SIZES:
            prefs.buffer_size = 0
        if prefs.refresh_fps not in REFRESH_RATES:
            prefs.refresh_fps = 60
        return prefs

    def save(self):
        stored = QSettings(*SETTINGS)
        for f in fields(self):
            stored.setValue(f"options/{f.name}", getattr(self, f.name))
