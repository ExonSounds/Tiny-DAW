// Warping in the window and in the terminal (ui/warp_dialog.h, ui/timeline_warp.cpp):
//   - the timeline: a warped clip in a tall lane shows its markers; one dragged (snapped to the grid, during
//     playback), a transient double-clicked into a marker, a marker right-clicked away; painted;
//   - Warp from here (the clip menu); Options → Warp new audio files (a "128bpm" file dropped in: warped);
//   - the main window's tempo change re-renders warped clips (twice as long at half the tempo), undo puts it back;
//   - exbit --tui: Warp… is in the clip's F2 menu; its dialog drawn as text, the markers' list moved by ←/→
//     (by the grid), Del removing one, OK applying it.
#include <cmath>
#include <thread>
#include <chrono>
#include <fstream>
#include <functional>
#include <string>
#include <vector>

#include "audio/engine.h"
#include "audio/sound_file.h"
#include "check.h"
#include "config.h"
#include "core/util.h"
#include "gfx/image.h"
#include "model/history.h"
#include "model/warping.h"
#include "platform/platform.h"
#include "tui/app.h"
#include "tui/screen.h"
#include "ui/clip_painter.h"
#include "ui/main_window.h"
#include "ui/theme.h"
#include "ui/timeline.h"
#include "ui/toolkit/menu.h"
#include "ui/toolkit/widget.h"
#include "ui/warp_dialog.h"

using namespace tui;

namespace {

const int SR = 48000;
const std::string TMP = "build/test-ui-warp";

// clicks every beat of `bpm` over a quiet tone
std::shared_ptr<AudioBuffer> clicks(double bpm, double seconds) {
    auto b = std::make_shared<AudioBuffer>();
    int64_t n = (int64_t)(seconds * SR);
    b->samples.assign((size_t)n * 2, 0.0f);
    for (int64_t i = 0; i < n; i++) b->samples[(size_t)(2 * i)] = b->samples[(size_t)(2 * i + 1)] = 0.05f * (float)std::sin(i * 0.03);
    for (double t = 0; t < seconds; t += 60.0 / bpm) {
        int64_t at = (int64_t)(t * SR);
        for (int64_t i = 0; i < SR / 100 && at + i < n; i++) {
            float v = 0.8f * (float)std::exp(-(double)i / (SR * 0.002)) * ((i * 7919) % 13 < 6 ? 1.0f : -1.0f);
            b->samples[(size_t)(2 * (at + i))] += v;
            b->samples[(size_t)(2 * (at + i) + 1)] += v;
        }
    }
    return b;
}

ui::MouseEvent mouse(double x, double y, int button = plat::LeftButton, int mods = 0) {
    ui::MouseEvent e;
    e.pos = Point((int)x, (int)y);
    e.globalPos = e.pos;
    e.button = button;
    e.buttons = button;
    e.mods = mods;
    return e;
}

ui::Action* actionStarting(const ui::Menu& m, const std::string& prefix) {
    for (auto& a : m.actions())
        if (a->text.compare(0, prefix.size(), prefix) == 0) return a.get();
    return nullptr;
}

Image paintScroll(ui::Widget& w) {
    ui::App::get().doLayouts();
    Image img(std::max(1, w.width()), std::max(1, w.height()), theme::c("window"));
    Painter p(img);
    w.paintTree(p, w.rect());
    return img;
}

Key key(int k, int mods = 0, std::string text = "") { return Key{k, mods, std::move(text)}; }

}  // namespace

