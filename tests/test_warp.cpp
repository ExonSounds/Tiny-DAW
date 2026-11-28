// Tests of warping (audio/warp.h, model/warping.h) and tempo detection (audio/tempo_detect.h), each at its worst:
//   - Beats: a click track at 100 BPM warped to 120 (and to a tempo map with a change) lands its clicks on the
//     song's beats within a few ms;
//   - Tones keeps a sine's pitch (and pitch still works); Complex keeps transients sharper than Tones (measured);
//   - no mode clicks: a mix warped faster, slower and pitched never steps or peaks far above the source;
//   - tempo detection from an acid chunk, from file names and by analysis (a 128 BPM loop, trimmed too);
//   - 128 warped clips under a tempo map of 1,000 changes render, follow a change of it, and play;
//   - markers changed 100 times while the engine plays them;
//   - a 2-hour file warped (Tones and Beats) without running out of memory or time;
//   - undo, save and load; old stretch projects load as they did.
#include <sys/resource.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <thread>
#include <vector>

#include "audio/engine.h"
#include "audio/fft.h"
#include "audio/tempo_detect.h"
#include "audio/warp.h"
#include "audio/workers.h"
#include "check.h"
#include "core/util.h"
#include "model/edits.h"
#include "model/history.h"
#include "model/project_io.h"
#include "model/warping.h"

static const double PI = 3.14159265358979323846;
static const int SR = 44100;
using namespace project_io;
static const std::string kDir = "build/test-tmp/warp";

static double since(std::chrono::steady_clock::time_point t) {
    return std::chrono::duration<double>(std::chrono::steady_clock::now() - t).count();
}

// clicks (8 ms of decaying noise) every beat of `bpm`, plus a sine of `hz` (0: none)
static std::shared_ptr<AudioBuffer> clicks(double bpm, double seconds, int sr, double hz = 0, double offsetS = 0) {
    auto b = std::make_shared<AudioBuffer>();
    int64_t n = (int64_t)(seconds * sr);
    b->samples.assign((size_t)n * 2, 0.0f);
    uint32_t seed = 1;
    for (double t = offsetS; t < seconds; t += 60.0 / bpm) {
        int64_t at = (int64_t)std::llround(t * sr);
        for (int64_t i = 0; i < (int64_t)(0.008 * sr) && at + i < n; i++) {
            seed = seed * 1664525u + 1013904223u;
            float v = (float)(((seed >> 9) & 0xffff) / 32768.0 - 1.0) * 0.8f * (float)std::exp(-(double)i / (0.002 * sr));
            b->samples[(size_t)(2 * (at + i))] += v;
            b->samples[(size_t)(2 * (at + i) + 1)] += v;
        }
    }
    if (hz > 0)
        for (int64_t i = 0; i < n; i++) {
            float v = (float)(0.3 * std::sin(2 * PI * hz * i / sr));
            b->samples[(size_t)(2 * i)] += v;
            b->samples[(size_t)(2 * i + 1)] += v;
        }
    return b;
}

static std::shared_ptr<AudioBuffer> sine(double hz, double seconds, int sr) {
    auto b = std::make_shared<AudioBuffer>();
    int64_t n = (int64_t)(seconds * sr);
    b->samples.resize((size_t)n * 2);
    for (int64_t i = 0; i < n; i++) b->samples[(size_t)(2 * i)] = b->samples[(size_t)(2 * i + 1)] = (float)(0.5 * std::sin(2 * PI * hz * i / sr));
    return b;
}

// where the clicks start: the first sample above `level` after at least 60 ms below it
static std::vector<double> findClicks(const AudioBuffer& a, int sr, float level = 0.25f) {
    std::vector<double> out;
    int64_t quiet = INT64_MAX / 2;  // (one at the very start counts)
    for (int64_t i = 0; i < a.frames(); i++) {
        float v = std::fabs(a.samples[(size_t)(2 * i)]);
        if (v > level && quiet >= (int64_t)(0.06 * sr)) out.push_back((double)i / sr);
        quiet = v > level ? 0 : quiet + 1;
    }
    return out;
}

