#include "model/timing.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <limits>
#include <numeric>

#include "config.h"
#include "core/util.h"

// ---------------------------------------------------------------------------------------------
// Frac

Frac::Frac(int64_t n, int64_t d) {
    if (d < 0) n = -n, d = -d;
    if (d == 0) d = 1;
    int64_t g = std::gcd(n < 0 ? -n : n, d);
    if (g > 1) n /= g, d /= g;
    num = n;
    den = d;
}

int64_t Frac::floor() const {
    int64_t q = num / den;
    if (num % den != 0 && num < 0) q--;
    return q;
}

Frac Frac::fromDouble(double x, int64_t maxDen) {
    // the best rational approximation with a bounded denominator (continued fractions), as
    // Python's Fraction(x).limit_denominator(maxDen) gives for the floats the program uses
    if (!std::isfinite(x)) return Frac(0);
    bool negative = x < 0;
    double v = std::fabs(x);
    int64_t p0 = 0, q0 = 1, p1 = 1, q1 = 0;
    double rest = v;
    for (int i = 0; i < 64; i++) {
        double a = std::floor(rest);
        if (a > 1e15) break;
        int64_t ai = (int64_t)a;
        int64_t q2 = q0 + ai * q1;
        if (q2 > maxDen) {
            // the semiconvergent with the largest denominator that fits, if nearer
            int64_t k = (maxDen - q0) / q1;
            Frac b1(p0 + k * p1, q0 + k * q1), b2(p1, q1);
            double e1 = std::fabs(b1.value() - v), e2 = std::fabs(b2.value() - v);
            Frac best = e1 < e2 ? b1 : b2;
            return negative ? Frac(-best.num, best.den) : best;
        }
        int64_t p2 = p0 + ai * p1;
        p0 = p1, q0 = q1, p1 = p2, q1 = q2;
        double f = rest - a;
        if (f < 1e-12) break;
        rest = 1.0 / f;
    }
    return Frac(negative ? -p1 : p1, q1);
}

// ---------------------------------------------------------------------------------------------

namespace timing {
const std::vector<Frac> STRAIGHT_STEPS = {Frac(1, 8), Frac(1, 4), Frac(1, 2), Frac(1, 1), Frac(2, 1)};
const std::vector<Frac> THIRDS_STEPS = {Frac(1, 12), Frac(1, 6), Frac(1, 3), Frac(2, 3), Frac(4, 3)};
const std::vector<Frac>& fixedSteps(bool thirds) {
    static const std::vector<Frac> straight = [] {
        std::vector<Frac> v = STRAIGHT_STEPS;
        v.push_back(Frac(4));
        return v;
    }();
    return thirds ? THIRDS_STEPS : straight;
}

bool validUnit(int den) { return den >= 1 && den <= 32 && (den & (den - 1)) == 0; }

int checkedUnit(int den, const char* where) {
    if (validUnit(den)) return den;
    util::debugf("%s: the time signature's denominator %d isn't 1, 2, 4, 8, 16 or 32: read as 4\n", where, den);
    return 4;
}

int clampedBeats(int num) { return std::min(std::max(num, 1), config::MAX_BEATS_PER_BAR); }
}  // namespace timing

bool TimeSig::parse(const std::string& text, TimeSig& out) {
    int n = 0, d = 4;
    char slash = 0, extra = 0;
    int got = std::sscanf(text.c_str(), " %d %c %d %c", &n, &slash, &d, &extra);
    if (got < 1 || (got >= 2 && slash != '/') || got == 2 || got > 3) return false;
    out.num = timing::clampedBeats(n);
    out.den = timing::checkedUnit(d, "a typed time signature");
    return out.num == n && out.den == d;
}

using timing::EPSILON;

// ---------------------------------------------------------------------------------------------
// TempoMap

