"""Skins: the interface's colours and fonts, kept in JSON files.

    {
      "format": "tinydaw-skin",
      "version": 1,
      "name": "Midnight",
      "colors": {"lane": "#161a28", "playhead": "#ff6b81", "selection_fill": "#7c8cff2d", …},
      "fonts": {"ui": "Segoe UI", "ui_size": 9, "mono": "Consolas"}
    }

The colour names are theme.COLORS ("#rrggbb", or "#rrggbbaa" with transparency); a skin may name
only some of them, the rest keep the default ("Dark"). Fonts: "" or 0 for the system's own.

The program's own skins are in daw/skins (Dark, Light, Midnight); your own in
%APPDATA%\\TinyDAW\\skins, where Options → Skin → Customize… saves them and Import… copies them.
"""
import json
import os
import re
from dataclasses import dataclass, field

from . import theme

FORMAT, VERSION = "tinydaw-skin", 1
BUILTIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skins")
USER_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "TinyDAW", "skins")
DEFAULT = "Dark"


@dataclass
class Skin:
    name: str
    colors: dict = field(default_factory=dict)  # name -> "#rrggbb"[aa]
    fonts: dict = field(default_factory=dict)
    path: str = None  # the file it came from
    builtin: bool = False  # one of the program's own (not changed in place; "Save as" instead)

    def complete(self):
        """Every colour and font, the skin's own or the default."""
        return ({**theme.DEFAULT_COLORS, **self.colors}, {**theme.FONTS, **self.fonts})


def to_json(skin):
    colors, fonts = skin.complete()
    return {"format": FORMAT, "version": VERSION, "name": skin.name, "colors": colors, "fonts": fonts}


def read(path, builtin=False):
    """(Skin, problems) from a skin file; raises ValueError if it isn't one. Problems: what was
    ignored (unknown names, values that aren't colours), as text."""
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Not valid JSON: {e}") from None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError(f'Not a Tiny DAW skin (it needs "format": "{FORMAT}").')
    problems, colors, fonts = [], {}, {}
    for name, value in (data.get("colors") or {}).items():
        if name not in theme.DEFAULT_COLORS:
            problems.append(f"unknown colour name: {name}")
        elif theme.parse_color(value) is None:
            problems.append(f"{name}: not a colour ({value!r}; use \"#rrggbb\" or \"#rrggbbaa\")")
        else:
            colors[name] = value.lower()
    for name, value in (data.get("fonts") or {}).items():
        if name not in theme.FONTS:
            problems.append(f"unknown font setting: {name}")
        elif name == "ui_size" and not (isinstance(value, (int, float)) and 0 <= value <= 40):
            problems.append(f"ui_size: not a size in points ({value!r})")
        elif name != "ui_size" and not isinstance(value, str):
            problems.append(f"{name}: not a font name ({value!r})")
        else:
            fonts[name] = value
    name = data.get("name") if isinstance(data.get("name"), str) and data.get("name").strip() else \
        os.path.splitext(os.path.basename(path))[0]
    return Skin(name.strip(), colors, fonts, path, builtin), problems


def write(skin, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(to_json(skin), f, indent=2)
    os.replace(temp, path)


def file_name(name):
    return (re.sub(r"[^\w\- ]+", "_", name).strip() or "skin") + ".json"


def library(user_dir=None):
    """Every skin there is: the program's own (Dark first), then yours, by name. Files that can't
    be read are left out."""
    found = []
    for folder, builtin in ((BUILTIN_DIR, True), (user_dir or USER_DIR, False)):
        if not os.path.isdir(folder):
            continue
        for entry in sorted(os.listdir(folder)):
            if entry.lower().endswith(".json"):
                try:
                    found.append(read(os.path.join(folder, entry), builtin)[0])
                except (OSError, ValueError):
                    pass
    found.sort(key=lambda s: (not s.builtin, s.name != DEFAULT, s.name.lower()))
    return found


def find(name, user_dir=None):
    """The skin of that name (yours first, if you have one of the same name), or the default."""
    skins = library(user_dir)
    for skin in sorted(skins, key=lambda s: s.builtin):
        if skin.name == name:
            return skin
    return next((s for s in skins if s.name == DEFAULT), Skin(DEFAULT, builtin=True))


_system_font = None


def apply(app, skin):
    """Put `skin` on the whole program: theme colours, palette, stylesheet, font; cached drawings
    that used the old colours are dropped. The caller then rebuilds what was built with them."""
    global _system_font
    if _system_font is None:
        _system_font = app.font()
    colors, fonts = skin.complete()
    theme.set_skin(colors, fonts)
    theme.apply_to(app, _system_font)
    from . import colors as clip_colors_module
    from .waveform_cache import TILES
    clip_colors_module.clip_colors.cache_clear()
    clip_colors_module.track_colors.cache_clear()
    TILES.clear()