static double peakHz(const AudioBuffer& a, int sr) {
    const int N = 32768;
    fft::Plan p(N);
    std::vector<fft::cd> x(N);
    int64_t start = std::max<int64_t>(0, a.frames() / 2 - N / 2);
    for (int i = 0; i < N; ++i) {
        int64_t j = start + i;
        x[i] = (j < a.frames() ? a.samples[(size_t)(2 * j)] : 0.0) * (0.5 - 0.5 * std::cos(2 * PI * i / N));
    }
    p.forward(x.data());
    int best = 1;
    for (int k = 1; k < N / 2; ++k)
        if (std::abs(x[k]) > std::abs(x[best])) best = k;
    double l = std::log(std::abs(x[best - 1]) + 1e-30), m = std::log(std::abs(x[best]) + 1e-30), r = std::log(std::abs(x[best + 1]) + 1e-30);
    return (best + 0.5 * (l - r) / (l - 2 * m + r)) * sr / N;
}

static warp::Job job(AudioPtr src, int sr, double bpm, warp::Mode mode, TempoMapPtr map, int preserve = warp::TRANSIENTS) {
    warp::Job j;
    j.source = src;
    j.sourceSr = sr;
    j.bpm = bpm;
    j.markers = warp::straight(src->frames(), sr, bpm);
    j.mode = mode;
    j.preserve = preserve;
    j.map = map;
    j.sr = sr;
    return j;
}

static void testBeatsClicks() {
    auto src = clicks(100, 4.8, SR);  // 8 beats at 100 BPM
    auto map = std::make_shared<const TempoMap>(120.0);
    AudioPtr out = warp::render(job(src, SR, 100, warp::Mode::Beats, map));
    CHECK_NEAR((double)out->frames() / SR, 4.0, 0.01);  // (8 beats at 120)
    auto found = findClicks(*out, SR);
    CHECK(found.size() == 8);
    double worst = 0;
    for (size_t k = 0; k < found.size(); k++) worst = std::max(worst, std::fabs(found[k] - 0.5 * (double)k));
    printf("beats: 100 -> 120 BPM, clicks off the song's beats by %.2f ms at most\n", worst * 1000);
    CHECK(worst < 0.003);
    // under a tempo change (bar 2 at 90 BPM) and every grid option: still on the beats
    std::vector<TempoChange> changes{{2, 90, 4}};
    auto changing = std::make_shared<const TempoMap>(120.0, 4, changes);
    for (int preserve : {warp::TRANSIENTS, warp::QUARTERS, warp::EIGHTHS, warp::SIXTEENTHS}) {
        AudioPtr o2 = warp::render(job(src, SR, 100, warp::Mode::Beats, changing, preserve));
        auto f2 = findClicks(*o2, SR);
        double w2 = f2.size() == 8 ? 0 : 1;
        for (size_t k = 0; k < f2.size() && k < 8; k++) w2 = std::max(w2, std::fabs(f2[k] - changing->secondsAt((double)k)));
        printf("beats (%s) under a change to 90 BPM: off by %.2f ms at most\n", warp::preserveName(preserve), w2 * 1000);
        CHECK(w2 < 0.003);
    }
    // slowed down to 60: the gaps are filled by the tails' loops fading out, nothing past the end
    AudioPtr slow = warp::render(job(src, SR, 100, warp::Mode::Beats, std::make_shared<const TempoMap>(60.0)));
    CHECK_NEAR((double)slow->frames() / SR, 8.0, 0.01);
    bool finite = true;
    for (float v : slow->samples) finite = finite && std::isfinite(v) && std::fabs(v) < 2;
    CHECK(finite);
}

static void testTonesPitch() {
    auto src = sine(440, 3.0, SR);
    auto map = std::make_shared<const TempoMap>(120.0);
    AudioPtr out = warp::render(job(src, SR, 100, warp::Mode::Tones, map));
    CHECK_NEAR((double)out->frames() / SR, 2.5, 0.01);
    double hz = peakHz(*out, SR);
    printf("tones: a 440 Hz sine warped 100 -> 120 BPM plays at %.2f Hz\n", hz);
    CHECK_NEAR(hz, 440, 1.0);
    // its level kept (no phasing): the middle's RMS near the sine's
    double s = 0;
    int64_t a = out->frames() / 4, b = out->frames() * 3 / 4;
    for (int64_t i = a; i < b; i++) s += (double)out->samples[(size_t)(2 * i)] * out->samples[(size_t)(2 * i)];
    CHECK_NEAR(std::sqrt(s / (b - a)), 0.5 / std::sqrt(2.0), 0.05);
    // and slowed to half speed, and an octave up: still the pitch asked for
    AudioPtr slow = warp::render(job(src, SR, 120, warp::Mode::Tones, std::make_shared<const TempoMap>(60.0)));
    CHECK_NEAR((double)slow->frames() / SR, 6.0, 0.01);
    CHECK_NEAR(peakHz(*slow, SR), 440, 1.0);
    warp::Job up = job(src, SR, 100, warp::Mode::Tones, map);
    up.semitones = 12;
    AudioPtr high = warp::render(up);
    CHECK_NEAR((double)high->frames() / SR, 2.5, 0.01);
    CHECK_NEAR(peakHz(*high, SR), 880, 3.0);
    // Complex keeps it too
    CHECK_NEAR(peakHz(*warp::render(job(src, SR, 100, warp::Mode::Complex, map)), SR), 440, 1.0);
}