double Segment::secondsFor(double b) const {
    if (b <= 0 || k == 0) return 60.0 * b / bpm;
    // ∫ 60 / (bpm + k·x) dx from 0 to b
    double x = k * b / bpm;
    if (x <= -1) return std::numeric_limits<double>::infinity();  // (past where a falling ramp would stop: never inside it)
    return 60.0 / k * std::log1p(x);
}

double Segment::beatsFor(double t) const {
    if (t <= 0 || k == 0) return t * bpm / 60.0;
    return bpm / k * std::expm1(k * t / 60.0);  // (secondsFor's inverse)
}

TempoMap::TempoMap(double bpm_, int beatsPerBar_, std::vector<TempoChange> changes_, bool ramp_, int beatUnit_)
    : bpm(bpm_), beatsPerBar(beatsPerBar_), beatUnit(beatUnit_), changes([&] {
          std::sort(changes_.begin(), changes_.end());
          return changes_;
      }()), ramp(ramp_) {
    auto unit = [](int den) { return timing::validUnit(den) ? den : 4; };  // (read through checkedUnit already)
    auto safeBpm = [](double v) { return v > 0 && std::isfinite(v) ? v : 1.0; };
    // the time signatures: bar 1's, then each change at a bar's start (two at one bar: the later one)
    meters_.push_back(Meter{0, 0.0, std::max(1, beatsPerBar), unit(beatUnit)});
    for (const TempoChange& c : changes) {
        if (!c.valid() || !c.atBar()) continue;
        Meter last = meters_.back();
        int bars = c.bar - 1 - last.bar;
        if (bars <= 0) {
            meters_.back().beatsPerBar = std::max(1, c.beatsPerBar);
            meters_.back().beatUnit = unit(c.beatUnit);
            continue;
        }
        meters_.push_back(Meter{c.bar - 1, last.beat + (double)bars * last.barQuarters(), std::max(1, c.beatsPerBar),
                                unit(c.beatUnit)});
    }
    // the tempo points, in beats (two at one place: the later one)
    struct P {
        double beat, bpm;
        bool ramp;
        int change;
    };
    std::vector<P> ps{P{0.0, safeBpm(bpm), ramp, -1}};
    for (size_t i = 0; i < changes.size(); i++) {
        const TempoChange& c = changes[i];
        if (!c.valid()) continue;
        double b = beatOf(c);
        P p{b, safeBpm(c.bpm), c.ramp, (int)i};
        if (b <= ps.back().beat) {
            if (ps.size() > 1 || b == 0) ps.back() = P{ps.back().beat, p.bpm, p.ramp, p.change};
            continue;
        }
        ps.push_back(p);
    }
    // the segments: one from every tempo point and every time signature
    std::vector<double> bounds;
    bounds.reserve(ps.size() + meters_.size());
    for (auto& p : ps) bounds.push_back(p.beat);
    for (auto& m : meters_) bounds.push_back(m.beat);
    std::sort(bounds.begin(), bounds.end());
    bounds.erase(std::unique(bounds.begin(), bounds.end()), bounds.end());
    segs_.reserve(bounds.size());
    size_t pi = 0, mi = 0;
    for (double b : bounds) {
        while (pi + 1 < ps.size() && ps[pi + 1].beat <= b) pi++;
        while (mi + 1 < meters_.size() && meters_[mi + 1].beat <= b) mi++;
        const P& p = ps[pi];
        double k = p.ramp && pi + 1 < ps.size() ? (ps[pi + 1].bpm - p.bpm) / (ps[pi + 1].beat - p.beat) : 0.0;
        const Meter& m = meters_[mi];
        int bar = m.bar + (int)std::floor((b - m.beat) / m.barQuarters() + 1e-9);
        Segment s;
        s.beat = b;
        s.bpm = p.bpm + k * (b - p.beat);
        s.k = k;
        s.bar = bar;
        s.beatsPerBar = m.beatsPerBar;
        s.beatUnit = m.beatUnit;
        s.barBeat = m.beat + (double)(bar - m.bar) * m.barQuarters();
        s.seconds = segs_.empty() ? 0.0 : segs_.back().seconds + segs_.back().secondsFor(b - segs_.back().beat);
        segs_.push_back(s);
    }
    points_.reserve(ps.size());
    for (size_t i = 0; i < ps.size(); i++)
        points_.push_back(TempoPoint{ps[i].beat, secondsAt(ps[i].beat), ps[i].bpm, ps[i].ramp && i + 1 < ps.size(),
                                     ps[i].change});
}

