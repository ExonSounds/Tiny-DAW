// VST2 plugins in Tiny DAW's plugin host: vst3host.Vst2Plugin(path), with the same methods as
// vst3host.Plugin (see vst3host.cpp), so the program treats both alike.
//
// A VST2 plugin is a DLL whose "VSTPluginMain" (or older "main") hands over the plugin's structure
// (vst2.h); host and plugin talk through one function each way. Threads as with VST3: loading,
// state, names and the window on the main thread, process() and render() on the audio thread
// (they release the GIL). VST2 lets parameters be set from either thread. While the plugin is
// being reconfigured, the audio thread gets its input (or silence) rather than waiting.
//
// A plugin with four or more inputs takes its third and fourth as its sidechain (the VST2 way).
// Its settings are its "chunk" if it keeps one, else the values of all its parameters.
// Shell plugins (several plugins in one DLL, e.g. Waves) aren't supported.
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <windows.h>

#include <algorithm>
#include <atomic>
#include <bitset>
#include <cstring>
#include <map>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

#include "vst2.h"

namespace py = pybind11;

namespace {

constexpr int32_t MAX_BLOCK = 8192;  // frames per call to the plugin (longer buffers are cut up)
constexpr int MAX_EVENTS = 2048;  // notes per buffer
constexpr int MAX_CHANNELS = 32;
const char CHUNK_MAGIC[] = "VST2CHNK", PARAMS_MAGIC[] = "VST2PARM";  // (state: 8 bytes, then the data)

std::wstring wide(const std::string& utf8) {
    int n = MultiByteToWideChar(CP_UTF8, 0, utf8.c_str(), -1, nullptr, 0);
    std::wstring w(n > 0 ? n - 1 : 0, L'\0');
    if (n > 1)
        MultiByteToWideChar(CP_UTF8, 0, utf8.c_str(), -1, w.data(), n);
    return w;
}

std::string last_error() {
    wchar_t* text = nullptr;
    FormatMessageW(FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS, nullptr,
                   GetLastError(), 0, (LPWSTR)&text, 0, nullptr);
    std::string message;
    if (text) {
        int n = WideCharToMultiByte(CP_UTF8, 0, text, -1, nullptr, 0, nullptr, nullptr);
        message.resize(n > 0 ? n - 1 : 0);
        WideCharToMultiByte(CP_UTF8, 0, text, -1, message.data(), n, nullptr, nullptr);
        LocalFree(text);
    }
    while (!message.empty() && (message.back() == '\n' || message.back() == '\r'))
        message.pop_back();
    return message;
}

std::map<std::string, HMODULE>& libraries() {
    static std::map<std::string, HMODULE> loaded;  // never unloaded: plugins live in them
    return loaded;
}

thread_local bool on_audio_thread = false;  // (for the plugins asking which thread calls them)

class Vst2Plugin;
thread_local Vst2Plugin* being_made = nullptr;  // (a plugin calls the host before it knows it)

vst2::intptr host_callback(vst2::Effect* effect, int32_t opcode, int32_t index, vst2::intptr value, void* ptr, float opt);

class Vst2Plugin {
public:
    explicit Vst2Plugin(const std::string& path) : path(path) {
        HMODULE library;
        auto& loaded = libraries();
        auto found = loaded.find(path);
        if (found != loaded.end()) {
            library = found->second;
        } else {
            library = LoadLibraryW(wide(path).c_str());
            if (!library)
                throw std::runtime_error("Cannot load " + path + ": " + last_error());
            loaded[path] = library;
        }
        auto entry = (vst2::EntryPoint)GetProcAddress(library, "VSTPluginMain");
        if (!entry)
            entry = (vst2::EntryPoint)GetProcAddress(library, "main");
        if (!entry)
            throw std::runtime_error(path + " is not a VST2 plugin");
        directory = path.substr(0, path.find_last_of("\\/"));
        being_made = this;
        effect = entry(host_callback);
        being_made = nullptr;
        if (!effect || effect->magic != vst2::MAGIC)
            throw std::runtime_error(path + " is not a VST2 plugin it can start");
        effect->host_reserved[0] = (vst2::intptr)this;
        dispatch(vst2::effect::OPEN);
        category = (int32_t)dispatch(vst2::effect::GET_PLUG_CATEGORY);
        if (category == vst2::CATEGORY_SHELL) {
            dispatch(vst2::effect::CLOSE);
            effect = nullptr;
            throw std::runtime_error(path + " holds several plugins (a shell, like Waves): not supported yet");
        }
        plugin_name = text_of(vst2::effect::GET_EFFECT_NAME);
        if (plugin_name.empty())
            plugin_name = text_of(vst2::effect::GET_PRODUCT_STRING);
        vendor_name = text_of(vst2::effect::GET_VENDOR_STRING);
        zeros.assign(MAX_BLOCK, 0.0f);
        scratch.assign(MAX_CHANNELS, std::vector<float>(MAX_BLOCK, 0.0f));
        in_pointers.assign(std::max(2, std::min(effect->inputs, MAX_CHANNELS)), nullptr);
        out_pointers.assign(std::max(2, std::min(effect->outputs, MAX_CHANNELS)), nullptr);
        midi.resize(MAX_EVENTS);
        event_list.resize(sizeof(vst2::Events) + MAX_EVENTS * sizeof(void*));
        std::memset(&time, 0, sizeof(time));
        set_transport(120.0, 4, 4, false, 0);
        prepare(48000.0);
    }

