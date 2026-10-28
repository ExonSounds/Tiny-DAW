# Tiny DAW
*Goal is to create a fully functional DAW, which is easy to navigate through by anybody.*
- Please report any bugs that do appear.
- Desired features are very welcome!

- File provided by AI.

A small Digital Audio Workstation written in Python. It shows a timeline where you place audio and
MIDI clips on tracks and play them through any Windows audio driver (ASIO, WASAPI, WDM-KS,
DirectSound or MME). MIDI is played by a simple built-in synth.

```
pip install -r requirements.txt
python main.py [project.json | audio and MIDI files...]
```

## Using it

| Do this | How |
|---|---|
| New / open / save a project | File menu: New (Ctrl+N), Open (Ctrl+O), Save (Ctrl+S), Save as (Ctrl+Shift+S). You are asked before unsaved changes would be lost. See "Project files" below |
| Find and add samples | the **Explorer** on the left (View → Explorer, Ctrl+E): add your sample folders with **+** (remembered), browse them as a tree, or type in **Search samples…** to find files by name in all of them and their subfolders (every word must match). Click a sample (or move to it with the arrow keys) to hear it while **🔊 Preview** is on; **■** stops it. Previews play on outputs 1/2 through the master volume, playing or not. Drag samples onto a track, or double-click one to add it at the selection cursor. **⟳** looks for new files; **−** removes a folder from the list (nothing is deleted) |
| Add audio or MIDI files | "Add file…" (Ctrl+I), or drag files from the Explorer or Windows Explorer onto a lane. A file dropped on a lane of the other kind, or on Master, gets a new track. In bars and beats, a drop lands on the grid (hold Alt to place it freely) |
| Add / remove a track | "+ Audio" (Ctrl+T), "+ MIDI" (Ctrl+Shift+T) / ✕ on the track's row in the right panel. The stripe on the row shows the kind: blue audio, green MIDI |
| Move clips | drag a clip by its **top strip** (the darker band with its name). All selected clips move together. They only change lanes if every one lands on a lane of its own kind. Clips never overlap: a clip you move, drop or add replaces the parts of other clips it lands on (they are cut away, as in other DAWs) |
| Select clips | click a clip; Ctrl+click to add/remove; Ctrl+A for all; Esc to clear |
| Select a time range | drag across a clip's **lower part** or empty lane space, over one or more lanes. Clips it touches are selected, and the selection cursor goes to its start |
| Split | S (or right-click): with a range, cuts the clips on its lanes at both range edges and selects the middle pieces. Without one, cuts the selected clips at the selection cursor |
| Colour clips | select clips, then C (or right-click → Clip colour…). Pick on the colour wheel, with the brightness slider or a hex code, or click a recent colour. Colours you use are remembered. "Default" goes back to blue (audio) / green (MIDI). Clip contents and names are recoloured automatically so they stay readable on any colour; or choose White or Dark grey for them, offered whenever that still has good contrast on the chosen colour |
| Colour a track's row | right-click the track's row in the right panel → Track colour…: the same dialog and options as for clips (wheel, brightness, hex, recent colours shared with clips, White / Dark grey for its name and LUFS readout when readable, Default for the standard look). The text is recoloured automatically so it stays readable |
| Group tracks | right-click a track's row → **Group tracks…** and tick the tracks (audio and MIDI alike), or select clips or a range and press **Ctrl+G**. A group gets a header lane above its tracks, which are kept together. On its row: **▾/▸** folds its tracks away (or double-click its lane; the header lane then shows their clips in miniature), **M** / **S** mute or solo all its tracks, **✕** ungroups (the tracks stay), and a meter shows its tracks together. Right-click it to rename it or give it a colour; right-click a track to add it to a group or take it out. Dropping files on a group's header lane adds them on new tracks in the group |
| Freeze a track | right-click its row → **Freeze track**: it is rendered (clips through its instrument and effects) and then plays that rendering, so its plugins stop using CPU. Its clips and plugins are locked (drawn with an icy tint, ❄ by its name) until **Unfreeze track**. The rendering is saved with the project |
| Bounce a track | right-click its row → **Bounce to new audio track**: its sound becomes one audio clip on a new track right below, and the original is muted (kept, unmute it to hear both). **Bounce in place…** replaces the track's clips, instrument and effects with that clip instead (a MIDI track becomes an audio track; cannot be undone). Rendering stops playback, shows its progress and can be cancelled. Mute, solo, the master volume and master effects are not part of it |
| Disable clips | select clips, then **0** (or right-click → Disable clips): they stay where they are, drawn as an outline in their colour, but don't play and are left out of freezing and bouncing. Do it again (Enable clips) to bring them back. You can still move, cut, colour and rename them |
| Rename | a track or group: double-click its name in the right panel (or right-click → Rename). Clips: double-click a clip's name strip, or select clips and press **Ctrl+R** (or right-click → Rename clip…); several selected clips all get the new name |
| Resize a track (its clips grow with it) | drag the bottom edge of its lane, in the timeline or on its row in the right panel. Double-click the edge for the default height |
| Cut out / delete | Delete (or right-click): with a range, removes just that stretch from the clips on its lanes. Without one, removes the selected clips |
| Play / pause, back to start | Space, Home |
| The two cursors | the **selection cursor** (blue) is where you click or start a range; **Play always starts from it**, and splitting and adding files use it. The **playback cursor** (red) moves while playing and stays where playback stopped. Each has a small arrow on the ruler. While playing, clicking the ruler also makes playback jump there (clicking a lane only moves the selection cursor). ⏮ Start (Home) puts both at 0 s. In bars and beats clicks snap to the nearest grid line (hold **Alt** to place it freely); so do the edges of a range selection. The time display shows the playback position while playing, the selection cursor while stopped |
| Tempo and time signature | the **BPM** and **/ 4** boxes on the top bar (type a value and press Enter, or use the arrows or the wheel). Clips keep their place in time when the tempo changes |
| Metronome | **Metronome** next to the BPM box (Ctrl+M) switches it on or off; it clicks while playing, on outputs 1/2 through the master volume, following the tempo and time signature (it is not in the master meters, LUFS or renders). **Right-click** it for its sounds: the click at the start of each bar and the click on the other beats, side by side, each with an attack-sustain-release envelope (drag its points, or type the times and level), pitch, waveform (sine, triangle, square, noise) and a Test button. Changes apply at once and are remembered |
| Bars and beats / seconds | **♩ Beats** (B) switches the ruler, grid and time display between bars and beats (`bar.beat.16th`) and minutes and seconds. The grid follows the zoom: zoomed out it shows bars (or every 2, 4, 8… bars), zoomed in beats, 8ths, 16ths and 32nds; clicks snap to whatever grid is shown |
| Triplets | **⅓ Thirds** (3), in bars and beats: the grid divides beats in thirds (half-note to 32nd-note triplets), and the time display counts thirds |
| Zoom / scroll | Ctrl+wheel / wheel |
| See the whole project | the **overview** bar above the editor: every track as a thin row and every clip in its colour, over the project's whole length; the outline is what the editor shows, the red line the playback cursor, the blue one the selection cursor. Click it to jump the view there, or drag the outline to slide the view |
| Follow playback | "⇥ Follow" or F toggles it. When on, once the playback cursor reaches the middle of the view the view moves along with it, keeping it centred |
| Mute / solo a track | M / S on its row |
| VST3 instruments and effects | **FX** on a track's row (lit when it has plugins): pick a VST3 **instrument** for a MIDI track (instead of the built-in synth) and add **effects** to any track; reorder, bypass (untick) or open a plugin's own window (double-click; it opens named after the plugin and track, fully on screen with its title bar to move, minimize and close it, and where you last left it). FX on the Master row adds master effects (they work on outputs 1/2). Plugins come from `C:\Program Files\Common Files\VST3`. The first load of a big instrument can take up to half a minute; meanwhile plugins pause (their tracks go quiet) and audio tracks keep playing, though a big synth loading during playback can make them stutter a little. Plugins keep running while stopped, so synths stay playable from their own keyboard. Projects save each plugin's settings |
| Master volume and mute | the Master row at the bottom of the panel |
| Choose a track's outputs | the "1/2" box on its row: pick any output pair of the audio device (3/4, 5/6, …). Switching to a device without that pair puts the track back on 1/2 |
| Levels | every track row and the Master row show one horizontal meter bar per output channel of the audio device (top to bottom = output 1, 2, …). On a track row, the bars of the pair it plays on have a lighter background |
| Loudness (LUFS) | every track row and the Master row: **S** short-term (last 3 s) and **I** integrated (since Play was pressed), per EBU R128; hover for momentary (last 0.4 s). A track is measured on its own output pair, the master over all outputs |
| Audio CPU load | top-right corner: current (last 0.25 s) and rolling average; a red ⚠ counts late buffers (likely clicks) since Play |
| Choose the audio driver | Options → **Audio properties…**: the Windows driver type (**ASIO**, **WASAPI** shared or exclusive, **WDM-KS**, **DirectSound**, **MME**), the output device, the buffer size (driver default, or 128–2048 frames: smaller = less delay but more CPU and risk of clicks, bigger = less CPU, more delay), and what the open device is doing (sample rate, outputs, delay). The choice is remembered |
| Lower the CPU use | Options menu, remembered between runs: the buffer size above, **Screen refresh** (60 / 30 / 15 per second), and switching off **Level meters** or **Loudness (LUFS)**, which also stops the measuring behind them |

