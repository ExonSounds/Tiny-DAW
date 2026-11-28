// The main window (main_window.py). See main_window.h.
#include "model/routing.h"
#include "ui/main_window.h"
#include "audio/midi_effects.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <ctime>

#include "audio/automation.h"
#include "audio/automation_write.h"
#include "audio/modulation.h"
#include "audio/devices.h"
#include "audio/metering.h"
#include "audio/patch.h"
#include "audio/plugin_audio.h"
#include "audio/sampler.h"
#include "config.h"
#include "core/discord_presence.h"  // (MainWindow::discordPresence is destroyed here)
#include "core/util.h"
#include "model/project_io.h"
#include "model/timebase.h"
#include "plugins/library.h"
#include "ui/audio_dialog.h"
#include "ui/autosave.h"
#include "ui/colors.h"
#include "ui/commands.h"
#include "ui/cpu_monitor.h"
#include "ui/timing_rows.h"
#include "ui/explorer.h"
#include "ui/history_window.h"
#include "ui/instrument_menu.h"
#include "ui/lane_panel.h"
#include "ui/level_meter.h"
#include "ui/loudness_display.h"
#include "ui/master_lane.h"
#include "ui/master_strip.h"
#include "ui/midi_input.h"
#include "ui/midi_ports_dialog.h"
#include "ui/midi_learn.h"
#include "ui/presets_ui.h"
#include "ui/computer_keyboard.h"
#include "ui/metronome_dialog.h"
#include "ui/midi_editor.h"
#include "ui/mix_export.h"
#include "ui/overview.h"
#include "ui/panel_style.h"
#include "ui/plugin_dialog.h"
#include "ui/plugin_folders_dialog.h"
#include "ui/wine_prefixes_dialog.h"
#include "ui/wine_install_steps.h"
#include "ui/wine_setup.h"
#include "ui/lanes.h"
#include "ui/scope_view.h"
#include "plugins/wine.h"
#include "ui/plugin_host.h"
#include "ui/recording.h"
#include "ui/templates.h"
#include "ui/project_safety.h"
#include "model/collect.h"
#include "ui/theme.h"
#include "ui/timeline.h"
#include "ui/toolkit/dialogs.h"
#include "ui/toolkit/layout.h"
#include "ui/toolkit/style.h"
#include "ui/track_render.h"
#include "ui/transport_bar.h"
#include "ui/update_notice.h"
#include "ui/widgets.h"
#include "model/warping.h"
#include "core/program_environment.h"

using ui::App;

namespace {

plat::WindowOptions mainOptions() {
    plat::WindowOptions o;
    o.title = "Untitled — Exbit";
    o.w = 1280;
    o.h = 800;
    o.acceptsFiles = true;  // files dragged from the desktop onto the lanes
    return o;
}

std::string fmtG(double v) {
    char b[64];
    snprintf(b, sizeof b, "%g", v);
    return b;
}

// "Project overview" beside the overview: same height; its box and text colour are the stylesheets'
// (default.css: OverviewCaption, the overview's colours with a line under it)
class OverviewCaption : public ui::Label {
public:
    OverviewCaption() : ui::Label("Project overview") {
        setStyleType("OverviewCaption");
        setAlignment(AlignCenter);
    }
};

}  // namespace

// ============================================================================================

MainWindowCore::MainWindowCore() : engine(project), prefs(Preferences::load()) {}

MainWindowCore::~MainWindowCore() {
    // the editors and finder first (they use the engine and project), then the rest
    keyboard.reset();  // (lets every key go)
    if (midiInput) midiInput->close();
    if (midiInput && midi_ports_dialog::host().hub == &midiInput->hub) midi_ports_dialog::setHost({});  // (its hub goes)
    engine.setMidiOut(nullptr);
    midiInput.reset();
    autosaver.reset();
    history.reset();
    recording.reset();
    exporter.reset();
    renderer.reset();
    plugins.reset();
    master.reset();
    engine.close();
}

MainWindow::MainWindow() : ui::Window(mainOptions()) {
    App::get().mainWindow = this;
    skin = skin::find(prefs.skin);  // before anything is built: widgets take its colours
    theme::rounded = prefs.roundedCorners;
    skinStamp_ = skinFilesStamp();  // (before reading: see showSkin)
    skinProblems = skin::apply(skin);
    updateMedia();
    metronomeSettings = metronome_dialog::loadSettings();
    engine.blocksize = prefs.bufferSize;
    engine.cores = prefs.processingCores;
    workers::setChosenCores(prefs.processingCores);

    menuBar = add<ui::MenuBar>();
    transport = add<TransportBar>();
    scroll = add<TimelineView>();
    timeline = scroll->makeContent<Timeline>(project, engine, scroll);
    wine::setPrefixes(prefs.wineList());  // (Windows plugins: the prefixes they're found in)
    wine::setWinePrograms(prefs.wineProgramMap());  // (…and the wine program chosen for some of them)
    plugins = std::make_unique<PluginHost>(this, project, engine, [this] { return prefs.vst2List(); });
    plugins->autoReload = prefs.reloadCrashedPlugins;
    // (hidden, for testing: more VST3 folders, separated by ':')
    if (const char* extra = environment::readProgramVariable("VST3_PATH")) {
        plugins->folders = plug::defaultFolders();
        for (auto& f : wine::vst3Folders(wine::prefixes())) plugins->folders.push_back(f);
        for (auto& f : util::split(extra, ':'))
            if (!f.empty()) plugins->folders.push_back(f);
    }
    // the master scope under the Master row (its options kept: options/scope); its height first
    ScopeSettings scopeSettings = ScopeSettings::fromJson(Json::parse(prefs.scope));
    lanes::setMasterScopeHeight(scopeSettings.shown ? theme::metric("scope-height", config::SCOPE_H) : 0);
    lanes::setTempoLaneShown(prefs.tempoLane);  // (View → Tempo lane, as it was left)
    lanes::setSignatureLaneShown(prefs.signatureLane);  // (View → Time signature lane)
    master = std::make_unique<MasterStrip>(engine, [this] { openMasterPlugins(); });
    master->scope->settings = scopeSettings;
    master->setScopeShown(scopeSettings.shown);
    master->scope->settingsChanged = [this] {
        prefs.scope = master->scope->settings.toJson().dump();
        prefs.save();
    };
    master->scope->hideRequested = [this] { showScope(false); };

    TrackRenderHost rh;
    rh.window = this;
    rh.stop = [this] { stop(); };
    rh.showMessage = [this](const std::string& t, int ms) { showStatus(t, ms); };
    rh.tracksRegrouped = [this] { timeline->tracksRegrouped(); };
    rh.placeClip = [this](const ClipPtr& c, int64_t f, const TrackPtr& t) { timeline->placeClip(c, f, t); };
    rh.closeEditors = [this](const std::vector<SlotPtr>& slots) { plugins->closeEditor(slots); };
    renderer = std::make_unique<TrackRenderer>(rh, engine);

    MixExportHost xh;
    xh.window = this;
    xh.stop = [this] { stop(); };
    xh.showMessage = [this](const std::string& t, int ms) { showStatus(t, ms); };
    xh.projectPath = [this] { return projectPath; };
    xh.masterDb = [this] { return (double)master->volume->value(); };
    xh.selection = [this](int64_t& start, int64_t& end) {
        if (!timeline->selection.range) return false;
        start = timeline->selection.range->start;
        end = timeline->selection.range->end;
        return true;
    };
    exporter = std::make_unique<MixExporter>(xh, engine);

    RecordingHost ch;
    ch.window = this;
    ch.prefs = &prefs;
    ch.metronome = &metronomeSettings;
    ch.currentTrack = [this] { return timeline->currentTrack; };
    ch.cursor = [this] { return timeline->cursor; };
    ch.seek = [this](int64_t f) { timeline->seek(f); };
    ch.stop = [this] { stop(); };
    ch.setPlaying = [this](bool on) { transport->setPlaying(on); };
    ch.setRecording = [this](bool on) { transport->setRecording(on); };
    ch.showRecording = [this](const std::vector<std::pair<TrackPtr, int64_t>>& takes) {
        timeline->recording = takes;
        timeline->update();
    };
    ch.placeClip = [this](const ClipPtr& c, int64_t f, const TrackPtr& t) { timeline->placeClip(c, f, t); };
    ch.showMessage = [this](const std::string& t, int ms) { showStatus(t, ms); };
    ch.resetMeters = [this] { resetMeters(); };
    ch.lanesChanged = [this] { timeline->lanesChanged(); };
    ch.midiInputReady = [this] {  // a MIDI keyboard chosen, or the computer keyboard playing
        return (midiInput && midiInput->hub.anyInputOpen()) || (keyboard && keyboard->isOn());
    };
    recording = std::make_unique<Recording>(ch, engine);

    panelScroll = add<ui::ScrollArea>();
    {
        auto p = std::make_unique<LanePanel>(
            project, *timeline, *master, engine.channels, [this](const TrackPtr& t) { openTrackPlugins(t); },
            [this](ui::Menu& m, const TrackPtr& t) { renderer->fillMenu(&m, t); },
            LanePanel::Instruments{[this](ui::Menu& m, const TrackPtr& t) { fillInstrumentMenu(m, t); },
                                   [this](const TrackPtr& t) { editInstrument(t); }});
        panel = p.get();
        panel->status = [this](const std::string& t, int ms) { showStatus(t, ms); };  // (TRACK SELECTION)
        // (● on a row: punching in and out while recording: ui/recording.h RECORDING SEVERAL TRACKS)
        panel->arm = [this](const std::vector<TrackPtr>& ts, bool on) { recording->setArmed(ts, on); };
        timeline->status = [this](const std::string& t, int ms) { showStatus(t, ms); };  // (slip, crop, fades)
        panel->loadPlugins = [this](const std::vector<SlotPtr>& slots) { return plugins->restore(slots); };
        panelScroll->setContent(std::move(p), false);  // (it sets its own height)
    }
    cpuMonitor = add<CpuMonitor>();
    overview = add<Overview>(project, engine, timeline, scroll, prefs.overviewH);
    overview->resizedTo.connect([this](int h) { overviewResized(h); });
    explorer = new Explorer([this](const std::string& path) { timeline->addFile(path, timeline->cursor); }, engine);
    status = add<ui::StatusBar>();
    status->followHints(prefs.statusHints);  // (Options → Hints in the status bar)
    plugins->statusBar = status;

    buildLayout();
    buildFileMenu();
    buildEditMenu();
    buildViewMenu();
    buildOptionsMenu();
    setUpCommands();  // every menu entry's keys, and the other keys, as the keymap binds them (ui/commands.h)
    connectControls();
    startTimers();
    applyMeterOptions();
    startDiscordPresence();  // (on unless turned off in Options → Discord status)
    updates = std::make_unique<UpdateChecker>();  // the notice under the master row: is there a newer version?
    updates->done.connect([this](const update_check::Result& r) { master->showUpdate(r); });
    if (!environment::readProgramVariable("NO_UPDATE_CHECK")) {  // (hidden: tests leave GitHub alone)
        updates->start();  // now, and every half hour (never installing anything: update_check.h)
        updateTimer_ = App::get().every(update_check::CHECK_EVERY_S * 1000, [this] { updates->start(); });
    }
    noticeTimer_ = App::get().every(60 * 1000, [this] { master->refreshUpdate(); });  // ("how long ago" counts on)
    openFirstDevice();
    setUpMidi();
    clip_hooks::tempoMap = [this] { return project.timing.audioMap(); };  // (warped clips follow its tempo: model/warping.h)
    timeline->warpNewFiles = prefs.warpNewFiles;
    history = std::make_unique<history::History>(project, engine.sr);
    // writing automation by moving controls (automation_write.h): at the song time being heard
    automation::recorder().songTime = [this](double& seconds) {
        if (!engine.playing || engine.sr <= 0) return false;
        seconds = (double)engine.heardPos() / engine.sr;
        return true;
    };
    setUpPresets();  // (presets' menus, the Explorer's Presets place, MIDI learn: main_window_presets.cpp)
    {
        std::weak_ptr<bool> alive = alive_;
        historyTimer_ = App::get().every(HISTORY_MS, [this, alive] {
            if (!alive.expired() && !closing) noteChanges();
        });
        skinTimer_ = App::get().every(config::SKIN_WATCH_MS, [this, alive] {
            if (!alive.expired() && !closing) checkSkinFile();
        });
    }
    autosave::Host ah;  // (and crash recovery: autosave.h)
    ah.window = this;
    ah.hasUnsavedChanges = [this] { return hasUnsavedChanges(); };
    ah.snapshot = [this] { return snapshot(); };
    ah.recordingActive = [this] { return recording->active(); };
    ah.pluginsEdited = [this] { return plugins->changed(); };
    ah.projectPath = [this] { return projectPath; };
    ah.describe = [this](const std::string& folder, std::map<std::string, std::string>& written,
                         std::vector<project_io::PendingAudio>& toWrite) {
        return project_io::describeRecovery(project, engine.sr, master->settings(), folder, written, toWrite);
    };
    ah.showMessage = [this](const std::string& t, int ms) { showStatus(t, ms); };
    ah.confirmDiscard = [this] { return confirmDiscard(); };
    ah.openProjectFile = [this](const std::string& path) { return openProjectFile(path); };
    ah.recovered = [this](const std::string& original) { recovered(original); };
    autosaver = std::make_unique<autosave::Autosave>(std::move(ah));
    markSaved();
    std::string start = templates::defaultTemplate(prefs);  // (Options → New projects start from)
    if (!start.empty()) startFromTemplate(start);
    closeRequested = [this] { return requestClose(); };
    timeline->setFocus();  // editing keys (Space, S, Delete…) work straight away
    if (!skinProblems.empty())
        showStatus("The skin " + skin.name + " has " + std::to_string(skinProblems.size()) +
                       " problem(s): Options → Skin → Problems", 10000);
    for (const std::string& p : skinProblems) util::debugf("skin: %s\n", p.c_str());
}

