"""Pictures of plugins for the effects window's patch panel: how each plugin's own window looks.

Where a plugin's picture comes from, best first:
  - one the program took of its window (plugin_host.py): every time the window is open it is
    photographed now and then, and the last picture is kept when it closes; an effect added
    without any picture yet is photographed once, its window opened invisibly for a moment;
  - the picture a VST3 bundle may carry of its plugin (Contents/Resources/Snapshots, part of the
    VST3 standard; Serum has one);
  - none: the panel draws an empty faceplate.
Taken pictures are kept small, one per plugin (not per track: every Pro-Q 4 shows the latest one),
in %APPDATA%\\TinyDAW\\plugin_pictures.
"""
import glob
import hashlib
import json
import os
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

FOLDER = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "TinyDAW", "plugin_pictures")
WIDTH = 480  # px: pictures are kept at most this wide (the panel shows them smaller; sharp on high-DPI screens)
_loaded = {}  # file -> (its mtime, QImage)


def file_for(slot):
    """Where the picture taken of this plugin is kept (readable name, plus a hash for uniqueness)."""
    key = f"{slot.path}|{slot.plugin_name or ''}"
    readable = re.sub(r"[^\w.-]+", "_", slot.name).strip("_")[:40] or "plugin"
    return os.path.join(FOLDER, f"{readable}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:10]}.png")


def bundle_of(path):
    """The .vst3 bundle folder of a plugin file inside one (X.vst3/Contents/x86_64-win/X.vst3), or None."""
    bundle = os.path.dirname(os.path.dirname(os.path.dirname(path)))
    return bundle if bundle.lower().endswith(".vst3") and os.path.isdir(bundle) else None


def snapshot(slot):
    """The picture the plugin's VST3 bundle carries of it (<class id>_snapshot[_2.0x].png), or None."""
    bundle = bundle_of(slot.path)
    if bundle is None:
        return None
    files = glob.glob(os.path.join(bundle, "Contents", "Resources", "Snapshots", "*_snapshot*.png"))
    ids = {os.path.basename(f).split("_snapshot")[0].upper() for f in files}
    if len(ids) > 1:  # several plugins in one bundle: the one named so in its moduleinfo.json
        try:
            text = open(os.path.join(bundle, "Contents", "Resources", "moduleinfo.json"), encoding="utf-8-sig").read()
            text = re.sub(r",(\s*[}\]])", r"\1", re.sub(r"(?m)^\s*//[^\n]*", "", text))
            classes = json.loads(text, strict=False).get("Classes", [])
            ids &= {c.get("CID", "").upper() for c in classes if c.get("Name") in (slot.plugin_name, slot.name)}
        except (OSError, ValueError, AttributeError):
            return None
    if len(ids) != 1:
        return None
    mine = sorted((f for f in files if os.path.basename(f).upper().startswith(next(iter(ids)))), key=lambda f: "_2.0x" not in f)
    return mine[0] if mine else None  # the high-resolution one first


def picture(slot):
    """The plugin's picture (QImage), or None (see the top)."""
    for file in (file_for(slot), snapshot(slot)):
        if file is None or not os.path.isfile(file):
            continue
        mtime = os.path.getmtime(file)
        cached = _loaded.get(file)
        if cached is None or cached[0] != mtime:
            image = QImage(file)
            if image.isNull():
                continue
            _loaded[file] = cached = (mtime, image)
        return cached[1]
    return None


def has_picture(slot):
    return picture(slot) is not None


def from_capture(captured):
    """A picture from plugin_windows.capture()'s (width, height, pixels), or None if there is nothing
    on it yet (one colour all over: the plugin hasn't drawn itself)."""
    if captured is None:
        return None
    width, height, pixels = captured
    image = QImage(pixels, width, height, width * 4, QImage.Format_RGB32).copy()  # (copy: owns its pixels)
    step_x, step_y = max(1, width // 16), max(1, height // 16)
    if len({image.pixel(x, y) for x in range(0, width, step_x) for y in range(0, height, step_y)}) < 2:
        return None
    return image


def save(slot, image):
    """Keep `image` as the plugin's picture. Returns True if it was saved."""
    if image.width() > WIDTH:
        image = image.scaledToWidth(WIDTH, Qt.SmoothTransformation)
    try:
        os.makedirs(FOLDER, exist_ok=True)
    except OSError:
        return False
    return image.save(file_for(slot), "PNG")