// how much of the clicks' high-frequency energy is within 5 ms of where they should be (1: all of it)
static double sharpness(const AudioBuffer& a, int sr, double every, int count) {
    double sum = 0;
    for (int k = 1; k < count; k++) {
        double t = k * every;
        double near = 0, around = 0;
        for (int64_t i = (int64_t)((t - 0.06) * sr); i < (int64_t)((t + 0.06) * sr); i++) {
            if (i < 1 || i >= a.frames()) continue;
            double d = (double)a.samples[(size_t)(2 * i)] - a.samples[(size_t)(2 * i - 2)];
            around += d * d;
            if (i >= (int64_t)((t - 0.001) * sr) && i < (int64_t)((t + 0.004) * sr)) near += d * d;
        }
        sum += around > 0 ? near / around : 0;
    }
    return sum / (count - 1);
}

static void testComplexTransients() {
    auto src = clicks(120, 4.0, SR, 220);  // drums and a tone
    auto map = std::make_shared<const TempoMap>(60.0);  // half speed: clicks every second
    AudioPtr tones = warp::render(job(src, SR, 120, warp::Mode::Tones, map));
    AudioPtr complex = warp::render(job(src, SR, 120, warp::Mode::Complex, map));
    double st = sharpness(*tones, SR, 1.0, 8), sc = sharpness(*complex, SR, 1.0, 8), so = sharpness(*src, SR, 0.5, 8);
    printf("transients (share of a click's energy within 5 ms of its place): source %.2f, Tones %.2f, Complex %.2f\n",
           so, st, sc);
    CHECK(sc > st * 1.5);
    CHECK(sc > 0.5 * so);
    CHECK_NEAR(peakHz(*complex, SR), 220, 2.0);
}

// a full mix in short: drum hits (decaying noise) every eighth at 120 BPM over a bass and a chord
static std::shared_ptr<AudioBuffer> mix(double seconds, int sr) {
    auto b = clicks(240, seconds, sr);
    for (int64_t i = 0; i < b->frames(); i++) {
        double t = (double)i / sr, v = 0;
        for (double hz : {55.0, 220.0, 277.18, 329.63, 1318.5}) v += 0.08 * std::sin(2 * PI * hz * t + hz);
        b->samples[(size_t)(2 * i)] += (float)v;
        b->samples[(size_t)(2 * i + 1)] += (float)(0.9 * v);
    }
    return b;
}

// the largest step from one sample to the next, and the largest sample (either channel)
static void jumpAndPeak(const AudioBuffer& a, float& jump, float& peak) {
    jump = peak = 0;
    for (size_t i = 0; i < a.samples.size(); i++) {
        peak = std::max(peak, std::fabs(a.samples[i]));
        if (i >= 2) jump = std::max(jump, std::fabs(a.samples[i] - a.samples[i - 2]));
    }
}

// every mode at several stretches, tempos and pitches: no click (no sample step or peak far above the source's)
static void testNoClicks() {
    auto src = mix(4.0, SR);
    float srcJump, srcPeak;
    jumpAndPeak(*src, srcJump, srcPeak);
    struct Case { double songBpm, semitones; };
    for (Case k : {Case{120, 0}, Case{120, -3}, Case{90, 0}, Case{160, 0}, Case{120, 5}, Case{140, -7}, Case{60, 0}}) {
        for (warp::Mode mode : {warp::Mode::Beats, warp::Mode::Tones, warp::Mode::Complex}) {
            warp::Job j = job(src, SR, 120, mode, std::make_shared<const TempoMap>(k.songBpm));
            j.semitones = k.semitones;
            AudioPtr out = warp::render(j);
            float jump, peak;
            jumpAndPeak(*out, jump, peak);
            printf("no clicks: %-7s at %3.0f BPM, %+.0f semitones: largest step %.3f (source %.3f), peak %.3f (source %.3f)\n",
                   warp::modeName(mode), k.songBpm, k.semitones, jump, srcJump, peak, srcPeak);
            CHECK(jump < 1.5f * srcJump);
            CHECK(peak < 1.5f * srcPeak);
        }
    }
}