    ~Vst2Plugin() {
        if (!effect)
            return;
        close_editor();
        {
            std::lock_guard<std::mutex> hold(process_lock);
            suspend();
        }
        dispatch(vst2::effect::CLOSE);
        effect = nullptr;
    }

    vst2::intptr dispatch(int32_t opcode, int32_t index = 0, vst2::intptr value = 0, void* ptr = nullptr, float opt = 0) {
        return effect->dispatcher(effect, opcode, index, value, ptr, opt);
    }

    std::string text_of(int32_t opcode, int32_t index = 0) {
        char text[512] = {0};  // (plugins often write past the interface's short limits)
        dispatch(opcode, index, 0, text);
        text[sizeof(text) - 1] = 0;
        return text;
    }

    // --- facts ---

    std::string name() const { return plugin_name; }
    std::string vendor() const { return vendor_name; }
    std::string subcategories() const { return is_instrument() ? "Instrument" : "Fx"; }
    bool is_instrument() const { return (effect->flags & vst2::flag::IS_SYNTH) || category == vst2::CATEGORY_SYNTH; }
    bool has_sidechain() const { return effect->inputs >= 4 && !is_instrument(); }
    int latency() const { return std::max(0, (int)effect->initial_delay); }
    double sample_rate() const { return rate; }

    // --- setting up ---

    void suspend() {
        if (active) {
            dispatch(vst2::effect::STOP_PROCESS);
            dispatch(vst2::effect::MAINS_CHANGED, 0, 0);
            active = false;
        }
    }

    void resume() {
        dispatch(vst2::effect::MAINS_CHANGED, 0, 1);
        dispatch(vst2::effect::START_PROCESS);
        active = true;
    }

    // (main thread) the sample rate to process at; the plugin starts afresh
    void prepare(double sample_rate) {
        std::lock_guard<std::mutex> hold(process_lock);
        suspend();
        rate = sample_rate;
        time.sample_rate = sample_rate;
        dispatch(vst2::effect::SET_SAMPLE_RATE, 0, 0, nullptr, (float)sample_rate);
        dispatch(vst2::effect::SET_BLOCK_SIZE, 0, MAX_BLOCK);
        resume();
    }

    void reset() {
        std::lock_guard<std::mutex> hold(process_lock);
        suspend();
        resume();
        held.reset();
    }

    // --- processing (audio thread) ---

    void set_transport(double tempo, int num, int den, bool playing, int64_t position) {
        time.tempo = tempo;
        time.time_sig_numerator = num;
        time.time_sig_denominator = den;
        time.sample_pos = (double)position;
        time.ppq_pos = position / std::max(1.0, rate) * tempo / 60.0;
        time.flags = vst2::time_flag::TEMPO_VALID | vst2::time_flag::TIME_SIG_VALID | vst2::time_flag::PPQ_VALID |
                     (playing ? vst2::time_flag::PLAYING : 0);
    }

