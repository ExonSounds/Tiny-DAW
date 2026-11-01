"""The colours and fonts of the whole interface, set by the current skin (skin.py).

Every colour the program draws or styles with is one of COLORS, by name. The module-level
names below (LANES, PLAYHEAD, METER_OK, …) are set from the skin by set_skin(); code reads them
as theme.NAME when it draws, so a new skin shows at the next repaint. Buttons and labels whose
look comes from the skin are styled by stylesheet() through Qt properties ("lit", "toggle",
"accent", "hint", "dimmed") and object names ("time", "lufs", "separator"), so they change at once too.

The look is flat: every control is a plain fill in the skin's colours, with no bevels, gradients
or shadows. Hovering lightens a control a little (towards its text colour), pressing a little more;
switches that are on, and a dialog's main button, are filled with the accent colour. Arrows and
check marks are small drawings (icon()) in the skin's text colours. Corners are slightly rounded,
unless `rounded` is off (Options → Rounded corners): then every corner is square (radius()).

A skin file names any of COLORS (as "#rrggbb", or "#rrggbbaa" with transparency) and FONTS;
whatever it leaves out keeps the default here, which is the "Dark" skin.
"""
import os
import tempfile

from PySide6.QtGui import QColor, QFont, QPalette

# (name in skin files, default, what it colours, group in the skin editor)
COLORS = (
    ("window", "#2b2d31", "Window background", "Window"),
    ("window_text", "#dcdde0", "Window text", "Window"),
    ("base", "#1e1f22", "Fields and lists", "Window"),
    ("alternate_base", "#2b2d31", "Lists, every other row", "Window"),
    ("text", "#dcdde0", "Text in fields and lists", "Window"),
    ("button", "#36383d", "Buttons", "Window"),
    ("button_text", "#dcdde0", "Button text", "Window"),
    ("highlight", "#3d7ab8", "Selected items", "Window"),
    ("highlighted_text", "#ffffff", "Selected items' text", "Window"),
    ("tooltip_base", "#1e1f22", "Tooltips", "Window"),
    ("tooltip_text", "#dcdde0", "Tooltip text", "Window"),
    ("disabled_text", "#6b6e75", "Disabled text", "Window"),
    ("hint_text", "#9a9da5", "Hints", "Window"),
    ("dimmed_text", "#70737c", "Dimmed notes (version, updates)", "Window"),
    ("accent", "#3d7ab8", "Lit buttons (FX with plugins, Preview, Metronome…)", "Window"),
    ("accent_text", "#ffffff", "Text on lit buttons", "Window"),
    ("frame_edge", "#111111", "Thin edges", "Window"),

    ("lane", "#232529", "Track lane", "Timeline"),
    ("lane_alt", "#282a2f", "Track lane, every other", "Timeline"),
    ("below_lanes", "#1d1e22", "Below the last track", "Timeline"),
    ("master_lane", "#1e2530", "Master lane and row", "Timeline"),
    ("master_divider", "#465a78", "Line above the master", "Timeline"),
    ("group_lane", "#33363e", "Group header (no colour of its own)", "Timeline"),
    ("group_accent", "#a0a0af", "Bar beside a group's tracks", "Timeline"),
    ("ruler_bg", "#18191c", "Ruler", "Timeline"),
    ("ruler_text", "#9696a0", "Ruler text", "Timeline"),
    ("grid_fine", "#2d2f35", "Grid: finer lines", "Timeline"),
    ("grid", "#373a40", "Grid: beats (and seconds)", "Timeline"),
    ("grid_bar", "#484c56", "Grid: bars", "Timeline"),
    ("playhead", "#ff5050", "Playback cursor", "Timeline"),
    ("cursor", "#5fafff", "Selection cursor", "Timeline"),
    ("selection_fill", "#78aae62d", "Selected range", "Timeline"),
    ("selection_edge", "#96c3f5c8", "Selected range's edges", "Timeline"),
    ("automation_lane", "#1c1f24", "Automation lane (and its row)", "Timeline"),
    ("automation_line", "#e0a84a", "Automation curve and its points", "Timeline"),
    ("loop", "#e0a33cb4", "Loop range on the ruler (looping on)", "Timeline"),
    ("loop_off", "#e0a33c46", "Loop range on the ruler (looping off)", "Timeline"),
    ("frozen_tint", "#bee1ff46", "Over a frozen track's clips", "Timeline"),
    ("overview_bg", "#141518", "Overview bar", "Timeline"),
    ("overview_view", "#c8d2e1c8", "Overview: outline of the view", "Timeline"),
    ("overview_view_fill", "#c8d2e11c", "Overview: inside the outline", "Timeline"),
    ("overview_edge", "#373a40", "Overview: line under it", "Timeline"),

    ("row", "#232529", "Track rows in the right-hand panel", "Tracks and clips"),
    ("row_alt", "#282a2f", "Track rows in the right-hand panel, every other", "Tracks and clips"),
    ("clip_audio", "#326996", "Audio clips (no colour of their own)", "Tracks and clips"),
    ("clip_midi", "#377d55", "MIDI clips (no colour of their own)", "Tracks and clips"),
    ("accent_audio", "#5096d2", "Audio track stripe", "Tracks and clips"),
    ("accent_midi", "#5abe78", "MIDI track stripe", "Tracks and clips"),
    ("mute", "#c0392b", "Mute (M) when on", "Tracks and clips"),
    ("solo", "#d4a017", "Solo (S) when on", "Tracks and clips"),
    ("record", "#d0433a", "Recording: ● Rec when on, the take as it grows", "Tracks and clips"),

    ("meter_bg", "#121316", "Meter background", "Meters"),
    ("meter_ok", "#46c86e", "Meter: up to -12 dB (and CPU load)", "Meters"),
    ("meter_warn", "#dcbe3c", "Meter: up to 0 dB", "Meters"),
    ("meter_hot", "#dc3c32", "Meter: at 0 dB", "Meters"),

    ("keys_white", "#e8e8ea", "White keys", "MIDI editor"),
    ("keys_black", "#1b1c1f", "Black keys", "MIDI editor"),
    ("keys_line", "#b8b9be", "Lines between keys", "MIDI editor"),
    ("keys_label", "#3a3c42", "Key names (C4…)", "MIDI editor"),
    ("key_sounding", "#5fafff96", "The key sounding", "MIDI editor"),
    ("roll_shade", "#0000006e", "Outside the clip", "MIDI editor"),
    ("selected_note", "#ffffff", "Selected notes", "MIDI editor"),

    ("sample_wave", "#78afe6", "Sample waveform", "Explorer"),
)
DEFAULT_COLORS = {name: default for name, default, _, _ in COLORS}
# fonts: "" / 0 = the system's own
FONTS = {"ui": "", "ui_size": 0, "mono": "Consolas"}
FONT_LABELS = {"ui": "Interface font", "ui_size": "Interface font size (pt)", "mono": "Numbers font (times, LUFS)"}