static void writeWav(const std::string& path, const AudioBuffer& a, int sr, double acidBpm) {
    FILE* f = fopen(path.c_str(), "wb");
    auto u32 = [&](uint32_t v) { fwrite(&v, 4, 1, f); };
    auto u16 = [&](uint16_t v) { fwrite(&v, 2, 1, f); };
    uint32_t data = (uint32_t)(a.frames() * 4), acid = acidBpm > 0 ? 8 + 24 : 0;
    fwrite("RIFF", 1, 4, f);
    u32(4 + 8 + 16 + acid + 8 + data);
    fwrite("WAVEfmt ", 1, 8, f);
    u32(16);
    u16(1);
    u16(2);
    u32((uint32_t)sr);
    u32((uint32_t)sr * 4);
    u16(4);
    u16(16);
    if (acidBpm > 0) {
        fwrite("acid", 1, 4, f);
        u32(24);
        u32(1);  // (one-shot off, root set…)
        u16(60);
        u16(0x8000);
        float zero = 0, tempo = (float)acidBpm;
        fwrite(&zero, 4, 1, f);
        u32(8);
        u16(4);
        u16(4);
        fwrite(&tempo, 4, 1, f);
    }
    fwrite("data", 1, 4, f);
    u32(data);
    for (float v : a.samples) u16((uint16_t)(int16_t)std::lround(std::max(-1.0f, std::min(1.0f, v)) * 32767));
    fclose(f);
}

// a 128 BPM loop: a kick on every beat, a hi-hat on every eighth, a bass note per bar
static std::shared_ptr<AudioBuffer> loop128(double seconds, int sr, double trim = 0) {
    auto b = std::make_shared<AudioBuffer>();
    int64_t n = (int64_t)(seconds * sr);
    b->samples.assign((size_t)n * 2, 0.0f);
    double beat = 60.0 / 128;
    uint32_t seed = 7;
    for (int k = 0; k * beat / 2 < seconds + trim; k++) {
        double t = k * beat / 2 - trim;
        int64_t at = (int64_t)std::llround(t * sr);
        for (int64_t i = 0; i < (int64_t)(0.2 * sr); i++) {
            int64_t p = at + i;
            if (p < 0 || p >= n) continue;
            double v = 0;
            if (k % 2 == 0) v += 0.8 * std::sin(2 * PI * 55 * i / sr) * std::exp(-(double)i / (0.05 * sr));
            seed = seed * 1664525u + 1013904223u;
            v += 0.3 * (((seed >> 9) & 0xffff) / 32768.0 - 1.0) * std::exp(-(double)i / (0.01 * sr));
            b->samples[(size_t)(2 * p)] += (float)v;
            b->samples[(size_t)(2 * p + 1)] += (float)v;
        }
    }
    return b;
}

static void testDetection() {
    util::makeDirs(kDir);
    auto a = loop128(4 * 60.0 / 128 * 4, SR);  // 4 bars
    writeWav(kDir + "/no tempo here.wav", *a, SR, 97.5);
    CHECK_NEAR(tempo_detect::acidTempo(kDir + "/no tempo here.wav"), 97.5, 1e-4);
    auto found = tempo_detect::detect(kDir + "/no tempo here.wav", a, SR);
    CHECK(found.from == "acid chunk" && std::fabs(found.bpm - 97.5) < 1e-4);
    writeWav(kDir + "/plain.wav", *a, SR, 0);
    CHECK(tempo_detect::acidTempo(kDir + "/plain.wav") == 0);
    CHECK(tempo_detect::acidTempo(kDir + "/missing.wav") == 0);
    CHECK(tempo_detect::nameTempo("Drums 120bpm.wav") == 120);
    CHECK(tempo_detect::nameTempo("loop_95_BPM.wav") == 95);
    CHECK(tempo_detect::nameTempo("bpm128 kick.wav") == 128);
    CHECK(tempo_detect::nameTempo("/a/b/Funk_124_Am.wav") == 124);
    CHECK(tempo_detect::nameTempo("Groove 87.5 BPM.flac") == 87.5);
    CHECK(tempo_detect::nameTempo("kick.wav") == 0);
    CHECK(tempo_detect::nameTempo("808 kick.wav") == 0);
    CHECK(tempo_detect::nameTempo("snare_01.wav") == 0);
    CHECK(tempo_detect::nameTempo("bpm.wav") == 0);
    found = tempo_detect::detect("/nowhere/Beat 140 BPM.wav", a, SR);
    CHECK(found.from == "file name" && found.bpm == 140);
    double bpm = tempo_detect::analyse(*a, SR);
    printf("analysis: a 128 BPM loop found at %.2f BPM\n", bpm);
    CHECK_NEAR(bpm, 128, 1.0);
    double trimmed = tempo_detect::analyse(*loop128(4 * 60.0 / 128 * 4 - 0.37, SR, 0.37), SR);
    printf("analysis: trimmed by 0.37 s: %.2f BPM\n", trimmed);
    CHECK_NEAR(trimmed, 128, 1.0);
    found = tempo_detect::detect(kDir + "/plain.wav", a, SR);
    CHECK(found.from == "analysis" && std::fabs(found.bpm - 128) < 1.0);
    // silence, and too short: none
    auto silent = std::make_shared<AudioBuffer>();
    silent->samples.assign((size_t)SR * 4, 0.0f);
    CHECK(tempo_detect::analyse(*silent, SR) == 0);
    auto tiny = std::make_shared<AudioBuffer>();
    tiny->samples.assign(200, 0.1f);
    CHECK(tempo_detect::analyse(*tiny, SR) == 0);
}

