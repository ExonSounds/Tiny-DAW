// Bars and beats: the project's tempo (and its changes), and the grid the ruler shows and clicks snap
// to (timing.py).
//
// Bar 1 starts at 0 s. Beats are quarter notes everywhere (tempo, seconds, every saved position). The time
// signature is beatsPerBar / beatUnit (TimeSig: 7/8, 6/8, 3/2…; beatUnit 1, 2, 4, 8, 16 or 32), so a bar is
// beatsPerBar · 4 / beatUnit quarter notes (TimeSig::barQuarters: 7/8 → 7/2), not always a whole number.
// What a bar counts (the ruler's "5.3", the metronome's clicks, position()) is the signature's unit: eighths
// in 6/8. The tempo can change at any beat and the time signature at the start of any bar
// (`changes`: TempoChange, at (bar, beat into it); `bpm`, `beatsPerBar`, `beatUnit` and `ramp` are bar 1's). A change
// is a STEP (the new tempo from there) or, with `ramp`, a RAMP: the tempo goes linearly, in BPM over beats,
// from the change's to the next change's (the last change can't ramp: it holds). A time signature belongs
// to a change at the start of a bar (beat 0); a change inside a bar changes the tempo only.
//
// The song is then a row of segments (segments()): each a stretch of one time signature over which the
// tempo is steady or goes linearly (bpm(b) = bpm + k·b, b beats into it). Beats are counted on through
// them. Time over a segment is the closed form ∫60/bpm db: (60/k)·ln(1 + k·b/bpm) (60·b/bpm when steady),
// and beatAt is its exact inverse (bpm/k)·(e^(k·t/60) − 1) (log1p / expm1 keep both exact for gentle
// ramps): no numeric steps anywhere, so beatAt(secondsAt(b)) == b to rounding.
//
// What stays where when the map changes is the song's TIMEBASE (timebase.h): Beats keeps clips, notes,
// automation and markers on their beats; Time keeps them in seconds. The grid, the ruler, snapping, the
// metronome, MIDI effects and the tempo plugins are given follow the map either way.
//
// The grid is dynamic: its lines are always at least GRID_MIN_PX apart, so zooming in makes it
// finer (bars → beats → 8ths → 16ths → 32nds) and zooming out coarser (2, 4, 8… bars). In thirds
// view (triplets) the steps within a bar divide beats in three instead: half-note, quarter, 8th,
// 16th and 32nd triplets. Grid positions are counted in beats as exact fractions, so thirds stay
// exact; only steps that fit a whole number of times in a bar are used, so the grid always lines up
// with the bars (counted from the last change at a bar's start). Inside a ramp the lines stay on their
// beats, closer together as the tempo rises. A beat line (labelled "bar.beat") is on the signature's unit.
//
// A fixed grid (the Grid button, `grid`) keeps its step at any zoom. Its "1 bar" is a bar of the
// time signature where it is (3 beats in 3/4, 3.5 in 7/8); a step that doesn't fit a whole number of times in a
// bar (1/2 in 3/4) is counted from each bar line, so every bar line stays a grid line (the bar's
// last step is shorter).
//
// THREADS. The tempo map (TempoMap: the segments) is an immutable object. The GUI thread uses
// Timing's own functions (each works on map(), rebuilt when bpm / beatsPerBar / ramp / changes changed).
// The audio thread (metronome, MIDI effects, the transport given to plugins) reads
// `timing.audioMap()`: the map as last published, an atomic snapshot. It is published whenever the
// GUI thread calls map() (any of Timing's tempo or grid functions) or publish(): the engine's idle()
// calls project.timing.publish() every frame, so a change of tempo is heard at once.
#pragma once
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

// an exact fraction (beats)
struct Frac {
    int64_t num = 0, den = 1;
    Frac() = default;
    Frac(int64_t n, int64_t d = 1);
    // the nearest fraction with a denominator at most maxDen (Python's Fraction(x).limit_denominator)
    static Frac fromDouble(double x, int64_t maxDen = 96);
    double value() const { return (double)num / (double)den; }
    int64_t floor() const;  // the whole number at or below it
    Frac operator+(const Frac& o) const { return Frac(num * o.den + o.num * den, den * o.den); }
    Frac operator-(const Frac& o) const { return Frac(num * o.den - o.num * den, den * o.den); }
    Frac operator*(const Frac& o) const { return Frac(num * o.num, den * o.den); }
    Frac operator/(const Frac& o) const { return Frac(num * o.den, den * o.num); }
    bool operator<(const Frac& o) const { return num * o.den < o.num * den; }
    bool operator<=(const Frac& o) const { return num * o.den <= o.num * den; }
    bool operator>(const Frac& o) const { return o < *this; }
    bool operator>=(const Frac& o) const { return o <= *this; }
    bool operator==(const Frac& o) const { return num == o.num && den == o.den; }
    bool operator!=(const Frac& o) const { return !(*this == o); }
};