const Segment& TempoMap::segmentAt(double seconds) const {
    auto it = std::upper_bound(segs_.begin(), segs_.end(), seconds,
                               [](double t, const Segment& s) { return t + 1e-9 < s.seconds; });
    return it == segs_.begin() ? segs_[0] : *(it - 1);
}

const Segment& TempoMap::segmentAtBeat(double beat) const {
    auto it = std::upper_bound(segs_.begin(), segs_.end(), beat,
                               [](double b, const Segment& s) { return b + 1e-9 < s.beat; });
    return it == segs_.begin() ? segs_[0] : *(it - 1);
}

const Meter& TempoMap::meterAtBeat(double beat) const {
    auto it = std::upper_bound(meters_.begin(), meters_.end(), beat,
                               [](double b, const Meter& m) { return b + 1e-9 < m.beat; });
    return it == meters_.begin() ? meters_[0] : *(it - 1);
}

const Segment* TempoMap::following(const Segment& s) const {
    if (&s >= segs_.data() && &s < segs_.data() + segs_.size()) {
        size_t i = (size_t)(&s - segs_.data()) + 1;
        return i < segs_.size() ? &segs_[i] : nullptr;
    }
    for (auto& n : segs_)
        if (n.seconds > s.seconds + 1e-9) return &n;
    return nullptr;
}

double TempoMap::beatAt(double seconds) const {
    const Segment& s = segmentAt(seconds);
    return s.beat + s.beatsFor(seconds - s.seconds);
}

double TempoMap::secondsAt(double beat) const {
    const Segment& s = segmentAtBeat(beat);
    return s.seconds + s.secondsFor(beat - s.beat);
}

double TempoMap::barBeat(int bar) const {
    const Meter* m = &meters_[0];
    for (size_t i = meters_.size(); i-- > 0;)
        if (meters_[i].bar <= bar) {
            m = &meters_[i];
            break;
        }
    return m->beat + (double)(bar - m->bar) * m->barQuarters();
}

double TempoMap::barSeconds(int bar) const { return secondsAt(barBeat(bar)); }

int TempoMap::barAtBeat(double beat) const {
    const Meter& m = meterAtBeat(beat);
    return m.bar + (int)((beat - m.beat) / m.barQuarters() + EPSILON);
}

int TempoMap::barAt(double seconds) const { return barAtBeat(beatAt(seconds)); }

double TempoMap::bpmAtBeat(double beat) const {
    const Segment& s = segmentAtBeat(beat);
    return s.bpmAfter(std::max(0.0, beat - s.beat));
}

double TempoMap::bpmAt(double seconds) const {
    const Segment& s = segmentAt(seconds);
    return s.k == 0 ? s.bpm : s.bpmAfter(std::max(0.0, s.beatsFor(seconds - s.seconds)));
}

double TempoMap::beatOf(const TempoChange& c) const {
    int bar = c.bar - 1;
    const Meter* m = &meters_[0];
    for (size_t i = meters_.size(); i-- > 0;)
        if (meters_[i].bar <= bar) {
            m = &meters_[i];
            break;
        }
    return m->beat + (double)(bar - m->bar) * m->barQuarters() + std::min(std::max(0.0, c.beat), m->barQuarters());
}