// a warped audio clip on a track
static ClipPtr warpedClip(const TrackPtr& t, AudioPtr src, int sr, double bpm, warp::Mode mode, int64_t start) {
    ClipPtr c = makeAudioClip("warped", src, sr, sr);
    c->track = t;
    c->start = start;
    ClipPtr w = warping::rewarped(c, true, mode, warp::TRANSIENTS, bpm, nullptr, 0, sr);
    w->track = t;
    return w;
}

// Tap tempo while playing one long Complex clip: each tap's retime renders on every core, as a render of its own
// does (it ran on one core inside an idle pool's job: seconds a tap on the GUI thread, the window frozen)
static void testTapWhilePlaying() {
    Project p;
    auto t = p.addTrack(Kind::Audio);
    auto src = mix(30, SR);
    TempoMapPtr map = std::make_shared<const TempoMap>(120.0);
    clip_hooks::tempoMap = [&map] { return std::atomic_load(&map); };
    ClipPtr c = warpedClip(t, src, SR, 120, warp::Mode::Complex, 0);
    p.addClip(c);
    auto t0 = std::chrono::steady_clock::now();
    warp::rendered(job(src, SR, 120, warp::Mode::Complex, std::make_shared<const TempoMap>(117.0)));
    double direct = since(t0);
    audio::Engine e(p);
    e.sr = SR;
    e.offline = true;
    e.play();
    std::atomic<bool> done{false};
    bool finite = true;
    std::thread audioThread([&] {
        std::vector<float> out(256 * 2);
        while (!done) {
            e.callback(nullptr, out.data(), 256);
            for (float v : out) finite = finite && std::isfinite(v);
            std::this_thread::sleep_for(std::chrono::milliseconds(5));  // (a device's pace: no spinning)
        }
    });
    double slowest = 0;
    int retimed = 0;
    for (double bpm : {118.0, 121.0, 119.5, 122.0, 118.5, 120.5, 123.0, 119.0}) {
        std::atomic_store(&map, std::make_shared<const TempoMap>(bpm));
        t0 = std::chrono::steady_clock::now();
        retimed += warping::retime(p, SR);
        slowest = std::max(slowest, since(t0));
    }
    done = true;
    audioThread.join();
    e.stop();
    clip_hooks::tempoMap = nullptr;
    printf("tap tempo while playing: slowest retime %.2f s, a render on every core %.2f s (%d cores)\n", slowest, direct,
           workers::availableCores());
    CHECK(retimed == 8 && finite);
    if (workers::availableCores() >= 4) CHECK(slowest < 2 * direct + 0.1);
}