static void testTimeline() {
    Project project;
    audio::Engine engine{project};
    engine.sr = SR;
    engine.offline = true;
    clip_hooks::tempoMap = [&project] { return project.timing.audioMap(); };
    project.timing.publish();
    ui::ScrollArea scroll;
    scroll.resize(1200, 420);
    Timeline* tl = scroll.makeContent<Timeline>(project, engine, &scroll);
    auto t = project.addTrack(Kind::Audio);
    t->height = 140;  // (taller than a strip: markers shown)
    ClipPtr c = makeAudioClip("Loop", clicks(100, 4.8), SR, SR);
    c->track = t;
    c->start = SR;  // (1 s: x 100 at 100 px/s)
    c = warping::rewarped(c, true, warp::Mode::Beats, warp::TRANSIENTS, 100, nullptr, 0, SR);
    c->track = t;
    project.addClip(c);
    tl->relayout();
    CHECK_NEAR(tl->pps, 100, 1e-9);
    auto body = tl->envelopeBody(*c);
    CHECK(body && clip_painter::warpShown(*c, *body));
    auto xs = clip_painter::warpMarkerXs(*c, *body, tl->pps, SR);
    CHECK(xs.size() == 2);  // (its start and its end)
    if (xs.size() != 2 || !body) return;
    CHECK_NEAR(xs[1].second, body->x + 400, 1.5);  // (8 beats of 100 BPM at 120: 4 s)
    float handleY = body->y + 2;
    CHECK(tl->warpClipAt(PointF(xs[1].second - 1, handleY)) == c);
    CHECK(tl->warpMarkerAt(c, PointF(xs[1].second - 1, handleY)) == -1);  // (at its end: the end's and the fade's)
    // double-click a transient's tick (its 5th click, beat 4: 2 s in): a marker there (nothing moves)
    auto ticks = clip_painter::transientXs(*c, *body, tl->pps, SR);
    CHECK(ticks.size() >= 6);
    if (ticks.size() < 6) return;
    CHECK_NEAR(ticks[4].second, body->x + 200, 1.5);
    auto e = mouse(ticks[4].second, handleY + 1);
    tl->mouseDoubleClick(e);
    CHECK(c->warpMarkers && c->warpMarkers->size() == 3);
    CHECK(std::llabs(c->frames() - 4 * SR) < SR / 100);
    xs = clip_painter::warpMarkerXs(*c, *body, tl->pps, SR);
    CHECK(xs.size() == 3 && tl->warpMarkerAt(c, PointF(xs[1].second, handleY)) == 1);
    CHECK(tl->warpMarkerAt(c, PointF(xs[1].second, body->y + 60)) == -1);  // (below the handles: the clip's body)
    // dragged 50 px left while it plays (snapped: the song's beat 3 of the clip; its end stays on beat 8)
    engine.pos = c->start;
    engine.play();
    std::vector<float> out(256 * 2);
    e = mouse(xs[1].second, handleY);
    CHECK(tl->mousePress(e));
    CHECK(std::holds_alternative<WarpDrag>(tl->drag));
    for (int step = 1; step <= 10; step++) {
        e = mouse(xs[1].second - 5 * step, handleY);
        tl->mouseMove(e);
        engine.callback(nullptr, out.data(), 256);
    }
    tl->mouseRelease(e);
    engine.stop();
    CHECK(std::holds_alternative<std::monostate>(tl->drag));
    CHECK(c->warpMarkers && c->warpMarkers->size() == 3);
    if (c->warpMarkers && c->warpMarkers->size() == 3) CHECK_NEAR((*c->warpMarkers)[1].beat, 3.0, 1e-6);
    CHECK(std::llabs(c->frames() - 4 * SR) < SR / 100);
    // right-click it: gone again
    xs = clip_painter::warpMarkerXs(*c, *body, tl->pps, SR);
    if (xs.size() == 3) {
        e = mouse(xs[1].second, handleY, plat::RightButton);
        tl->contextMenu(e);
        CHECK(c->warpMarkers->size() == 2);
    }
    // painted (its markers and transients) without trouble
    Image img = paintScroll(scroll);
    CHECK(img.width() == 1200);
    util::makeDirs("build/ui-samples/timeline2");
    savePng("build/ui-samples/timeline2/warp-markers.png", img);
    // the clip menu: Warp…, Warp from here (on a warped clip only)
    tl->selection.selectOnly(c);
    ui::Menu menu;
    tl->fillMenu(menu, Point((int)(body->x + 160), (int)(body->y + 30)));
    CHECK(actionStarting(menu, "Warp…") && actionStarting(menu, "Warp…")->enabled);
    ui::Action* here = actionStarting(menu, "Warp from here");
    CHECK(here && here->enabled);
    // Warp from here at 1.6 s into the clip (2.6 s on the timeline): that audio onto the nearest bar (2 s)
    tl->warpFromHere(c, SR + (int64_t)(1.6 * SR));
    CHECK(c->warpMarkers && c->warpMarkers->size() >= 2);
    bool onBar = false;
    for (auto& m : *c->warpMarkers)
        if (std::llabs(warping::frameOfBeat(*c, m.beat, SR) - 2 * SR) < 2) onBar = true;
    CHECK(onBar);
    // Warp… through the dialog: Tones, an octave up, warped markers kept; then off again
    WarpDialog::testRun = [](WarpDialog& d) {
        CHECK(d.warpBox->isChecked() && d.markerRows().size() >= 2);
        d.modeBox->setCurrentIndex(1);
        d.semitones->setValue(12);
        return true;
    };
    tl->warpSelected();
    ClipPtr tones = project.clips[0];
    CHECK(tones != c && tones->warp && tones->warpMode == warp::Mode::Tones && tones->pitch == 12);
    CHECK(tones->warpMarkers && c->warpMarkers && *tones->warpMarkers == *c->warpMarkers && tones->start == c->start);
    WarpDialog::testRun = [](WarpDialog& d) {
        d.warpBox->setChecked(false);
        d.semitones->setValue(0);
        return true;
    };
    tl->selection.selectOnly(tones);
    tl->warpSelected();
    WarpDialog::testRun = nullptr;
    ClipPtr plain = project.clips[0];
    CHECK(!plain->warp && plain->pitch == 0 && std::llabs(plain->frames() - (int64_t)(4.8 * SR)) < SR / 50);
    // Options → Warp new audio files: a "128bpm" file comes in warped at 128
    util::makeDirs(TMP);
    auto loop = clicks(128, 60.0 / 128 * 8);
    std::string error, path = TMP + "/Drums 128bpm.wav";
    CHECK(writeWav(path, loop->samples.data(), (size_t)loop->frames(), 2, SR, SampleFormat::Pcm16, error));
    tl->warpNewFiles = true;
    ClipPtr dropped = tl->loadClip(path);
    CHECK(dropped && dropped->warp && dropped->sourceBpm == 128);
    tl->warpNewFiles = false;
    ClipPtr asIs = tl->loadClip(path);
    CHECK(asIs && !asIs->warp && asIs->sourceBpm == 0);
    clip_hooks::tempoMap = nullptr;
}