    void process(py::array_t<float, py::array::c_style> input, py::object sidechain,
                 py::array_t<float, py::array::c_style> output) {
        auto out = output.mutable_unchecked<2>();
        auto in = input.unchecked<2>();
        if (out.shape(0) < 2 || in.shape(1) != out.shape(1))
            throw std::invalid_argument("process(input (channels, n), sidechain, output (2, n))");
        py::array_t<float, py::array::c_style> side;
        const float* side_rows[2] = {nullptr, nullptr};
        if (!sidechain.is_none()) {
            side = py::array_t<float, py::array::c_style>::ensure(sidechain);
            if (!side || side.ndim() != 2 || side.shape(1) != out.shape(1))
                throw std::invalid_argument("sidechain must be (channels, n) like the input");
            side_rows[0] = side.data(0, 0);
            side_rows[1] = side.data(side.shape(0) > 1 ? 1 : 0, 0);
        }
        const float* in_rows[2] = {in.data(0, 0), in.data(in.shape(0) > 1 ? 1 : 0, 0)};
        float* out_rows[2] = {out.mutable_data(0, 0), out.mutable_data(1, 0)};
        int64_t frames = out.shape(1);
        py::gil_scoped_release free;
        run(in_rows, side_rows[0] ? side_rows : nullptr, out_rows, frames, false);
    }

    void render(const std::vector<std::tuple<int, bool, int, int, int>>& notes,
                py::array_t<float, py::array::c_style> output) {
        auto out = output.mutable_unchecked<2>();
        float* out_rows[2] = {out.mutable_data(0, 0), out.mutable_data(1, 0)};
        int64_t frames = out.shape(1);
        pending_notes.insert(pending_notes.end(), notes.begin(), notes.end());  // (after any still waiting)
        py::gil_scoped_release free;
        run(nullptr, nullptr, out_rows, frames, true);
    }

    bool holding() const { return held.any(); }

    void notes_off() { notes_off_wanted = true; }

    void run(const float* const* in_rows, const float* const* side_rows, float* const* out_rows, int64_t frames,
             bool instrument) {
        std::unique_lock<std::mutex> hold(process_lock, std::try_to_lock);
        if (!hold.owns_lock() || !active) {
            for (int c = 0; c < 2; c++)
                if (in_rows && !instrument)
                    std::memmove(out_rows[c], in_rows[c], frames * sizeof(float));
                else
                    std::memset(out_rows[c], 0, frames * sizeof(float));
            // (its notes wait for the next buffer, at its start: a note-off lost here would leave a note on)
            for (auto& note : pending_notes)
                std::get<0>(note) = 0;
            return;
        }
        on_audio_thread = true;
        for (int64_t done = 0; done < frames; done += MAX_BLOCK) {
            int32_t n = (int32_t)std::min<int64_t>(MAX_BLOCK, frames - done);
            process_block(in_rows, side_rows, out_rows, done, n, instrument);
        }
        on_audio_thread = false;
        pending_notes.clear();
    }

    void process_block(const float* const* in_rows, const float* const* side_rows, float* const* out_rows,
                       int64_t at, int32_t n, bool instrument) {
        for (size_t c = 0; c < in_pointers.size(); c++) {
            const float* source = nullptr;
            if (c < 2 && in_rows)
                source = in_rows[c] + at;
            else if (c >= 2 && c < 4 && side_rows && has_sidechain())
                source = side_rows[c - 2] + at;
            in_pointers[c] = const_cast<float*>(source ? source : zeros.data());
        }
        for (size_t c = 0; c < out_pointers.size(); c++)
            out_pointers[c] = c < 2 ? out_rows[c] + at : scratch[std::min<size_t>(c, MAX_CHANNELS - 1)].data();
        send_notes(at, n, instrument);
        if (effect->flags & vst2::flag::CAN_REPLACE) {
            effect->process_replacing(effect, in_pointers.data(), out_pointers.data(), n);
        } else {  // (old plugins add to what is there)
            for (int c = 0; c < 2; c++)
                std::memset(out_rows[c] + at, 0, n * sizeof(float));
            effect->process_accumulating(effect, in_pointers.data(), out_pointers.data(), n);
        }
        if (effect->outputs == 1)
            std::memcpy(out_rows[1] + at, out_rows[0] + at, n * sizeof(float));
        if (time.flags & vst2::time_flag::PLAYING) {
            time.sample_pos += n;
            time.ppq_pos += n / rate * time.tempo / 60.0;
        }
    }