static void testManyTempoChanges() {
    const int sr = 22050;
    Project p;
    std::vector<TempoChange> changes;
    for (int bar = 2; bar <= 1001; bar++) changes.push_back({bar, 80.0 + (bar * 37) % 81, 4});
    TempoMapPtr map = std::make_shared<const TempoMap>(120.0, 4, changes);
    clip_hooks::tempoMap = [&map] { return map; };
    auto t0 = std::chrono::steady_clock::now();
    auto src = clicks(100, 2.4, sr, 330);  // 4 beats at 100 BPM
    for (int i = 0; i < 128; i++) {
        auto t = p.addTrack(Kind::Audio);
        double at = map->secondsAt(4.0 * (i * 7 % 1000));  // (on a bar, somewhere along the changes)
        p.addClip(warpedClip(t, src, sr, 100, (warp::Mode)(i % 3), (int64_t)std::llround(at * sr)));
    }
    double made = since(t0);
    // each one's beats on the song's: the clicks of a Beats clip where the map says
    ClipPtr c = p.clips[3];
    double startS = (double)c->start / sr, b0 = map->beatAt(startS);
    auto found = findClicks(*c->data, sr, 0.5f);
    double worst = found.size() == 4 ? 0 : 1;
    for (size_t k = 0; k < found.size() && k < 4; k++)
        worst = std::max(worst, std::fabs(startS + found[k] - map->secondsAt(b0 + (double)k)));
    printf("1,000 tempo changes: 128 warped clips made in %.2f s; clicks off the beats by %.2f ms at most\n", made,
           worst * 1000);
    CHECK(worst < 0.004);
    // the tempo map changes: every clip follows (rendered again on every core), its start kept
    std::vector<int64_t> starts;
    for (auto& k : p.clips) starts.push_back(k->start);
    changes[0].bpm = 150;
    map = std::make_shared<const TempoMap>(130.0, 4, changes);
    t0 = std::chrono::steady_clock::now();
    CHECK(warping::retime(p, sr) == 128);
    printf("1,000 tempo changes: 128 clips retimed in %.2f s\n", since(t0));
    CHECK(warping::retime(p, sr) == 0);  // (done: nothing more)
    bool kept = true, keyed = true;
    for (size_t i = 0; i < p.clips.size(); i++) {
        kept = kept && p.clips[i]->start == starts[i];
        keyed = keyed && p.clips[i]->warpKey && p.clips[i]->warpKey->map == map &&
                p.clips[i]->warpKey->origin == p.clips[i]->start - p.clips[i]->offset;
    }
    CHECK(kept && keyed);
    c = p.clips[3];
    startS = (double)(c->start - c->offset) / sr;
    b0 = map->beatAt(startS);
    found = findClicks(*c->data, sr, 0.5f);
    worst = found.size() == 4 ? 0 : 1;
    for (size_t k = 0; k < found.size() && k < 4; k++)
        worst = std::max(worst, std::fabs(startS + found[k] - map->secondsAt(b0 + (double)k)));
    CHECK(worst < 0.004);
    // a clip moved along the changes: rendered again for its new place; under one steady tempo: not
    p.clips[0]->start += sr * 3;
    CHECK(warping::retime(p, sr) == 1);
    map = std::make_shared<const TempoMap>(120.0);
    CHECK(warping::retime(p, sr) == 128);
    auto before = p.clips[0]->data;
    p.clips[0]->start += sr * 5;
    CHECK(warping::retime(p, sr) == 0);
    CHECK(p.clips[0]->data == before && p.clips[0]->warpKey->origin == p.clips[0]->start - p.clips[0]->offset);
    // and they play: the engine through a stretch with clips
    map = std::make_shared<const TempoMap>(120.0, 4, changes);
    warping::retime(p, sr);
    audio::Engine e(p);
    e.sr = sr;
    e.offline = true;
    e.idle();
    e.pos = p.clips[5]->start;
    e.play();
    std::vector<float> out(512 * 2);
    float peak = 0;
    bool finite = true;
    for (int b = 0; b < sr * 2 / 512; b++) {
        e.callback(nullptr, out.data(), 512);
        for (float v : out) {
            finite = finite && std::isfinite(v);
            peak = std::max(peak, std::fabs(v));
        }
    }
    e.stop();
    CHECK(finite && peak > 0.01);
    clip_hooks::tempoMap = nullptr;
}

static void testMarkersWhilePlaying() {
    Project p;
    auto t = p.addTrack(Kind::Audio);
    auto src = clicks(100, 4.8, SR, 220);
    ClipPtr c = warpedClip(t, src, SR, 100, warp::Mode::Beats, 0);
    p.addClip(c);
    audio::Engine e(p);
    e.sr = SR;
    e.offline = true;
    e.play();
    std::atomic<bool> done{false};
    bool finite = true;
    std::thread audioThread([&] {
        std::vector<float> out(64 * 2);
        for (int b = 0; b < SR * 3 / 64; b++) {
            e.callback(nullptr, out.data(), 64);
            for (float v : out) finite = finite && std::isfinite(v);
            if (e.pos > SR * 4) e.pos = 0;
        }
        done = true;
    });
    uint32_t seed = 3;
    int moves = 0;
    while (!done || moves < 100) {
        warp::Markers m = warping::markersOf(*c);
        // a marker in the middle dragged anywhere between its neighbours, one added, one removed
        seed = seed * 1664525u + 1013904223u;
        double beat = 1 + ((seed >> 8) % 1000) / 1000.0 * 5;
        m.push_back({(double)src->frames() / 2, beat});
        if (m.size() > 4) m.erase(m.begin() + 1);
        warping::setMarkers(c, m, SR);
        if (moves % 10 == 0) c->warpMode = (warp::Mode)(moves / 10 % 3);
        moves++;
        if (moves > 400) break;
    }
    audioThread.join();
    e.stop();
    printf("markers: %d changes while playing\n", moves);
    CHECK(finite && moves >= 100);
    CHECK(c->data && c->offset + c->frames() <= c->data->frames());
}