MainWindow::~MainWindow() {
    *alive_ = false;
    if (timeline) timeline->commands_ = nullptr;  // (the registry goes first)
    if (panel) panel->commands_ = nullptr;
    clip_hooks::tempoMap = nullptr;  // (it asked this window's project)
    automation::recorder().releaseAll();
    automation::recorder().songTime = nullptr;  // (it asked this window's engine)
    // (presets and MIDI learn: their hooks let go first)
    if (midi_learn::active() == learner.get()) midi_learn::setActive(nullptr);
    if (midiInput) midiInput->controlHook = nullptr;
    presets_ui::setHost({});
    historyWindow.reset();  // (it reads the history)
    if (frameId_ >= 0) App::get().removeFrame(frameId_);
    if (cpuTimer_ >= 0) App::get().cancelTimer(cpuTimer_);
    if (historyTimer_ >= 0) App::get().cancelTimer(historyTimer_);
    if (skinTimer_ >= 0) App::get().cancelTimer(skinTimer_);
    if (updateTimer_ >= 0) App::get().cancelTimer(updateTimer_);
    if (noticeTimer_ >= 0) App::get().cancelTimer(noticeTimer_);
    for (auto& [key, w] : fxWindows) {
        (void)key;
        w->finished.disconnectAll();
    }
    for (auto& [key, e] : midiEditors) {
        (void)key;
        e->closed.disconnectAll();
    }
    fxWindows.clear();
    midiEditors.clear();
    updates.reset();
    masterPins.clear();
    if (plugins) {
        plugins->closeEditor();
        plugins->stopFindingKinds();
        plugins->statusBar = nullptr;
    }
    engine.close();
    if (App::get().mainWindow == this) App::get().mainWindow = nullptr;
}

void MainWindow::showStatus(const std::string& text, int ms) {
    if (!text.empty()) util::debugf("status: %s\n", text.c_str());
    if (status) status->showMessage(text, ms);
}

// --- setup ----------------------------------------------------------------------------------

void MainWindow::buildLayout() {
    // always shown, so the panel's matching bottom padding is always right
    scroll->setHorizontalPolicy(ui::ScrollArea::Policy::AlwaysOn);

    panelScroll->setHorizontalPolicy(ui::ScrollArea::Policy::AlwaysOff);
    panelScroll->setVerticalPolicy(ui::ScrollArea::Policy::AlwaysOff);
    panelScroll->setFixedWidth(lane_panel::panelWidth());
    // the master lane and its row stay at the bottom of the screen, over the tracks scrolling beneath
    std::unique_ptr<ui::Widget> lane = timeline->takeMasterView();
    masterPins.push_back(std::make_unique<PinToBottom>(std::move(lane), scroll->viewport(), lanes::masterHeight()));
    masterPins.push_back(std::make_unique<PinToBottom>(master->releaseFrame(), panelScroll->viewport(),
                                                       lanes::masterHeight(), theme::scrollbarWidth()));
    ui::ScrollBar* timelineBar = scroll->verticalScrollBar();
    ui::ScrollBar* panelBar = panelScroll->verticalScrollBar();
    timelineBar->valueChanged.connect([panelBar](int v) { panelBar->setValue(v); });
    panelBar->valueChanged.connect([timelineBar](int v) { timelineBar->setValue(v); });

    auto* caption = add<OverviewCaption>();
    overviewCaption = caption;
    caption->setFixedHeight(overview->height());  // same height as the overview

    auto* root = ui::vbox(this, 0, 0);
    menuBar->setSizePolicy(ui::SizePolicy::Expanding, ui::SizePolicy::Fixed);
    root->addWidget(menuBar);
    transport->setFixedHeight(transport->sizeHint().y);
    transport->hPolicy = ui::SizePolicy::Expanding;
    root->addWidget(transport);

    auto* body = add<ui::Widget>();
    auto* row = ui::hbox(body, 0, 0);
    explorerDock = body->add<ExplorerDock>(explorer, [this] { setExplorerVisible(false); });
    explorerHandle = body->add<ui::SplitterHandle>(ui::Orientation::Horizontal);
    setExplorerWidth(theme::metric("explorer-width", config::EXPLORER_W));
    auto start = std::make_shared<int>(0);
    explorerHandle->pressed.connect([this, start] { *start = explorerWidth; });
    explorerHandle->dragged.connect([this, start](int d) { setExplorerWidth(*start + d); });
    row->addWidget(explorerDock);
    row->addWidget(explorerHandle);

    auto* central = body->add<ui::Widget>();
    central->hPolicy = ui::SizePolicy::Expanding;
    auto* line = ui::hbox(central, 0, 0);
    auto left = std::make_unique<ui::VBox>();
    left->spacing = 0;
    // (the overview, scroll, caption, monitor and panel scroll are the window's children so far)
    for (ui::Widget* w : std::initializer_list<ui::Widget*>{overview, scroll, caption, cpuMonitor, panelScroll})
        central->adopt(release(w));
    frameGap = central->add<ui::Widget>();
    tempoRow = central->add<TimingLaneRow>(TimingLaneRow::Kind::Tempo, *timeline, [this] { showTempoLane(false); });
    signatureRow =
        central->add<TimingLaneRow>(TimingLaneRow::Kind::Signature, *timeline, [this] { showSignatureLane(false); });
    left->addWidget(overview);
    left->addWidget(scroll, 1);
    auto right = std::make_unique<ui::VBox>();
    right->spacing = 0;
    right->addWidget(caption);
    right->addWidget(frameGap);    // (the timeline frame's top: none in the default look)
    right->addWidget(cpuMonitor);  // same height as the ruler, so the rows still line up
    right->addWidget(tempoRow);    // (as tall as the tempo lane, while shown)
    right->addWidget(signatureRow);  // (as tall as the time-signature lane)
    right->addWidget(panelScroll, 1);
    line->addLayout(std::move(left), 1);
    line->addLayout(std::move(right));
    row->addWidget(central, 1);
    root->addWidget(body, 1);
    status->setSizePolicy(ui::SizePolicy::Expanding, ui::SizePolicy::Fixed);
    root->addWidget(status);
    fitTimelineFrame();
}

void MainWindow::fitTimelineFrame() {
    auto* view = dynamic_cast<TimelineView*>(scroll);
    int top = view ? view->insetTop() : 0, bottom = view ? view->insetBottom() : 0;
    if (frameGap) {
        frameGap->setFixedHeight(std::max(0, top));
        frameGap->setVisible(top > 0);
    }
    if (masterPins.size() > 1) masterPins[1]->setMargin(theme::scrollbarWidth() + std::max(0, bottom));
    // (the window no lower than the master row pinned at the bottom and a whole track row above it)
    panelScroll->setMinimumHeight(lanes::masterHeight() + config::TRACK_H + theme::scrollbarWidth() + std::max(0, bottom));
}

void MainWindow::setExplorerVisible(bool on) {
    explorerDock->setVisible(on);
    explorerHandle->setVisible(on);
    if (explorerAction) explorerAction->checked = on;
}

void MainWindow::setExplorerWidth(int w) {
    explorerChosen = w;
    fitExplorerWidth();
    explorerChosen = explorerWidth;  // (dragged past the room: what was shown is what was chosen)
}

void MainWindow::fitExplorerWidth() {
    // (no narrower than its own controls need: its buttons and texts whole; not yet laid out: no room known)
    int least = std::max(120, explorerDock->minimumSizeHint().x);
    int most = width() > 0 ? width() - lane_panel::panelWidth() - 200 : explorerChosen;
    explorerWidth = std::max(least, std::min(std::max(160, most), explorerChosen));
    explorerDock->setFixedWidth(explorerWidth);
}

Point MainWindow::leastSize() const {
    // the Explorer counted at its narrowest: a window maximized with it wide may still be restored narrower (it
    // narrows with the window: fitExplorerWidth)
    Point m = ui::Window::leastSize();
    int excess = explorerDock && explorerDock->isVisible()
                     ? std::max(0, explorerWidth - std::max(120, explorerDock->minimumSizeHint().x)) : 0;
    if (excess) {  // (the body that much narrower; the menu bar, transport and status bar as they need)
        auto least = [](const ui::Widget* w) { return std::max(w->minimumSize().x, w->minimumSizeHint().x); };
        m.x = std::max({least(menuBar), least(transport), least(status), m.x - excess});
    }
    return m;
}

void MainWindow::buildFileMenu() {
    ui::Menu* menu = menuBar->addMenu("&File");
    struct Entry {
        const char* text;
        const char* keys;
        std::function<void()> slot;
    };
    std::vector<Entry> entries = {
        {"&New project", "Ctrl+N", [this] { newProject(); }},
        {"New from &template", "submenu:templates", nullptr},
        {"&Open project…", "Ctrl+O", [this] { openProject(); }},
        {"Open &recent", "submenu:recent", nullptr},
        {"&Save", "Ctrl+S", [this] { saveProject(); }},
        {"Save &as…", "Ctrl+Shift+S", [this] { saveProjectAs(); }},
        {"Save as t&emplate…", nullptr, [this] { templates::saveAsTemplate(*this); }},
        {"Co&llect and save", nullptr, [this] { project_safety::collectAndSave(*this); }},
        {"Save as arc&hive…", nullptr, [this] { project_safety::saveArchive(*this); }},
        {"Fin&d missing samples…", nullptr, [this] { project_safety::showMissing(*this, true); }},
        {"Open a &backup…", nullptr, [this] { openBackup(); }},
        {"&Import Ableton Live set…", nullptr, [this] { importAbleton(); }},
        {nullptr, nullptr, nullptr},
        {"&Render selection…", "Ctrl+Shift+R", [this] { exporter->renderSelection(); }},
        {"Render &project…", "Ctrl+Shift+E", [this] { exporter->renderProject(); }},
        {"Export &stems…", nullptr, [this] { exportStems(); }},
        {"Export selected &MIDI clips…", nullptr, [this] {
             exportMidi(std::vector<ClipPtr>(timeline->selection.clips.begin(), timeline->selection.clips.end()));
         }},
        {nullptr, nullptr, nullptr},
        {"Add audio or MIDI &file…", nullptr, [this] { addFiles(); }},  // (Ctrl+I shown: the toolbar's)
    };
    for (auto& e : entries) {
        if (!e.text) {
            menu->addSeparator();
            continue;
        }
        std::string keys = e.keys ? e.keys : "";
        if (keys == "submenu:templates" || keys == "submenu:recent") {  // (a submenu, filled as it opens)
            ui::Menu* sub = menu->addMenu(e.text);
            if (keys == "submenu:recent") sub->aboutToShow = [this](ui::Menu* m) { templates::fillRecentMenu(m, *this); };
            else sub->aboutToShow = [this](ui::Menu* m) { templates::fillTemplateMenu(m, *this); };
            continue;
        }
        menu->addAction(e.text, e.slot, keys);
    }
}