    void send_notes(int64_t at, int32_t n, bool instrument) {
        int count = 0;
        auto add = [&](int32_t frame, bool on, int pitch, int velocity, int channel) {
            if (count >= MAX_EVENTS)
                return;
            vst2::MidiEvent& e = midi[count];
            e = vst2::MidiEvent();
            e.delta_frames = frame;
            e.midi[0] = (char)((on ? 0x90 : 0x80) | (channel & 15));
            e.midi[1] = (char)(pitch & 127);
            e.midi[2] = (char)(on ? std::max(1, velocity & 127) : 0);
            reinterpret_cast<vst2::Events*>(event_list.data())->events[count++] = &e;
            if (on)
                held.set((channel & 15) * 128 + (pitch & 127));
            else
                held.reset((channel & 15) * 128 + (pitch & 127));
        };
        if (notes_off_wanted.exchange(false))
            for (int ch = 0; ch < 16; ch++)
                for (int p = 0; p < 128; p++)
                    if (held.test(ch * 128 + p))
                        add(0, false, p, 0, ch);
        if (instrument)
            for (auto& note : pending_notes) {
                int frame = std::get<0>(note) - (int)at;
                if (frame >= 0 && frame < n)
                    add(frame, std::get<1>(note), std::get<2>(note), std::get<3>(note), std::get<4>(note));
            }
        if (count == 0)
            return;
        auto* list = reinterpret_cast<vst2::Events*>(event_list.data());
        list->count = count;
        list->reserved = 0;
        dispatch(vst2::effect::PROCESS_EVENTS, 0, 0, list);
    }

    // --- parameters ---

    py::list parameters() {
        py::list result;
        for (int32_t i = 0; i < effect->parameters; i++) {
            py::dict d;
            d["id"] = i;
            d["name"] = text_of(vst2::effect::GET_PARAM_NAME, i);
            d["short_name"] = d["name"];
            d["units"] = text_of(vst2::effect::GET_PARAM_LABEL, i);
            d["steps"] = 0;
            d["default"] = effect->get_parameter(effect, i);
            d["automatable"] = true;  // (VST2 plugins rarely say; any parameter can be set)
            d["read_only"] = false;
            d["hidden"] = false;
            d["bypass"] = false;
            d["program_change"] = false;
            result.append(d);
        }
        return result;
    }

    double get_param(int32_t id) { return in_range(id) ? effect->get_parameter(effect, id) : 0.0; }

    void set_param(int32_t id, double value) {  // (either thread: VST2 allows it)
        if (in_range(id))
            effect->set_parameter(effect, id, (float)std::max(0.0, std::min(1.0, value)));
    }

    // the plugin's text for a value: VST2 plugins only describe their current value, so another
    // value is given as a number
    std::string param_text(int32_t id, double value) {
        if (!in_range(id))
            return "";
        if (std::abs(effect->get_parameter(effect, id) - value) > 1e-5) {
            char number[32];
            std::snprintf(number, sizeof(number), "%.2f", value);
            return number;
        }
        std::string display = text_of(vst2::effect::GET_PARAM_DISPLAY, id), label = text_of(vst2::effect::GET_PARAM_LABEL, id);
        auto trim = [](std::string s) {
            s.erase(0, s.find_first_not_of(' '));
            s.erase(s.find_last_not_of(' ') + 1);
            return s;
        };
        display = trim(display);
        label = trim(label);
        return label.empty() ? display : display + " " + label;
    }

    bool in_range(int32_t id) const { return id >= 0 && id < effect->parameters; }

    std::vector<int32_t> edited() {
        std::lock_guard<std::mutex> hold(edits_lock);
        std::vector<int32_t> result;
        result.swap(edits);
        return result;
    }

    void automated(int32_t id) {  // (the plugin: a parameter was changed in its window)
        std::lock_guard<std::mutex> hold(edits_lock);
        edits.push_back(id);
    }

    void window_edit(int32_t id, double value) {
        set_param(id, value);
        automated(id);
    }

    // (main thread) time for the plugin's window; a changed latency
    void idle() {
        if (editor)
            dispatch(vst2::effect::EDIT_IDLE);
        if (io_changed.exchange(false)) {  // (initial_delay is read by latency() as it is)
            std::lock_guard<std::mutex> hold(process_lock);
            suspend();
            resume();
        }
    }

    // --- state ---

    py::bytes component_state() {
        std::string data;
        {
            py::gil_scoped_release free;
            if (effect->flags & vst2::flag::PROGRAM_CHUNKS) {
                void* chunk = nullptr;
                auto size = dispatch(vst2::effect::GET_CHUNK, 0, 0, &chunk);  // (0: the whole bank)
                data.assign(CHUNK_MAGIC, 8);
                if (chunk && size > 0)
                    data.append((const char*)chunk, (size_t)size);
            } else {
                data.assign(PARAMS_MAGIC, 8);
                for (int32_t i = 0; i < effect->parameters; i++) {
                    float v = effect->get_parameter(effect, i);
                    data.append((const char*)&v, sizeof(v));
                }
            }
        }
        return py::bytes(data);
    }