namespace timing {
enum Strength { FINE = 0, BEAT = 1, BAR = 2 };
constexpr double EPSILON = 1e-4;  // beats; a position rounded to whole frames still counts as on its beat
extern const std::vector<Frac> STRAIGHT_STEPS;  // 1/8, 1/4, 1/2, 1, 2 beats
extern const std::vector<Frac> THIRDS_STEPS;    // 1/12, 1/6, 1/3, 2/3, 4/3
// a fixed grid's choices (thirds or not): STRAIGHT_STEPS + 4 ("1 bar"), or THIRDS_STEPS
const std::vector<Frac>& fixedSteps(bool thirds);
const Frac WHOLE_BAR(4);  // the fixed "1 bar" choice: a bar of the time signature where it is (gridStep)
// a signature's unit (its denominator) as read: 1, 2, 4, 8, 16 or 32; anything else (missing, 0, not a power
// of two) is 4, with a debug message naming `where`
bool validUnit(int den);
int checkedUnit(int den, const char* where = "time signature");
int clampedBeats(int num);  // a numerator kept in 1…MAX_BEATS_PER_BAR
}  // namespace timing

// a time signature: `num` beats of 1/`den` notes a bar (6/8: six eighths)
struct TimeSig {
    int num = 4, den = 4;
    Frac barQuarters() const { return Frac(num * 4, den); }  // a bar in quarter notes (7/8 → 7/2)
    Frac unitQuarters() const { return Frac(4, den); }       // one counted beat in quarter notes (8ths: 1/2)
    // compound (6/8, 9/8, 12/8, 6/16…): counted in dotted groups of three units
    bool compound() const { return den >= 8 && num > 3 && num % 3 == 0; }
    std::string text() const { return std::to_string(num) + "/" + std::to_string(den); }  // "7/8"
    // "7/8", or "7" (a numerator alone: /4); false if it isn't one (the fallback rules applied to what is)
    static bool parse(const std::string& text, TimeSig& out);
    bool operator==(const TimeSig& o) const { return num == o.num && den == o.den; }
    bool operator!=(const TimeSig& o) const { return !(*this == o); }
};

struct GridLine {
    double seconds;
    int strength;       // timing::BAR / BEAT / FINE
    std::string label;  // "5" for bar 5, "5.3" for its third beat, or "" (none)
};

// a change of tempo (and, at a bar's start, of time signature) at `beat` beats into bar `bar` (1-based; in
// bar 1 only after its start)
struct TempoChange {
    int bar = 2;
    double bpm = 120;
    int beatsPerBar = 4;  // from here on; only a change at a bar's start (beat 0) sets it (the numerator)
    double beat = 0;      // quarter notes into the bar (0: at its start); past its end: at the next bar's start
    bool ramp = false;    // the tempo goes linearly from here to the next change's (in BPM over beats)
    int beatUnit = 4;     // the signature's denominator, with beatsPerBar
    TimeSig sig() const { return TimeSig{beatsPerBar, beatUnit}; }
    bool operator==(const TempoChange& o) const {
        return bar == o.bar && bpm == o.bpm && beatsPerBar == o.beatsPerBar && beat == o.beat && ramp == o.ramp &&
               beatUnit == o.beatUnit;
    }
    bool operator!=(const TempoChange& o) const { return !(*this == o); }
    bool operator<(const TempoChange& o) const {  // (by place, then Python's tuple order)
        if (bar != o.bar) return bar < o.bar;
        if (beat != o.beat) return beat < o.beat;
        if (bpm != o.bpm) return bpm < o.bpm;
        if (beatsPerBar != o.beatsPerBar) return beatsPerBar < o.beatsPerBar;
        if (ramp != o.ramp) return ramp < o.ramp;
        return beatUnit < o.beatUnit;
    }
    bool atBar() const { return beat == 0; }  // at a bar's start: it sets the time signature too
    bool valid() const { return bar >= 1 && beat >= 0 && (bar >= 2 || beat > 0); }
};