def parse_color(text):
    """"#rrggbb" or "#rrggbbaa" -> QColor, or None if it isn't one."""
    if not isinstance(text, str) or not text.startswith("#") or len(text) not in (7, 9):
        return None
    try:
        values = [int(text[i:i + 2], 16) for i in range(1, len(text), 2)]
    except ValueError:
        return None
    return QColor(*values)


def hex_of(color):
    """QColor -> "#rrggbb" ("#rrggbbaa" when not opaque)."""
    text = color.name()
    return text if color.alpha() == 255 else f"{text}{color.alpha():02x}"


current = {}  # name -> QColor, of the skin in use
fonts = dict(FONTS)


def set_skin(colors=None, font_settings=None):
    """Use these colours ({name: "#rrggbb"[aa]}) and fonts; anything missing or invalid: the default."""
    global current, fonts
    current = {name: parse_color(default) for name, default in DEFAULT_COLORS.items()}
    for name, value in (colors or {}).items():
        color = parse_color(value)
        if name in current and color is not None:
            current[name] = color
    fonts = {**FONTS, **{k: v for k, v in (font_settings or {}).items() if k in FONTS}}
    c = current.__getitem__
    globals().update(
        LANES=(c("lane"), c("lane_alt")), ROWS=(c("row"), c("row_alt")), BELOW_LANES=c("below_lanes"), MASTER_LANE=c("master_lane"),
        MASTER_DIVIDER=c("master_divider"), GROUP_LANE=c("group_lane"), GROUP_ACCENT=c("group_accent"),
        RULER_BG=c("ruler_bg"), RULER_TEXT=c("ruler_text"), DIMMED_TEXT=c("dimmed_text"), HINT_TEXT=c("hint_text"),
        GRID=c("grid"), GRID_BY_STRENGTH=(c("grid_fine"), c("grid"), c("grid_bar")),
        PLAYHEAD=c("playhead"), CURSOR=c("cursor"), FROZEN_TINT=c("frozen_tint"),
        OVERVIEW_BG=c("overview_bg"), OVERVIEW_VIEW=c("overview_view"), OVERVIEW_VIEW_FILL=c("overview_view_fill"),
        OVERVIEW_EDGE=c("overview_edge"),
        CLIP_FILL={"audio": c("clip_audio"), "midi": c("clip_midi")},
        TRACK_ACCENT={"audio": c("accent_audio"), "midi": c("accent_midi")},
        SELECTION_FILL=c("selection_fill"), SELECTION_EDGE=c("selection_edge"), LOOP=c("loop"), LOOP_OFF=c("loop_off"),
        AUTOMATION_LANE=c("automation_lane"), AUTOMATION_LINE=c("automation_line"),
        MUTE=c("mute"), SOLO=c("solo"), RECORD=c("record"), ACCENT=c("accent"), ACCENT_TEXT=c("accent_text"), FRAME_EDGE=c("frame_edge"),
        METER_BG=c("meter_bg"), METER_OK=c("meter_ok"), METER_WARN=c("meter_warn"), METER_HOT=c("meter_hot"),
        KEYS_WHITE=c("keys_white"), KEYS_BLACK=c("keys_black"), KEYS_LINE=c("keys_line"), KEYS_LABEL=c("keys_label"),
        KEY_SOUNDING=c("key_sounding"), ROLL_SHADE=c("roll_shade"), SELECTED_NOTE=c("selected_note"),
        SAMPLE_WAVE=c("sample_wave"), MONO_FONT=fonts["mono"] or "Consolas",
    )