void MainWindow::buildEditMenu() {
    // undo and redo (history.h): from any of the program's windows (MIDI editor, FX windows too)
    ui::Menu* menu = menuBar->addMenu("&Edit");
    undoAction = menu->addAction("&Undo", [this] { undo(); }, "Ctrl+Z");
    redoAction = menu->addAction("&Redo", [this] { redo(); }, "Ctrl+Shift+Z");
    menu->addAction("&History…", [this] { showHistory(); })->tooltip =
        "Every change to the project by name: click one to go back (or forward) to it";
    menu->aboutToShow = [this](ui::Menu*) { showUndoState(); };
    menu->addSeparator();
    // (this window only: the MIDI editor copies notes with the same keys)
    menu->addAction("Cu&t clips", [this] { useClipboard("cut"); }, "Ctrl+X");
    menu->addAction("&Copy clips", [this] { useClipboard("copy"); }, "Ctrl+C");
    menu->addAction("&Paste clips at the cursor", [this] { useClipboard("paste"); }, "Ctrl+V");
    // (auto_follow.h: a project setting, saved and undoable; also in the transport's Grid menu)
    ui::Action* follows = menu->addAction("&Automation follows clips", [this] {
        setAutomationFollows(!project.timing.automationFollows);
    });
    follows->checkable = true;
    follows->tooltip = "Moving, copying and duplicating clips takes the automation under them along";
    menu->aboutToShow = [this, follows](ui::Menu*) {
        showUndoState();
        follows->checked = project.timing.automationFollows;
    };
    menu->addSeparator();
    // (in the whole song: song_time.h)
    menu->addAction("&Insert time…", [this] { showTimeEdit(insertTime()); }, "Ctrl+Shift+I");
    menu->addAction("&Delete time", [this] { showTimeEdit(deleteTime()); }, "Ctrl+Shift+Del");
    menu->addSeparator();
    addCaptureAction(menu);  // (Capture: capture.h)
}

void MainWindow::setAutomationFollows(bool on) {
    project.timing.automationFollows = on;
    util::debugf("automation follows clips: %s\n", on ? "on" : "off");
    showStatus(on ? "Automation follows clips: moving, copying and duplicating clips takes it along"
                  : "Automation stays on the timeline when clips move", 4000);
}

void MainWindow::showTimeEdit(const std::string& message) {
    if (!message.empty()) showStatus(message, 6000);
}

bool MainWindow::mouseHeld() const {
    if (App::get().headless()) {
        for (ui::Window* w : App::get().windows())
            if (w->grabWidget()) return true;
        return false;
    }
    return plat::heldButtons() != 0;
}

bool MainWindow::rendersPending() {
    for (auto& [key, w] : renderWindows) {
        (void)key;
        if (w.pending && w.pending()) return true;
    }
    return false;
}

void MainWindow::flushRenders() {
    // render now what those windows were about to render (before undo / redo, so their change is a step)
    auto windows = renderWindows;
    for (auto& [key, w] : windows) {
        (void)key;
        if (w.pending && w.pending() && w.flush) w.flush();
    }
}

void MainWindow::noteChanges() {
    // Now and then: a change to the project becomes an undo step, once no mouse button is held (a drag
    // is one step), and once the clips a MIDI effects or sampler window changed are rendered again (the
    // change and how the clips then sound are one step). A curve being written by moving its control is
    // one step once the touch ends (automation_write.h).
    if (history && !mouseHeld() && !rendersPending() && !automation::recorder().busy() && !tapTempo.tapping(plat::now())) {
        retimeWarped();  // (a warped clip moved along tempo changes: its new audio and the move are one step)
        history->check(engine.sr);
    }
    if (historyWindow && historyWindow->isWindowShown()) historyWindow->refresh();  // (it follows while open)
}

void MainWindow::showUndoState() {
    noteChanges();
    undoAction->enabled = history->canUndo();
    redoAction->enabled = history->canRedo();
}

void MainWindow::undo() { stepHistory(false); }

void MainWindow::redo() { stepHistory(true); }

void MainWindow::stepHistory(bool forward) {
    if (mouseHeld() || dragging()) {
        // (undoing under a drag would leave it working on what undo took away)
        showStatus("Let go of the mouse first: undo and redo wait until the drag is done", 3000);
        return;
    }
    automation::recorder().releaseAll();  // (a curve being written: written now, a step of its own)
    flushRenders();
    noteChanges();  // (a change not yet noted is a step of its own: undo takes it back first)
    if (!(forward ? history->redo(engine.sr) : history->undo(engine.sr))) {
        showStatus(forward ? "Nothing to redo" : "Nothing to undo", 2000);
        return;
    }
    historyMoved(forward);
}

void MainWindow::showHistory() {
    if (!historyWindow) {
        historyWindow = std::make_unique<HistoryWindow>(this, *history, [this](int position) { jumpHistory(position); });
    }
    noteChanges();  // (a change not yet noted shows as a step)
    historyWindow->refresh();
    historyWindow->open();
    historyWindow->raiseWindow();
}

void MainWindow::jumpHistory(int position) {
    if (mouseHeld() || dragging()) {
        showStatus("Let go of the mouse first: undo and redo wait until the drag is done", 3000);
        return;
    }
    automation::recorder().releaseAll();
    flushRenders();
    noteChanges();
    int from = history->position();
    if (!history->jumpTo(position, engine.sr)) return;
    historyMoved(history->position() > from);
}

void MainWindow::historyMoved(bool forward) {
    timeline->selection.clear();  // (what was selected may be gone)
    timeline->relayout();
    timeline->tracksChanged.emit();  // the panel's rows, the FX windows
    master->refreshFx(*project.masterEffects);
    transport->showTiming(project.timing);
    refreshTimingRows();
    checkMidiEditors();
    reloadEditors();
    auto windows = renderWindows;  // (they show the track's MIDI effects or sampler as they are now)
    for (auto& [key, w] : windows) {
        (void)key;
        if (w.reload) w.reload();
    }
    if (historyWindow && historyWindow->isWindowShown()) historyWindow->refresh();
    showStatus(forward ? "Redone" : "Undone", 2000);
}

void MainWindow::buildViewMenu() {
    ui::Menu* menu = menuBar->addMenu("&View");
    explorerAction = menu->addCheckable("&Explorer", true, [this](bool on) { setExplorerVisible(on); }, "Ctrl+E");
    tempoLaneAction = menu->addCheckable("&Tempo lane", lanes::tempoLaneShown(), [this](bool on) { showTempoLane(on); });
    tempoLaneAction->tooltip = "A lane under the ruler with the tempo changes: click to add one, drag it, double-click "
                               "to type its tempo, right-click for ramps and the timebase";
    signatureLaneAction = menu->addCheckable("Time &signature lane", lanes::signatureLaneShown(),
                                             [this](bool on) { showSignatureLane(on); });
    signatureLaneAction->tooltip = "A lane under the tempo lane with the time signatures: click a bar to add one, drag a "
                                   "block's start along the bars, double-click to type it, right-click to remove it";
    menu->addSeparator();
    menu->addAction("Show all automation", [this] { showAllAutomation(true); });
    menu->addAction("Hide all automation", [this] { showAllAutomation(false); });
    menu->addSeparator();
    menu->addAction("Command &palette…", [this] { showPalette(); }, "Ctrl+Shift+P")->tooltip =
        "Every command by typing a few of its letters (the terminal: Ctrl+P)";
}

void MainWindow::showTempoLane(bool on) {
    lanes::setTempoLaneShown(on);
    prefs.tempoLane = on;
    prefs.save();
    if (tempoLaneAction) tempoLaneAction->setChecked(on);
    refreshTimingRows();       // (its row beside it, as tall: the panel's rows still line up)
    timeline->lanesChanged();  // (the lanes start lower; the panel rebuilds)
    if (overview) overview->update();
    util::debugf("main window: tempo lane %s\n", on ? "shown" : "hidden");
}

void MainWindow::showSignatureLane(bool on) {
    lanes::setSignatureLaneShown(on);
    prefs.signatureLane = on;
    prefs.save();
    if (signatureLaneAction) signatureLaneAction->setChecked(on);
    refreshTimingRows();
    timeline->lanesChanged();
    if (overview) overview->update();
    util::debugf("main window: time-signature lane %s\n", on ? "shown" : "hidden");
}

void MainWindow::refreshTimingRows() {
    for (TimingLaneRow* row : {tempoRow, signatureRow})
        if (row) {
            row->fit();
            row->refresh();
        }
}

void MainWindow::showAllAutomation(bool on) {
    // every track with automation lanes shows them, or none does
    for (auto& t : project.tracks) t->showAutomation = on && !t->automation.empty();
    timeline->lanesChanged();
}