// a stretch of one time signature over which the tempo is steady or one linear ramp: from `seconds`
// (`beat` beats since 0 s, in bar `bar`, 0-based, which starts at beat `barBeat`) to the next one
struct Segment {
    double seconds = 0;
    double beat = 0;
    int bar = 0;
    double bpm = 120;      // at its start
    int beatsPerBar = 4;   // its time signature: beatsPerBar / beatUnit
    int beatUnit = 4;
    double k = 0;          // BPM more per beat: 0 steady, else a ramp's slope
    double barBeat = 0;    // where its bar starts (= beat when it starts on a bar line)
    double beatS() const { return 60.0 / bpm; }  // seconds per beat at its start
    TimeSig sig() const { return TimeSig{beatsPerBar, beatUnit}; }
    double barQuarters() const { return beatsPerBar * 4.0 / beatUnit; }  // a bar's length in beats (exact)
    double bpmAfter(double beats) const { return bpm + k * beats; }  // the tempo `beats` into it
    // seconds from its start to `beats` into it, and back: the exact closed forms (before it: steady)
    double secondsFor(double beats) const;
    double beatsFor(double seconds) const;
};

// a tempo change as the map placed it (the tempo lane's points): bar 1's (change -1) and each of `changes`
struct TempoPoint {
    double beat = 0, seconds = 0, bpm = 120;
    bool ramp = false;  // it ramps (and there is a next point to ramp to)
    int change = -1;    // its index in TempoMap::changes; -1: bar 1's
};

// a counted beat (the signature's unit: an eighth in 6/8)
struct BeatTime {
    double seconds;
    bool barStart;            // the first beat of a bar
    bool groupStart = false;  // (not a bar's) the first of a dotted group in a compound signature (6/8's 4th)
};

// where a time signature begins: bar `bar` (0-based), `beat` beats since 0 s
struct Meter {
    int bar;
    double beat;
    int beatsPerBar;
    int beatUnit = 4;
    TimeSig sig() const { return TimeSig{beatsPerBar, beatUnit}; }
    double barQuarters() const { return beatsPerBar * 4.0 / beatUnit; }
};

// The segments of a tempo map (immutable; see THREADS above). Any thread.
class TempoMap {
public:
    TempoMap(double bpm = 120, int beatsPerBar = 4, std::vector<TempoChange> changes = {}, bool ramp = false,
             int beatUnit = 4);
    const double bpm;       // bar 1's
    const int beatsPerBar;  // bar 1's
    const int beatUnit;     // bar 1's
    const std::vector<TempoChange> changes;  // sorted by place
    const bool ramp;        // bar 1's tempo ramps to the first change's
    const std::vector<Segment>& segments() const { return segs_; }
    const std::vector<TempoPoint>& points() const { return points_; }  // (by place; two at one place: the later)
    const Segment& segmentAt(double seconds) const;
    const Segment& segmentAtBeat(double beat) const;
    const Segment* following(const Segment& s) const;  // the next segment, or null
    double beatAt(double seconds) const;  // beats since 0 s (through every segment) at `seconds`
    double secondsAt(double beat) const;
    double barSeconds(int bar) const;     // where bar `bar` (0-based) starts, in seconds
    double barBeat(int bar) const;        // …and in beats
    int barAt(double seconds) const;      // the bar (0-based) `seconds` falls in
    int barAtBeat(double beat) const;
    double bpmAt(double seconds) const;   // the tempo at that moment (inside a ramp: as far as it has got)
    double bpmAtBeat(double beat) const;
    double beatSAt(double seconds) const { return 60.0 / bpmAt(seconds); }
    int beatsPerBarAt(double seconds) const { return segmentAt(seconds).beatsPerBar; }  // the numerator
    TimeSig sigAt(double seconds) const { return segmentAt(seconds).sig(); }
    double barQuartersAt(double seconds) const { return segmentAt(seconds).barQuarters(); }  // a bar's beats
    // every time signature: bar 1's, then each change at a bar's start (the tempo lane's signatures)
    const std::vector<Meter>& meters() const { return meters_; }
    double beatOf(const TempoChange& c) const;  // where a change is, in beats since 0 s
    // the time signature at `beat` and the bar (0-based) and beat where it began
    const Meter& meterAtBeat(double beat) const;
    // every counted beat (the signature's unit) from t0 to t1 seconds (the metronome); the audio thread passes a vector with room
    // (cleared first; it only allocates when it has to grow)
    void beatTimes(double t0, double t1, std::vector<BeatTime>& out) const;
    std::vector<BeatTime> beatTimes(double t0, double t1) const;
    bool hasChanges() const { return segs_.size() > 1 || segs_[0].k != 0; }

private:
    std::vector<Meter> meters_;  // bar 1's, then each change at a bar's start
    std::vector<Segment> segs_;
    std::vector<TempoPoint> points_;
};
using TempoMapPtr = std::shared_ptr<const TempoMap>;