    py::bytes controller_state() { return py::bytes(); }

    void set_state(py::bytes component, py::bytes /*controller*/) {
        std::string data(component);
        py::gil_scoped_release free;
        if (data.size() < 8)
            return;
        std::string kind = data.substr(0, 8), body = data.substr(8);
        if (kind == CHUNK_MAGIC) {
            std::lock_guard<std::mutex> hold(process_lock);
            dispatch(vst2::effect::SET_CHUNK, 0, (vst2::intptr)body.size(), body.data());
        } else if (kind == PARAMS_MAGIC) {
            for (int32_t i = 0; i < effect->parameters && (size_t)(i + 1) * sizeof(float) <= body.size(); i++) {
                float v;
                std::memcpy(&v, body.data() + i * sizeof(float), sizeof(v));
                effect->set_parameter(effect, i, v);
            }
        }
    }

    // --- its window (main thread) ---

    py::object open_editor(uintptr_t hwnd, double /*scale*/) {
        close_editor();
        if (!(effect->flags & vst2::flag::HAS_EDITOR))
            return py::none();
        int w = 0, h = 0;
        {
            py::gil_scoped_release free;
            vst2::Rect* rect = nullptr;
            dispatch(vst2::effect::EDIT_GET_RECT, 0, 0, &rect);  // (some plugins only know their size after opening)
            dispatch(vst2::effect::EDIT_OPEN, 0, 0, (void*)hwnd);
            rect = nullptr;
            dispatch(vst2::effect::EDIT_GET_RECT, 0, 0, &rect);
            if (rect) {
                w = rect->right - rect->left;
                h = rect->bottom - rect->top;
            }
            editor = true;
        }
        if (w <= 0 || h <= 0)
            w = 640, h = 400;
        wanted_w = w;
        wanted_h = h;
        return py::make_tuple(w, h);
    }

    void close_editor() {
        if (editor) {
            dispatch(vst2::effect::EDIT_CLOSE);
            editor = false;
        }
    }

    bool editor_open() const { return editor; }
    bool editor_resizable() { return false; }  // (VST2 plugins resize themselves: requested_size)

    py::object requested_size() {
        if (!resize_wanted.exchange(false))
            return py::none();
        return py::make_tuple(wanted_w, wanted_h);
    }

    py::tuple editor_resized(int w, int h) { return py::make_tuple(w, h); }

    void plugin_resized(int w, int h) {
        wanted_w = w;
        wanted_h = h;
        resize_wanted = true;
    }

    // --- for the host callback ---
    vst2::TimeInfo time;
    std::atomic<bool> io_changed{false};
    std::string directory;
    double rate = 48000.0;

private:
    std::string path, plugin_name, vendor_name;
    vst2::Effect* effect = nullptr;
    int32_t category = 0;
    bool active = false, editor = false;
    std::mutex process_lock, edits_lock;
    std::vector<float> zeros;
    std::vector<std::vector<float>> scratch;
    std::vector<float*> in_pointers, out_pointers;
    std::vector<vst2::MidiEvent> midi;
    std::vector<char> event_list;
    std::vector<std::tuple<int, bool, int, int, int>> pending_notes;
    std::bitset<16 * 128> held;
    std::atomic<bool> notes_off_wanted{false};
    std::vector<int32_t> edits;
    int wanted_w = 0, wanted_h = 0;
    std::atomic<bool> resize_wanted{false};
};

vst2::intptr host_callback(vst2::Effect* effect, int32_t opcode, int32_t index, vst2::intptr value, void* ptr, float) {
    Vst2Plugin* plugin = effect && effect->host_reserved[0] ? (Vst2Plugin*)effect->host_reserved[0] : being_made;
    auto copy = [&](const char* text) {
        if (ptr)
            std::strcpy((char*)ptr, text);
        return (vst2::intptr)1;
    };
    switch (opcode) {
        case vst2::host::VERSION:
            return 2400;
        case vst2::host::AUTOMATE:
            if (plugin)
                plugin->automated(index);
            return 0;
        case vst2::host::GET_TIME:
            return plugin ? (vst2::intptr)&plugin->time : 0;
        case vst2::host::IO_CHANGED:
            if (plugin)
                plugin->io_changed = true;
            return 1;
        case vst2::host::SIZE_WINDOW:
            if (plugin)
                plugin->plugin_resized(index, (int)value);
            return 1;
        case vst2::host::GET_SAMPLE_RATE:
            return plugin ? (vst2::intptr)plugin->rate : 48000;
        case vst2::host::GET_BLOCK_SIZE:
            return MAX_BLOCK;
        case vst2::host::GET_CURRENT_PROCESS_LEVEL:
            return on_audio_thread ? vst2::PROCESS_LEVEL_REALTIME : vst2::PROCESS_LEVEL_USER;
        case vst2::host::GET_AUTOMATION_STATE:
            return 1;  // (off)
        case vst2::host::GET_VENDOR_STRING:
            return copy("Tiny DAW");
        case vst2::host::GET_PRODUCT_STRING:
            return copy("Tiny DAW");
        case vst2::host::GET_VENDOR_VERSION:
            return 1;
        case vst2::host::GET_LANGUAGE:
            return 1;  // (English)
        case vst2::host::GET_DIRECTORY:
            return plugin ? (vst2::intptr)plugin->directory.c_str() : 0;
        case vst2::host::CAN_DO: {
            static const char* yes[] = {"sendVstEvents", "sendVstMidiEvent", "sendVstTimeInfo", "receiveVstEvents",
                                        "receiveVstMidiEvent", "sizeWindow", "supplyIdle"};
            for (const char* thing : yes)
                if (ptr && std::strcmp((const char*)ptr, thing) == 0)
                    return 1;
            return 0;
        }
        case vst2::host::WANT_MIDI:
        case vst2::host::NEED_IDLE:
        case vst2::host::UPDATE_DISPLAY:
        case vst2::host::BEGIN_EDIT:
        case vst2::host::END_EDIT:
            return 1;
        default:
            return 0;
    }
}

}  // namespace