void TempoMap::beatTimes(double t0, double t1, std::vector<BeatTime>& out) const {
    out.clear();
    if (t1 < t0) return;
    // (counted in each signature's unit from where it began: in N/4 every quarter, as it always was)
    double b0 = std::max(0.0, beatAt(t0)), b1 = beatAt(t1);
    for (size_t mi = (size_t)(&meterAtBeat(b0) - meters_.data()); mi < meters_.size(); mi++) {
        const Meter& m = meters_[mi];
        double unit = 4.0 / m.beatUnit, next = mi + 1 < meters_.size() ? meters_[mi + 1].beat : b1 + 1;
        int group = m.sig().compound() ? 3 : 0;
        for (int64_t j = std::max<int64_t>(0, (int64_t)std::ceil((std::max(b0, m.beat) - m.beat) / unit - 1e-9));; j++) {
            double b = m.beat + (double)j * unit;
            if (b > b1 + 1e-9 || b >= next - 1e-9) break;
            double at = secondsAt(b);
            if (t0 - 1e-9 <= at && at <= t1) {
                int64_t in = j % m.beatsPerBar;
                out.push_back({at, in == 0, group && in != 0 && in % group == 0});
            }
        }
        if (next > b1 + 1e-9) break;
    }
}

std::vector<BeatTime> TempoMap::beatTimes(double t0, double t1) const {
    std::vector<BeatTime> out;
    beatTimes(t0, t1, out);
    return out;
}

// ---------------------------------------------------------------------------------------------
// Timing

Timing::Timing() : bpm(config::DEFAULT_BPM), beatsPerBar(config::DEFAULT_BEATS_PER_BAR) {}

Timing::Timing(const Timing& o)
    : bpm(o.bpm), beatsPerBar(o.beatsPerBar), beatUnit(o.beatUnit), beats(o.beats), thirds(o.thirds), loopStart(o.loopStart),
      loopEnd(o.loopEnd), looping(o.looping), changes(o.changes), ramp(o.ramp), timebaseBeats(o.timebaseBeats),
      grid(o.grid), snapOn(o.snapOn), key(o.key), scale(o.scale), swing(o.swing), swingGrid(o.swingGrid),
      automationFollows(o.automationFollows), tempoLaneLo(o.tempoLaneLo), tempoLaneHi(o.tempoLaneHi), map_(std::atomic_load(&o.map_)) {}

Timing& Timing::operator=(const Timing& o) {
    if (this == &o) return *this;
    bpm = o.bpm;
    beatsPerBar = o.beatsPerBar;
    beatUnit = o.beatUnit;
    beats = o.beats;
    thirds = o.thirds;
    loopStart = o.loopStart;
    loopEnd = o.loopEnd;
    looping = o.looping;
    changes = o.changes;
    ramp = o.ramp;
    timebaseBeats = o.timebaseBeats;
    grid = o.grid;
    snapOn = o.snapOn;
    key = o.key;
    scale = o.scale;
    swing = o.swing;
    swingGrid = o.swingGrid;
    automationFollows = o.automationFollows;
    tempoLaneLo = o.tempoLaneLo;
    tempoLaneHi = o.tempoLaneHi;
    publish();  // (the audio thread hears the new tempo map at once)
    return *this;
}

bool Timing::tempoLaneFixed() const {
    return config::MIN_BPM <= tempoLaneLo && tempoLaneLo < tempoLaneHi && tempoLaneHi <= config::MAX_BPM;
}

bool Timing::loopRange(double& start, double& end) const {
    if (loopEnd - loopStart >= config::LOOP_MIN_S) {
        start = loopStart;
        end = loopEnd;
        return true;
    }
    return false;
}

TempoMapPtr Timing::map() const {
    TempoMapPtr m = std::atomic_load(&map_);
    if (m && m->bpm == bpm && m->beatsPerBar == beatsPerBar && m->beatUnit == beatUnit && m->ramp == ramp && m->changes.size() == changes.size()) {
        if (std::is_sorted(changes.begin(), changes.end())) {  // (as they are kept: compared without a copy)
            if (changes == m->changes) return m;
        } else {
            std::vector<TempoChange> sorted = changes;
            std::sort(sorted.begin(), sorted.end());
            if (sorted == m->changes) return m;
        }
    }
    m = std::make_shared<const TempoMap>(bpm, beatsPerBar, changes, ramp, beatUnit);
    std::atomic_store(&map_, m);
    return m;
}