void MainWindow::buildOptionsMenu() {
    // audio properties, and options that lower CPU use; all remembered between runs
    ui::Menu* menu = menuBar->addMenu("&Options");
    menu->addAction("&Audio properties…", [this] { showAudioProperties(); });
    addMidiOptions(menu);  // Recording offset…, MIDI input…, Computer keyboard
    addPresetOptions(menu);  // MIDI controller links…
    ui::Menu* starts = menu->addMenu("&New projects start from");  // (an empty project, or a template: templates.h)
    starts->aboutToShow = [this](ui::Menu* m) { templates::fillDefaultMenu(m, *this); };
    menu->addAction("&VST2 plugin folders…", [this] { chooseVst2Folders(); });
#ifndef _WIN32  // (Wine's: on Windows plugins and their installers run as they are)
    menu->addAction("&Wine prefixes…", [this] { chooseWinePrefixes(); });
    menu->addAction("&Install Windows plugin…", [this] { installWindowsPlugin(); });
#endif
    menu->addAction("&Keyboard shortcuts…", [this] { showShortcuts(); })->tooltip =
        "Every command and its keys: find one, give it other keys; keymaps to import and export";
#ifdef _WIN32
    menu->addCheckable("Reload crashed plugins automaticall&y", prefs.reloadCrashedPlugins, [this](bool on) {
            prefs.reloadCrashedPlugins = plugins->autoReload = on;
            prefs.save();
        })->tooltip = "A plugin whose plugin host crashed starts again by itself, with the settings it last had "
                      "(at most 3 times a minute; then it stays stopped)";
#else
    menu->addCheckable("Reload crashed Windows plugins automaticall&y", prefs.reloadCrashedPlugins, [this](bool on) {
            prefs.reloadCrashedPlugins = plugins->autoReload = on;
            prefs.save();
        })->tooltip = "A Windows plugin whose Wine host crashed starts again by itself, with the settings it last had "
                      "(at most 3 times a minute; then it stays stopped)";
#endif
    menu->addCheckable("&Warp new audio files", prefs.warpNewFiles, [this](bool on) {
            prefs.warpNewFiles = timeline->warpNewFiles = on;
            prefs.save();
        })->tooltip = "Audio files added to the song get their tempo found (from the file, else by listening) and "
                      "follow the song's tempo (Warp…); off: they play as recorded";
    menu->addCheckable("&Hints in the status bar", prefs.statusHints, [this](bool on) {
            prefs.statusHints = on;
            prefs.save();
            status->followHints(on);
        })->tooltip = "The status bar at the bottom shows what the control under the mouse does (the first line "
                      "of its tooltip)";
    addDiscordOptions(menu);  // Discord status: Show in Discord, Application ID…
    menu->addSeparator();

    ui::Menu* refresh = menu->addMenu("Screen &refresh");
    for (int fps : prefs::refreshRates()) {
        std::string label = fps ? std::to_string(fps) + " per second"
                                : "As the screen (" + std::to_string((int)std::lround(App::get().displayRate())) + " per second)";
        ui::Action* a = refresh->addCheckable(label, fps == prefs.refreshFps,
                                              [this, fps](bool on) {
                                                  if (on) setRefreshRate(fps);
                                              });
        a->group = 1;
        a->tooltip = "How often the playhead and meters move. Lower: less CPU, less smooth. "
                     "(Drags and resizes always move with every picture the screen shows)";
    }
    menu->addSeparator();
    menu->addCheckable("&Level meters", prefs.levelMeters, [this](bool on) { setMeterOption("level_meters", on); });
    menu->addCheckable("Loudness (&LUFS)", prefs.loudnessMeters,
                       [this](bool on) { setMeterOption("loudness_meters", on); });
    scopeAction = menu->addCheckable("Master &scope", master->scope->settings.shown, [this](bool on) { showScope(on); });
    scopeAction->tooltip = "Under the Master row: the waveform or spectrum of what the master plays (right-click it "
                           "for its options)";
    auto* cpuAction = menu->addCheckable("&CPU per track", prefs.trackCpu, [this](bool on) {
        prefs.trackCpu = on;
        prefs.save();
        if (!on)
            for (auto& kv : panel->frames) kv.second->setLoad(-1);
    });
    cpuAction->tooltip = "A thin bar along the bottom of each track's row: how much of one processor core the track "
                         "takes (its plugins and effects). The Audio CPU monitor's tooltip "
                         "lists the costliest tracks";
    menu->addSeparator();
    ui::Menu* colours = menu->addMenu("&Colours");
    colours->addCheckable("&Random colour for new tracks", prefs.randomTrackColors,
                          [this](bool on) { setColourOption("random_track_colors", on); })
        ->tooltip = "Every new track gets a colour of its own, unlike its neighbours'";
    colours->addCheckable("&Clips take their track's colour", prefs.clipsFollowTrack,
                          [this](bool on) { setColourOption("clips_follow_track", on); })
        ->tooltip = "Clips without a colour of their own are drawn in their track's colour";
    colours->addSeparator();
    colours->addAction("&Default clip and row colours…", [this] { editSkin(); })->tooltip =
        "They are the skin's variables (--clip-audio, --clip-midi, --row…): this edits the skin";
    applyColourOptions();
    skinMenu = menu->addMenu("&Skin");
    skinMenu->aboutToShow = [this](ui::Menu*) { fillSkinMenu(); };
    fillSkinMenu();
    roundedAction = menu->addCheckable("Rounded c&orners", prefs.roundedCorners,
                                       [this](bool on) { setRoundedCorners(on); });
    roundedAction->tooltip = "Off: every corner of buttons, fields, sliders and scroll bars is square";
    // the whole window bigger or smaller: from the next start (main.cpp)
    ui::Menu* sizes = menu->addMenu("Interface si&ze");
    int current = prefs::interfaceScale();
    for (int size : prefs::interfaceSizes()) {
        ui::Action* a = sizes->addCheckable(std::to_string(size) + " %", size == current);
        a->group = 3;
        a->triggered = [this, size] { setInterfaceScale(size); };
        sizeActions[size] = a;
    }
    sizes->aboutToShow = [this](ui::Menu*) { offerInterfaceSizes(); };
    // whose frames the windows have: Exbit's own (a title bar and border in the skin's look) or the window
    // manager's; every window changes at once (remembered)
    ui::Menu* frames = menu->addMenu("&Window frames");
    bool own = prefs.windowFrames != "system";
    ui::Action* ownFrames = frames->addCheckable("&Exbit's own", own);
    ownFrames->group = 4;
    ownFrames->triggered = [this] { setWindowFrames(true); };
    ownFrames->tooltip = "Every window's title bar and border are drawn by Exbit, in the skin's look (the skin "
                         "can change them: TitleBar in docs/skins.md)";
    ui::Action* systemFrames = frames->addCheckable("The &system's", !own);
    systemFrames->group = 4;
    systemFrames->triggered = [this] { setWindowFrames(false); };
    systemFrames->tooltip = "Every window's title bar and border are the window manager's, as other programs have";
    // see-through windows (the skin's translucent backgrounds, rounded corners against the desktop, shadows under
    // menus and tooltips), while the desktop composites windows
    frames->addSeparator();
    transparencyAction = frames->addCheckable("T&ransparency", prefs.transparency, [this](bool on) { setTransparency(on); });
    frames->aboutToShow = [this](ui::Menu*) { offerTransparency(); };
    offerTransparency();
}

void MainWindow::offerTransparency() {
    if (!transparencyAction) return;
    bool available = App::get().transparencyAvailable();
    transparencyAction->setEnabled(available);
    transparencyAction->tooltip =
        available ? "Windows may be see-through where the skin makes them so (Window:transparent in docs/skins.md): "
                    "rounded corners against the desktop, shadows under menus and tooltips. Windows opened "
                    "while it was off stay opaque until they are opened again (this one: after a restart)"
                  : "No compositing manager runs on this desktop: windows can't be see-through (they are opaque, "
                    "their corners cut)";
}

void MainWindow::setTransparency(bool on) {
    // Options → Window frames → Transparency (remembered)
    prefs.transparency = on;
    prefs.save();
    App::get().setTransparency(on);
}

void MainWindow::setWindowFrames(bool own) {
    // Options → Window frames: every window changes at once (remembered)
    prefs.windowFrames = own ? "own" : "system";
    prefs.save();
    App::get().setOwnFrames(own);
}

bool MainWindow::interfaceSizeFits(int size) {
    // Would the window, drawn at `size` %, fit across this screen? (Its narrowest is the same in its own
    // pixels at every size: the top bar's, with every control on it in its short form.)
    Rect screen = App::get().screenAt(App::get().cursorPos());
    if (screen.empty()) return true;
    double across = screen.w * (double)App::get().scale();  // (in screen pixels)
    int narrowest = std::max(minimumSizeHint().x, transport->narrowestWidth());
    return narrowest * size / 100.0 <= across + 1;
}

void MainWindow::offerInterfaceSizes() {
    // Options → Interface size: sizes too wide for this screen can't be chosen
    int current = prefs::interfaceScale();
    for (auto& [size, a] : sizeActions) {
        bool fits = size == current || interfaceSizeFits(size);
        a->enabled = fits;
        a->tooltip = fits ? "" : "Too wide for this screen: the window couldn't fit across it";
    }
}

void MainWindow::setInterfaceScale(int size) {
    // Options → Interface size: remembered, and used from the next start (everything is sized once)
    prefs.interfaceScale = size;
    prefs.save();
    ui::msg::information(this, "Interface size",
                         "Exbit will be drawn at " + std::to_string(size) +
                             " % from the next time it starts: close it and start it again. (For more contrast, "
                             "try Options → Skin → High contrast.)");
}

// --- skins (skin.h) -------------------------------------------------------------------------

void MainWindow::fillSkinMenu() {
    // the skins there are (looked up each time the menu opens), and what to do with them
    ui::Menu* menu = skinMenu;
    menu->clear();
    for (const skin::Skin& s : skin::library()) {
        ui::Action* a = menu->addCheckable(s.builtin ? s.name : s.name + "  (yours)",
                                           s.name == skin.name && s.builtin == skin.builtin);
        a->group = 2;
        a->tooltip = s.path;
        a->triggered = [this, s] { chooseSkin(s); };
    }
    menu->addSeparator();
    collectSkinProblems();
    menu->addAction("&Edit this skin…", [this] { editSkin(); })->tooltip =
        "Opens its stylesheet in your text editor (one of the program's own: a copy of it, yours); what you save "
        "shows at once";
    ui::Action* problems = menu->addAction(
        skinProblems.empty() ? std::string("Skin &problems") : "Skin &problems (" + std::to_string(skinProblems.size()) + ")…",
        [this] { showSkinProblems(); });
    problems->enabled = !skinProblems.empty();
    problems->tooltip = "What the skin's stylesheets have that Exbit left out (it keeps the default there)";
    menu->addAction("&Import skin…", [this] { importSkin(); });
    menu->addAction("&Export this skin…", [this] { exportSkin(); });
    menu->addAction("&Open the skins folder", [this] { openSkinsFolder(); });
}

void MainWindow::chooseSkin(const skin::Skin& s) {
    // use it from now on (remembered)
    skin = s;
    prefs.skin = s.name;
    prefs.save();
    showSkin(s);
}

void MainWindow::showSkin(const skin::Skin& s) {
    // put a skin on the whole program at once. (Its files' stamp is taken before they are read: one saved
    // in between is then seen as changed and read again, never missed.)
    std::string stamp = skinFilesStamp();
    skinProblems = skin::apply(s);
    skinStamp_ = stamp;
    restyle();
}

std::string MainWindow::skinFilesStamp() const {
    // the skin's stylesheet, default.css, and what they @import (as last read: skin::watchedFiles)
    std::vector<std::string> files = skin::watchedFiles();
    for (const std::string& path : {skin.path, skin::defaultSheet()})
        if (!path.empty() && std::find(files.begin(), files.end(), path) == files.end()) files.push_back(path);
    std::string stamp;
    for (const std::string& path : files)
        stamp += path + "=" + std::to_string(util::fileMtime(path)) + ":" + std::to_string(util::fileSize(path)) + ";";
    return stamp;
}

void MainWindow::updateMedia() {
    // @media rules and vw / vh follow the window's size and the interface size
    css::Media m;
    m.width = std::max(1, width());
    m.height = std::max(1, height());
    m.scale = prefs::interfaceScale() / 100.0;
    if (theme::setMedia(m) && panel) restyle();
}

void MainWindow::checkSkinFile() {
    // (every SKIN_WATCH_MS) the skin's stylesheet, or default.css, was saved: put it on again, so what is
    // edited in a text editor shows at once
    std::string now = skinFilesStamp();
    if (now == skinStamp_) return;
    skinStamp_ = now;
    if (!skin.path.empty() && !util::fileExists(skin.path)) return;  // (being replaced, or gone: as it is until then)
    skin::Skin s = skin;
    std::vector<std::string> problems;
    std::string err;
    if (!skin.path.empty() && skin::read(skin.path, s, problems, err, skin.builtin) && s.name != skin.name) {
        prefs.skin = s.name;  // (its --skin-name was changed: remembered under the new one)
        prefs.save();
    }
    skin = s;
    showSkin(s);
    if (skinProblems.empty()) showStatus("Skin " + s.name + " reloaded", 4000);
    else
        showStatus("Skin " + s.name + " reloaded; " + std::to_string(skinProblems.size()) + " problem(s), the first: " +
                       skinProblems[0],
                   15000);
}

void MainWindow::restyle() {
    // rebuild or repaint what took its colours from the old skin, and put the skin's sizes back where they were
    // set once (the panel's width, the master row's height and margin, the scope's, the Explorer's)
    lanes::setMasterScopeHeight(master->scope->settings.shown ? theme::metric("scope-height", config::SCOPE_H) : 0);
    panelScroll->setFixedWidth(lane_panel::panelWidth());
    for (auto& pin : masterPins) pin->setHeight(lanes::masterHeight());
    fitTimelineFrame();  // (the panel's master row's margin too)
    fitExplorerWidth();
    panel->rebuild();
    master->restyle();
    overviewCaption->update();
    overview->forgetImage();  // (its cached image used the old colours)
    overview->refresh();
    transport->fit();  // (another font: the top bar's controls may be wider or narrower)
    transport->setFixedHeight(transport->sizeHint().y);  // (and taller or lower: the skin's buttons)
    refreshTimingRows();  // (the skin's lane heights and panel width)
    App::get().repaintAll();
}