static void testTwoHours() {
    const int sr = 4000;
    const int64_t n = (int64_t)sr * 7200;
    auto src = std::make_shared<AudioBuffer>();
    src->samples.resize((size_t)n * 2);
    for (int64_t i = 0; i < n; i++)
        src->samples[(size_t)(2 * i)] = src->samples[(size_t)(2 * i + 1)] =
            (float)(0.4 * std::sin(2 * PI * 300 * i / sr) + (i % (sr * 6 / 10) < 40 ? 0.5 : 0));
    AudioPtr s = src;
    auto map = std::make_shared<const TempoMap>(120.0);
    struct rusage ru;
    getrusage(RUSAGE_SELF, &ru);
    long before = ru.ru_maxrss;
    for (auto mode : {warp::Mode::Tones, warp::Mode::Beats}) {
        auto t0 = std::chrono::steady_clock::now();
        AudioPtr out = warp::render(job(s, sr, 100, mode, map, warp::SIXTEENTHS));
        double took = since(t0);
        getrusage(RUSAGE_SELF, &ru);
        printf("2 hours (%s): %.0f s -> %.0f s in %.2f s, memory grown by %ld MB\n", warp::modeName(mode), 7200.0,
               (double)out->frames() / sr, took, (ru.ru_maxrss - before) / 1024);
        CHECK_NEAR((double)out->frames() / sr, 6000, 1);
        CHECK(took < 8);
        // the middle still a 300 Hz tone at its level
        double sum = 0;
        for (int64_t i = out->frames() / 2; i < out->frames() / 2 + sr; i++) sum += std::fabs(out->samples[(size_t)(2 * i)]);
        CHECK(sum / sr > 0.15);
    }
    getrusage(RUSAGE_SELF, &ru);
    CHECK((ru.ru_maxrss - before) / 1024 < 900);  // (the audio and its result, not the work's spectra)
}