TempoMapPtr Timing::audioMap() const {
    TempoMapPtr m = std::atomic_load(&map_);
    if (m) return m;
    static const TempoMapPtr fallback = std::make_shared<const TempoMap>();  // (never published yet)
    return fallback;
}

void Timing::setChange(int bar, double bpm_, int beatsPerBar_, int beatUnit_) {
    const TempoChange* was = changeAt(bar, 0);
    setChange(TempoChange{bar, bpm_, beatsPerBar_, 0.0, was && was->ramp, beatUnit_});
}

void Timing::setChange(const TempoChange& c) {
    removeChange(c.bar, c.beat);
    changes.push_back(c);
    std::sort(changes.begin(), changes.end());
}

void Timing::removeChange(int bar, double beat) {
    changes.erase(std::remove_if(changes.begin(), changes.end(),
                                 [bar, beat](const TempoChange& c) { return c.bar == bar && c.beat == beat; }),
                  changes.end());
}

const TempoChange* Timing::changeAt(int bar, double beat) const {
    for (auto& c : changes)
        if (c.bar == bar && c.beat == beat) return &c;
    return nullptr;
}

namespace {
bool rampsAt(const TempoMap& m, double beat) {  // (the tempo point before `beat` ramps on through it)
    const std::vector<TempoPoint>& ps = m.points();
    size_t i = 0;
    while (i + 1 < ps.size() && ps[i + 1].beat <= beat + 1e-9) i++;
    return ps[i].ramp;
}
}  // namespace

TimeSig Timing::sigAtBar(int bar) const {
    TempoMapPtr m = map();
    return m->meterAtBeat(m->barBeat(std::max(0, bar - 1))).sig();
}

void Timing::setSignature(int bar, TimeSig sig) {
    sig.num = timing::clampedBeats(sig.num);
    sig.den = timing::checkedUnit(sig.den, "setSignature");
    if (bar <= 1) {
        beatsPerBar = sig.num, beatUnit = sig.den;
        return;
    }
    if (const TempoChange* c = changeAt(bar, 0)) {
        TempoChange moved = *c;
        moved.beatsPerBar = sig.num, moved.beatUnit = sig.den;
        setChange(moved);
        return;
    }
    // (a new change: the tempo there as it was; inside a ramp it goes on ramping to the next point)
    TempoMapPtr m = map();
    double b = m->barBeat(bar - 1);
    setChange(TempoChange{bar, m->bpmAtBeat(b), sig.num, 0.0, rampsAt(*m, b), sig.den});
}

bool Timing::removeSignature(int bar) {
    const TempoChange* c = changeAt(bar, 0);
    if (!c) return false;
    TempoChange kept = *c;
    removeChange(bar, 0);
    TempoMapPtr m = map();  // (without it)
    double b = m->barBeat(bar - 1);
    if (kept.ramp || rampsAt(*m, b) || m->bpmAtBeat(b) != kept.bpm) {  // (it changes the tempo too: that stays)
        TimeSig before = m->meterAtBeat(b).sig();
        kept.beatsPerBar = before.num, kept.beatUnit = before.den;
        setChange(kept);
    }
    return true;
}

bool Timing::moveSignature(int fromBar, int toBar) {
    if (fromBar < 2 || toBar < 2 || !changeAt(fromBar, 0)) return false;
    TimeSig sig = changeAt(fromBar, 0)->sig();
    if (fromBar == toBar) return true;
    removeSignature(fromBar);
    setSignature(toBar, sig);
    return true;
}