def palette():
    """The window colours as a QPalette (with dimmed text where disabled)."""
    c = current
    p = QPalette()
    for role, name in ((QPalette.Window, "window"), (QPalette.WindowText, "window_text"), (QPalette.Base, "base"),
                       (QPalette.AlternateBase, "alternate_base"), (QPalette.Text, "text"),
                       (QPalette.Button, "button"), (QPalette.ButtonText, "button_text"),
                       (QPalette.Highlight, "highlight"), (QPalette.HighlightedText, "highlighted_text"),
                       (QPalette.ToolTipBase, "tooltip_base"), (QPalette.ToolTipText, "tooltip_text"),
                       (QPalette.PlaceholderText, "disabled_text")):
        p.setColor(role, c[name])
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, c["disabled_text"])
    return p


def mix(a, b, t):
    """The colour t of the way from QColor a to b (0: a, 1: b), opaque, as "#rrggbb"."""
    return QColor(*(round(x + (y - x) * t) for x, y in ((a.red(), b.red()), (a.green(), b.green()),
                                                        (a.blue(), b.blue())))).name()


# the small drawings stylesheets need (arrows, check mark): SVG polylines in a 10 x 10 box
ICON_LINES = {"down": "2,3.5 5,6.5 8,3.5", "up": "2,6.5 5,3.5 8,6.5", "right": "3.5,2 6.5,5 3.5,8",
              "check": "1.8,5.2 4.1,7.5 8.3,2.6"}
ICON_DIR = os.path.join(tempfile.gettempdir(), "tiny-daw-icons")


def icon(kind, color):
    """Path of a small drawing (ICON_LINES) in `color` ("#rrggbb"), for a stylesheet's url()."""
    path = os.path.join(ICON_DIR, f"{kind}-{color.lstrip('#')}.svg")
    if not os.path.exists(path):
        try:
            os.makedirs(ICON_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
                        f'<polyline points="{ICON_LINES[kind]}" fill="none" stroke="{color}" stroke-width="1.5" '
                        'stroke-linecap="round" stroke-linejoin="round"/></svg>')
        except OSError:
            return ""  # (no arrow drawn; everything else still works)
    return path.replace("\\", "/")


