// Warped audio clips in the project (see warping.h)
#include "model/warping.h"

#include <algorithm>
#include <cmath>

#include "audio/workers.h"
#include "core/util.h"
#include "model/clip_envelope.h"

namespace warping {

namespace {

int64_t rounded(double x) { return (int64_t)std::llround(x); }

// where a warped clip's data must start (a timeline frame) for its window's first frame, source frame
// `windowSrc`, to play at its start on its beat of `map` (reversed: where it starts now)
int64_t originFor(const Clip& c, double windowSrc, const TempoMap& map, int sr) {
    if (c.reverse) return c.start - c.offset;
    warp::BeatMap bm(markersOf(c), bpmOf(c), c.sourceSr);
    double b0 = map.beatAt((double)c.start / sr) - bm.beatAt(windowSrc);
    return rounded(map.secondsAt(b0) * sr);
}

// c's window (from `old`, as `before` placed it on its source) moved onto its data as `after` places it
void remap(Clip& c, const Clip& old, const Frames& before, const Frames& after, int sr) {
    auto f = [&](double d) { return after.toData(before.toSource(d)); };
    double off = (double)std::min<int64_t>(old.offset, (int64_t)before.length);
    double o2 = f(off);
    c.offset = std::max<int64_t>(0, rounded(o2));
    int64_t oldFrames = old.frames();
    double end = (double)old.offset + (double)oldFrames, e2 = f(end);
    if (old.length >= 0) {
        bool toEnd = !old.loopingAudio() && end >= before.length - 1;  // (it reached its audio's end: it still does)
        c.length = std::max<int64_t>(1, (toEnd ? rounded(after.length) : rounded(e2)) - c.offset);
    }
    c.fadeIn = std::max<int64_t>(0, rounded(f(off + (double)old.fadeIn) - o2));
    c.fadeOut = std::max<int64_t>(0, rounded(e2 - f(end - (double)old.fadeOut)));
    c.fadeLead = std::max<int64_t>(0, rounded(o2 - f(off - (double)old.fadeLead)));
    c.fadeTail = std::max<int64_t>(0, rounded(f(end + (double)old.fadeTail) - e2));
    if (auto envelope = old.gainEnvelope) {
        std::vector<AutoPoint> pts = *envelope;
        for (auto& p : pts) p.t = std::max(0.0, f(p.t));
        c.gainEnvelope = clip_envelope::make(std::move(pts));
    }
    if (old.loopLength > 0) {
        double ls = f(old.loopStart * sr), le = f((old.loopStart + old.loopLength) * sr);
        c.loopStart = std::max(0.0, ls / sr);
        c.loopLength = std::max(1.0 / sr, (le - ls) / sr);
    }
}

// `c` (a copy, its new settings set, its window still as `before` says) prepared again for `map`: its start
// stays, its window keeps the same part of its audio
void rewarp(Clip& c, const Frames& before, const TempoMapPtr& map, int sr) {
    Clip old = c;
    double windowSrc = before.toSource((double)c.offset);
    int64_t origin = c.warp ? originFor(c, windowSrc, *map, sr) : c.start - c.offset;
    c.offset = c.start - origin;
    clip_hooks::UseTempoMap use(map);
    c.data = nullptr;
    c.peaks = nullptr;
    c.prepare(sr);
    remap(c, old, before, framesOf(c, sr), sr);
    if (c.warp) c.offset = std::max<int64_t>(0, c.start - origin);  // (exactly where its rendering starts)
    if (c.loopingAudio()) c.prepare(sr);
    c.fitWindow();
}

// what a placed clip is now, from a prepared copy of it (swapped in: the audio thread may be playing it)
void takeFrom(Clip& c, const Clip& r) {
    c.offset = r.offset;
    c.length = r.length;
    c.fadeIn = r.fadeIn;
    c.fadeOut = r.fadeOut;
    c.fadeLead = r.fadeLead;
    c.fadeTail = r.fadeTail;
    c.loopStart = r.loopStart;
    c.loopLength = r.loopLength;
    c.baseFrames = r.baseFrames;
    c.loopFrom = r.loopFrom;
    c.loopFor = r.loopFor;
    c.warpMarkers = r.warpMarkers;
    c.warpKey = r.warpKey;
    std::atomic_store(&c.gainEnvelope, r.gainEnvelope);
    std::atomic_store(&c.peaks, r.peaks);
    std::atomic_store(&c.data, r.data);
}

}  // namespace

double bpmOf(const Clip& c) { return c.sourceBpm > 0 ? c.sourceBpm : warp::DEFAULT_BPM; }

warp::Markers markersOf(const Clip& c) {
    if (auto m = c.warpMarkers)
        if (!m->empty()) return *m;
    return warp::straight(c.source ? c.source->frames() : 0, c.sourceSr, bpmOf(c));
}

double Frames::beatAt(double d) const { return map ? map->beatAt(origin + d / sr) - b0 : 0; }
double Frames::dataAt(double beat) const { return map ? (map->secondsAt(b0 + beat) - origin) * sr : 0; }

double Frames::toSource(double d) const {
    if (!warped) return reverse ? n - d * k : d * k;
    if (reverse) d = length - d;
    return beats.frameAt(beatAt(d));
}

double Frames::toData(double s) const {
    if (!warped) return reverse ? (n - s) / k : s / k;
    double d = dataAt(beats.beatAt(s));
    return reverse ? length - d : d;
}

Frames framesOf(const Clip& c, int sr) {
    Frames f;
    f.sr = std::max(1, sr);
    f.reverse = c.reverse;
    f.n = c.source ? (double)c.source->frames() : 0;
    f.length = (double)(c.baseFrames ? c.baseFrames : (c.data ? c.data->frames() : 0));
    f.k = (double)c.sourceSr / f.sr / std::max(1e-6, c.stretch);
    auto key = c.warpKey;
    if (c.warp && key && key->map) {
        f.warped = true;
        f.map = key->map;
        f.origin = (double)key->origin / f.sr;
        f.b0 = f.map->beatAt(f.origin);
        f.beats = warp::BeatMap(markersOf(c), bpmOf(c), c.sourceSr);
    }
    return f;
}

ClipPtr rewarped(const ClipPtr& clip, bool on, warp::Mode mode, int preserve, double bpm,
                 std::shared_ptr<const warp::Markers> markers, double pitch, int sr) {
    Frames before = framesOf(*clip, sr);
    ClipPtr c = copyClip(clip);
    c->warp = on && c->kind == Kind::Audio && c->source;
    c->warpMode = mode;
    c->warpPreserve = std::max(0, std::min(3, preserve));
    c->sourceBpm = bpm > 0 ? bpm : 0;
    if (markers) markers = std::make_shared<const warp::Markers>(warp::ordered(*markers));
    c->warpMarkers = markers;
    c->pitch = pitch;
    rewarp(*c, before, clip_hooks::currentTempoMap(), sr);
    util::debugf("warp: \"%s\" %s (%s, %.2f BPM, %zu markers, %+.2f semitones): %lld frames\n", c->name.c_str(),
                 c->warp ? "warped" : "not warped", warp::modeName(c->warpMode), bpmOf(*c), markersOf(*c).size(),
                 c->pitch, (long long)c->frames());
    return c;
}

void setMarkers(const ClipPtr& clip, warp::Markers markers, int sr) {
    Clip ready = *clip;
    ready.track = std::atomic_load(&clip->track);
    ready.warpMarkers = std::make_shared<const warp::Markers>(warp::ordered(std::move(markers)));
    double oldLength = (double)clip->baseFrames;
    ready.prepare(sr);
    if (ready.length >= 0 && !clip->loopingAudio() && (double)(clip->offset + clip->length) >= oldLength - 1)
        ready.length = std::max<int64_t>(1, ready.baseFrames - ready.offset);  // (it reached its audio's end: still)
    ready.fitWindow();
    takeFrom(*clip, ready);
    util::debugf("warp: \"%s\": %zu markers\n", clip->name.c_str(), ready.warpMarkers->size());
}

warp::Markers fromHere(const Clip& c, int64_t frame, const TempoMap& map, int sr) {
    Frames fr = framesOf(c, sr);
    warp::Markers all = markersOf(c);
    if (!fr.warped || fr.reverse) return all;
    double src = fr.toSource((double)(frame - c.start + c.offset));
    double t = (double)frame / sr;
    int bar = map.barAt(t);
    double a = map.barSeconds(bar), b = map.barSeconds(bar + 1);
    double beat = map.beatAt(t - a <= b - t ? a : b) - fr.b0;  // (the nearest bar, in the clip's beats)
    warp::Markers out;
    for (auto& m : all)
        if (m.frame < src - 0.5 && m.beat < beat) out.push_back(m);
    out.push_back({src, beat});
    if (fr.n > src + 1) out.push_back({fr.n, beat + (fr.n - src) / c.sourceSr * bpmOf(c) / 60.0});
    util::debugf("warp: from here: source frame %.0f on beat %.3f of \"%s\"\n", src, beat, c.name.c_str());
    return warp::ordered(out);
}

int retime(Project& p, int sr) {
    TempoMapPtr map = clip_hooks::currentTempoMap();
    std::vector<ClipPtr> todo;
    for (auto& c : p.clips) {
        if (c->kind != Kind::Audio || !c->warp || !c->source) continue;
        auto key = c->warpKey;
        if (key && key->map && warp::sameTempo(*key->map, *map)) {
            int64_t want = originFor(*c, framesOf(*c, sr).toSource((double)c->offset), *map, sr);
            if (std::llabs(want - key->origin) <= 1) continue;
            if (!map->hasChanges()) {  // (one steady tempo: moved, its audio is the same)
                c->warpKey = std::make_shared<const WarpKey>(WarpKey{key->map, c->start - c->offset});
                continue;
            }
        }
        todo.push_back(c);
    }
    if (todo.empty()) return 0;
    struct Work {
        std::vector<ClipPtr>* todo;
        std::vector<Clip> ready;
        TempoMapPtr map;
        int sr;
    } work{&todo, {}, map, sr};
    work.ready.reserve(todo.size());
    for (auto& c : todo) {
        work.ready.push_back(*c);
        work.ready.back().track = std::atomic_load(&c->track);
    }
    auto job = [](void* ctx, int i) {
        Work& w = *static_cast<Work*>(ctx);
        const ClipPtr& c = (*w.todo)[(size_t)i];
        rewarp(w.ready[(size_t)i], framesOf(*c, w.sr), w.map, w.sr);
    };
    int threads = todo.size() >= 4 && !workers::inJob() ? workers::workersFor(workers::chosenCores()) : 0;
    if (threads == 0) {  // (not as a pool's job: each clip's render then runs on every core itself, not on one)
        for (int i = 0; i < (int)todo.size(); i++) job(&work, i);
    } else {
        workers::Pool pool(threads, false);  // (as a render's: never outranking playback)
        pool.run((int)todo.size(), job, &work);
    }
    for (size_t i = 0; i < todo.size(); i++) takeFrom(*todo[i], work.ready[i]);
    util::debugf("warp: %zu warped clip(s) rendered again for the song's tempo (%d threads)\n", todo.size(), threads + 1);
    return (int)todo.size();
}

int64_t frameOfBeat(const Clip& c, double beat, int sr) {
    Frames f = framesOf(c, sr);
    return c.start - c.offset + rounded(f.warped ? f.dataAt(beat) : beat * 60.0 / bpmOf(c) * sr);
}

double beatAtFrame(const Clip& c, int64_t frame, int sr) {
    Frames f = framesOf(c, sr);
    double d = (double)(frame - c.start + c.offset);
    return f.warped ? f.beatAt(d) : d / sr * bpmOf(c) / 60.0;
}

int64_t frameOfSource(const Clip& c, double s, int sr) {
    return c.start - c.offset + rounded(framesOf(c, sr).toData(s));
}

}  // namespace warping