static void testMainWindowAndTui() {
    ui::App& app = ui::App::get();
    app.paintWindows = false;
    auto* w = new MainWindow();
    w->resizeWindow(1280, 800);
    w->showWindow();
    w->askBeforeClosing = false;
    int sr = w->engine.sr;
    Project& p = w->project;
    w->setTiming({120.0, {}, {}, {}});
    auto t = p.addTrack(Kind::Audio);
    ClipPtr c = makeAudioClip("Loop", clicks(100, 4.8), sr, sr);
    c->track = t;
    c->start = 2 * sr;
    c = warping::rewarped(c, true, warp::Mode::Beats, warp::TRANSIENTS, 100, nullptr, 0, sr);
    c->track = t;
    p.addClip(c);
    w->timeline->relayout();
    w->noteChanges();
    CHECK(std::llabs(c->frames() - 4 * sr) < sr / 100);
    // the tempo halved: twice as long, its start kept on its beat (a new project's timebase is Beats: beat 4 is
    // at 4 s now); undo: back, without rendering again
    CHECK(p.timing.timebaseBeats);
    w->setTiming({60.0, {}, {}, {}});
    CHECK(std::llabs(c->frames() - 8 * sr) < sr / 100 && c->start == 4 * sr);
    w->noteChanges();
    w->undo();
    CHECK(p.timing.bpm == 120 && std::llabs(c->frames() - 4 * sr) < sr / 100);
    auto data = c->data;
    w->noteChanges();
    CHECK(c->data == data);
    // a ramp (the tempo lane's): 120 at bar 1 slowing to 60 at bar 3: rendered again, longer than at 120 and
    // shorter than at 60, its 8 beats (4.8 s of clicks at 100 BPM) still spanning 8 of the song's beats
    {
        int64_t frames = c->frames();
        p.timing.ramp = true;
        p.timing.changes = {TempoChange{3, 60.0, 4, 0, false}};
        w->noteChanges();
        CHECK(c->data != data && c->frames() > frames + sr / 10 && c->frames() < 8 * sr);
        double startBeat = p.timing.beatAt((double)c->start / sr), endBeat = p.timing.beatAt((double)c->end() / sr);
        CHECK_NEAR(endBeat - startBeat, 8.0, 0.02);
        w->undo();
        CHECK(!p.timing.ramp && p.timing.changes.empty() && std::llabs(c->frames() - 4 * sr) < sr / 100);
    }
    // the terminal: F2 on the clip lists Warp…; the dialog as text, its markers' list worked by keys
    Front f(*w);
    Screen screen(120, 40);
    w->timeline->selection.selectOnly(c);
    w->timeline->setCursor(c->start + sr);
    f.pane = Front::Pane::Timeline;
    f.key(key(plat::Key_F2));
    f.draw(screen);
    CHECK(screen.lines().find("Warp…") != std::string::npos && screen.lines().find("Warp from here") != std::string::npos);
    f.key(key(plat::Key_Escape));
    bool sawDialog = false, moved = false, removed = false;
    double gridBeats = w->timeline->gridStepAt(w->timeline->cursor, true);
    app.nestedLoop = [&](const std::function<bool()>& done) {
        WarpDialog* dlg = nullptr;
        for (auto* win : app.windows())
            if (auto* d = dynamic_cast<WarpDialog*>(win)) dlg = d;
        if (!dlg) return;
        f.draw(screen);
        std::string text = screen.lines();
        sawDialog = text.find("Warp") != std::string::npos && text.find("beat") != std::string::npos &&
                    text.find("Recorded at") != std::string::npos;
        util::makeDirs("build/ui-samples/tui");
        std::ofstream("build/ui-samples/tui/warp-dialog.txt") << text;
        dlg->markerList->setFocus();
        dlg->markerList->setCurrentRow(1);
        double was = dlg->markerRows()[1].beat;
        f.key(key(plat::Key_Left));
        f.key(key(plat::Key_Left));
        f.key(key(plat::Key_Right));
        moved = std::fabs(dlg->markerRows()[1].beat - (was - gridBeats)) < 1e-9;
        // (a third marker to remove: Warp from here at the cursor, then Del on it)
        dlg->fromHere();
        size_t n = dlg->markerRows().size();
        dlg->markerList->setFocus();
        f.key(key(plat::Key_Delete));
        removed = dlg->markerRows().size() == n - 1;
        dlg->markerList->setCurrentRow(1);
        dlg->accept();
        for (int i = 0; i < 20 && !done(); i++) {
            app.advanceTimers(0.05);
            app.tick();
        }
    };
    w->timeline->warpSelected();
    app.nestedLoop = nullptr;
    CHECK(sawDialog && moved && removed);
    ClipPtr after = p.clips[0];
    CHECK(after != c && after->warp && after->warpMarkers);
    w->close();
    app.collectGarbage();
}