void MainWindow::setRoundedCorners(bool on) {
    // Options → Rounded corners: square corners everywhere when off (remembered)
    prefs.roundedCorners = on;
    prefs.save();
    theme::rounded = on;
    showSkin(skin);
}

void MainWindow::overviewResized(int height) {
    // the overview's bottom edge was dragged: the caption beside it follows; remembered
    overviewCaption->setFixedHeight(height);
    prefs.overviewH = height;
    prefs.save();
}

void MainWindow::setColourOption(const std::string& attr, bool on) {
    if (attr == "random_track_colors") prefs.randomTrackColors = on;
    else prefs.clipsFollowTrack = on;
    prefs.save();
    applyColourOptions();
}

void MainWindow::applyColourOptions() {
    // Options → Colours: random colours for new tracks; clips taking their track's colour
    colors::followTrack = prefs.clipsFollowTrack;
    timeline->randomTrackColors = prefs.randomTrackColors;
    restyle();
}

void MainWindow::editSkin() {
    // the skin's stylesheet in the system's text editor; one of the program's own is copied into your skins
    // folder first (it is edited as yours). What is saved shows at once (checkSkinFile).
    skin::Skin s = skin;
    if (s.builtin || s.path.empty()) {
        std::string name = s.name + " (mine)";
        if (!ui::getText(this, "Edit this skin",
                         s.name + " is one of Exbit's own skins: you edit a copy of it, kept in your skins "
                                  "folder. The copy's name:",
                         name))
            return;
        name = util::trim(name);
        if (name.empty()) return;
        std::string target = util::joinPath(skin::userDir(), skin::fileName(name));
        if (util::fileExists(target) &&
            ui::msg::question(this, "Edit this skin",
                              "You already have a skin called " + name + ". Replace it with a copy of " + s.name +
                                  "?") != ui::msg::Yes)
            return;
        std::string err;
        std::string path = skin::makeCopy(s, name, err);
        skin::Skin copy;
        std::vector<std::string> problems;
        if (path.empty() || !skin::read(path, copy, problems, err)) {
            ui::msg::warning(this, "Edit this skin", "Cannot make the copy.\n\n" + err);
            return;
        }
        chooseSkin(copy);
        s = copy;
    }
    if (skin::openInEditor(s.path))
        showStatus("Editing " + s.path + " in your text editor: what you save shows at once", 10000);
    else
        ui::msg::information(this, "Edit this skin",
                             "No editor could be started for it. Open it in a text editor yourself:\n\n" + s.path +
                                 "\n\nWhat you save there shows in Exbit at once.");
}

void MainWindow::collectSkinProblems() {
    for (const css::Problem& p : theme::styles().takeProblems()) {
        std::string text = p.text();
        if (std::find(skinProblems.begin(), skinProblems.end(), text) == skinProblems.end()) skinProblems.push_back(text);
    }
}

void MainWindow::showSkinProblems() {
    collectSkinProblems();
    if (skinProblems.empty()) return;
    std::string list;
    for (size_t i = 0; i < skinProblems.size() && i < 40; i++) list += (i ? "\n" : "") + skinProblems[i];
    if (skinProblems.size() > 40) list += "\n… and " + std::to_string(skinProblems.size() - 40) + " more";
    ui::msg::information(this, "Skin problems",
                         "Exbit left these out of the skin " + skin.name + " (the default stays there):\n\n" + list);
}

void MainWindow::importSkin() { skin::importSkin(this, [this](const skin::Skin& s) { chooseSkin(s); }); }

void MainWindow::exportSkin() {
    std::string path = skin::exportSkin(this, skin);
    if (!path.empty()) showStatus("Skin saved as " + path, 5000);
}

void MainWindow::openSkinsFolder() { skin::openSkinsFolder(); }

void MainWindow::chooseVst2Folders() {
    // Options → VST2 plugin folders: where VST2 plugins are looked for (remembered)
    std::vector<std::string> chosen;
    {
        PluginFoldersDialog d(this, prefs.vst2List());
        if (!d.exec()) return;
        chosen = d.folders();
    }
    prefs.setVst2List(chosen);
    prefs.save();
    int found = 0;
    for (auto& p : plugins->rescan().plugins)
        if (plug::isVst2Path(p.path)) ++found;
    showStatus(std::to_string(found) + " VST2 plugin" + (found == 1 ? "" : "s") + " found", 5000);
}

void MainWindow::showScope(bool on) {
    // Options → Master scope (and "Hide the scope" in its menu): the Master row and lane that much taller
    lanes::setMasterScopeHeight(on ? theme::metric("scope-height", config::SCOPE_H) : 0);
    master->setScopeShown(on);
    for (auto& pin : masterPins) pin->setHeight(lanes::masterHeight());
    fitTimelineFrame();  // (the window's least height)
    if (scopeAction) scopeAction->setChecked(on);
    timeline->relayout();
    prefs.scope = master->scope->settings.toJson().dump();
    prefs.save();
}

void MainWindow::chooseWinePrefixes() {
    // Options → Wine prefixes: where Windows plugins are looked for (remembered)
    std::vector<std::string> chosen;
    std::map<std::string, std::string> programs;
    {
        WinePrefixesDialog d(this, prefs.wineList(), prefs.wineProgramMap());
        if (!d.exec()) return;
        chosen = d.prefixes();
        programs = d.programs();
    }
    prefs.setWineList(chosen);
    prefs.setWineProgramMap(programs);
    prefs.save();
    wine::setPrefixes(prefs.wineList());
    wine::setWinePrograms(prefs.wineProgramMap());
    int found = 0;
    for (auto& p : plugins->rescan().plugins)
        if (p.looksWindows()) ++found;
    showStatus(std::to_string(found) + " Windows plugin" + (found == 1 ? "" : "s") + " found", 5000);
}

void MainWindow::installWindowsPlugin() {
    // Options → Install Windows plugin…: an installer run in a Wine prefix, then what it added looked for
    const std::string title = "Install Windows plugin";
    std::vector<std::string> prefixes = wine::prefixes();
    std::set<std::string> known, before = wine_setup::vst2Dlls(prefixes);
    for (auto& p : plugins->library().plugins) known.insert(p.path);
    std::string used, made, endNote, logFile;
    if (!wine_setup::installPlugin(this, prefixes, used, made, &endNote, &logFile)) return;
    if (!made.empty()) {  // (a prefix made for it: from now on searched)
        auto list = prefs.wineList();
        list.push_back(made);
        prefs.setWineList(list);
        prefs.save();
        wine::setPrefixes(prefs.wineList());
    }
    // VST2 plugins land wherever the installer chose: those in a usual folder not searched yet are offered
    auto folders = wine_setup::newVst2Folders(before, wine_setup::vst2Dlls({used}), prefs.vst2List());
    if (!folders.empty()) {
        std::string text = "The installer put VST2 plugins (.dll) in folders Exbit doesn't look in yet:\n";
        for (auto& f : folders) text += "\n" + f;
        if (ui::msg::question(this, title, text + "\n\nAdd them to Options → VST2 plugin folders?") == ui::msg::Yes) {
            auto v = prefs.vst2List();
            v.insert(v.end(), folders.begin(), folders.end());
            prefs.setVst2List(v);
            prefs.save();
        }
    }
    std::vector<std::string> added;
    for (auto& p : plugins->rescan().plugins)
        if (p.looksWindows() && !known.count(p.path) && std::find(added.begin(), added.end(), p.name) == added.end())
            added.push_back(p.name);
    if (added.empty()) {
        // (how the installer ended, and its log's last lines, where it didn't end well)
        ui::msg::box(this, endNote.empty() ? ui::msg::Icon::Information : ui::msg::Icon::Warning, title,
                     "No new Windows plugins were found in " + used + ".\n\nIf the installer is still open, "
                     "finish it, then look again (Options → Wine prefixes → OK). VST2 plugins installed "
                     "elsewhere are found once their folder is in Options → VST2 plugin folders." +
                         (endNote.empty() ? std::string() : "\n\n" + endNote + "\n\nIts last lines are below."),
                     ui::msg::Ok, ui::msg::Ok, endNote.empty() ? "" : wine_setup::shownLastLines(logFile));
        return;
    }
    std::string names;
    for (auto& n : added) names += "\n" + n;
    showStatus(std::to_string(added.size()) + " new Windows plugin" + (added.size() == 1 ? "" : "s"), 5000);
    ui::msg::information(this, title, "New in " + used + ":\n" + names);
}

void MainWindow::showAudioProperties() {
    Preferences& p = prefs;
    std::string type, dev;
    int buffer;
    bool exclusive;
    int cores;
    {
        AudioPropertiesDialog d(this, p.driverType, device, p.bufferSize, p.wasapiExclusive, engine);
        d.lowLatencyBox->setChecked(p.lowLatencyMonitoring);
        if (!d.exec()) return;
        if (d.lowLatency != p.lowLatencyMonitoring) {
            p.lowLatencyMonitoring = d.lowLatency;
            p.save();
            util::debugf("audio: low-latency monitoring %s\n", d.lowLatency ? "on" : "off");
            showStatus(d.lowLatency ? "Low-latency monitoring: effects with look-ahead are left out on the tracks being "
                                      "recorded, while recording"
                                    : "Low-latency monitoring off",
                       5000);
        }
        type = d.driverType;
        dev = d.device;
        buffer = d.bufferSize;
        exclusive = d.exclusive;
        cores = d.cores;
    }
    bool coresChanged = cores != p.processingCores;
    if (coresChanged) {  // (takes effect when the device opens again: below)
        p.processingCores = engine.cores = cores;
        workers::setChosenCores(cores);
        p.save();
    }
    if (type != p.driverType || dev != device || buffer != p.bufferSize || exclusive != p.wasapiExclusive)
        setAudioDevice(type, dev, buffer, exclusive);
    else if (coresChanged && !device.empty())
        openDevice();
}

void MainWindow::setAudioDevice(const std::string& driverType, const std::string& dev, int bufferSize, bool exclusive) {
    // play through `dev` (a device id of `driverType`) with `bufferSize` frames (0 = driver default);
    // `exclusive` where the driver has it. Remembered for next time. Takes effect by reopening the
    // device, which stops playback.
    device = dev;
    Preferences& p = prefs;
    p.driverType = driverType;
    p.wasapiExclusive = exclusive;
    p.deviceName.clear();
    for (auto& d : audio::outputDevices(driverType))
        if (d.id == dev) p.deviceName = d.name;
    p.bufferSize = engine.blocksize = bufferSize;
    p.save();
    if (!device.empty()) openDevice();
}

void MainWindow::setBufferSize(int frames) { setAudioDevice(prefs.driverType, device, frames, prefs.wasapiExclusive); }

void MainWindow::setRefreshRate(int fps) {
    prefs.refreshFps = fps;
    prefs.save();
    App::get().setFrameRate(fps);
}

void MainWindow::setMeterOption(const std::string& attr, bool on) {
    if (attr == "level_meters") prefs.levelMeters = on;
    else prefs.loudnessMeters = on;
    prefs.save();
    applyMeterOptions();
}

void MainWindow::applyMeterOptions() {
    // switched-off meters are hidden, and the engine stops measuring for them
    bool levels = prefs.levelMeters, loudness = prefs.loudnessMeters;
    engine.metering->levelsOn = levels;
    engine.metering->loudnessOn = loudness;
    panel->setMetersVisible(levels, loudness);
}