RADIUS = 3  # px, the corners of buttons and fields
rounded = True  # False: every corner square (Options → Rounded corners; apply_to() again after changing it)
SCROLLBAR_W = 10  # px, the thickness of scroll bars (the panel leaves room for the timeline's)


def radius(px=RADIUS):
    """A corner's radius in px: `px`, or 0 with rounded corners off."""
    return px if rounded else 0


def stylesheet():
    """Program-wide styling from the skin: the flat look of every control, and the widgets marked
    with these properties / names."""
    q = current
    c = {name: hex_of(color) for name, color in q.items()}
    mono = fonts["mono"] or "Consolas"
    r = radius()
    # shades worked out from the skin, so every skin gets them
    hover = mix(q["button"], q["button_text"], 0.10)  # a control under the mouse
    pressed = mix(q["button"], q["button_text"], 0.18)  # pressed, or switched on
    disabled = mix(q["button"], q["window"], 0.5)
    line = mix(q["window"], q["window_text"], 0.10)  # dividing lines, edges of menus
    field_edge = mix(q["base"], q["text"], 0.14)  # edge of fields (stronger under the mouse)
    field_hover = mix(q["base"], q["text"], 0.26)
    item_hover = mix(q["base"], q["text"], 0.07)  # list rows under the mouse
    panel = mix(q["window"], q["base"], 0.45)  # boxes grouping controls (metronome sounds)
    handle = mix(q["window"], q["window_text"], 0.22)  # scroll bar handles
    handle_hover = mix(q["window"], q["window_text"], 0.38)
    accent_hover = mix(q["accent"], q["accent_text"], 0.15)
    opaque = lambda name: q[name].name()  # (icons: "#rrggbb")
    arrow, arrow_off = icon("down", opaque("text")), icon("down", opaque("disabled_text"))
    up, right = icon("up", opaque("text")), icon("right", opaque("text"))
    check, menu_check = icon("check", opaque("accent_text")), icon("check", opaque("text"))

    def lit(selectors, color, color_hover):
        """Filled with `color` (text in accent_text), `color_hover` under the mouse."""
        hovered = ", ".join(f"{sel}:hover" for sel in selectors.split(", "))
        return (f"{selectors} {{ background: {color}; color: {c['accent_text']}; border-color: {color}; }}\n"
                f"{hovered} {{ background: {color_hover}; border-color: {color_hover}; }}")

    def toggle(role):
        return lit(f'QToolButton[toggle="{role}"]:checked', c[role], mix(q[role], q["accent_text"], 0.15))

    return f"""
QWidget {{ outline: none; }}
QToolTip {{ background: {c['tooltip_base']}; color: {c['tooltip_text']}; border: 1px solid {line}; padding: 4px; }}

QPushButton {{ background: {c['button']}; color: {c['button_text']}; border: 1px solid {c['button']};
    border-radius: {r}px; padding: 4px 12px; min-height: 16px; }}
QPushButton:hover {{ background: {hover}; border-color: {hover}; }}
QPushButton:pressed, QPushButton:checked {{ background: {pressed}; border-color: {pressed}; }}
QPushButton:focus {{ border-color: {c['accent']}; }}
QPushButton:disabled {{ background: {disabled}; border-color: {disabled}; color: {c['disabled_text']}; }}
QPushButton::menu-indicator {{ image: url({arrow}); width: 10px; height: 10px;
    subcontrol-origin: padding; subcontrol-position: center right; right: -4px; }}

QToolButton {{ background: {c['button']}; color: {c['button_text']}; border: 1px solid {c['button']};
    border-radius: {r}px; padding: 2px; }}
QToolButton[autoRaise="true"] {{ background: transparent; border-color: transparent; padding: 3px 4px; }}
QToolButton:hover, QToolButton[autoRaise="true"]:hover {{ background: {hover}; border-color: {hover}; }}
QToolButton:pressed, QToolButton:checked, QToolButton[autoRaise="true"]:pressed {{
    background: {pressed}; border-color: {pressed}; }}
QToolButton:disabled {{ color: {c['disabled_text']}; }}
QToolButton[popupMode="1"] {{ padding-right: 13px; }}
QToolButton::menu-button {{ border: none; border-left: 1px solid {line}; width: 13px;
    border-top-right-radius: {r}px; border-bottom-right-radius: {r}px; }}
QToolButton::menu-button:hover {{ background: {pressed}; }}
QToolButton::menu-arrow, QToolButton::menu-indicator {{ image: url({arrow}); width: 8px; height: 8px; }}
QToolButton::menu-indicator {{ subcontrol-origin: padding; subcontrol-position: bottom right; }}

QLineEdit, QAbstractSpinBox, QComboBox {{ background: {c['base']}; color: {c['text']};
    border: 1px solid {field_edge}; border-radius: {r}px; padding: 1px 4px;
    selection-background-color: {c['highlight']}; selection-color: {c['highlighted_text']}; }}
QLineEdit:hover, QAbstractSpinBox:hover, QComboBox:hover {{ border-color: {field_hover}; }}
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus, QComboBox:on {{ border-color: {c['accent']}; }}
QLineEdit:disabled, QAbstractSpinBox:disabled, QComboBox:disabled {{ color: {c['disabled_text']};
    border-color: {field_edge}; }}
QComboBox {{ padding-right: 18px; }}
QComboBox::drop-down {{ border: none; width: 18px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({arrow}); width: 10px; height: 10px; }}
QComboBox::down-arrow:disabled {{ image: url({arrow_off}); }}
QComboBox QAbstractItemView {{ background: {c['base']}; color: {c['text']}; border: 1px solid {line};
    selection-background-color: {c['highlight']}; selection-color: {c['highlighted_text']}; padding: 2px; }}
QAbstractSpinBox {{ padding-right: 16px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ border: none; background: transparent; width: 16px;
    subcontrol-origin: border; }}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: {r}px; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: {r}px; }}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: {item_hover}; }}
QAbstractSpinBox::up-arrow {{ image: url({up}); width: 8px; height: 8px; }}
QAbstractSpinBox::down-arrow {{ image: url({arrow}); width: 8px; height: 8px; }}
QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off,
QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off {{ image: none; }}

QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; background: {c['base']}; border: 1px solid {field_hover};
    border-radius: {r}px; }}
QCheckBox::indicator:hover {{ border-color: {c['accent']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; image: url({check}); }}
QCheckBox::indicator:disabled {{ background: {disabled}; border-color: {disabled}; }}

QSlider {{ background: transparent; }}
QSlider::groove:horizontal {{ height: 4px; background: {field_edge}; border-radius: {radius(2)}px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: {radius(2)}px; }}
QSlider::handle:horizontal {{ background: {c['button_text']}; width: 10px; margin: -3px 0; border-radius: {radius(5)}px; }}
QSlider::handle:horizontal:hover {{ background: {c['accent']}; }}
QSlider::sub-page:horizontal:disabled {{ background: {field_hover}; }}
QSlider::handle:horizontal:disabled {{ background: {c['disabled_text']}; }}

QScrollBar {{ background: {c['window']}; border: none; margin: 0; }}
QScrollBar:horizontal {{ height: {SCROLLBAR_W}px; }}
QScrollBar:vertical {{ width: {SCROLLBAR_W}px; }}
QScrollBar::handle {{ background: {handle}; border-radius: {r}px; margin: 2px; }}
QScrollBar::handle:hover, QScrollBar::handle:pressed {{ background: {handle_hover}; }}
QScrollBar::handle:horizontal {{ min-width: 24px; }}
QScrollBar::handle:vertical {{ min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QAbstractScrollArea {{ border: none; }}
QAbstractItemView {{ background: {c['base']}; alternate-background-color: {c['alternate_base']};
    selection-background-color: {c['highlight']}; selection-color: {c['highlighted_text']}; }}
QListView::item {{ padding: 2px 4px; }}
QAbstractItemView::item:hover {{ background: {item_hover}; }}
QAbstractItemView::item:selected {{ background: {c['highlight']}; color: {c['highlighted_text']}; }}
QHeaderView::section {{ background: {c['window']}; color: {c['hint_text']}; border: none;
    border-bottom: 1px solid {line}; padding: 3px 6px; }}

QMenuBar {{ background: {c['window']}; border: none; }}
QMenuBar::item {{ background: transparent; padding: 4px 8px; }}
QMenuBar::item:selected {{ background: {hover}; }}
QMenuBar::item:pressed {{ background: {pressed}; }}
QMenu {{ background: {c['base']}; color: {c['text']}; border: 1px solid {line}; padding: 4px 0; }}
QMenu::item {{ padding: 5px 24px 5px 24px; background: transparent; }}
QMenu::item:selected {{ background: {c['highlight']}; color: {c['highlighted_text']}; }}
QMenu::item:disabled {{ color: {c['disabled_text']}; background: transparent; }}
QMenu::separator {{ height: 1px; background: {line}; margin: 4px 8px; }}
QMenu::indicator {{ width: 12px; height: 12px; left: 6px; }}
QMenu::indicator:checked {{ image: url({menu_check}); }}
QMenu::right-arrow {{ image: url({right}); width: 10px; height: 10px; right: 6px; }}

QTabWidget::pane {{ border: none; border-top: 1px solid {line}; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {c['hint_text']}; padding: 6px 12px; border: none;
    border-bottom: 2px solid transparent; }}
QTabBar::tab:hover {{ color: {c['window_text']}; background: {hover}; }}
QTabBar::tab:selected {{ color: {c['window_text']}; border-bottom-color: {c['accent']}; }}

QGroupBox {{ background: {panel}; border: none; border-radius: {r}px; margin-top: 20px; padding: 8px 6px 6px 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 2px; padding: 0 2px;
    color: {c['window_text']}; }}

QToolBar {{ background: {c['window']}; border: none; border-bottom: 1px solid {line}; spacing: 2px; padding: 2px; }}
QFrame#separator {{ background: {line}; margin: 6px 3px; }}
QMainWindow::separator {{ background: {line}; width: 1px; height: 1px; }}
QMainWindow::separator:hover {{ background: {c['accent']}; }}
QDockWidget::title {{ background: {c['window']}; padding: 4px 6px; text-align: left; }}
QDockWidget::close-button, QDockWidget::float-button {{ border: none; background: transparent; border-radius: {r}px; }}
QDockWidget::close-button:hover, QDockWidget::float-button:hover {{ background: {hover}; }}
QStatusBar {{ background: {c['window']}; border-top: 1px solid {line}; }}
QStatusBar::item {{ border: none; }}

{lit('QToolButton[lit="true"]', c['accent'], accent_hover)}
{lit('QToolButton[accent="true"]:checked, QPushButton[accent="true"]:checked', c['accent'], accent_hover)}
{lit('QToolButton[autoRaise="true"]:checked', c['accent'], accent_hover)}
{toggle('mute')}
{toggle('solo')}
{toggle('record')}
{lit('QPushButton:default', c['accent'], accent_hover)}
QLabel[hint="true"] {{ color: {c['hint_text']}; }}
QLabel[dimmed="true"] {{ color: {c['dimmed_text']}; font-size: 8pt; }}
QLabel#time {{ font-family: "{mono}"; font-size: 12pt; }}
QLabel#lufs {{ font-family: "{mono}"; font-size: 8pt; }}
"""


def font():
    """The interface font, or None for the system's own."""
    if not fonts["ui"] and not fonts["ui_size"]:
        return None
    f = QFont(fonts["ui"]) if fonts["ui"] else QFont()
    if fonts["ui_size"]:
        f.setPointSizeF(float(fonts["ui_size"]))
    return f


def apply_to(app, system_font=None):
    """Put the current skin on the whole application (palette, stylesheet, font)."""
    app.setStyle("Fusion")
    app.setPalette(palette())
    app.setStyleSheet(stylesheet())
    chosen = font()
    if chosen is not None:
        app.setFont(chosen)
    elif system_font is not None:
        app.setFont(system_font)


def apply_dark_palette(app):
    """The default skin on the application (main.py; skins replace it later)."""
    set_skin()
    apply_to(app)


set_skin()  # the defaults, until a skin is chosen