int Timing::setSignatureNear(double beat, TimeSig sig) {
    if (!std::isfinite(beat) || beat < 0) {
        util::debugf("timing: a time signature %d/%d at beat %g: not placed\n", sig.num, sig.den, beat);
        return 0;
    }
    TempoMapPtr m = map();
    int bar = m->barAtBeat(beat);  // (0-based)
    double into = beat - m->barBeat(bar);
    // inside a bar: to the nearest bar line (from its middle on, the next one)
    if (into >= m->meterAtBeat(beat).barQuarters() / 2) bar++;
    if (into > EPSILON) util::debugf("timing: a time signature %d/%d at beat %g (inside a bar) set at bar %d\n", sig.num,
                                     sig.den, beat, bar + 1);
    if (bar > 0 && !changeAt(bar + 1, 0) && sigAtBar(bar + 1) == sig) return bar + 1;
    setSignature(bar + 1, sig);
    return bar + 1;
}

bool Timing::plainSignature() const {
    const std::vector<Meter>& ms = map()->meters();
    return std::all_of(ms.begin(), ms.end(), [](const Meter& m) { return m.sig() == TimeSig{4, 4}; });
}

// --- the grid ---

std::optional<Frac> Timing::fixedStep() const {
    if (!(grid > 0)) return std::nullopt;
    Frac step = Frac::fromDouble(grid, 96);
    const std::vector<Frac>& steps = timing::fixedSteps(thirds);
    if (std::find(steps.begin(), steps.end(), step) != steps.end()) return step;
    // (thirds switched since: the nearest of theirs)
    Frac best = steps[0];
    double gap = std::fabs((steps[0] - step).value());
    for (auto& s : steps) {
        double g = std::fabs((s - step).value());
        if (g < gap) best = s, gap = g;
    }
    return best;
}

Frac Timing::adaptiveStep(double pps, double seconds) const { return adaptiveStepOn(*map(), pps, seconds); }

Frac Timing::adaptiveStepOn(const TempoMap& m, double pps, double seconds) const {
    double beatPx = pps * m.beatSAt(seconds);  // (the tempo there: inside a ramp, as far as it has got)
    Frac bar = m.sigAt(seconds).barQuarters();
    for (const Frac& step : thirds ? timing::THIRDS_STEPS : timing::STRAIGHT_STEPS)
        if (step < bar && (bar / step).den == 1 && step.value() * beatPx >= config::GRID_MIN_PX) return step;
    Frac step = bar;
    for (int i = 0; i < 60 && step.value() * beatPx < config::GRID_MIN_PX; i++) step = step * Frac(2);
    return step;
}

Frac Timing::gridStep(double pps, double seconds, bool drawing) const { return gridStepOn(*map(), pps, seconds, drawing); }

Frac Timing::gridStepOn(const TempoMap& m, double pps, double seconds, bool drawing) const {
    Frac adaptive = adaptiveStepOn(m, pps, seconds);
    std::optional<Frac> fixed = fixedStep();
    if (!fixed) return adaptive;
    Frac step = *fixed;
    if (step == timing::WHOLE_BAR && !thirds) step = m.sigAt(seconds).barQuarters();
    return drawing ? std::max(step, adaptive) : step;
}

void Timing::stepGrid(bool finer, double pps, double seconds) {
    const std::vector<Frac>& steps = timing::fixedSteps(thirds);
    std::optional<Frac> fixed = fixedStep();
    Frac now;
    if (fixed) {
        now = *fixed;
    } else {
        now = pps > 0 ? adaptiveStep(pps, seconds) : Frac(1, thirds ? 3 : 4);
        if (std::find(steps.begin(), steps.end(), now) == steps.end() &&
            now >= sigAt(seconds).barQuarters() && !thirds)
            now = timing::WHOLE_BAR;  // (a bar or more shown: the coarsest is "1 bar")
    }
    std::vector<Frac> below, above;
    for (auto& s : steps) {
        if (s < now) below.push_back(s);
        if (s > now) above.push_back(s);
    }
    Frac chosen = finer ? (below.empty() ? steps.front() : below.back()) : (above.empty() ? steps.back() : above.front());
    grid = chosen.value();
}

