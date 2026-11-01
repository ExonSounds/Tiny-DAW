"""Numbers shared across the program. Colours live in daw/ui/theme.py."""

# --- timeline geometry (pixels) ---------------------------------------------
RULER_H = 24  # time ruler at the top; the CPU monitor uses the same height
TRACK_H = 72  # default lane height
MASTER_FOOTER_H = 28  # under the master's controls: the version and the update notice
MASTER_H = TRACK_H + MASTER_FOOTER_H  # the master lane and its row (pinned to the bottom of the screen)
GROUP_H = 52  # a group's header lane (and its row in the panel)
AUTOMATION_H = 64  # an automation lane under its track (and its row in the panel)
AUTOMATION_MIN_H, AUTOMATION_MAX_H = 40, 320  # range for resizing an automation lane
AUTOMATION_PAD = 6  # px above and below the curve in an automation lane
AUTOMATION_GRAB_PX = 6  # how close to a point the mouse must be to drag it
MIN_TRACK_H, MAX_TRACK_H = 60, 320  # range for resizing a track lane (drag its bottom edge)
LANE_EDGE_GRAB_PX = 4  # how close to a lane's bottom edge the mouse must be to resize it
PANEL_W = 250  # right-hand panel with the track buttons
OVERVIEW_H = 44  # project overview bar above the editor (its default height)
LOOP_MIN_S = 0.1  # the shortest loop range
OVERVIEW_MAX_H = 320  # the tallest it can be dragged (its bottom edge)
OVERVIEW_MIN_S = 30  # the overview spans at least this many seconds (so short projects aren't stretched)
EXPLORER_W = 250  # sample explorer on the left (its width can be dragged)

# --- zoom ----------------------------------------------------------------------
DEFAULT_PPS = 100.0  # pixels per second
MIN_PPS, MAX_PPS = 5.0, 2000.0
ZOOM_STEP = 1.25  # per Ctrl+wheel notch
EMPTY_SECONDS_AFTER_END = 60  # room to drag clips past the last one

# --- ruler -----------------------------------------------------------------------
RULER_STEPS = (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600)  # seconds
RULER_MIN_TICK_PX = 70

# --- bars and beats (see timing.py) --------------------------------------------------
DEFAULT_BPM = 120.0
MIN_BPM, MAX_BPM = 20.0, 999.0
DEFAULT_BEATS_PER_BAR = 4  # time signature 4/4
TAP_COUNT = 8  # tap tempo: the tempo is the average over this many latest taps
TAP_RESET_S = 2.0  # tap tempo: a pause longer than this starts counting afresh
MAX_BEATS_PER_BAR = 16
GRID_MIN_PX = 16  # the finest grid lines shown (and snapped to) are at least this far apart
BAR_LABEL_MIN_PX = 40  # bar numbers skip bars (every 2, 4, 8…) to stay this far apart
BEAT_LABEL_MIN_PX = 40  # beats get their own ruler label ("5.3") once they are this far apart

# --- audio -------------------------------------------------------------------------
FALLBACK_SAMPLE_RATE = 48000  # used until an audio device is opened
PEAK_BLOCK = 256  # samples per waveform peak
EDGE_FADE_FRAMES = 64  # fade at every clip edge (~1.5 ms) so cut points don't click
MIN_DB, MAX_DB = -60, 6  # master volume slider; MIN_DB means silence
FILE_FILTER = "Audio and MIDI (*.wav *.flac *.ogg *.mp3 *.aif *.aiff *.mid *.midi)"

# --- sample explorer -------------------------------------------------------------
EXPLORER_MAX_FILES = 200_000  # samples listed for searching, across all its folders
EXPLORER_MAX_RESULTS = 500  # search results shown at once
PREVIEW_GAIN = 0.7  # sample previews play a little quieter than full level (then through the master volume)

# --- VST3 plugins (audio/plugins.py) ------------------------------------------------

# --- freezing and bouncing tracks (audio/render.py) ----------------------------------
RENDER_BLOCK = 1024  # frames rendered at a time
RENDER_TAIL_S = 4.0  # after the last clip, render this long at most for plugins to ring out (reverb, delay)
RENDER_SILENCE = 1e-5  # (-100 dB) the tail is cut once the sound stays below this
RENDER_PREROLL_S = 2.0  # rendering the mix starts this much earlier, so effects are already ringing

# --- editing -------------------------------------------------------------------------
DRAG_THRESHOLD_PX = 3  # a press that moves less than this is a click, not a range selection
CURSOR_ARROW_W, CURSOR_ARROW_H = 11, 7  # px: the arrows on the ruler marking both cursors
TRANSPORT_BUTTON_PX = 28  # the playback buttons (Play, Rec, Start, Loop, Follow): square, this wide
TRANSPORT_ICON_PX = 14  # their icons (DAW_icons)
CLIP_HANDLE_H = 16  # top strip of a clip (with its name): drag here to move; below selects and cuts
CLIP_EDGE_PX = 6  # px at either end of a clip: drag there to trim it, or to bring back what was cut off
CLIP_MIN_S = 0.01  # a clip trimmed by its edges is at least this long

# --- GUI refresh (the playhead/meter rate is an option: ui/preferences.py) ---------
CPU_REFRESH_MS = 250  # "current" CPU load is measured over this window; LUFS readouts update as often
CPU_AVERAGE_SECONDS = 5.0  # "avg" CPU load is a rolling average over roughly this long
METER_DECAY = 0.85  # level meter fall-off per frame