struct Timing {
    double bpm;
    int beatsPerBar;       // bar 1's time signature: beatsPerBar / beatUnit
    int beatUnit = 4;
    bool beats = true;     // edit in bars and beats (ruler, grid, snapping); false = minutes and seconds
    bool thirds = false;   // the grid divides beats in thirds (triplets)
    double loopStart = 0;  // the loop range, in seconds (end <= start: none chosen yet)
    double loopEnd = 0;
    bool looping = false;  // playback jumps from the loop's end back to its start
    std::vector<TempoChange> changes;  // by place: the tempo and time signature from there (GUI thread)
    bool ramp = false;     // bar 1's tempo ramps to the first change's
    // the song's timebase (timebase.h): true Beats (it stays on its beats when the tempo changes), false Time
    // (it stays in seconds). New projects: Beats; project files from before the setting load as Time.
    bool timebaseBeats = true;
    double grid = 0;       // a fixed grid step in beats (STRAIGHT_STEPS / THIRDS_STEPS / 4); 0 = it follows the zoom
    bool snapOn = true;    // clicks and drags snap to the grid (Ctrl+4); Alt does the opposite for one drag
    int key = 0;           // the song's key (0 = C … 11 = B), with `scale`: the MIDI editor's scale mode
    std::string scale;     // a scale of the MIDI effects' SCALES ("Major", "Minor"…); "" = none
    double swing = 0;      // the song's swing, 0 % (straight) … 100 %: off-beats later (midi_effects.h SWING)
    int swingGrid = 1;     // what swings: 0 the 8ths, 1 the 16ths
    bool automationFollows = true;  // moved, copied and duplicated clips take their tracks' automation along (auto_follow.h)
    // the tempo lane's BPM range, bottom to top (a view: saved with the song, not an undo step; history.h). Auto (it
    // fits the points) unless MIN_BPM <= tempoLaneLo < tempoLaneHi <= MAX_BPM: 0 / 0, Max <= Min or out of range
    double tempoLaneLo = 0, tempoLaneHi = 0;
    bool tempoLaneFixed() const;  // a fixed range (not Auto)?

    Timing();
    Timing(const Timing& o);
    Timing& operator=(const Timing& o);

    // (start, end) of the loop range, if there is one
    bool loopRange(double& start, double& end) const;

    // --- the tempo map ---
    TempoMapPtr map() const;       // GUI thread: the map of the fields as they are now (and publishes it)
    void publish() const { map(); }
    TempoMapPtr audioMap() const;  // any thread: the map as last published
    const std::vector<Segment>& segments() const { return map()->segments(); }  // (valid until the next change)
    Segment segmentAt(double seconds) const { return map()->segmentAt(seconds); }
    // the tempo and time signature from bar `bar` (≥ 2) on (a ramp there stays one)
    void setChange(int bar, double bpm, int beatsPerBar, int beatUnit = 4);
    void setChange(const TempoChange& c);  // (replacing the one at its place)
    void removeChange(int bar, double beat = 0);  // no change at bar `bar` (`beat` into it) any more
    const TempoChange* changeAtBar(int bar) const { return changeAt(bar, 0); }  // null if none
    const TempoChange* changeAt(int bar, double beat) const;
    double beatAt(double seconds) const { return map()->beatAt(seconds); }
    double secondsAt(double beat) const { return map()->secondsAt(beat); }
    double barSeconds(int bar) const { return map()->barSeconds(bar); }
    int barAt(double seconds) const { return map()->barAt(seconds); }
    double bpmAt(double seconds) const { return map()->bpmAt(seconds); }
    double barBeat(int bar) const { return map()->barBeat(bar); }
    double beatSAt(double seconds) const { return map()->beatSAt(seconds); }
    int beatsPerBarAt(double seconds) const { return map()->beatsPerBarAt(seconds); }
    TimeSig sigAt(double seconds) const { return map()->sigAt(seconds); }
    double barQuartersAt(double seconds) const { return map()->barQuartersAt(seconds); }
    TimeSig sig() const { return TimeSig{beatsPerBar, beatUnit}; }  // bar 1's

