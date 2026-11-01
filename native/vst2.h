// The VST 2.4 plugin binary interface, as far as Tiny DAW needs it to host plugins.
//
// Declared here from the interface's public behaviour (how plugins and hosts talk: the plugin's
// structure, the numbers of the commands passed each way), for interoperability. It is not
// Steinberg's VST2 SDK, which Steinberg no longer licenses. Only what the host uses is here.
#pragma once
#include <cstdint>

namespace vst2 {

using intptr = std::intptr_t;
struct Effect;

// host <- plugin, and plugin <- host: (effect, command, index, value, pointer, float)
using Callback = intptr (*)(Effect*, int32_t, int32_t, intptr, void*, float);
using ProcessFunction = void (*)(Effect*, float** inputs, float** outputs, int32_t frames);
using SetParameterFunction = void (*)(Effect*, int32_t index, float value);
using GetParameterFunction = float (*)(Effect*, int32_t index);
using EntryPoint = Effect* (*)(Callback host);  // the DLL's "VSTPluginMain" (or, older, "main")

constexpr int32_t MAGIC = 0x56737450;  // 'VstP'

// the plugin, as its DLL hands it over
struct Effect {
    int32_t magic;
    Callback dispatcher;
    ProcessFunction process_accumulating;  // (very old plugins only)
    SetParameterFunction set_parameter;
    GetParameterFunction get_parameter;
    int32_t programs, parameters, inputs, outputs, flags;
    intptr host_reserved[2];  // (the host's own: Tiny DAW keeps its plugin object here)
    int32_t initial_delay;  // frames of latency
    int32_t real_qualities, off_qualities;
    float io_ratio;
    void* object;
    void* user;
    int32_t unique_id, version;
    ProcessFunction process_replacing;
    void* process_double_replacing;
    char future[56];
};

namespace flag {
constexpr int32_t HAS_EDITOR = 1 << 0;
constexpr int32_t CAN_REPLACE = 1 << 4;
constexpr int32_t PROGRAM_CHUNKS = 1 << 5;
constexpr int32_t IS_SYNTH = 1 << 8;
}  // namespace flag

// commands to the plugin (its dispatcher)
namespace effect {
constexpr int32_t OPEN = 0, CLOSE = 1, SET_PROGRAM = 2, GET_PROGRAM = 3;
constexpr int32_t GET_PARAM_LABEL = 6, GET_PARAM_DISPLAY = 7, GET_PARAM_NAME = 8;
constexpr int32_t SET_SAMPLE_RATE = 10, SET_BLOCK_SIZE = 11, MAINS_CHANGED = 12;
constexpr int32_t EDIT_GET_RECT = 13, EDIT_OPEN = 14, EDIT_CLOSE = 15, EDIT_IDLE = 19;
constexpr int32_t GET_CHUNK = 23, SET_CHUNK = 24, PROCESS_EVENTS = 25, CAN_BE_AUTOMATED = 26;
constexpr int32_t GET_PLUG_CATEGORY = 35;
constexpr int32_t GET_EFFECT_NAME = 45, GET_VENDOR_STRING = 47, GET_PRODUCT_STRING = 48;
constexpr int32_t CAN_DO = 51, GET_VST_VERSION = 58;
constexpr int32_t START_PROCESS = 71, STOP_PROCESS = 72;
}  // namespace effect

// commands from the plugin (to the host's callback)
namespace host {
constexpr int32_t AUTOMATE = 0, VERSION = 1, CURRENT_ID = 2, IDLE = 3, WANT_MIDI = 6, GET_TIME = 7;
constexpr int32_t PROCESS_EVENTS = 8, IO_CHANGED = 13, NEED_IDLE = 14, SIZE_WINDOW = 15;
constexpr int32_t GET_SAMPLE_RATE = 16, GET_BLOCK_SIZE = 17, GET_INPUT_LATENCY = 18, GET_OUTPUT_LATENCY = 19;
constexpr int32_t GET_CURRENT_PROCESS_LEVEL = 23, GET_AUTOMATION_STATE = 24;
constexpr int32_t GET_VENDOR_STRING = 32, GET_PRODUCT_STRING = 33, GET_VENDOR_VERSION = 34;
constexpr int32_t CAN_DO = 37, GET_LANGUAGE = 38, GET_DIRECTORY = 41, UPDATE_DISPLAY = 42;
constexpr int32_t BEGIN_EDIT = 43, END_EDIT = 44;
}  // namespace host

constexpr int32_t CATEGORY_SYNTH = 2, CATEGORY_SHELL = 10;
constexpr int32_t PROCESS_LEVEL_USER = 1, PROCESS_LEVEL_REALTIME = 2;

constexpr int32_t MIDI_EVENT = 1;

struct MidiEvent {
    int32_t type = MIDI_EVENT;
    int32_t byte_size = sizeof(MidiEvent);
    int32_t delta_frames = 0;
    int32_t flags = 0;
    int32_t note_length = 0, note_offset = 0;
    char midi[4] = {0, 0, 0, 0};
    char detune = 0, note_off_velocity = 0, reserved1 = 0, reserved2 = 0;
};

// a list of events: the count, then pointers (as many as there are; declared with two)
struct Events {
    int32_t count;
    intptr reserved;
    void* events[2];
};

namespace time_flag {
constexpr int32_t CHANGED = 1, PLAYING = 2, PPQ_VALID = 1 << 9, TEMPO_VALID = 1 << 10, BARS_VALID = 1 << 11;
constexpr int32_t TIME_SIG_VALID = 1 << 13;
}  // namespace time_flag

struct TimeInfo {
    double sample_pos, sample_rate, nano_seconds, ppq_pos, tempo, bar_start_pos, cycle_start_pos, cycle_end_pos;
    int32_t time_sig_numerator, time_sig_denominator, smpte_offset, smpte_frame_rate, samples_to_next_clock, flags;
};

struct Rect {
    int16_t top, left, bottom, right;
};

static_assert(sizeof(Effect) == 192, "the plugin structure must match the interface (64-bit)");
static_assert(sizeof(MidiEvent) == 32, "a MIDI event must match the interface");

}  // namespace vst2