std::string Timing::gridName() const {
    std::optional<Frac> step = fixedStep();
    if (!step) return "follows the zoom";
    Frac whole = *step / Frac(4);  // (in whole notes: a beat is a quarter)
    if (thirds) return "1/" + std::to_string((int)(whole.den * 2 / 3)) + " triplets";
    return *step == Frac(4) ? "1 bar" : "1/" + std::to_string(whole.den);
}

double Timing::snap(double seconds, double pps, bool floor) const {
    seconds = std::max(0.0, seconds);
    TempoMapPtr m = map();
    // (in beats, counted from where the time signature began; inside a ramp the grid is on its beats)
    double b = m->beatAt(seconds);
    const Meter& meter = m->meterAtBeat(b);
    double mBeat = meter.beat;
    double step = gridStepOn(*m, pps, seconds).value();
    double bar = meter.barQuarters();
    double atBeat;
    double ratio = bar / step;
    double beats_ = b - mBeat;
    if (step < bar && ratio - std::floor(ratio) > 1e-9) {  // (a step that doesn't fit a bar: counted from its bar line)
        double barStart = std::floor(beats_ / bar + 1e-9) * bar;
        double within = beats_ - barStart;
        double low = std::floor(within / step + 1e-9) * step;
        double high = std::min(low + step, bar);
        atBeat = mBeat + barStart + (floor || within - low <= high - within ? low : high);
    } else {
        double steps = beats_ / step;
        // Python's round() rounds halves to even; std::nearbyint does the same in the default mode
        atBeat = mBeat + (floor ? std::floor(steps + 1e-9) : std::nearbyint(steps)) * step;
    }
    double at = m->secondsAt(atBeat);
    if (!floor) {  // (the next change at a bar's start is nearer: its bar)
        const Segment& s = m->segmentAt(seconds);
        for (const Segment* next = m->following(s); next && next->seconds < seconds + std::fabs(at - seconds);
             next = m->following(*next))
            if (next->beat == next->barBeat) {
                if (std::fabs(next->seconds - seconds) < std::fabs(at - seconds)) at = next->seconds;
                break;
            }
    }
    return std::max(0.0, at);
}

std::vector<GridLine> Timing::lines(double t0, double t1, double pps) const {
    std::vector<GridLine> found;
    for (auto& line : linesBetween(t0, t1, pps))
        if (t0 - 1e-9 <= line.seconds && line.seconds <= t1 + 1e-9) found.push_back(std::move(line));
    return found;
}