void MainWindow::connectControls() {
    TransportBar* t = transport;
    t->addFileAction->triggered.connect([this] { addFiles(); });
    t->addAudioTrackAction->triggered.connect([this] { timeline->addTrack(Kind::Audio); });
    t->addMidiTrackAction->triggered.connect([this] { timeline->addTrack(Kind::Midi); });
    t->addReturnTrackAction->triggered.connect([this] { timeline->addTrack(Kind::Return); });
    t->captureAction->triggered.connect([this] { capture(); });
    t->keysAction->toggled.connect([this](bool on) {  // the computer keyboard as a piano (computer_keyboard.h)
        if (keyboard) keyboard->setOn(on);
        if (keysAction) keysAction->checked = on;
    });
    t->onGridChange = [this](const GridChange& c) {
        switch (c.what) {
        case GridChange::Grid: changeGrid(c.grid); break;
        case GridChange::Snap: changeGrid({}, c.snap); break;
        case GridChange::Finer: changeGrid({}, {}, true); break;
        case GridChange::Coarser: changeGrid({}, {}, false); break;
        case GridChange::Follow: setAutomationFollows(c.snap); break;
        }
    };
    t->gridActions["finer"]->triggered.connect([this] { changeGrid({}, {}, true); });
    t->gridActions["coarser"]->triggered.connect([this] { changeGrid({}, {}, false); });
    t->gridActions["snap"]->triggered.connect([this] { changeGrid({}, !project.timing.snapOn); });
    t->gridActions["fixed"]->triggered.connect(
        [this] { changeGrid(project.timing.fixedStep() ? 0.0 : 0.25); });  // (1/16: a quarter of a beat)
    t->playAction->triggered.connect([this] { togglePlay(); });
    t->recordAction->triggered.connect([this] { recording->toggle(); });
    t->recordButton->rightClicked.connect([this](ui::MouseEvent&) { recording->showSettings(); });
    t->startAction->triggered.connect([this] { goToStart(); });
    t->loopAction->toggled.connect([this](bool on) { setLooping(on); });
    timeline->loopChanged.connect([this] { syncLoop(); });
    timeline->loopRangeChosen.connect([this] { setLooping(true); });
    timeline->tracksChanged.connect([this] { checkFxWindows(); });
    timeline->editNotes.connect([this](ClipPtr clip) { openMidiEditor(clip); });
    timeline->exportMidi.connect([this](std::vector<ClipPtr> clips) { exportMidi(clips); });
    timeline->clipboardChosen.connect([this](std::string what) { useClipboard(what); });
    timeline->timeEdited.connect([this](std::string message) { showTimeEdit(message); });
    timeline->timingChanged.connect([this] { setTiming({}); });  // (a tempo change on the ruler)
    timeline->tempoRangeChanged.connect([this] { refreshTimingRows(); });  // (its row's Min, Max and Auto)
    t->bpmBox->valueChanged.connect([this](double v) { setTiming({v, {}, {}, {}}); });
    t->signatureBox->valueChanged.connect([this](double v) { setTiming({{}, (int)std::lround(v), {}, {}}); });
    t->unitBox->currentIndexChanged.connect([this, t](int) { setTiming({{}, {}, {}, {}, t->unitBox->currentDataInt(4)}); });
    t->beatsAction->toggled.connect([this](bool on) { setTiming({{}, {}, on, {}}); });
    t->tapButton->pressed.connect([this] { tap(); });  // on press, not release: the moment of the tap counts
    t->metronomeButton->setCheckedSilently(prefs.metronome);
    t->metronomeButton->toggled.connect([this](bool on) { setMetronomeOn(on); });
    t->metronomeButton->rightClicked.connect([this](ui::MouseEvent&) { showMetronomeSettings(); });
    applyMetronome();
    t->thirdsAction->toggled.connect([this](bool on) { setTiming({{}, {}, {}, on}); });
    for (ui::SpinBox* box : {t->bpmBox, t->signatureBox})  // after Enter, keys (Space, S, …) go back to the timeline
        box->editor()->returnPressed.connect([this] { timeline->setFocus(); });
    connectScale();
    t->showTiming(project.timing);
}

void MainWindow::tap() {
    // tap tempo: a tap from the Tap button (or T)
    double bpm = tapTempo.tap(plat::now());
    int taps = tapTempo.count();
    if (bpm <= 0) {
        showStatus("Tap tempo: keep tapping in time…", 3000);
        return;
    }
    transport->bpmBox->setValue(bpm);  // sets the project's tempo
    showStatus("Tap tempo: " + fmtG(bpm) + " BPM from " + std::to_string(taps) + " taps", 3000);
}

// --- metronome ------------------------------------------------------------------------------

void MainWindow::setMetronomeOn(bool on) {
    prefs.metronome = on;
    prefs.save();
    applyMetronome();
}

void MainWindow::applyMetronome() {
    // give the engine the metronome's sounds while it is on
    if (prefs.metronome) engine.setMetronome(metronomeSettings);
    else engine.setMetronome(std::nullopt);
}

void MainWindow::showMetronomeSettings() {
    MetronomeDialog d(this, metronomeSettings, [this](const metronome::MetronomeSettings&) { applyMetronome(); },
                      [this](const metronome::Click& c) { testClick(c); });
    d.exec();
}

void MainWindow::testClick(const metronome::Click& click) { metronome_dialog::testClick(engine, click); }

void MainWindow::setLooping(bool on) {
    // Loop on or off. Turned on with a time range selected, the loop becomes that range; with no
    // loop range yet, the whole arrangement (or 4 bars of an empty project).
    Timing& t = project.timing;
    if (on) {
        double s0, s1;
        if (timeline->selection.range) {
            timeline->setLoop(timeline->selection.range->start, timeline->selection.range->end);
        } else if (!t.loopRange(s0, s1)) {
            int64_t bars = std::llround(t.barSeconds(4) * engine.sr);  // (4 bars of the tempo map)
            int64_t end = project.endFrame();
            timeline->setLoop(0, end ? end : bars);
        }
    }
    t.looping = on;
    transport->showTiming(t);
    syncLoop();
    timeline->update();
}

void MainWindow::syncLoop() {
    // hand the loop range to the engine while looping (in frames, at its sample rate)
    const Timing& t = project.timing;
    int sr = engine.sr;
    double s0, s1;
    std::optional<audio::LoopRange> want;
    if (t.looping && t.loopRange(s0, s1)) want = audio::LoopRange{std::llround(s0 * sr), std::llround(s1 * sr)};
    auto have = engine.loop();
    bool same = (bool)want == (bool)have && (!want || (want->start == have->start && want->end == have->end));
    if (same) return;
    if (want) engine.setLoop(want->start, want->end);
    else engine.clearLoop();
}

void MainWindow::setTiming(const TimingChange& c) {
    // change the tempo, time signature or grid view; what is placed in the song follows as its timebase says
    // (model/timebase.h: Beats keeps its beats, Time its seconds)
    Timing& t = project.timing;
    bool meter = c.beatsPerBar || c.beatUnit;
    timebase::Before before = c.bpm || meter ? timebase::remember(project) : timebase::Before();
    if (c.bpm) t.bpm = *c.bpm;
    if (c.beatsPerBar) t.beatsPerBar = *c.beatsPerBar;
    if (c.beatUnit) t.beatUnit = timing::checkedUnit(*c.beatUnit, "the transport's time signature");
    if (!timebase::follow(project, before, engine.sr).empty()) timeline->relayout();
    if (c.beats) t.beats = *c.beats;
    if (c.thirds) t.thirds = *c.thirds;
    // (MIDI effects work in beats: an arpeggio follows the tempo; no changes: one on the ruler)
    bool none = !c.bpm && !meter && !c.beats && !c.thirds;
    if (none || c.bpm || meter)
        for (auto& clip : project.clips) {
            TrackPtr track = std::atomic_load(&clip->track);
            if (clip->kind == Kind::Midi && track && (!track->midiEffects.empty() || midi_effects::swings(*track)))
                timeline->prepareLive(clip);  // (swing too: it is in beats)
        }
    if (none || c.bpm || meter) {
        if (!tapTempo.tapping(plat::now())) retimeWarped();  // (warped audio follows the tempo; tapping: once it stops)
        for (auto& clip : project.clips) {  // (a sampler warped, its LFO synced or sliced on a grid: so do its notes)
            TrackPtr track = std::atomic_load(&clip->track);
            SamplerPtr s = track ? std::atomic_load(&track->sampler) : nullptr;
            if (clip->kind == Kind::Midi && s && track->playsSampler() && sampler::followsTempo(*s))
                timeline->prepareLive(clip);
        }
    }
    transport->showTiming(t);
    refreshTimingRows();  // (Auto: the range fits the changes now)
    timeline->update();
}

void MainWindow::retimeWarped() {
    // (after every tempo edit: setTiming, and noteChanges before each undo step, so the tempo lane's drags and
    // ramps re-render warped clips once the mouse is let go)
    project.timing.publish();  // (the map warped clips are prepared with: clip_hooks::tempoMap)
    if (warping::retime(project, engine.sr) > 0) timeline->relayout();
}

void MainWindow::startTimers() {
    App::get().setFrameRate(prefs.refreshFps);
    std::weak_ptr<bool> alive = alive_;
    frameId_ = App::get().onFrame([this, alive] {
        if (!alive.expired()) onFrame();
    });
    cpuTimer_ = App::get().every(config::CPU_REFRESH_MS, [this, alive] {
        if (!alive.expired()) onCpuTimer();
    });
}

void MainWindow::openFirstDevice() {
    // Open the driver type and device used last time (Options → Audio properties). If that driver
    // type is gone, use the first available one; if the device is gone, that type's default device.
    Preferences& p = prefs;
    auto available = audio::driverTypes();
    bool have = false;
    for (auto& d : available) have = have || d.key == p.driverType;
    if (!have && !available.empty()) p.driverType = available[0].key;
    device.clear();
    for (auto& d : audio::outputDevices(p.driverType))
        if (!p.deviceName.empty() && d.name == p.deviceName) {
            device = d.id;
            break;
        }
    if (device.empty()) device = audio::defaultOutput(p.driverType);
    if (!device.empty()) openDevice();
#ifdef _WIN32
    else if (available.empty())  // (no output device, or the Windows Audio service isn't running)
        showStatus("No audio output device found: projects can be edited but not heard. Connect one, then "
                   "Options → Audio properties…");
#else
    else if (available.empty())  // (none of ALSA, PulseAudio and JACK is installed, or none has an output)
        showStatus("No sound system found (ALSA, PulseAudio or JACK): projects can be edited but not heard. "
                   "Install one, then Options → Audio properties…");
#endif
    else showStatus("No audio output device found.");
}

// --- actions --------------------------------------------------------------------------------

void MainWindow::openDevice() {
    stop();
    int oldSr = engine.sr;
    bool unchanged = history && !hasUnsavedChanges();
    std::string error;
    if (engine.open(prefs.driverType, device, -1, prefs.wasapiExclusive, error)) {
        std::string kind = audio::driverLabel(engine.driverType) + (engine.exclusive ? " exclusive" : "");
        std::string buffer = !engine.blocksize ? "driver buffer" : std::to_string(engine.blocksize) + "-frame buffer";
        char lat[32];
        snprintf(lat, sizeof lat, "%.1f", engine.outputLatency() * 1000);
        showStatus(kind + ": " + engine.deviceName() + "  ·  " + std::to_string(engine.channels) + " outputs  ·  " +
                   std::to_string(engine.sr) + " Hz  ·  " + buffer + "  ·  " + lat + " ms output latency");
    } else {
        showStatus("Could not open the audio device: " + error + "  (Options → Audio properties…)");
    }
    if (engine.sr != oldSr && history) {  // (the clips were converted by the engine: the cursor and range follow)
        sampleRateChanged(oldSr, engine.sr);
        if (unchanged) savedSnapshot = snapshot();  // (a saved project stays saved: converting isn't an edit)
        history->reset(engine.sr);
    }
    panel->inputs = engine.isOpen() ? engine.inputCount() : 0;  // (an audio track's input chooser)
    panel->setChannels(engine.channels);
    applyMeterOptions();  // the engine's metering was recreated for the device
    timeline->relayout();
}

void MainWindow::addFiles() {
    for (auto& path : ui::files::openFiles(this, "Add audio or MIDI", "", config::FILE_FILTER))
        timeline->addFile(path, timeline->cursor);
}