static void testUndoSaveLoad() {
    util::makeDirs(kDir);
    Project p;
    clip_hooks::tempoMap = [&p] { return p.timing.audioMap(); };
    p.timing.bpm = 120;
    p.timing.publish();
    auto t = p.addTrack(Kind::Audio);
    // an old-style stretched clip, and a warped one
    ClipPtr old = makeAudioClip("old", clicks(90, 2, SR, 440), SR, SR);
    old->track = t;
    old->start = 0;
    old = edits::restretched(old, 1.5, 2, SR);
    p.addClip(old);
    ClipPtr w = warpedClip(t, clicks(100, 4.8, SR, 220), SR, 100, warp::Mode::Complex, SR * 10);
    p.addClip(w);
    CHECK(w->warp && w->frames() == (int64_t)std::llround(4.0 * SR));
    history::History h(p, SR);
    // markers: undo puts them, and the audio, back
    auto data0 = w->data;
    warp::Markers m = warping::markersOf(*w);
    m.insert(m.begin() + 1, warp::Marker{SR * 2.4, 3.5});
    warping::setMarkers(w, m, SR);
    CHECK(w->data != data0 && w->warpMarkers->size() == 3);
    CHECK(h.check(SR));
    CHECK(h.labels().back() == "Warp markers");
    CHECK(h.undo(SR));
    CHECK(!w->warpMarkers && w->data == data0);
    CHECK(h.redo(SR));
    CHECK(w->warpMarkers && w->warpMarkers->size() == 3);
    // the tempo: retimed, one step with it; undone, nothing renders again
    p.timing.bpm = 60;
    p.timing.publish();
    CHECK(warping::retime(p, SR) == 1);
    CHECK(std::llabs(w->frames() - (int64_t)std::llround(8.0 * SR)) < SR / 100);  // (twice as long)
    CHECK(h.check(SR));
    CHECK(h.undo(SR));
    CHECK(warping::retime(p, SR) == 0);
    CHECK(std::llabs(w->frames() - (int64_t)std::llround(4.0 * SR)) < SR / 100);
    CHECK(!h.check(SR));
    // saved and opened: the same settings and the same audio
    std::string path = kDir + "/Warped.json", err;
    MasterSettings master;
    CHECK(saveProject(path, p, SR, master, err));
    std::string text;
    util::readFile(path, text);
    CHECK(text.find("\"warp\"") != std::string::npos);
    Project q;
    MasterSettings m2;
    q.timing.bpm = 77;  // (a project open before it: its own tempo is used)
    q.timing.publish();
    clip_hooks::tempoMap = [&q] { return q.timing.audioMap(); };
    CHECK(loadProject(path, q, SR, m2, err));
    CHECK(q.clips.size() == 2);
    ClipPtr qo = q.clips[0], qw = q.clips[1];
    if (qo->warp) std::swap(qo, qw);
    CHECK(qw->warp && qw->warpMode == warp::Mode::Complex && qw->warpMarkers && *qw->warpMarkers == *w->warpMarkers);
    CHECK(qw->start == w->start && qw->offset == w->offset && qw->frames() == w->frames());
    CHECK(qw->data && w->data && qw->data->samples.size() == w->data->samples.size());
    double diff = 0;
    for (size_t i = 0; qw->data && i < std::min(qw->data->samples.size(), w->data->samples.size()); i++)
        diff = std::max(diff, (double)std::fabs(qw->data->samples[i] - w->data->samples[i]));
    CHECK(diff < 1e-3);  // (its file is 16 bits)
    CHECK(warping::retime(q, SR) == 0);
    // the old stretched clip: as it was, no warp written for it
    CHECK(!qo->warp && qo->stretch == 1.5 && qo->pitch == 2 && qo->frames() == old->frames());
    CHECK(qo->data->samples.size() == old->data->samples.size());
    // a project saved before warping existed (no "warp" anywhere) loads unwarped
    Project r;
    r.addClip(old);
    std::string oldPath = kDir + "/Old.json";
    std::atomic_store(&old->track, r.addTrack(Kind::Audio));
    CHECK(saveProject(oldPath, r, SR, master, err));
    util::readFile(oldPath, text);
    CHECK(text.find("\"warp\"") == std::string::npos);
    Project r2;
    CHECK(loadProject(oldPath, r2, SR, m2, err));
    CHECK(r2.clips.size() == 1 && !r2.clips[0]->warp && r2.clips[0]->frames() == old->frames());
    double d2 = 0;
    for (size_t i = 0; i < std::min(r2.clips[0]->data->samples.size(), old->data->samples.size()); i++)
        d2 = std::max(d2, (double)std::fabs(r2.clips[0]->data->samples[i] - old->data->samples[i]));
    CHECK(d2 < 1e-3);
    // warp turned off again: its stretch back, its window on the same audio
    ClipPtr off = warping::rewarped(w, false, w->warpMode, w->warpPreserve, w->sourceBpm, w->warpMarkers, 0, SR);
    CHECK(!off->warp && !off->warpKey && std::llabs(off->frames() - (int64_t)(4.8 * SR)) < SR / 50);
    clip_hooks::tempoMap = nullptr;
}

static void testFromHere() {
    Project p;
    auto map = std::make_shared<const TempoMap>(120.0);
    clip_hooks::tempoMap = [&map] { return map; };
    auto t = p.addTrack(Kind::Audio);
    // a click track whose downbeat is 0.3 s in: warp from there puts it on the nearest bar
    auto src = clicks(120, 6.3, SR, 0, 0.3);
    ClipPtr c = warpedClip(t, src, SR, 120, warp::Mode::Beats, SR * 2);  // bar 2 at 2 s
    int64_t at = c->start + (int64_t)(0.3 * SR);
    warp::Markers m = warping::fromHere(*c, at, *map, SR);
    warping::setMarkers(c, m, SR);
    auto found = findClicks(*c->data, SR);
    CHECK(!found.empty() && std::fabs((double)(c->start - c->offset) / SR + found[0] - 2.0) < 0.003);
    clip_hooks::tempoMap = nullptr;
}

int main() {
    util::setDebug(true);
    auto t0 = std::chrono::steady_clock::now();
    testBeatsClicks();
    testTonesPitch();
    testComplexTransients();
    testNoClicks();
    testDetection();
    testFromHere();
    testUndoSaveLoad();
    testMarkersWhilePlaying();
    testManyTempoChanges();
    testTwoHours();
    testTapWhilePlaying();
    printf("all in %.1f s\n", since(t0));
    TEST_MAIN_END();
}