## Project files

Saving `MySong.json` also creates a folder `MySong_audio` next to it:

```
MySong.json        tracks, clips (position, cut, colour), MIDI notes, master settings, tempo and grid
MySong_audio/      the audio of every audio clip and every frozen track's rendering, as 32-bit float WAV
```

The audio is saved exactly as the program plays it, whatever format it was imported from, so the
project does not depend on the original files. Saving again only adds new audio to the folder; it
never overwrites or deletes files there. Keep the JSON and the folder together when you move a project.

## License note

VST3 support uses [pedalboard](https://github.com/spotify/pedalboard), which is licensed under the
GPL-3.0.

## Folder layout

```
main.py                    start the program
requirements.txt
daw/
  config.py                numbers: sizes, zoom limits, meter ranges, refresh rates
  model.py                 Project, Group, Track, AudioClip, MidiClip: the data. No sound card or GUI code.
  edits.py                 editing operations: split clips, cut a time range out of clips, make room for a placed clip
  timing.py                tempo, time signature, and the bars-and-beats grid (dynamic with zoom, thirds) clicks snap to
  project_io.py            saving and loading projects (JSON + audio folder)
  audio/                   talks to the sound card. No Qt.
    __init__.py            switches on ASIO support in sounddevice (must run before it is imported); the other
                           Windows driver types are always there
    devices.py             lists output devices per Windows driver type (ASIO, WASAPI, WDM-KS, DirectSound, MME)
    engine.py              the output stream (any driver type, all device outputs), play/pause, playhead, master gain, metering
    mixer.py               adds clips together per track into the output buffer, handing each track to the meters
    render.py              renders one track offline (for freezing and bouncing)
    metronome.py           the metronome's two click sounds (attack-sustain-release) and placing them on the beats
    loudness.py            LUFS measurement (K-weighting, momentary / short-term / integrated with gating)
    metering.py            takes copies of what played from the audio thread; measures peaks and LUFS on the GUI thread
    cpu.py                 measures how much of each buffer's time the audio code uses
    synth.py               built-in instrument that turns MIDI notes into audio
    plugins.py             VST3 plugins: finding them, loading them, playing instruments and effects
    dsp.py                 numpy helpers: stereo conversion, resampling, waveform peaks, dB
    file_io.py             loads audio files and MIDI files
  ui/                      everything you see (PySide6 / Qt)
    main_window.py         builds the window and connects all the parts below
    transport_bar.py       top bar: buttons, time display, tempo, time signature, beats / thirds
    audio_dialog.py        Options → Audio properties: driver type, device, buffer size, current device details
    timeline.py            ruler, lanes, the two cursors; mouse/keyboard editing (select, move, drop, delete)
    explorer.py            the sample explorer on the left: sample folders, search by name, drag samples in
    overview.py            the project overview bar above the editor: every lane and clip, compressed; moves the view
    clip_painter.py        draws a clip: box, waveform or MIDI notes, name; and the selected range
    waveform_cache.py      waveforms and MIDI notes drawn once into image tiles; redraws just copy them
    colors.py              automatic contrast: derives every colour on a clip from its chosen colour
    color_picker.py        colour dialog for clips and track rows: colour wheel, brightness, hex, recent colours, preview
    selection.py           what is selected: clips and an optional time range over lanes
    lane_panel.py          right panel: one row per track (M, S, ✕, outputs, meters, LUFS) and per group
    lanes.py               which lanes the editor shows, top to bottom (group headers, tracks, master)
    master_strip.py        master row: mute, LUFS, level meter, volume
    cpu_monitor.py         the audio CPU display in the top-right corner
    level_meter.py         level meter with one bar per output channel (tracks and master)
    loudness_display.py    the small S / I LUFS readout on each row
    metronome_dialog.py    the metronome's settings window (two envelope editors), remembered between runs
    preferences.py         the Options menu's settings, remembered between runs
    plugin_host.py         loads VST3 plugins and opens their windows (main thread, with a "please wait")
    track_render.py        freezing, unfreezing and bouncing tracks (right-click menu, progress window)
    plugin_windows.py      Windows-only: titles plugin windows and places them on screen (pedalboard opens them off-screen)
    plugin_dialog.py       a track's / the master's plugins window: instrument and effect chain
    widgets.py             small shared helpers (lane-coloured frame, M/S buttons)
    formatting.py          time and dB text
    theme.py               all colours and the dark palette
```

## How the parts talk

- `Project` holds the tracks and clips. The timeline edits it and the engine reads it.
- Every clip has `data`: audio at the engine's sample rate. An audio clip resamples its file into it.
  A MIDI clip renders its notes into it with `audio/synth.py` when it is loaded, so the mixer treats
  both kinds the same way.
- A clip plays a window of its `data` (`offset` and `length`). Splitting makes new clips that share
  the same `data` with different windows, so cutting never copies audio. The mixer applies a 64-sample
  fade at every clip edge so cuts don't click.
- `Engine._callback` runs on the audio driver's own thread, many times a second. It reads
  `project.clips` and `project.tracks`. These are tuples that are **replaced, never changed in
  place**, so the audio thread never sees a half-edited list. Keep that rule when you add data
  the callback reads.
- The audio callback is Python, so it needs the GIL: anything that holds it for long (a slow paint,
  a big computation, a full garbage collection) delays the next buffer and you hear a click. That is
  why `Engine.play()` freezes existing objects out of the garbage collector while playing, and why the
  CPU monitor shows a red ⚠ count of late buffers. Keep work done while playing in small pieces.
- Clip contents are drawn once per zoom level, lane height and colour into cached image tiles
  (`ui/waveform_cache.py`), so scrolling and playback only copy pixels. If you change how a waveform
  or note looks, change the `render_*_tile` functions there, and add anything new that affects the
  pixels to the cache key in `TileCache.tile()`.
- VST3 plugins are hosted through the `pedalboard` library. Its rules on Windows (found by testing):
  load plugins and open their windows on the main thread only; from the audio thread only process
  with `reset=False`. Loading releases the GIL, so audio keeps playing while a big instrument loads.
  MIDI notes go to a VST3 instrument live, buffer by buffer (`note_messages()` in `audio/plugins.py`);
  when playback starts or jumps, instruments get "all notes off" so no note hangs.
- The callback mixes and runs the plugins. Metering (`audio/metering.py`) gets copies of what played and does the
  measuring on the GUI thread in batches, which is far cheaper than doing it every buffer.
- The GUI never waits for the audio thread. Two timers in `main_window.py` read the engine:
  one every frame (playhead, level meters, overview) and one every 250 ms (CPU monitor, LUFS).
  The overview redraws its clips only when they change; if you add something to a clip that
  should show there, add it to `Overview.content_key()`.
- When tracks are added, removed, grouped or folded, `Timeline.tracks_changed` fires and `LanePanel` rebuilds
  its rows. The timeline, panel and overview all lay out their lanes with `ui/lanes.py`, so they line up.

## Where to start when changing something

- **Different look**: `ui/theme.py` for colours, `config.py` for sizes. The contrast targets for clips
  (`MIN_FILL_VS_LANE`, `MIN_CONTENT_VS_FILL`) are at the top of `ui/colors.py`.
- **New per-track control (volume, pan…)**: add a field to `Track` in `model.py`, a button or
  slider in `track_header()` in `ui/lane_panel.py`, and use it in `audio/mixer.py`.
- **Effects or processing on the output**: `Engine._callback` in `audio/engine.py`, after `mix_clips`.
  Whatever you add there counts towards the CPU monitor.
- **A different MIDI sound**: the constants and `render_note()` in `audio/synth.py`.
- **A new editing command (copy, loop, fade…)**: write the operation in `edits.py`, then call it
  from `ui/timeline.py` next to `split_selected()`, using `Timeline.selection` for the selected
  clips and time range. Add it to the key handler and the right-click menu there.
- **Grid and snapping**: the grid steps are `STRAIGHT_STEPS` / `THIRDS_STEPS` in `timing.py`, how dense it
  may get is `GRID_MIN_PX` in `config.py`; `Timeline.snapped_frame_at()` decides what snaps.
- **Better resampling**: replace `resample()` in `audio/dsp.py`.
- **New file formats**: `audio/file_io.py`.
- **Something new to save in projects**: add it to `describe()` and `load_project()` in `project_io.py`.
  `describe()` is also how the program notices unsaved changes.