void MainWindow::goToStart() {
    // both cursors to 0 s (Home)
    timeline->setCursor((int64_t)0);
    timeline->seek(0);
}

void MainWindow::togglePlay() {
    if (!engine.isOpen()) return;
    if (recording->active()) {  // stops recording, keeping the take
        recording->toggle();
    } else if (engine.playing) {
        engine.stop();  // the playback cursor stays where it stopped
    } else {
        timeline->seek(timeline->cursor);  // play always starts from the selection cursor
        resetMeters();
        engine.play();
    }
    transport->setPlaying(engine.playing);
}

void MainWindow::resetMeters() {
    // playback (or recording) starts: the loudest peaks and the clip lights count from now
    if (master->meter) master->meter->resetPeaks();
    for (auto& [track, meter] : panel->meters) {
        (void)track;
        if (meter) meter->resetPeaks();
    }
    for (auto& [group, meter] : panel->groupMeters) {
        (void)group;
        if (meter) meter->resetPeaks();
    }
}

// --- plugins --------------------------------------------------------------------------------

void MainWindow::openTrackPlugins(const TrackPtr& track) {
    if (track->frozen) {
        ui::msg::information(this, "Track frozen", track->name + " is frozen, so its plugins are locked.\n"
                                                                 "Unfreeze it first (right-click its row).");
        return;
    }
    showFxWindow({0, track.get()}, [this, track] {
        auto* w = new PluginsDialog(this, *plugins, "Effects: " + track->name, project, track, [this] { panel->rebuild(); });
        w->alsoOn = [this, track](const SlotPtr& slot) { return panel->addToSelected(track, slot); };
        return w;
    });
}

void MainWindow::openMasterPlugins() {
    showFxWindow({0, nullptr}, [this] {
        return new PluginsDialog(this, *plugins, "Effects: Master (outputs 1/2)", project, nullptr,
                                 [this] { master->refreshFx(*project.masterEffects); });
    });
}

void MainWindow::fillInstrumentMenu(ui::Menu& menu, const TrackPtr& track) {
    // a MIDI track's instruments to choose from (instrument_menu.h)
    instrument_menu::fillInstrumentMenu(&menu, this, *plugins, track, [this, track] { instrumentChosen(track); },
                                        [this](const TrackPtr& t) { openInstrumentPanel(t); });
}

void MainWindow::instrumentChosen(const TrackPtr& track) {
    // After a track's instrument changed: its row shows it; its instrument patch panel's window
    // closes if the panel isn't played any more.
    patch::ensureStates(project);
    panel->rebuild();
    auto it = fxWindows.find({1, track.get()});
    if (it != fxWindows.end() && !track->usesRack) it->second->close();
}

void MainWindow::openInstrumentPanel(const TrackPtr& track) {
    // a MIDI track's instrument patch panel (several instruments, each with its own volume)
    if (track->frozen) {
        ui::msg::information(this, "Track frozen", track->name + " is frozen, so its plugins are locked.\n"
                                                                 "Unfreeze it first (right-click its row).");
        return;
    }
    showFxWindow({1, track.get()}, [this, track] {
        return new InstrumentPanelDialog(this, *plugins, "Instruments: " + track->name, project, track,
                                         [this] { panel->rebuild(); });
    });
}

void MainWindow::editInstrument(const TrackPtr& track) {
    // open a MIDI track's instrument's own window, to configure it
    instrument_menu::editInstrument(this, *plugins, track, [this](const TrackPtr& t) { openInstrumentPanel(t); });
}

void MainWindow::showFxWindow(std::pair<int, const void*> key, const std::function<PluginsDialog*()>& make) {
    // The plugins window of a track (or the master), one per owner (a track's instrument patch
    // panel has its own). It doesn't block: the project can be played and edited while it is open.
    auto it = fxWindows.find(key);
    PluginsDialog* window;
    if (it == fxWindows.end()) {
        window = fxWindows[key] = make();
        // closed (Close, Esc, or by the program): forget it at once (it is deleted a moment later)
        std::weak_ptr<bool> alive = alive_;
        window->finished.connect([this, alive, key](int) {
            if (!alive.expired()) fxWindows.erase(key);
        });
    } else {
        window = it->second;
    }
    window->open();
    window->raiseWindow();
}

void MainWindow::checkFxWindows() {
    // After tracks changed: close the effects (and instrument patch panel) windows of tracks that
    // are gone or frozen (or not playing their panel); the others show their plugins afresh.
    project.forgetGoneTracks();  // (sidechains listening to a removed track)
    projectReplaced();           // (the MIDI effects and sampler windows of tracks that are gone)
    engine.metering->forgetGone(project.allTracks());
    patch::ensureStates(project);
    std::vector<PluginsDialog*> windows;
    for (auto& [key, w] : fxWindows) {
        (void)key;
        windows.push_back(w);
    }
    for (PluginsDialog* w : windows) {
        bool still = false;
        for (auto& [key, x] : fxWindows) {
            (void)key;
            still = still || x == w;
        }
        if (!still) continue;  // (closed meanwhile)
        const TrackPtr& owner = w->owner;
        if (!owner) w->refresh();
        else if ([&] { auto all = project.allTracks(); return std::find(all.begin(), all.end(), owner) == all.end(); }() || owner->frozen || (w->INSTRUMENTS && !owner->usesRack)) w->close();
        else w->refresh();
    }
}

void MainWindow::closeFxWindows() {
    std::vector<ui::Window*> windows;
    for (auto& [key, w] : fxWindows) {
        (void)key;
        windows.push_back(w);
    }
    for (auto& [key, e] : midiEditors) {
        (void)key;
        windows.push_back(e);
    }
    for (ui::Window* w : windows) w->close();
    fxWindows.clear();
    midiEditors.clear();
}

void MainWindow::openMidiEditor(const ClipPtr& clip) {
    // the MIDI editor of a clip (one per clip); like the FX windows it doesn't block anything
    auto it = midiEditors.find(clip.get());
    MidiEditor* editor;
    if (it == midiEditors.end()) {
        MidiEditor::TimelineLink link;
        link.cursor = [this] { return timeline->cursor; };
        link.setCursor = [this](int64_t f) { timeline->setCursor(f); };
        link.seek = [this](int64_t f) { timeline->seek(f); };
        link.editNotes = [this](ClipPtr c) { openMidiEditor(c); };
        auto edited = [this, key = clip.get()] { notesEdited(key); };
        editor = midiEditors[clip.get()] = new MidiEditor(this, clip, project, engine, edited, link);
        std::weak_ptr<bool> alive = alive_;
        const Clip* key = clip.get();
        editor->closed.connect([this, alive, key, editor] {
            if (alive.expired()) return;
            auto f = midiEditors.find(key);
            if (f != midiEditors.end() && f->second == editor) midiEditors.erase(f);
        });
    } else {
        editor = it->second;
    }
    editor->showWindow();
    editor->raiseWindow();
}

void MainWindow::notesEdited(const Clip* from) {
    timeline->relayout();  // the clip may have grown
    // linked MIDI clips (model.h): the other ones' editors show the notes they now have too
    auto it = midiEditors.find(from);
    if (it == midiEditors.end() || !it->second->clip->link) return;
    std::shared_ptr<ClipLink> link = it->second->clip->link;
    std::vector<MidiEditor*> others;
    for (auto& [key, editor] : midiEditors)
        if (key != from && editor->clip->link == link) others.push_back(editor);
    for (MidiEditor* editor : others) editor->reload();
}

void MainWindow::checkMidiEditors() {
    // close the editors of clips that are gone (deleted, split, cut) or locked (frozen track)
    std::vector<std::pair<const Clip*, MidiEditor*>> editors(midiEditors.begin(), midiEditors.end());
    for (auto& [key, editor] : editors) {
        const ClipPtr& clip = editor->clip;
        TrackPtr track = clip ? std::atomic_load(&clip->track) : nullptr;
        if (!project.clips.contains(clip) || (track && track->frozen)) {
            midiEditors.erase(key);
            editor->close();
        } else {
            editor->updateTitle();
        }
    }
}

void MainWindow::restorePlugins() {
    // load the plugins of a just-opened project; say which could not be loaded
    std::vector<SlotPtr> slots = project.pluginSlots();
    std::vector<std::string> failed;
    if (!slots.empty()) failed = plugins->restore(slots);
    patch::ensureStates(project);
    panel->rebuild();
    master->refreshFx(*project.masterEffects);
    if (!failed.empty()) {
        std::string names;
        for (auto& f : failed) names += (names.empty() ? "" : "\n") + f;
        ui::msg::warning(this, "Plugins missing",
                         "These plugins could not be loaded, so they are silent for now:\n\n" + names +
                             "\n\nTheir settings are kept: saving the project does not lose them.");
    }
}

// --- projects -------------------------------------------------------------------------------

Json MainWindow::snapshot() {
    // the project without plugin settings (project_io::describeSlot); a plugin whose settings
    // changed counts as a change instead (plugins->changed())
    return project_io::describe(project, engine.sr, master->settings(), nullptr, false);
}

void MainWindow::markSaved() {
    // remember the project as it is now, to tell later whether it has unsaved changes
    savedSnapshot = snapshot();
    plugins->saved();
    std::string name = projectPath.empty() ? "Untitled" : util::baseName(projectPath);
    setTitle(name + " — Exbit");
}

bool MainWindow::hasUnsavedChanges() { return plugins->changed() || !(snapshot() == savedSnapshot); }

bool MainWindow::confirmDiscard() {
    // True if it is fine to replace or close the current project (saving it first if asked). A recording
    // in progress is finished first: its take goes into the project, so it counts as a change to save.
    if (recording->active()) recording->toggle();
    if (!hasUnsavedChanges()) return true;
    int answer = ui::msg::question(this, "Unsaved changes", "Save the changes to the current project?",
                                   ui::msg::Save | ui::msg::Discard | ui::msg::Cancel);
    if (answer == ui::msg::Save) return saveProject();
    return answer == ui::msg::Discard;
}

void MainWindow::stop() {
    if (recording->active()) recording->toggle();  // (keeping the take)
    engine.stop();
    transport->setPlaying(false);
}

void MainWindow::resetView() {
    // after the project was replaced: empty selection, both cursors at 0, everything redrawn
    timeline->selection.clear();
    timeline->setCursor((int64_t)0);
    timeline->seek(0);
    timeline->relayout();
    timeline->tracksChanged.emit();
    transport->showTiming(project.timing);
    refreshTimingRows();
}

void MainWindow::newProject() {
    if (!confirmDiscard()) return;
    std::string start = templates::defaultTemplate(prefs);  // (Options → New projects start from)
    if (!start.empty() && startFromTemplate(start)) return;
    stop();
    closeFxWindows();  // they belong to the project being replaced
    plugins->closeEditor();
    project.clear();
    master->apply(project_io::MasterSettings{});
    master->refreshFx({});
    projectPath.clear();
    projectReplaced();
    resetView();
    history->reset(engine.sr);
    autosaver->clear();
    markSaved();
}

void MainWindow::openRecent(const std::string& path) {
    if (confirmDiscard()) openProjectFile(path);
}

void MainWindow::newFromTemplate(const std::string& path) {
    if (confirmDiscard()) startFromTemplate(path);
}

bool MainWindow::startFromTemplate(const std::string& path) {
    // a new, untitled project made from the template at `path` (Save asks where to keep it)
    if (!openProjectFile(path)) return false;
    projectPath.clear();
    project.createdUnixSeconds = (int64_t)std::time(nullptr);  // (a new project: made now, not when the template was)
    markSaved();  // (unchanged until you change it: nothing to ask about)
    setTitle("Untitled — Exbit");
    showStatus("New project from the template " + util::stem(path), 5000);
    return true;
}

void MainWindow::openProject() {
    if (!confirmDiscard()) return;
    std::string path = ui::files::openFile(this, "Open project", "",
                                           "Exbit project (*.json " + collect::archivePatterns() + ");;Exbit archive (" +
                                               collect::archivePatterns() + ")");
    if (path.empty()) return;
    if (collect::isArchive(path)) project_safety::openArchive(*this, path);  // (unpacked first)
    else openProjectFile(path);
}