void register_vst2(py::module_& m) {
    py::class_<Vst2Plugin>(m, "Vst2Plugin")
        .def(py::init<const std::string&>(), py::arg("path"), py::call_guard<py::gil_scoped_release>())
        .def_property_readonly("name", &Vst2Plugin::name)
        .def_property_readonly("vendor", &Vst2Plugin::vendor)
        .def_property_readonly("subcategories", &Vst2Plugin::subcategories)
        .def_property_readonly("is_instrument", &Vst2Plugin::is_instrument)
        .def_property_readonly("has_sidechain", &Vst2Plugin::has_sidechain)
        .def_property_readonly("latency", &Vst2Plugin::latency)
        .def_property_readonly("sample_rate", &Vst2Plugin::sample_rate)
        .def("prepare", &Vst2Plugin::prepare, py::arg("sample_rate"), py::call_guard<py::gil_scoped_release>())
        .def("reset", &Vst2Plugin::reset, py::call_guard<py::gil_scoped_release>())
        .def("set_transport", &Vst2Plugin::set_transport, py::arg("tempo"), py::arg("numerator"),
             py::arg("denominator"), py::arg("playing"), py::arg("position"))
        .def("process", &Vst2Plugin::process, py::arg("input"), py::arg("sidechain"), py::arg("output"))
        .def("render", &Vst2Plugin::render, py::arg("notes"), py::arg("output"))
        .def("notes_off", &Vst2Plugin::notes_off)
        .def("holding", &Vst2Plugin::holding)
        .def("parameters", &Vst2Plugin::parameters)
        .def("get_param", &Vst2Plugin::get_param)
        .def("set_param", &Vst2Plugin::set_param)
        .def("param_text", &Vst2Plugin::param_text)
        .def("edited", &Vst2Plugin::edited)
        .def("window_edit", &Vst2Plugin::window_edit)
        .def("idle", &Vst2Plugin::idle, py::call_guard<py::gil_scoped_release>())
        .def("component_state", &Vst2Plugin::component_state)
        .def("controller_state", &Vst2Plugin::controller_state)
        .def("set_state", &Vst2Plugin::set_state, py::arg("component"), py::arg("controller"))
        .def("open_editor", &Vst2Plugin::open_editor, py::arg("hwnd"), py::arg("scale") = 1.0)
        .def("close_editor", &Vst2Plugin::close_editor, py::call_guard<py::gil_scoped_release>())
        .def_property_readonly("editor_open", &Vst2Plugin::editor_open)
        .def("editor_resizable", &Vst2Plugin::editor_resizable)
        .def("requested_size", &Vst2Plugin::requested_size)
        .def("editor_resized", &Vst2Plugin::editor_resized);
}