    // --- time signatures by bar (1-based; the tempo lane). Each is a change at the bar's start ---
    TimeSig sigAtBar(int bar) const;  // the signature bar `bar` is in
    // bar `bar` is in `sig` from there on (bar 1: the song's); a new change there keeps the tempo as it was
    // (a ramp through it goes on). The numerator is clamped, a bad denominator is 4 (timing::checkedUnit)
    void setSignature(int bar, TimeSig sig);
    // no time signature at bar `bar` (≥ 2) any more: its change goes, unless it changes the tempo too (then it
    // keeps the signature before it). False if there was none
    bool removeSignature(int bar);
    bool moveSignature(int fromBar, int toBar);  // (≥ 2 both) its signature from one bar to the other
    // a time signature read from a file at `beat` (quarter notes since 0 s), on the bar line nearest it; one that
    // keeps the signature in force there (and no change there) adds nothing. Returns the bar (1-based); 0: none
    // (a beat below 0 or not finite)
    int setSignatureNear(double beat, TimeSig sig);
    bool plainSignature() const;  // 4/4 all through (a new project's): no time signature of its own
    std::vector<BeatTime> beatTimes(double t0, double t1) const { return map()->beatTimes(t0, t1); }
    double beatSeconds() const { return 60.0 / bpm; }  // seconds per beat at bar 1 (beatSAt(): anywhere)

    // --- the grid ---
    // do clicks and drags snap to the grid? In bars and beats, when snapping is on (Alt held: the other way)
    bool snaps(bool alt = false) const { return beats && snapOn != alt; }
    std::optional<Frac> fixedStep() const;  // the fixed grid step, or none when the grid follows the zoom
    // beats between grid lines at zoom pps where `seconds` is: the fixed grid's step if there is one
    // (drawing: never closer than GRID_MIN_PX, so a fine grid zoomed out draws coarser lines)
    Frac gridStep(double pps, double seconds = 0, bool drawing = false) const;
    // Ctrl+1 / Ctrl+2: the next finer or coarser fixed grid (from the one shown at zoom pps where
    // `seconds` is, when it follows the zoom; pps <= 0: none shown)
    void stepGrid(bool finer, double pps = 0, double seconds = 0);
    std::string gridName() const;  // "1/16", "1/8 triplets", "1 bar", "follows the zoom"
    Frac adaptiveStep(double pps, double seconds = 0) const;  // the grid that follows the zoom
    // `seconds` moved to the nearest grid line at zoom pps (floor: the one at or before it)
    double snap(double seconds, double pps, bool floor = false) const;
    // every grid line from t0 to t1 seconds; bars labelled every 1, 2, 4… bars and beats once there is room
    std::vector<GridLine> lines(double t0, double t1, double pps) const;
    std::vector<GridLine> linesBetween(double t0, double t1, double pps) const;  // (and a line either side)
    // (bar, beat, part) counted from 1: part is the 16th within the beat, or the third in thirds view
    void position(double seconds, int& bar, int& beat, int& part) const;

private:
    mutable TempoMapPtr map_;  // the last map made (read with atomic_load by the audio thread)
    // (the same on a map already in hand: map() compares every change, so loops over many lines take it once)
    Frac adaptiveStepOn(const TempoMap& m, double pps, double seconds) const;
    Frac gridStepOn(const TempoMap& m, double pps, double seconds, bool drawing = false) const;
};

// Works out a tempo from taps (the Tap button): the average interval of the last TAP_COUNT taps.
class TapTempo {
public:
    // a tap at `now` seconds; returns the tempo (BPM) once there are two taps, else 0
    double tap(double now);
    int count() const { return (int)taps_.size(); }  // the taps counted (Python's len(taps))
    // are taps still coming at `now` (the last one less than TAP_RESET_S ago)?
    bool tapping(double now) const;
private:
    std::vector<double> taps_;
};
