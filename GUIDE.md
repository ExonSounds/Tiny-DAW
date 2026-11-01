# Tiny DAW

A small Digital Audio Workstation written in Python. It shows a timeline where you place audio and
MIDI clips on tracks and play them through any Windows audio driver (ASIO, WASAPI, WDM-KS,
DirectSound or MME). MIDI is played by a simple built-in synth.

```
pip install -r requirements.txt
python start.py [project.json | audio and MIDI files...]
```

Or double-click **Start Tiny DAW.bat** (it uses Python 3.12 through the `py` launcher when it can).
`start.py` keeps the program up to date file by file: before starting, it compares each file with
the repository on GitHub (ExonSounds/Tiny-DAW, main branch) and downloads only the ones that changed,
checking each against the repository's own fingerprint before putting it in place; files the
repository dropped are removed. A file you changed yourself is kept (delete it to get the
repository's again). Without a connection it starts with the files it has. `python start.py --check`
only lists what would change; `--no-update` starts without looking; `python main.py` starts the
program as it is, as before. What it knows about the files is in `.tinydaw-files.json`.

VST3 plugins are hosted by Tiny DAW's own plugin host, a compiled module
(`daw/audio/vst3host.cp312-win_amd64.pyd`, for 64-bit Python 3.12 on Windows) that comes with the
releases. To build it yourself (another Python version, or after changing `native/vst3host.cpp`),
see "Building the plugin host" below. Without it the program runs, but without plugins.

## Using it

| Do this | How |
|---|---|
| New / open / save a project | File menu: New (Ctrl+N), Open (Ctrl+O), Save (Ctrl+S), Save as (Ctrl+Shift+S). You are asked before unsaved changes would be lost. See "Project files" below |
| Find and add samples | the **Explorer** on the left (View → Explorer, Ctrl+E): add your sample folders with **+** (remembered), browse them as a tree, or type in **Search samples…** to find files by name in all of them and their subfolders (every word must match). Click a sample (or move to it with the arrow keys) to see its exact waveform at the top of the Explorer (a MIDI file: what the built-in synth plays of it; click the waveform to play from that point) and to hear it while **🔊 Preview** is on; with **⟲ Loop** on it plays over and over (also when switched on while it plays); **■** stops it. Previews play on outputs 1/2 through the master volume, playing or not. Drag samples onto a track, or double-click one to add it at the selection cursor. **⟳** looks for new files; **−** removes a folder from the list (nothing is deleted) |
| Add audio or MIDI files | "Add file…" (Ctrl+I), or drag files from the Explorer or Windows Explorer onto a lane. A file dropped on a lane of the other kind, or on Master, gets a new track. In bars and beats, a drop lands on the grid (hold Alt to place it freely) |
| Add / remove a track | "+ Audio" (Ctrl+T), "+ MIDI" (Ctrl+Shift+T) / ✕ on the track's row in the right panel. The stripe on the row shows the kind: blue audio, green MIDI |
| Choose a MIDI track's instrument | a new MIDI track's row shows only its name and **Choose instrument** (the track is silent until then): click it for the **Built-in synth** or a VST instrument (only instruments are listed: see "Plugin types" below; one whose type couldn't be found out yet is in a submenu, "Not loaded before"). Once the instrument is loaded the row has its usual controls, plus **🎹**: click it to open the instrument's own window and configure it (also: right-click the row → Open the … window); its **▾** opens the same menu, to choose another instrument. With the built-in synth (it has no settings) clicking 🎹 opens the menu. **Instrument patch panel…** in the same menu plays several instruments on the track at once (below) |
| Several instruments on one MIDI track | choose **Instrument patch panel…** in a MIDI track's instrument menu (**Choose instrument**, or ▾ next to 🎹): the track plays the instruments of its own patch panel, which opens (afterwards 🎹 opens it). It works like the FX window: **IN** on the left is the track's notes (its clips, and the keys played in the MIDI editor), **OUT** on the right goes on to the track's effects, and each VST instrument is a unit with its picture, a notes input (left) and a sound output (right). Its instrument so far goes in first, wired, so it keeps sounding. Place more with **Add…** or by double-clicking an empty spot; like effects, a new one is only placed, so wire it: from IN to its input, from its output to OUT (cables can't go from one instrument into another). Every instrument wired from IN gets the notes, and every one wired to OUT is heard, added up. Each has its own **volume fader** under its name (drag it, or turn the mouse wheel over it, 1 dB a notch; double-click for 0 dB; −60 dB is silent), and its light mutes it. Right-click an empty spot for **Wire every instrument in** and **Unplug every cable**. Instruments with look-ahead are lined up with each other. Their parameters can be automated, freezing and bouncing render them all, and projects save the panel (instruments, places, cables, volumes). Choosing a single instrument (or the built-in synth) again plays only that one; the panel is kept as it was, to go back to (the menu shows how many instruments it holds). VST instruments only: the built-in synth can't go in it |
| Move clips | drag a clip by its **top strip** (the darker band with its name). All selected clips move together. In bars and beats the clip you grabbed starts on the nearest grid line (the others keep their distance to it); hold **Alt** to move freely. In minutes and seconds they move freely. They only change lanes if every one lands on a lane of its own kind. Clips never overlap: a clip you move, drop or add replaces the parts of other clips it lands on (they are cut away, as in other DAWs) |
| Trim or lengthen a clip | drag either end of a clip (the pointer becomes ↔ there): inwards to shorten it, outwards to bring back what was cut off, by splitting, cutting a range or a clip placed over it. An audio clip goes as far as its audio does; a MIDI clip can be made as long as you like (empty past its last note). Snaps to the grid in bars and beats (Alt: freely); at least 10 ms long; what it then covers is replaced, as when placing a clip. A frozen track's clips are locked |
| Select clips | click a clip; Ctrl+click to add/remove; Ctrl+A for all; Esc to clear |
| Select a time range | drag across a clip's **lower part** or empty lane space, over one or more lanes. Clips it touches are selected, and the selection cursor goes to its start |
| Duplicate | Ctrl+D (or right-click): with a range, copies what lies in it (on its lanes) to right after it, and the range moves on to the copy, so Ctrl+D again repeats it once more. Without one, copies the selected clips to right after the last of them, keeping their spacing, and selects the copies. Whatever was in the way is replaced. In the MIDI editor, Ctrl+D copies the selected notes right after them (by their span, rounded up to the grid in bars and beats) and selects the copies |
| Split | S (or right-click): with a range, cuts the clips on its lanes at both range edges and selects the middle pieces. Without one, cuts the selected clips at the selection cursor |
| Colour clips | select clips, then C (or right-click → Clip colour…). Pick on the colour wheel, with the brightness slider or a hex code, or click a recent colour. Colours you use are remembered. "Default" goes back to blue (audio) / green (MIDI). Clip contents and names are recoloured automatically so they stay readable on any colour; or choose White or Dark grey for them, offered whenever that still has good contrast on the chosen colour |
| Colour a track's row | right-click the track's row in the right panel → Track colour…: the same dialog and options as for clips (wheel, brightness, hex, recent colours shared with clips, White / Dark grey for its name and LUFS readout when readable, Default for the standard look). The text is recoloured automatically so it stays readable |
| Colour options | Options → **Colours**: **Random colour for new tracks** gives every new track a colour of its own, picked to stand apart from the other tracks' colours; **Clips take their track's colour** draws clips that have no colour of their own in their track's colour (a clip's own colour still wins). **Default clip and row colours…** opens the skin editor on its "Tracks and clips" tab: the default audio and MIDI clip colours and the colour of the track rows in the right-hand panel (which can differ from the timeline's lanes). All remembered between runs |
| Group tracks | right-click a track's row → **Group tracks…** and tick the tracks (audio and MIDI alike), or select clips or a range and press **Ctrl+G**. A group gets a header lane above its tracks, which are kept together. On its row: **▾/▸** folds its tracks away (or double-click its lane; the header lane then shows their clips in miniature), **M** / **S** mute or solo all its tracks, **✕** ungroups (the tracks stay), and a meter shows its tracks together. Right-click it to rename it or give it a colour; right-click a track to add it to a group or take it out. Dropping files on a group's header lane adds them on new tracks in the group |
| Freeze a track | right-click its row → **Freeze track**: it is rendered (clips through its instrument and effects) and then plays that rendering, so its plugins stop using CPU. Its clips and plugins are locked (drawn with an icy tint, ❄ by its name) until **Unfreeze track**. The rendering is saved with the project |
| Bounce a track | right-click its row → **Bounce to new audio track**: its sound becomes one audio clip on a new track right below, and the original is muted (kept, unmute it to hear both). **Bounce in place…** replaces the track's clips, instrument and effects with that clip instead (a MIDI track becomes an audio track; cannot be undone). Rendering stops playback, shows its progress and can be cancelled. Mute, solo, the master volume and master effects are not part of it |
| Disable clips | select clips, then **0** (or right-click → Disable clips): they stay where they are, drawn as an outline in their colour, but don't play and are left out of freezing and bouncing. Do it again (Enable clips) to bring them back. You can still move, cut, colour and rename them |
| Rename | a track or group: double-click its name in the right panel (or right-click → Rename). Clips: double-click a clip's name strip, or select clips and press **Ctrl+R** (or right-click → Rename clip…); several selected clips all get the new name |
| Resize a track (its clips grow with it) | drag the bottom edge of its lane, in the timeline or on its row in the right panel. Double-click the edge for the default height |
| Cut out / delete | Delete (or right-click): with a range, removes just that stretch from the clips on its lanes. Without one, removes the selected clips |
| Play / pause, back to start | Space, Home, or the square **Play** / **Start** buttons in the middle of the top bar (with **Rec**, **Loop** and **Follow**; they stay centred in the window whenever there is room). They show icons (from the `DAW_icons` folder, in the skin's colours; Play shows the pause icon while playing), and their names and keys are in their tooltips. Loop has no icon yet and shows ⟲ (a `loop.svg` put in `DAW_icons` is used instead) |
| Loop | **Loop** (⟲, L) next to Play: playback jumps from the loop range's end back to its start, seamlessly. Turned on with a time range selected, the loop is that range (also: right-click the range → **Loop this range**); otherwise it keeps the last loop range, or loops the whole arrangement. The range is a band on the ruler (bright while looping, dim while not); drag its ends to change it (they snap to the grid in bars and beats; Alt: freely). Playback started after the loop's end just plays on. Saved with the project |
| Select a track | click its lane, or its row in the right-hand panel: the row is outlined. Recording goes onto the selected track |
| Record audio | select an audio track, put the selection cursor where the take should start, and press **Rec** (the ring, R) next to Play; **Rec** again, Play or Space stops it. The take becomes an audio clip on the track (replacing what it covers), moved earlier by the audio device's input and output delay so it lines up with what you heard while playing. Right-click **● Rec** for its settings: the input to record (each input alone, as mono on both sides, or a pair in stereo) and a **count-in** of 1, 2 or 4 bars of metronome clicks before recording starts (the metronome's own sounds; it plays even if the metronome is off). The loop is ignored while recording. With ASIO the device's inputs come with its outputs; with the other driver types, Windows' default input device of that type is used, opened only while recording. Takes are saved with the project like any audio |
| Automation | **Auto** on a track's row (under its outputs; or right-click the row → Automate a parameter…): choose one of the parameters of the track's instrument or effects (pick the plugin, type part of the parameter's name). The 10 parameters you changed last in the plugin's own window are listed first, under "Recently tweaked": turn a knob there, then choose it here without searching (loading a preset, which changes many at once, and parameters automation is moving don't count). Its lane appears under the track: a curve from 0 at the bottom to 1 at the top. The curve's range is a tinted band between two lines (a dotted one halfway), with the parameter's value at the top and at the bottom written at the left (as the plugin shows them, e.g. +30.00 dB and 0.00 dB), and a line above each lane keeps stacked lanes apart. A parameter with only a few values (a switch such as Off / On, a choice such as Pro-L 2's Noise Shaping) gets a line at each of its values, named when there is room; its points snap to those values when placed or dragged, and its curve is drawn and played as steps: each point's value holds until the next point, where it jumps (VST2 plugins don't say how many values their parameters have, so theirs take any value). Drag the lane's bottom edge (in the timeline or the panel) to make it taller for finer editing, 40 to 320 px; double-click the edge for the standard height. Click to add a point (keep the button down to drag it), drag points (in time they snap to the grid in bars and beats, hold Alt to place them freely; a point never passes its neighbours), double-click or right-click a point to delete it; right-click the lane to clear the curve or remove the lane. While dragging, the value shows as the plugin shows it. **+** on a lane's row automates another parameter: its lane is stacked under the others. **Auto** hides and shows a track's lanes (they are kept); View → Show / Hide all automation does it for every track. While playing, and when rendering, freezing or bouncing, each automated parameter follows its curve (set at the start of every audio buffer); a lane without points leaves its parameter alone, and while stopped nothing is changed, so the plugin's own controls still work. Removing a plugin removes its lanes; a frozen track's lanes are locked. Saved with the project |
| Render to a file | File → **Render selection…** (Ctrl+Shift+R): the selected time range; **Render project…** (Ctrl+Shift+E): from 0 s to the end of the last clip, plus the effects' tail. The mix as you hear it (mute and solo, instruments, effects, plugin delay compensation, master effects and volume) from all tracks, as WAV (24-bit, 16-bit or 32-bit float) or FLAC, at the device's sample rate. It starts rendering 2 s early, so reverbs and delays are already ringing at the start. Not included: the metronome and previews |
| The two cursors | the **selection cursor** (blue) is where you click or start a range; **Play always starts from it**, and splitting and adding files use it. The **playback cursor** (red) moves while playing and stays where playback stopped. Each has a small arrow on the ruler. While playing, clicking the ruler also makes playback jump there (clicking a lane only moves the selection cursor). ⏮ Start (Home) puts both at 0 s. In bars and beats clicks snap to the nearest grid line (hold **Alt** to place it freely); so do the edges of a range selection. The time display shows the playback position while playing, the selection cursor while stopped |
| Tempo and time signature | the **BPM** and **/ 4** boxes on the top bar (type a value and press Enter, or use the arrows or the wheel). Clips keep their place in time when the tempo changes. **Tap** (or T), right next to the BPM box: click it in time with the music, at least twice, and the BPM follows the average of your last 8 taps (a pause of 2 s starts over) |
| Metronome | **Metronome** next to the BPM box (Ctrl+M) switches it on or off; it clicks while playing, on outputs 1/2 through the master volume, following the tempo and time signature (it is not in the master meters, LUFS or renders). **Right-click** it for its sounds: the click at the start of each bar and the click on the other beats, side by side, each with an attack-sustain-release envelope (drag its points, or type the times and level), pitch, waveform (sine, triangle, square, noise) and a Test button. Changes apply at once and are remembered |
| Bars and beats / seconds | **♩ Beats** (B) switches the ruler, grid and time display between bars and beats (`bar.beat.16th`) and minutes and seconds. The grid follows the zoom: zoomed out it shows bars (or every 2, 4, 8… bars), zoomed in beats, 8ths, 16ths and 32nds; clicks snap to whatever grid is shown |
| Triplets | **⅓ Thirds** (3), in bars and beats: the grid divides beats in thirds (half-note to 32nd-note triplets), and the time display counts thirds |
| Zoom / scroll | Ctrl+wheel / wheel |
| See the whole project | the **overview** bar above the editor: every track as a thin row and every clip in its colour, over the project's whole length; the outline is what the editor shows, the red line the playback cursor, the blue one the selection cursor. Click it to jump the view there, or drag the outline to slide the view. Drag its bottom edge down to make it taller (up to 320 px, remembered); double-click the edge for the standard height |
| Follow playback | **Follow** (the last of the playback buttons) or F toggles it. When on, once the playback cursor reaches the middle of the view the view moves along with it, keeping it centred |
| Mute / solo a track | M / S on its row |
| Track volume | the slider under the meter on every audio and MIDI track's row, with its setting in dB next to it, under the LUFS readout (−60 dB = silent … +6 dB; double-click the slider or the dB value for 0 dB). It works after the track's effects; freezing and bouncing leave it out, so it still applies afterwards |
| New MIDI clip | double-click empty space on a MIDI track, or right-click it → **New MIDI clip** (one bar long in bars and beats, else 2 s). With a range selected, right-click → New MIDI clip over the range makes one on each MIDI track of the range, exactly as long. It opens in the MIDI editor |
| Edit MIDI notes | double-click a MIDI clip (or right-click → Edit notes…): a piano roll, keys on the left, the notes' velocities underneath. Click an empty spot for a new note (one grid step long; drag to make it longer), drag a note to move it, drag its right end to resize it, right-click to delete (or select and press Delete; Ctrl+A selects all; Ctrl+D duplicates the selected notes right after them). Click or drag across the velocity bars to set velocities. Notes sound through the track itself (its VST3 instrument or the built-in synth, its effects and volume), playing or not: a pressed key, a new note or a grabbed note sounds while the mouse is held; drag over the keys to play every key you pass, and moving a note to another pitch plays the pitches on the way. Wheel scrolls, Shift+wheel sideways, Ctrl+wheel zooms, Alt+wheel (or Keys − / +) makes the keys taller or shorter; **⤢ Expand** lets the editor fill the screen. Times snap to the project's grid in bars and beats (Alt: freely). A note past the clip's end makes the clip longer. The ruler at the top shows the timeline's selection cursor (blue) and playback cursor (red) with their arrows, and both lines run down through the notes; click or drag the ruler to move the selection cursor (while playing, playback jumps there too). The editor is a normal window: you can play while it is open |
| VST3 and VST2 instruments and effects | a MIDI track's instrument: **Choose instrument** / **🎹** on its row (above). **FX** on a track's row (lit when it has effects): add **effects** to any track. The FX window is a patch space: the sound comes in at **IN** (left) and leaves at **OUT** (right edge, on to the track's fader), and each effect is a unit you place anywhere in between, showing a picture of the plugin's own window and its name, with an input jack (left) and an output jack (right). Nothing is wired for you: a newly chosen effect is only placed, so it isn't heard until you wire it in (a new track has IN cabled straight to OUT, so it plays unchanged meanwhile). Drag from a jack to another to plug in a cable, from IN or an effect's output to an effect's input or OUT (either way round). An output can feed several inputs, and an input adds up all its cables, so parallel chains are easy: e.g. IN → OUT plus IN → compressor → OUT for parallel compression. Pick a plug up off an input to move it to another input, or drop it anywhere else to unplug it; right-click a cable to remove it. A cable that would make a loop can't be plugged in. What isn't wired to OUT isn't heard (nothing wired to OUT: the track is silent). Each unit's light: green = on, amber = placed but not heard ("Not wired") or with nothing wired into it ("No input"), dark = bypassed (click the light: its input passes through unchanged), red = not loaded (not installed here any more) or stopped after an error. Drag a unit to move it; drag it past the edge and the space grows, both ways (it scrolls), and the window can be resized both ways. Click a unit to select it, double-click it to open the plugin's own window (it opens named after the plugin and track, fully on screen with its title bar to move, minimize and close it, and where you last left it), right-click it for its menu (open, bypass, take a new picture, unplug its cables, remove). Double-click an empty spot (or right-click → Place an effect here…, or **Add…**) to place an effect; right-click an empty spot also offers **Wire every effect in a row (left to right)** and **Unplug every cable** (IN straight to OUT). Delete removes the selected effect, with its cables. Where paths with different plugin delays meet (a look-ahead limiter on one, nothing on the other), the quicker one is held back to match, so they add up in time. Projects save where each effect is and how it's wired; projects from before keep their effects wired one after the other, as they played. Effects with a sidechain input (compressors, limiters, dynamic EQs: Pro-L 2, Pro-Q 4…) have a third jack, **SC**, next to their input: what is wired into it steers the effect without being heard (turn the plugin's own sidechain option on in its window, e.g. Pro-L 2's "Side Chain Triggering"). Wire into it IN, another effect's output (an EQ'd copy of the track, for instance), or **another track**: along the bottom of the patch space every other track has a jack ("OTHER TRACKS"), its sound after its effects and fader, for sidechains only. Plug the kick track into the bass compressor's SC and the bass ducks under every kick; a muted ("ghost") kick still does, without being heard. A track that already listens to this one can't be plugged in (its jack is dimmed: it would make a loop). Tracks are mixed in the order their sidechains need, and the delay of a look-ahead plugin on the kick counts for the bass too, so they stay in time; freezing or bouncing the bass renders the kick for it, so the rendering is ducked the same way. The master's patch can listen to every track. Removing a track unplugs its cables. The pictures are taken by the program: an effect without one is photographed once when it is added or its FX window opens (its window opens invisibly for about a second; it never shows or takes the focus), and every time you open a plugin's window it is photographed again, so the picture shows how you left it. One picture per plugin, shared by all tracks, kept in `%APPDATA%\TinyDAW\plugin_pictures`; a plugin whose VST3 bundle carries a picture of itself shows that one until the program has taken its own, and a plugin without any picture shows an empty faceplate. The FX window can stay open while you play (Space, Home and the transport buttons keep working, also from inside it) and edit, one per track. FX on the Master row adds master effects (they work on outputs 1/2). Plugins come from `C:\Program Files\Common Files\VST3` (VST3), and from the folders you choose in **Options → VST2 plugin folders…** (VST2: add a folder, or **Add the usual folders**, which adds those of `C:\Program Files\VstPlugins`, `C:\Program Files\Steinberg\VstPlugins` and `C:\Program Files\Common Files\VST2` (or `…\Steinberg\VST2`) that are on the computer; subfolders are searched too, remembered between runs). VST2 plugins work like VST3 ones everywhere (effects, instruments, their windows, pictures, automation, sidechains: a VST2 plugin with four inputs takes its 3rd and 4th as its sidechain, latency, saving); their names end in "(VST2)", as many plugins come in both kinds. Only 64-bit VST2 plugins can be used, and not yet shells holding several plugins (such as Waves). **Plugin types**: you never have to load a plugin to find out whether it is an instrument or an effect. When the program starts, the plugins whose type isn't known yet (new ones, or all of them the first time) are looked at in the background, in a separate process, so a plugin that crashes or hangs can't take the program with it: VST3 plugins say what they are without being created, VST2 plugins are created there for a moment and asked. The status bar shows how it goes (a few seconds for dozens of plugins); the menus and lists show the result as soon as they are opened again. A file holding several plugins (Serum 2 and Serum 2 FX) is listed once per plugin. Each file is looked at once and remembered (again only when it changes, e.g. updated); one that fails, crashes or takes over 30 s is given up on and shows as "type not known yet" until it is first loaded. Choosing new VST2 folders looks at their plugins too. Plugins load in well under a second, also big synths such as Serum 2 and when a project opens (the "please wait" window says how long each took last time); everything else keeps playing meanwhile. A plugin's own window is an ordinary window above the main one: several can be open at once, nothing waits for them, and plugins that allow it can be resized by dragging the window's edge. Plugins keep running while stopped, so synths stay playable from their own keyboard. Projects save each plugin's settings. Plugins that look ahead (limiters such as Pro-L 2, linear-phase EQs) hold their track's sound back; the other tracks are held back to match (plugin delay compensation), so everything stays in time. Playback then starts that much after pressing Play, and the red playback cursor follows what you hear. Bypassing such a plugin removes its delay |
| Master volume and mute | the Master row. The master lane and its row always stay at the bottom of the screen, drawn over the tracks, which scroll underneath them; clicking the master lane moves the selection cursor, and files dropped on it go onto new tracks |
| Choose a track's outputs | the "1/2" box on its row: pick any output pair of the audio device (3/4, 5/6, …). Switching to a device without that pair puts the track back on 1/2 |
| Version and updates | under the Master row, slightly dimmed: **Version: 0.4**, and under it whether a newer version is out on GitHub ([ExonSounds/Tiny-DAW](https://github.com/ExonSounds/Tiny-DAW): its latest release, or else its highest version tag), checked once in the background when the program starts. Its link opens the page; nothing is downloaded. The version only changes when the author says so (`daw/version.py`) |
| Levels | every meter shows only the outputs in use, one horizontal bar each (top to bottom, lowest output first): a track's row its output pair, a group's row its tracks' outputs, the Master row every output any track plays on. A bar is never thicker than a tenth of the meter's width. Hover a meter to see which outputs it shows |
| Loudness (LUFS) | every track row and the Master row: **S** short-term (last 3 s) and **I** integrated (since Play was pressed), per EBU R128; hover for momentary (last 0.4 s). A track is measured on its own output pair, the master over all outputs |
| Audio CPU load | top-right corner: current (last 0.25 s) and rolling average; a red ⚠ counts late buffers (likely clicks) since Play |
| Choose the audio driver | Options → **Audio properties…**: the Windows driver type (**ASIO**, **WASAPI** shared or exclusive, **WDM-KS**, **DirectSound**, **MME**), the output device, the buffer size (driver default, or 128–2048 frames: smaller = less delay but more CPU and risk of clicks, bigger = less CPU, more delay), and what the open device is doing (sample rate, outputs, delay). The choice is remembered |
| Skin (colours and fonts) | Options → **Skin**: pick a skin (Dark, Light, Midnight, OLED, or your own; OLED is true black wherever it can be, for OLED screens: unlit pixels use no power and never wear); it changes the whole interface at once and is remembered. Every skin has the same flat look: plain fills in the skin's colours, no bevels, gradients or shadows; a control lightens a little under the mouse, and switches that are on (Follow, Loop, Metronome, Beats, FX with effects…) and a dialog's main button are filled with the accent colour. Corners are slightly rounded; turn **Options → Rounded corners** off to make every corner square (buttons, fields, sliders, scroll bars, colour swatches), in every skin. Remembered between runs. **Customize this skin…** shows every colour (grouped: window, timeline, tracks and clips, meters, MIDI editor, Explorer) and the fonts, changing live; save them as your own skin. **Import skin…** adds a skin file, **Export this skin…** writes one to share, **Open the skins folder** shows where yours are (`%APPDATA%\TinyDAW\skins`). See "Skin files" below |
| Lower the CPU use | Options menu, remembered between runs: the buffer size above, **Screen refresh** (60 / 30 / 15 per second), and switching off **Level meters** or **Loudness (LUFS)**, which also stops the measuring behind them |

## Project files

Saving `MySong.json` also creates a folder `MySong_audio` next to it:

```
MySong.json        tracks, clips (position, cut, colour), MIDI notes, master settings, tempo and grid
MySong_audio/      the audio of every audio clip and every frozen track's rendering, as 32-bit float WAV
```

The audio is saved exactly as the program plays it, whatever format it was imported from, so the
project does not depend on the original files. Each sound is saved once: clips with the same audio
(split clips, or a sample used several times) share one file. A project saved by version 0.2 or
earlier may hold copies of the same sample; after saving it again, all its clips use one of them,
and the unused copies can be deleted by hand. Saving again only adds new audio to
the folder; it never overwrites or deletes files there. Keep the JSON and the folder together when you move a project.

## Skin files

A skin is a JSON file:

```json
{
  "format": "tinydaw-skin",
  "version": 1,
  "name": "My skin",
  "colors": {"lane": "#232529", "playhead": "#ff5050", "selection_fill": "#78aae62d"},
  "fonts": {"ui": "Segoe UI", "ui_size": 9, "mono": "Consolas"}
}
```

Colours are `"#rrggbb"`, or `"#rrggbbaa"` with transparency. A skin may name only some colours; the
others keep the default (the Dark skin). The names, with what each one colours, are `COLORS` in
`daw/ui/theme.py`; `daw/skins/dark.json` lists them all. Fonts: `"ui"` is the interface font and
`"ui_size"` its size in points (`""` / `0`: the system's own), `"mono"` the font of times and LUFS
numbers. Unknown names and values that aren't colours are left out (import says which).

## License note

The plugin host is built on [Steinberg's VST 3 SDK](https://github.com/steinbergmedia/vst3sdk) (MIT
license) and [pybind11](https://github.com/pybind/pybind11) (BSD-style license). Their notices are in
`THIRD_PARTY_NOTICES.txt`, which has to come with every copy that includes the built host. VST2
plugins are hosted through `native/vst2.h`, Tiny DAW's own declaration of the VST2 plugin interface
(the structure and command numbers plugins and hosts use), written for interoperability: not
Steinberg's VST2 SDK, which Steinberg no longer licenses (Steinberg doesn't license VST2 hosting
either; open-source hosts commonly do it this way). VST is a
registered trademark of Steinberg Media Technologies GmbH. (Versions 0.1 to 0.3.1 hosted plugins
through pedalboard, GPL-3.0; it is no longer used. Projects saved with those versions keep their
plugins' settings.)

## Building the plugin host

The host is `native/vst3host.cpp`. It needs Visual Studio 2022 or its Build Tools with "Desktop
development with C++", CMake, git, and the Python the program runs with (the built module only
works with that Python version). Then:

```
powershell -ExecutionPolicy Bypass -File native\build.ps1
```

The first build fetches the VST 3 SDK and pybind11 from GitHub (into `native/build`) and takes a few
minutes; it puts the module into `daw/audio/`.

## Folder layout

```
main.py                    start the program
start.py                   start the program with its files up to date (downloads changed files from GitHub)
Start Tiny DAW.bat         double-click to run start.py with Python 3.12
requirements.txt
THIRD_PARTY_NOTICES.txt    the licences of the VST 3 SDK and pybind11 (in the built plugin host)
DAW_icons/                 the playback buttons' icons (SVG, drawn in the skin's colours)
native/                    the plugin host's C++ source: vst3host.cpp (VST3), vst2plugin.cpp and vst2.h (VST2), CMakeLists.txt, build.ps1 (build/ is made by building)
daw/
  config.py                numbers: sizes, zoom limits, meter ranges, refresh rates
  model.py                 Project, Group, Track, AudioClip, MidiClip: the data. No sound card or GUI code.
  edits.py                 editing operations: split clips, cut a time range out of clips, make room for a placed clip
  timing.py                tempo, time signature, and the bars-and-beats grid (dynamic with zoom, thirds) clicks snap to
  project_io.py            saving and loading projects (JSON + audio folder)
  version.py               the program's version (0.1) and its GitHub repository
  update_check.py          is a newer version on GitHub? (latest release, or highest version tag)
  audio/                   talks to the sound card. No Qt.
    __init__.py            switches on ASIO support in sounddevice (must run before it is imported); the other
                           Windows driver types are always there
    devices.py             lists output devices per Windows driver type (ASIO, WASAPI, WDM-KS, DirectSound, MME)
    engine.py              the audio stream (any driver type, all device outputs; ASIO inputs too), play/pause, playhead, looping, recording, master gain, metering
    mixer.py               adds clips together per track into the output buffer, handing each track to the meters
    automation.py          automation: plugin parameters following their curves, set every buffer while playing and rendering
    render.py              renders offline: one track (freezing, bouncing) or the whole mix (Render selection / project)
    live.py                notes played live into a track (the MIDI editor's keys), through its instrument and effects
    metronome.py           the metronome's two click sounds (attack-sustain-release) and placing them on the beats
    loudness.py            LUFS measurement (K-weighting, momentary / short-term / integrated with gating)
    metering.py            takes copies of what played from the audio thread; measures peaks and LUFS on the GUI thread
    cpu.py                 measures how much of each buffer's time the audio code uses
    synth.py               built-in instrument that turns MIDI notes into audio
    plugins.py             VST3 plugins: finding them, loading them, playing instruments and effects
    plugin_probe.py        finds out plugins' types (instrument / effect), run in a process of its own
    patch.py               the sound through a track's or the master's effects, as they are wired (parallel paths kept in time),
                           and a MIDI track's instrument patch panel (its instruments added up, each at its volume)
    vst3host.*.pyd         the plugin host, built from native/ (see "Building the plugin host")
    juce_state.py          reads plugin settings saved by versions up to 0.3.1 (pedalboard / JUCE format)
    dsp.py                 numpy helpers: stereo conversion, resampling, waveform peaks, dB
    file_io.py             loads audio files and MIDI files
  skins/                   the program's own skins (dark.json, light.json, midnight.json, oled.json)
  ui/                      everything you see (PySide6 / Qt)
    main_window.py         builds the window and connects all the parts below
    transport_bar.py       top bar: buttons (the playback ones square, with icons), time display, tempo, time signature, beats / thirds
    audio_dialog.py        Options → Audio properties: driver type, device, buffer size, current device details
    timeline.py            ruler, lanes, the two cursors; mouse/keyboard editing (select, move, drop, delete)
    explorer.py            the sample explorer on the left: sample folders, search by name, drag samples in
    overview.py            the project overview bar above the editor: every lane and clip, compressed; moves the view
    clip_painter.py        draws a clip: box, waveform or MIDI notes, name; and the selected range
    waveform_cache.py      waveforms and MIDI notes drawn once into image tiles; redraws just copy them
    colors.py              automatic contrast: derives every colour on a clip from its chosen colour
    midi_editor.py         the MIDI editor: ruler with both cursors, piano roll (keys, notes) and velocity lane
    sample_waveform.py     the waveform of the selected sample, at the top of the Explorer
    color_picker.py        colour dialog for clips and track rows: colour wheel, brightness, hex, recent colours, preview
    selection.py           what is selected: clips and an optional time range over lanes
    lane_panel.py          right panel: one row per track (M, S, ✕, outputs, meters, LUFS) and per group
    lanes.py               which lanes the editor shows, top to bottom (group headers, tracks, master)
    master_strip.py        master row: mute, LUFS, level meter, volume (pinned to the bottom of the panel)
    update_notice.py       the dimmed update notice under the master row (checks in the background)
    master_lane.py         the master lane, pinned to the bottom of the editor's view over the tracks
    cpu_monitor.py         the audio CPU display in the top-right corner
    level_meter.py         level meter with one bar per output in use (tracks, groups and master)
    loudness_display.py    the small S / I LUFS readout on each row
    metronome_dialog.py    the metronome's settings window (two envelope editors), remembered between runs
    preferences.py         the Options menu's settings, remembered between runs
    plugin_host.py         loads VST3 and VST2 plugins and opens their windows (main thread, with a "please wait"); takes their pictures;
                           finds out the types of new plugins in the background (KindFinder)
    track_render.py        freezing, unfreezing and bouncing tracks (right-click menu, progress window)
    mix_export.py          File → Render selection / Render project: the mix to a WAV or FLAC file
    recording.py           Rec: recording onto the selected audio track, its settings (input, count-in)
    automation_dialog.py   choosing a plugin parameter to automate (with a search box)
    plugin_editor.py       a plugin's own window (the plugin draws in a Qt window; several at once)
    plugin_windows.py      Windows-only: photographs plugin windows (also invisible ones)
    plugin_dialog.py       a track's / the master's effects window (FX), and a MIDI track's instrument patch panel
    plugin_folders_dialog.py  Options → VST2 plugin folders
    patch_panel.py         the effects window's patch space: IN, OUT, units placed freely with their pictures, cables plugged by hand
    plugin_pictures.py     the plugins' pictures: taken of their windows, or from their VST3 bundle
    instrument_menu.py     choosing a MIDI track's instrument (Choose instrument / 🎹 on its row), or its instrument patch panel
    widgets.py             small shared helpers (lane-coloured frame, M/S buttons, volume slider and its dB)
    formatting.py          time and dB text
    skin.py                skins: reading, writing and listing skin files, putting one on the program
    skin_dialog.py         Options → Skin → Customize: every colour and the fonts, live
    theme.py               all colours and fonts, by name, from the current skin; the application palette and the flat stylesheet
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
- VST3 plugins are hosted by `native/vst3host.cpp` (the module `vst3host`): loading, state, parameter
  names and plugin windows on the main thread, processing on the audio thread (it releases the GIL).
  Values pass between the two through small queues the audio thread never waits on; the main thread
  calls each plugin's `idle()` every frame. A plugin's sidechain is its first auxiliary input bus.
  A plugin's processor and its controller (which runs its window) are connected directly, not
  through the SDK's `ConnectionProxy`: that drops every message not sent on the main thread, and
  plugins such as Pro-Q 4 and Pro-L 2 send their analyzer and meter data from the audio thread (their
  windows stood still).
  MIDI notes go to a VST3 instrument live, buffer by buffer, timed to the frame (`note_messages()` and
  `play_instrument()` in `audio/plugins.py`); when playback starts or jumps, instruments get note-offs
  for every note still held, so none hangs. A track with an instrument patch panel (`Track.uses_rack`,
  `rack`, `rack_cables`) plays each heard instrument into its own buffer, holds the quicker ones back to
  line up with the latest, scales each by its `volume_db` and adds them up (`play_instruments()` in
  `audio/patch.py`); that is the track's sound at its effects' IN.
- Plugin delay compensation (`audio/mixer.py`): each track's latency is its slowest path through its
  effects' cables (`audio/patch.py`), from its active plugins' reported `latency`; every track goes through a `DelayLine` so all come out with the latest one.
  `mix_clips` returns that lag; the engine shows the playback cursor at `heard_pos()` (that much behind)
  and plays the metronome in step with it. Renders (freeze, bounce) drop the track's own latency.
- The callback mixes and runs the plugins. Metering (`audio/metering.py`) gets copies of what played and does the
  measuring on the GUI thread in batches, which is far cheaper than doing it every buffer.
- The GUI never waits for the audio thread. Two timers in `main_window.py` read the engine:
  one every frame (playhead, level meters, overview) and one every 250 ms (CPU monitor, LUFS).
  The overview redraws its clips only when they change; if you add something to a clip that
  should show there, add it to `Overview.content_key()`.
- When tracks are added, removed, grouped or folded, `Timeline.tracks_changed` fires and `LanePanel` rebuilds
  its rows. The timeline, panel and overview all lay out their lanes with `ui/lanes.py`, so they line up.

## Where to start when changing something

- **Different look**: a skin (Options → Skin, or a JSON file, see "Skin files"); new colour names go in
  `COLORS` in `ui/theme.py`. Sizes are in `config.py`. The contrast targets for clips
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