// Tap tempo while playing: the taps change the tempo at once, but a warped clip is rendered again only once the
// tapping stops (a render on every tap, on the GUI thread, froze the window and the taps queued behind it)
static void testTapWhilePlaying() {
    ui::App& app = ui::App::get();
    auto* w = new MainWindow();
    w->askBeforeClosing = false;
    int sr = w->engine.sr;
    Project& p = w->project;
    w->setTiming({120.0, {}, {}, {}});
    auto t = p.addTrack(Kind::Audio);
    ClipPtr c = makeAudioClip("Loop", clicks(100, 4.8), sr, sr);
    c->track = t;
    c = warping::rewarped(c, true, warp::Mode::Complex, warp::TRANSIENTS, 100, nullptr, 0, sr);
    c->track = t;
    p.addClip(c);
    w->noteChanges();
    w->engine.play();
    auto data = c->data;
    bool kept = true;
    for (int i = 0; i < 4; i++) {
        w->tap();
        w->noteChanges();  // (the history timer, between taps)
        kept = kept && c->data == data;
        std::this_thread::sleep_for(std::chrono::milliseconds(250));
    }
    CHECK(kept && std::fabs(p.timing.bpm - 240) < 20);
    std::this_thread::sleep_for(std::chrono::duration<double>(config::TAP_RESET_S));
    w->noteChanges();  // (tapping over: rendered for the tapped tempo)
    CHECK(c->data != data && std::llabs(c->frames() - (int64_t)(8 * 60.0 / p.timing.bpm * sr)) < sr / 50);
    w->engine.stop();
    w->close();
    app.collectGarbage();
}

int main() {
    plat::initWithoutDisplay();
    ui::App::get().initHeadless();
    theme::setSkin();
    util::makeDirs(TMP);
    settings::setPath(TMP + "/settings.json");
    testTimeline();
    testMainWindowAndTui();
    testTapWhilePlaying();
    TEST_MAIN_END();
}