std::vector<GridLine> Timing::linesBetween(double t0, double t1, double pps) const {
    std::vector<GridLine> found;
    TempoMapPtr m = map();
    const std::vector<Segment>& segs = m->segments();
    const double inf = std::numeric_limits<double>::infinity();
    double b0 = m->beatAt(t0), b1 = m->beatAt(t1);
    for (size_t i = 0; i < segs.size(); i++) {
        const Segment& s = segs[i];
        double end = i + 1 < segs.size() ? segs[i + 1].seconds : inf;
        double endBeat = i + 1 < segs.size() ? segs[i + 1].beat : inf;
        if (end < t0 || s.seconds > t1) continue;
        // (a ramp: the step and labels for its fastest end, so lines are never too close)
        Frac step = std::max(gridStepOn(*m, pps, std::max(t0, s.seconds), true),
                             gridStepOn(*m, pps, std::max(s.seconds, std::min(t1, end) - 1e-9), true));
        const Meter& meter = m->meterAtBeat(s.beat);
        int mBar = meter.bar;
        double mBeat = meter.beat;
        Frac bar = meter.sig().barQuarters(), unit = meter.sig().unitQuarters();  // (a counted beat: the unit)
        double fastest = std::max(s.bpm, end < inf ? s.bpmAfter(endBeat - s.beat) : s.bpm);
        double beatPx = pps * 60.0 / fastest;
        int64_t every = 1;  // label every this many bars
        while ((double)every * meter.barQuarters() * beatPx < config::BAR_LABEL_MIN_PX && every < (int64_t(1) << 40))
            every *= 2;
        bool labelBeats = unit.value() * beatPx >= config::BEAT_LABEL_MIN_PX && (unit / step).den == 1;
        double stepV = step.value();
        int64_t first = std::max<int64_t>(0, (int64_t)std::floor((std::max(b0, s.beat) - mBeat) / stepV));
        int64_t last = (int64_t)std::ceil((std::min(b1, endBeat) - mBeat) / stepV) + 1;
        std::vector<Frac> positions;
        if (step < bar && (bar / step).den != 1) {  // (a step that doesn't fit a bar: from each bar line)
            int64_t perBar = (bar / step).floor() + 1;  // ceil(bar / step) (never a whole number here)
            int64_t c0 = (Frac(first) * step / bar).floor(), c1 = (Frac(last) * step / bar).floor() + 2;
            for (int64_t c = c0; c < c1; c++)
                for (int64_t j = 0; j < perBar; j++) positions.push_back(Frac(c) * bar + Frac(j) * step);
        } else {
            for (int64_t k = first; k < last; k++) positions.push_back(Frac(k) * step);
        }
        for (const Frac& pos : positions) {
            double beatAbs = mBeat + pos.value();
            if (beatAbs < s.beat - 1e-9) continue;  // (a segment starting inside a bar: from its start)
            if (beatAbs >= endBeat - 1e-9) break;
            double seconds = s.seconds + s.secondsFor(beatAbs - s.beat);
            if (seconds >= end - 1e-9) break;
            int64_t barIndex = (pos / bar).floor();
            Frac beat = (pos - Frac(barIndex) * bar) / unit;  // (in counted beats)
            int64_t barNumber = mBar + barIndex;
            GridLine line{seconds, timing::FINE, std::string()};
            if (beat.num == 0) {
                line.strength = timing::BAR;
                if (barNumber % every == 0) line.label = std::to_string(barNumber + 1);
            } else if (beat.den == 1) {
                line.strength = timing::BEAT;
                if (labelBeats) line.label = std::to_string(barNumber + 1) + "." + std::to_string(beat.num + 1);
            }
            found.push_back(std::move(line));
        }
    }
    return found;
}

void Timing::position(double seconds, int& bar, int& beat, int& part) const {
    TempoMapPtr m = map();
    double at = m->beatAt(seconds) + EPSILON;
    const Meter& meter = m->meterAtBeat(at);
    double unit = 4.0 / meter.beatUnit;  // (counted in the signature's unit: 8ths in 6/8)
    double beats_ = (at - meter.beat) / unit;
    double b = std::floor(beats_ / meter.beatsPerBar);
    double within = beats_ - b * meter.beatsPerBar;  // Python's divmod (floor)
    bar = meter.bar + (int)b + 1;
    beat = (int)std::floor(within) + 1;
    double frac = within - std::floor(within);
    part = (int)(frac * (thirds ? 3 : std::max(1, 16 / meter.beatUnit))) + 1;  // (16ths: 4 a quarter, 2 an 8th)
}

bool TapTempo::tapping(double now) const { return !taps_.empty() && now - taps_.back() <= config::TAP_RESET_S; }

double TapTempo::tap(double now) {
    if (!taps_.empty() && now - taps_.back() > config::TAP_RESET_S) taps_.clear();
    taps_.push_back(now);
    if ((int)taps_.size() > config::TAP_COUNT) taps_.erase(taps_.begin(), taps_.end() - config::TAP_COUNT);
    if (taps_.size() < 2 || taps_.back() <= taps_.front()) return 0;
    double bpm = 60.0 * (taps_.size() - 1) / (taps_.back() - taps_.front());
    bpm = std::min(std::max(bpm, config::MIN_BPM), config::MAX_BPM);
    return std::nearbyint(bpm * 100) / 100;
}