bool MainWindow::openProjectFile(const std::string& path) {
    stop();
    closeFxWindows();  // they belong to the project being replaced
    plugins->closeEditor();
    project_io::MasterSettings m;
    std::string error;
    bool loaded;
    {  // (its samples decoded on every core: a progress window if that takes a while)
        ui::ProgressDialog busy(this, "Opening", "Reading the project's samples\xe2\x80\xa6", false);
        loaded = project_io::loadProject(path, project, engine.sr, m, error, project_safety::progressIn(busy));
        if (busy.isWindowShown()) busy.hideWindow();
    }
    if (!loaded) {
        ui::msg::warning(this, "Cannot open project", path + "\n\n" + error);
        return false;
    }
    master->apply(m);
    // (the program's own spelling, as the file chooser gives it: absolute, '/' also on Windows, where a command
    // line gives "C:\…" and the title would show it whole)
    projectPath = util::absolutePath(path);
    if (projectPath != path) util::debugf("project: %s opened as %s\n", path.c_str(), projectPath.c_str());
    projectReplaced();
    resetView();
    restorePlugins();
    history->reset(engine.sr);
    autosaver->clear();
    markSaved();
    templates::remember(prefs, path);  // (File → Open recent; not backups, recovery files, templates)
    showStatus("Opened " + projectPath, 5000);
    project_safety::showMissing(*this);  // (samples not where it names them: find them, or leave them silent)
    return true;
}

void MainWindow::openBackup() {
    // File → Open a backup…: one of the versions kept each time the project was saved over
    // (project_io::keepBackup). It opens as unsaved changes to the project: Save makes it the project
    // again (keeping the version it replaces as a backup in turn).
    std::string path = projectPath;
    if (path.empty() || project_io::backups(path).empty()) {
        ui::msg::information(this, "Open a backup",
                             "There are no backups of this project yet. Each time you save over a project, the "
                             "version it replaces is kept in a folder next to it (“<name>_backups”), the newest 10.");
        return;
    }
    if (!confirmDiscard()) return;
    std::string chosen = ui::files::openFile(this, "Open a backup", project_io::backupsFolder(path),
                                             "Exbit project (*.json)");
    if (chosen.empty() || !openProjectFile(chosen)) return;
    projectPath = path;
    savedSnapshot = Json();  // (unsaved: it differs from the project's file)
    setTitle(util::baseName(path) + " (from a backup) — Exbit");
    showStatus("Opened the backup " + util::baseName(chosen) + ": save to make it the project again", 10000);
}

void MainWindow::recovered(const std::string& original) {
    projectPath = original;
    savedSnapshot = Json();  // (unsaved: it differs from the file, if there is one)
    std::string name = projectPath.empty() ? "Untitled" : util::baseName(projectPath);
    setTitle(name + " (recovered) — Exbit");
    showStatus("Recovered: save it to keep it (File → Save)", 10000);
}

bool MainWindow::saveProject() {
    // save to the current file (asks for one the first time); true if saved
    if (projectPath.empty()) return saveProjectAs();
    return saveProjectFile(projectPath);
}

bool MainWindow::saveProjectAs() {
    std::string path = ui::files::saveFile(this, "Save project", projectPath.empty() ? "Untitled.json" : projectPath,
                                           "Exbit project (*.json)");
    if (path.empty()) return false;
    if (util::extension(path) != ".json") path += ".json";
    return saveProjectFile(path);
}

bool MainWindow::saveProjectFile(const std::string& path, const std::function<void(int, int)>& progress) {
    if (!engine.isOpen())  // with a device open, the audio thread keeps the plugins processing (in sync)
        for (auto& slot : project.pluginSlots()) plugin_audio::syncState(*slot, engine.sr);
    std::string error, backup;
    if (!project_io::keepBackup(path, backup, error)) {  // (the version being replaced: File → Open a backup…)
        int answer = ui::msg::question(this, "Cannot keep a backup",
                                       "The version of " + path + " being replaced couldn't be kept as a backup:\n\n" +
                                           error + "\n\nSave over it anyway?");
        if (answer != ui::msg::Yes) return false;
    }
    error.clear();
    if (!project_io::saveProject(path, project, engine.sr, master->settings(), error, progress)) {
        ui::msg::warning(this, "Cannot save project", path + "\n\n" + error);
        return false;
    }
    projectPath = path;
    autosaver->clear();  // (saved: the recovery copy isn't needed)
    markSaved();
    templates::remember(prefs, path);
    showStatus("Saved " + path + " (audio in " + project_io::audioFolder(path) + ")", 5000);
    return true;
}

// --- periodic updates -----------------------------------------------------------------------

void MainWindow::onFrame() {
    // playhead, time display, level meters and follow-scrolling
    Timeline* tl = timeline;
    syncLoop();  // (also after opening a project or changing the sample rate)
    engine.idle();
    if (engine.deviceLost() != deviceLostShown_) {  // (the device unplugged, or back)
        deviceLostShown_ = !deviceLostShown_;
        util::debugf("audio: the device is %s\n", deviceLostShown_ ? "lost" : "back");
        showStatus(deviceLostShown_ ? "The audio device went away (unplugged, or its sound server stopped). Plug it "
                                      "back in, or choose another: Options \xe2\x86\x92 Audio properties\xe2\x80\xa6"
                                    : "The audio device is back: " + engine.deviceName());
    }
    if (midiInput) midiInput->poll();  // (what the MIDI keyboards played)
    midiFrame();  // (External synths, Sync)
    if (recording) recording->tick();  // (the MIDI effects' notes played since, the loop's passes, monitoring)
    automation::resolve(project);  // (parameters of plugins just loaded)
    modulation::resolve(project);  // (and the modulation cables' parameters)
    {  // writing automation (automation_write.h): touches idle long enough end; the curves being written show
        automation::Recorder& writer = automation::recorder();
        writer.tick();
        if (writer.writes() != shownWrites_) {
            shownWrites_ = writer.writes();
            tl->update();
        }
    }
    plugins->idle(project.pluginSlots());  // (values to their windows, latency changes)
    int64_t heard = engine.heardPos();  // (behind the engine's position while plugins hold sound back)
    tl->setPlayhead(heard);
    if (!tl->recording.empty()) {  // the takes grow on their lanes
        auto tops = tl->trackTops();
        for (auto& taking : tl->recording) {
            auto it = tops.find(taking.first.get());
            if (it != tops.end()) tl->update(Rect(0, it->second, tl->width(), taking.first->height));
        }
    }
    overview->refresh();
    int64_t shown = engine.playing ? heard : tl->cursor;  // stopped: where Play will start
    transport->setTime((double)shown / engine.sr, project.timing);
    std::vector<float> masterPeaks;
    std::map<const Track*, std::vector<float>> trackPeaks, meanSquares;
    engine.metering->takePeaks(masterPeaks, trackPeaks, &meanSquares);  // also queues audio for LUFS
    if (prefs.levelMeters) {
        auto squaresOf = [&](const Track* key) {  // (the meters' RMS bars)
            auto it = meanSquares.find(key);
            return it != meanSquares.end() ? &it->second : nullptr;
        };
        master->meter->push(masterPeaks);
        master->meter->pushRms(squaresOf(audio::MASTER));
        // a group's tracks together: the loudest (a group without a bus, or a plain one the mixer skips)
        auto loudestOf = [&](const Group* group, LevelMeter* meter) {
            std::vector<const std::vector<float>*> peaks, squares;
            for (auto& t : routing::inside(project, group)) {
                auto it = trackPeaks.find(t.get());
                if (it != trackPeaks.end()) peaks.push_back(&it->second);
                squares.push_back(squaresOf(t.get()));  // (its RMS bar: its loudest track's)
            }
            if (peaks.empty()) {
                meter->push(nullptr);
            } else {
                std::vector<float> loudest = level_meter::loudest(peaks);
                meter->push(&loudest);
            }
            std::vector<float> loudestSquares = level_meter::loudest(squares);
            meter->pushRms(loudestSquares.empty() ? nullptr : &loudestSquares);
        };
        for (auto& [track, meter] : panel->meters) {
            if (track->busOf && routing::isPlain(*track)) {
                loudestOf(track->busOf, meter);
                continue;
            }
            auto it = trackPeaks.find(track.get());
            meter->push(it != trackPeaks.end() ? &it->second : nullptr);  // null (silent) lets the meter fall back
            meter->pushRms(squaresOf(track.get()));
        }
        for (auto& [group, meter] : panel->groupMeters) loudestOf(group.get(), meter);
    }
    panel->showMixerValues(engine.playing ? std::optional<int64_t>(heard) : std::nullopt, engine.sr);
    if (engine.playing) followPlayhead();
}

void MainWindow::followPlayhead() {
    Timeline* tl = timeline;
    ui::ScrollBar* hbar = scroll->horizontalScrollBar();
    int viewW = scroll->viewport()->width();
    if (tl->playheadX > tl->width() - viewW) tl->relayout();  // grow the timeline as playback runs past the end
    if (transport->followAction->isChecked()) {
        // Once the playhead reaches the middle of the view, the view moves with it and keeps it
        // centred; if it is off-screen (e.g. after a seek) the view jumps to centre it.
        int left = hbar->value(), centre = viewW / 2;
        if (tl->playheadX > left + centre || tl->playheadX < left) hbar->setValue(tl->playheadX - centre);
    }
}

void MainWindow::onCpuTimer() {
    // CPU monitor and LUFS readouts, a few times a second (and MIDI editors whose clip went)
    if (!midiEditors.empty()) checkMidiEditors();
    project.purgeRetired(plat::now());  // (plugins of removed slots, once the audio thread let go)
    std::optional<std::pair<double, double>> load;
    double cur = 0, avg = 0;
    if (engine.isOpen() && engine.cpu.read(config::CPU_AVERAGE_SECONDS, cur, avg)) load = std::make_pair(cur, avg);
    cpuMonitor->showLoad(load, engine.cpu.late);
    feedDiscordPresence(load ? load->second : -1);
    {  // each track's share of a core (the monitor's tooltip; the rows' bars: Options → CPU per track)
        std::vector<const Track*> known;
        for (auto& t : *project.tracks) known.push_back(t.get());
        auto shares = engine.trackCpu.read(known);
        if (load) {
            std::vector<std::pair<std::string, double>> top;
            for (auto& s : shares) {
                if (top.size() == 5 || s.second < 0.001) break;
                for (auto& t : *project.tracks)
                    if (t.get() == s.first) top.emplace_back(t->name, s.second);
            }
            cpuMonitor->showTracks(top);
        }
        if (prefs.trackCpu && load)
            for (auto& kv : panel->frames) {
                double share = 0;
                for (auto& s : shares)
                    if (s.first == kv.first.get()) share = s.second;
                kv.second->setLoad(share);
            }
    }
    if (!prefs.loudnessMeters) return;
    auto& metering = *engine.metering;
    metering.updateLoudness();
    double m, s, i;
    metering.read(audio::MASTER, m, s, i);
    master->loudness->showValues(m, s, i);
    master->showTruePeak(metering.truePeakDb());
    for (auto& [track, display] : panel->loudness) {
        metering.read(track.get(), m, s, i);
        display->showValues(m, s, i);
    }
}

// --- window events --------------------------------------------------------------------------

void MainWindow::resized() {
    ui::Window::resized();
    updateMedia();
    if (explorerDock) fitExplorerWidth();  // (its chosen width kept for when the window is wide again)
    if (timeline) timeline->relayout();
}

bool MainWindow::requestClose() {
    if (closing) return true;
    if (askBeforeClosing && !confirmDiscard()) return false;
    closing = true;
    beforeClose();
    autosaver->close();  // (closing normally: nothing to recover)
    closeFxWindows();
    plugins->closeEditor();
    plugins->stopFindingKinds();
    engine.close();
    App::get().quit();
    return true;
}
