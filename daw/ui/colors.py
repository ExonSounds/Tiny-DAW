"""Automatic contrast for clip colours.

Whatever colour a clip gets, clip_colors() derives everything drawn on it so it
stays readable: the fill is lifted until it stands out from the dark lanes, the
waveform/notes are pushed lighter or darker until they stand out from the fill,
and the name is drawn in white or near-black, whichever reads better, on a
handle strip shaded until the name is easy to read. A coloured track's row in the panel
follows the same rules (track_colors()).

Which colour a clip has: its own, else (with Options → Colours → "Clips take their track's
colour", `follow_track`) its track's, else its kind's default from the skin (clip_color()).
New tracks can get a random colour (random_color()).
Contrast is the WCAG ratio (1 = none, 21 = black on white).
"""
import random
from dataclasses import dataclass
from functools import lru_cache

from PySide6.QtGui import QColor

from . import theme

WHITE, INK = QColor(255, 255, 255), QColor(18, 20, 24)
MIN_FILL_VS_LANE = 2.0  # clip body against the lane background
MIN_CONTENT_VS_FILL = 3.0  # waveform / MIDI notes against the clip body
MIN_TEXT_VS_HANDLE = 4.5  # clip name against the handle strip (WCAG AA for text)
SILENT_SATURATION = 0.15  # muted clips keep a hint of their colour


def luminance(c):
    """WCAG relative luminance, 0 (black) .. 1 (white)."""
    def channel(v):
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(c.redF()) + 0.7152 * channel(c.greenF()) + 0.0722 * channel(c.blueF())


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def mix(a, b, t):
    """a blended toward b by t (0 = a, 1 = b)."""
    return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * t, a.greenF() + (b.greenF() - a.greenF()) * t,
                           a.blueF() + (b.blueF() - a.blueF()) * t)


def ensure_contrast(color, against, ratio):
    """color, blended toward white or black just enough to reach `ratio` against `against`.

    Goes in whichever direction can reach the ratio, preferring the one that already helps.
    """
    if contrast(color, against) >= ratio:
        return color
    directions = sorted((WHITE, INK), key=lambda target: -contrast(target, against))
    for target in directions:
        if contrast(target, against) < ratio:
            continue
        for step in range(1, 21):
            candidate = mix(color, target, step / 20)
            if contrast(candidate, against) >= ratio:
                return candidate
    return directions[0]


def readable_text(background):
    return WHITE if contrast(WHITE, background) >= contrast(INK, background) else INK


@dataclass(frozen=True)
class ClipColors:
    fill: QColor  # clip body
    handle: QColor  # top strip with the name (drag to move)
    content: QColor  # waveform or MIDI notes
    text: QColor  # clip name
    border: QColor
    selected_border: QColor


follow_track = False  # clips without a colour of their own take their track's (Options → Colours)


def default_color(kind):
    return theme.CLIP_FILL[kind].name()


def clip_color(clip):
    """(colour "#rrggbb", tone) a clip is drawn in: its own, else its track's (if clips follow their
    track's colour and the track has one), else its kind's default."""
    if clip.color:
        return clip.color, clip.tone
    track = clip.track
    if follow_track and track is not None and track.color:
        return track.color, clip.tone or track.tone
    return default_color(clip.kind), clip.tone


def random_color(taken=()):
    """A pleasant random colour ("#rrggbb") for a new track, its hue as far as possible from the
    colours in `taken`, so neighbours stay easy to tell apart."""
    hues = [QColor(c).hsvHueF() for c in taken if c]
    hues = [h for h in hues if h >= 0]  # (greys have no hue)

    def distance(h):
        return min((min(abs(h - t), 1 - abs(h - t)) for t in hues), default=1.0)

    hue = max((random.random() for _ in range(24)), key=distance)
    return QColor.fromHsvF(hue, random.uniform(0.45, 0.65), random.uniform(0.62, 0.8)).name()


# Fixed colours the user may pick for a clip's waveform/notes and name (Clip.tone),
# offered only where they have enough contrast on that clip's colour.
TONES = {"light": WHITE, "dark": QColor("#2a2c30")}


def clip_fill(hex_color, audible=True):
    """The clip body colour actually drawn: greyed when silent, lifted off the lane background."""
    base = QColor(hex_color)
    if not audible:  # muted, or not soloed while others are: nearly grey and a bit darker
        h, s, l, _ = base.getHslF()
        base = QColor.fromHslF(max(h, 0.0), s * SILENT_SATURATION, l * 0.8)
    return ensure_contrast(base, theme.LANES[1], MIN_FILL_VS_LANE)


def tone_available(hex_color, tone, audible=True):
    """True if the fixed tone ("light" / "dark") contrasts enough with this clip colour."""
    return contrast(TONES[tone], clip_fill(hex_color, audible)) >= MIN_CONTENT_VS_FILL


@lru_cache(maxsize=128)
def track_colors(hex_color, tone=None):
    """(background, text) for a track's row in the panel, by the same rules as a clip's body and its
    waveform: the background is lifted off the lanes, and the text (name, LUFS readout) is white or
    near-black, or the fixed tone when that contrasts enough."""
    fill = clip_fill(hex_color)
    text = TONES[tone] if tone and tone_available(hex_color, tone) else readable_text(fill)
    return fill, text


@lru_cache(maxsize=512)
def clip_colors(hex_color, audible=True, tone=None):
    """All colours for drawing a clip whose chosen colour is hex_color ("#rrggbb").

    tone "light" / "dark" draws the waveform/notes and name in white / dark grey when that
    contrasts enough; otherwise (or with tone None) they are picked automatically.
    """
    fill = clip_fill(hex_color, audible)
    dark_fill = luminance(fill) < 0.3
    handle = mix(fill, INK, 0.3) if luminance(fill) > 0.1 else mix(fill, WHITE, 0.12)
    if tone and tone_available(hex_color, tone, audible):
        content = text = TONES[tone]
    else:
        content = ensure_contrast(mix(fill, WHITE if dark_fill else INK, 0.55), fill, MIN_CONTENT_VS_FILL)
        text = readable_text(handle)
    handle = ensure_contrast(handle, text, MIN_TEXT_VS_HANDLE)  # shade the strip until the name reads well
    return ClipColors(fill=fill, handle=handle, content=content, text=text,
                      border=mix(fill, INK, 0.45), selected_border=WHITE if dark_fill else INK)
