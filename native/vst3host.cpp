// Tiny DAW's VST3 plugin host, as a Python module (vst3host), on Steinberg's VST3 SDK.
//
// classes(path)    the plugins in a .vst3 file: name, category, sub-categories, vendor
// Plugin(path, name=None)
//                  one plugin, loaded and ready to process (stereo; 48 kHz until prepare())
//
// Threads (the VST3 rules): loading, state, parameters' names and texts, and the plugin's window
// belong to the main thread; process() and render() are called from the audio thread. Values
// flow between them through small queues: set_param() (either thread) reaches the plugin's
// processor with the next buffer and its window with the next idle(); changes made in the window
// reach the processor with the next buffer, and are listed by edited(). Call idle() now and then
// from the main thread. process() releases the GIL. While the plugin is being reconfigured
// (prepare(), reset(), set_state()) the audio thread gets silence (or, for an effect, its input
// unchanged) rather than waiting. Everything that can take a while (loading, prepare(), states,
// opening the window) lets other Python threads run meanwhile: the audio callback never waits for it.
//
// A plugin's two halves (its processor and its controller, which runs its window) are connected
// directly, as JUCE-based hosts do, rather than through the SDK's ConnectionProxy: that one drops
// every message not sent on the main thread, and many plugins send their meters and analyzers
// (Pro-Q 4's spectrum, Pro-L 2's levels…) from the audio thread, so their windows stood still.
//
// Buses: the main input and output are stereo (when the plugin accepts it); a plugin's first
// auxiliary audio input, if it has one, is its sidechain: process() feeds it, silence by default.
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "pluginterfaces/base/funknownimpl.h"
#include "pluginterfaces/gui/iplugview.h"
#include "pluginterfaces/gui/iplugviewcontentscalesupport.h"
#include "pluginterfaces/vst/ivstaudioprocessor.h"
#include "pluginterfaces/vst/ivstcomponent.h"
#include "pluginterfaces/vst/ivsteditcontroller.h"
#include "pluginterfaces/vst/ivstevents.h"
#include "pluginterfaces/vst/ivstprocesscontext.h"
#include "public.sdk/source/common/memorystream.h"
#include "public.sdk/source/vst/hosting/connectionproxy.h"
#include "public.sdk/source/vst/hosting/eventlist.h"
#include "public.sdk/source/vst/hosting/hostclasses.h"
#include "public.sdk/source/vst/hosting/module.h"
#include "public.sdk/source/vst/hosting/parameterchanges.h"
#include "public.sdk/source/vst/hosting/plugprovider.h"
#include "public.sdk/source/vst/utility/stringconvert.h"

#include <windows.h>

#include <atomic>
#include <bitset>
#include <cstring>
#include <map>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

namespace py = pybind11;
using namespace Steinberg;
using namespace Steinberg::Vst;

namespace {

constexpr int32 MAX_BLOCK = 8192;  // frames per process call to the plugin (longer buffers are cut up)
constexpr int32 MAX_EVENTS = 2048;  // notes per buffer
constexpr int32 MAX_CHANNELS = 32;  // per bus (surround plugins get silence on the channels past stereo)

// PlugProvider, with the processor and controller connected to each other directly (see the top)
class Provider : public PlugProvider {
public:
    using PlugProvider::PlugProvider;

    void connect_directly() {
        auto processor_end = U::cast<IConnectionPoint>(component);
        auto controller_end = U::cast<IConnectionPoint>(controller);
        if (!processor_end || !controller_end || processor_end == controller_end)
            return;  // (a single-component plugin talks to itself)
        if (componentCP && controllerCP) {  // (the SDK's proxies, which drop messages from the audio thread)
            componentCP->disconnect();
            controllerCP->disconnect();
        }
        componentCP = nullptr;
        controllerCP = nullptr;
        processor_end->connect(controller_end);
        controller_end->connect(processor_end);
        direct = {processor_end, controller_end};
    }

    void disconnect_directly() {
        if (direct.first) {
            direct.first->disconnect(direct.second);
            direct.second->disconnect(direct.first);
            direct = {};
        }
    }

private:
    std::pair<IPtr<IConnectionPoint>, IPtr<IConnectionPoint>> direct;
};

class TinyDawHost : public HostApplication {
public:
    tresult PLUGIN_API getName(String128 name) override {
        return StringConvert::convert("Tiny DAW", name) ? kResultTrue : kInternalError;
    }
};

TinyDawHost& host_context() {
    static TinyDawHost* host = [] {
        auto* h = new TinyDawHost();  // (lives as long as the program: plugins keep pointers to it)
        PluginContextFactory::instance().setPluginContext(h);
        return h;
    }();
    return *host;
}

std::map<std::string, VST3::Hosting::Module::Ptr>& modules() {
    static std::map<std::string, VST3::Hosting::Module::Ptr> loaded;  // never unloaded: plugins live in them
    return loaded;
}

VST3::Hosting::Module::Ptr load_module(const std::string& path) {
    auto& loaded = modules();
    auto found = loaded.find(path);
    if (found != loaded.end())
        return found->second;
    std::string error;
    auto module = VST3::Hosting::Module::create(path, error);
    if (!module)
        throw std::runtime_error("Cannot load " + path + (error.empty() ? "" : ": " + error));
    loaded[path] = module;
    return module;
}

std::string to_utf8(const String128 text) { return StringConvert::convert(text); }

std::string str(const char* s, size_t max) { return std::string(s, strnlen(s, max)); }

struct Change {
    ParamID id;
    ParamValue value;
};

// A short list guarded by a mutex: the audio thread only ever try_locks it (never waits).
struct ChangeQueue {
    std::mutex lock;
    std::vector<Change> changes;
    void add(ParamID id, ParamValue value) {
        std::lock_guard<std::mutex> hold(lock);
        for (auto& c : changes)
            if (c.id == id) {
                c.value = value;
                return;
            }
        changes.push_back({id, value});
    }
    bool find(ParamID id, ParamValue& value) {
        std::lock_guard<std::mutex> hold(lock);
        for (auto& c : changes)
            if (c.id == id) {
                value = c.value;
                return true;
            }
        return false;
    }
    bool take(std::vector<Change>& into, bool wait) {
        std::unique_lock<std::mutex> hold(lock, std::defer_lock);
        if (wait)
            hold.lock();
        else if (!hold.try_lock())
            return false;
        into.swap(changes);
        changes.clear();
        return true;
    }
};

class Plugin;

class ComponentHandler : public U::Implements<U::Directly<IComponentHandler>> {
public:
    explicit ComponentHandler(Plugin* plugin) : plugin(plugin) {}
    tresult PLUGIN_API beginEdit(ParamID) override { return kResultOk; }
    tresult PLUGIN_API performEdit(ParamID id, ParamValue value) override;
    tresult PLUGIN_API endEdit(ParamID) override { return kResultOk; }
    tresult PLUGIN_API restartComponent(int32 flags) override;
    Plugin* plugin;
};

class PlugFrame : public U::Implements<U::Directly<IPlugFrame>> {
public:
    explicit PlugFrame(Plugin* plugin) : plugin(plugin) {}
    tresult PLUGIN_API resizeView(IPlugView* view, ViewRect* size) override;
    Plugin* plugin;
};

class Plugin {
public:
    Plugin(const std::string& path, const std::string& wanted) {
        host_context();
        module = load_module(path);
        const auto& factory = module->getFactory();
        VST3::Hosting::ClassInfo chosen;
        bool found = false;
        for (const auto& info : factory.classInfos()) {
            if (info.category() != kVstAudioEffectClass)
                continue;
            if (wanted.empty() || info.name() == wanted) {
                chosen = info;
                found = true;
                break;
            }
        }
        if (!found)
            throw std::runtime_error(wanted.empty() ? "No VST3 plugin in " + path : wanted + " is not in " + path);
        info = chosen;
        provider = owned(new Provider(factory, chosen, true));
        if (!provider->initialize())
            throw std::runtime_error("Cannot start " + chosen.name());
        provider->connect_directly();
        component = provider->getComponentPtr();
        controller = provider->getControllerPtr();
        processor = U::cast<IAudioProcessor>(component);
        if (!component || !processor)
            throw std::runtime_error(chosen.name() + " has no audio processor");
        handler = owned(new ComponentHandler(this));
        frame = owned(new PlugFrame(this));
        if (controller)
            controller->setComponentHandler(handler);
        sync_controller_to_component();
        setup_buses();
        input_changes.setMaxParameters(controller ? controller->getParameterCount() + 8 : 8);
        output_changes.setMaxParameters(controller ? controller->getParameterCount() + 8 : 8);
        events.setMaxSize(MAX_EVENTS);
        zeros.assign(MAX_BLOCK, 0.0f);
        scratch.assign(MAX_CHANNELS, std::vector<float>(MAX_BLOCK, 0.0f));
        prepare(48000.0);
    }

    ~Plugin() {
        close_editor();
        deactivate();
        if (controller)
            controller->setComponentHandler(nullptr);
        processor = nullptr;
        component = nullptr;
        controller = nullptr;
        provider->disconnect_directly();
        provider = nullptr;  // (terminates the plugin)
    }

    // --- facts ---

    std::string name() const { return info.name(); }
    std::string vendor() const { return info.vendor(); }
    std::string subcategories() const { return info.subCategoriesString(); }
    bool is_instrument() const {
        return info.subCategoriesString().find("Instrument") != std::string::npos ||
               (main_in < 0 && component->getBusCount(kEvent, kInput) > 0);
    }
    bool has_sidechain() const { return aux_in >= 0; }
    int latency() const { return latency_frames.load(); }
    double sample_rate() const { return rate; }

    // --- setting up ---

    void setup_buses() {
        int32 inputs = component->getBusCount(kAudio, kInput), outputs = component->getBusCount(kAudio, kOutput);
        std::vector<SpeakerArrangement> in_arr(inputs), out_arr(outputs);
        for (int32 i = 0; i < inputs; i++) {
            BusInfo bus;
            component->getBusInfo(kAudio, kInput, i, bus);
            processor->getBusArrangement(kInput, i, in_arr[i]);
            if (bus.busType == kMain && main_in < 0) {
                main_in = i;
                in_arr[i] = SpeakerArr::kStereo;
            } else if (bus.busType == kAux && aux_in < 0) {
                aux_in = i;
                in_arr[i] = SpeakerArr::kStereo;
            }
        }
        for (int32 i = 0; i < outputs; i++) {
            BusInfo bus;
            component->getBusInfo(kAudio, kOutput, i, bus);
            processor->getBusArrangement(kOutput, i, out_arr[i]);
            if (bus.busType == kMain && main_out < 0) {
                main_out = i;
                out_arr[i] = SpeakerArr::kStereo;
            }
        }
        if (main_out < 0 && outputs > 0)
            main_out = 0;
        // ask for stereo; if the plugin refuses, it keeps what it had
        processor->setBusArrangements(in_arr.data(), inputs, out_arr.data(), outputs);
        for (int32 i = 0; i < inputs; i++)
            component->activateBus(kAudio, kInput, i, i == main_in || i == aux_in);
        for (int32 i = 0; i < outputs; i++)
            component->activateBus(kAudio, kOutput, i, i == main_out);
        if (component->getBusCount(kEvent, kInput) > 0)
            component->activateBus(kEvent, kInput, 0, true);
        in_buses.assign(inputs, AudioBusBuffers());
        out_buses.assign(outputs, AudioBusBuffers());
        in_pointers.assign(inputs, std::vector<float*>());
        out_pointers.assign(outputs, std::vector<float*>());
        for (int32 i = 0; i < inputs; i++) {
            processor->getBusArrangement(kInput, i, in_arr[i]);
            in_buses[i].numChannels = std::min(SpeakerArr::getChannelCount(in_arr[i]), MAX_CHANNELS);
            in_pointers[i].assign(in_buses[i].numChannels, nullptr);
            in_buses[i].channelBuffers32 = in_pointers[i].data();
        }
        for (int32 i = 0; i < outputs; i++) {
            processor->getBusArrangement(kOutput, i, out_arr[i]);
            out_buses[i].numChannels = std::min(SpeakerArr::getChannelCount(out_arr[i]), MAX_CHANNELS);
            out_pointers[i].assign(out_buses[i].numChannels, nullptr);
            out_buses[i].channelBuffers32 = out_pointers[i].data();
        }
    }

    void sync_controller_to_component() {
        // a separate controller starts from the processor's settings (the VST3 way)
        if (!controller || U::cast<IEditController>(component))
            return;
        MemoryStream stream;
        if (component->getState(&stream) == kResultTrue) {
            stream.seek(0, IBStream::kIBSeekSet, nullptr);
            controller->setComponentState(&stream);
        }
    }

    void deactivate() {
        if (active) {
            processor->setProcessing(false);
            component->setActive(false);
            active = false;
        }
    }

    void activate() {
        component->setActive(true);
        processor->setProcessing(true);
        active = true;
        latency_frames = (int)processor->getLatencySamples();
    }

    // (main thread) the sample rate (and longest buffer) to process at; the plugin starts afresh
    void prepare(double sample_rate) {
        std::lock_guard<std::mutex> hold(process_lock);
        deactivate();
        rate = sample_rate;
        ProcessSetup setup{kRealtime, kSample32, MAX_BLOCK, sample_rate};
        if (processor->setupProcessing(setup) != kResultOk)
            throw std::runtime_error(name() + " can't process at " + std::to_string((int)sample_rate) + " Hz");
        activate();
    }

    // forget what it was holding or playing (tails, delays, notes)
    void reset() {
        std::lock_guard<std::mutex> hold(process_lock);
        deactivate();
        activate();
        held.reset();
    }

    // --- processing (audio thread) ---

    void set_transport(double tempo, int num, int den, bool playing, int64_t position) {
        context.tempo = tempo;
        context.timeSigNumerator = num;
        context.timeSigDenominator = den;
        context.projectTimeSamples = position;
        context.state = ProcessContext::kTempoValid | ProcessContext::kTimeSigValid |
                        ProcessContext::kProjectTimeMusicValid | (playing ? ProcessContext::kPlaying : 0);
        context.projectTimeMusic = position / rate * tempo / 60.0;
    }

    // input, sidechain: (channels, frames) float32; output: (2, frames) float32, written
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

    // an instrument: notes [(frame, on, pitch, velocity, channel)] into output (2, frames)
    void render(const std::vector<std::tuple<int, bool, int, int, int>>& notes,
                py::array_t<float, py::array::c_style> output) {
        auto out = output.mutable_unchecked<2>();
        float* out_rows[2] = {out.mutable_data(0, 0), out.mutable_data(1, 0)};
        int64_t frames = out.shape(1);
        pending_notes.insert(pending_notes.end(), notes.begin(), notes.end());  // (after any still waiting)
        py::gil_scoped_release free;
        run(nullptr, nullptr, out_rows, frames, true);
    }

    // notes it was sent that it hasn't been sent the note-off of (audio thread)
    bool holding() const { return held.any(); }

    // with the next buffer: note-offs for every note still sounding
    void notes_off() { notes_off_wanted = true; }

    void run(const float* const* in_rows, const float* const* side_rows, float* const* out_rows, int64_t frames,
             bool instrument) {
        std::unique_lock<std::mutex> hold(process_lock, std::try_to_lock);
        if (!hold.owns_lock() || !active) {  // being set up: let the sound pass (or be silent) meanwhile
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
        for (int64_t done = 0; done < frames; done += MAX_BLOCK) {
            int32 n = (int32)std::min<int64_t>(MAX_BLOCK, frames - done);
            process_block(in_rows, side_rows, out_rows, done, n, instrument);
        }
        pending_notes.clear();
    }

    void process_block(const float* const* in_rows, const float* const* side_rows, float* const* out_rows,
                       int64_t at, int32 n, bool instrument) {
        int scratch_used = 0;
        auto spare = [&]() { return scratch[std::min(scratch_used++, MAX_CHANNELS - 1)].data(); };
        for (size_t b = 0; b < in_buses.size(); b++) {
            for (int32 c = 0; c < in_buses[b].numChannels; c++) {
                const float* source = nullptr;
                if ((int)b == main_in && in_rows && c < 2)
                    source = in_rows[c] + at;
                else if ((int)b == aux_in && side_rows && c < 2)
                    source = side_rows[c] + at;
                in_pointers[b][c] = const_cast<float*>(source ? source : zeros.data());
            }
            in_buses[b].silenceFlags = 0;
        }
        for (size_t b = 0; b < out_buses.size(); b++) {
            for (int32 c = 0; c < out_buses[b].numChannels; c++)
                out_pointers[b][c] = ((int)b == main_out && c < 2) ? out_rows[c] + at : spare();
            out_buses[b].silenceFlags = 0;
        }
        // (a mono output: copied to the right channel afterwards)
        bool mono_out = main_out >= 0 && out_buses[main_out].numChannels == 1;
        input_changes.clearQueue();
        output_changes.clearQueue();
        if (to_processor.take(changes_now, false))
            for (auto& c : changes_now) {
                int32 index;
                if (auto* queue = input_changes.addParameterData(c.id, index)) {
                    int32 point;
                    queue->addPoint(0, c.value, point);
                }
            }
        events.clear();
        if (instrument || component->getBusCount(kEvent, kInput) > 0) {
            if (notes_off_wanted.exchange(false))
                for (int ch = 0; ch < 16; ch++)
                    for (int p = 0; p < 128; p++)
                        if (held.test(ch * 128 + p))
                            add_note(0, false, p, 0, ch);
            for (auto& note : pending_notes) {
                int frame = std::get<0>(note) - (int)at;
                if (frame >= 0 && frame < n)
                    add_note(frame, std::get<1>(note), std::get<2>(note), std::get<3>(note), std::get<4>(note));
            }
        }
        ProcessData data;
        data.processMode = kRealtime;
        data.symbolicSampleSize = kSample32;
        data.numSamples = n;
        data.numInputs = (int32)in_buses.size();
        data.numOutputs = (int32)out_buses.size();
        data.inputs = in_buses.empty() ? nullptr : in_buses.data();
        data.outputs = out_buses.empty() ? nullptr : out_buses.data();
        data.inputParameterChanges = &input_changes;
        data.outputParameterChanges = &output_changes;
        data.inputEvents = &events;
        data.processContext = &context;
        context.sampleRate = rate;
        if (main_out < 0 || processor->process(data) != kResultOk) {
            for (int c = 0; c < 2; c++)
                std::memset(out_rows[c] + at, 0, n * sizeof(float));
        } else if (mono_out) {
            std::memcpy(out_rows[1] + at, out_rows[0] + at, n * sizeof(float));
        }
        if (context.state & ProcessContext::kPlaying)
            context.projectTimeSamples += n;
        for (int32 i = 0; i < output_changes.getParameterCount(); i++) {  // the plugin's own changes, to its window
            auto* queue = output_changes.getParameterData(i);
            int32 offset;
            ParamValue value;
            if (queue && queue->getPointCount() > 0 && queue->getPoint(queue->getPointCount() - 1, offset, value) == kResultOk)
                from_processor.add(queue->getParameterId(), value);
        }
    }

    void add_note(int32 frame, bool on, int pitch, int velocity, int channel) {
        Event e{};
        e.busIndex = 0;
        e.sampleOffset = frame;
        if (on) {
            e.type = Event::kNoteOnEvent;
            e.noteOn = {(int16)channel, (int16)pitch, 0.0f, velocity / 127.0f, 0, -1};
            held.set(channel * 128 + pitch);
        } else {
            e.type = Event::kNoteOffEvent;
            e.noteOff = {(int16)channel, (int16)pitch, velocity / 127.0f, -1, 0.0f};
            held.reset(channel * 128 + pitch);
        }
        events.addEvent(e);
    }

    // --- parameters ---

    py::list parameters() {
        py::list result;
        if (!controller)
            return result;
        for (int32 i = 0; i < controller->getParameterCount(); i++) {
            ParameterInfo p{};
            if (controller->getParameterInfo(i, p) != kResultOk)
                continue;
            py::dict d;
            d["id"] = p.id;
            d["name"] = to_utf8(p.title);
            d["short_name"] = to_utf8(p.shortTitle);
            d["units"] = to_utf8(p.units);
            d["steps"] = p.stepCount;
            d["default"] = p.defaultNormalizedValue;
            d["automatable"] = (p.flags & ParameterInfo::kCanAutomate) != 0;
            d["read_only"] = (p.flags & ParameterInfo::kIsReadOnly) != 0;
            d["hidden"] = (p.flags & ParameterInfo::kIsHidden) != 0;
            d["bypass"] = (p.flags & ParameterInfo::kIsBypass) != 0;
            d["program_change"] = (p.flags & ParameterInfo::kIsProgramChange) != 0;
            result.append(d);
        }
        return result;
    }

    // (main thread) its value: the latest set, even if it hasn't reached the window yet
    double get_param(ParamID id) {
        double value;
        if (to_controller.find(id, value))
            return value;
        return controller ? controller->getParamNormalized(id) : 0.0;
    }

    // either thread: to the processor with the next buffer, to the window with the next idle()
    void set_param(ParamID id, double value) {
        value = std::max(0.0, std::min(1.0, value));
        to_processor.add(id, value);
        to_controller.add(id, value);
    }

    std::string param_text(ParamID id, double value) {
        if (!controller)
            return "";
        String128 text{};
        if (controller->getParamStringByValue(id, value, text) != kResultOk)
            return "";
        return to_utf8(text);
    }

    // parameters changed in the plugin's window since the last call, in order
    std::vector<ParamID> edited() {
        std::lock_guard<std::mutex> hold(edits_lock);
        std::vector<ParamID> result;
        result.swap(edits);
        return result;
    }

    void performed(ParamID id, ParamValue value) {  // (from the window, main thread)
        to_processor.add(id, value);
        std::lock_guard<std::mutex> hold(edits_lock);
        edits.push_back(id);
    }

    // a change as if made in the plugin's window (for tests)
    void window_edit(ParamID id, double value) {
        if (controller)
            controller->setParamNormalized(id, value);
        performed(id, value);
    }

    // (main thread) values on their way to the window; a changed latency; a reload the plugin asked for
    void idle() {
        std::vector<Change> now;
        if (controller) {
            from_processor.take(now, true);
            for (auto& c : now)
                controller->setParamNormalized(c.id, c.value);
            to_controller.take(now, true);
            for (auto& c : now)
                controller->setParamNormalized(c.id, c.value);
        }
        int flags = restart_flags.exchange(0);
        if (flags & (kReloadComponent | kIoChanged)) {
            std::lock_guard<std::mutex> hold(process_lock);
            deactivate();
            activate();
        } else if (flags & kLatencyChanged) {
            std::lock_guard<std::mutex> hold(process_lock);  // (latency is read while inactive, to be safe)
            deactivate();
            activate();
        }
    }

    // --- state ---

    py::bytes component_state() { return state_of(component.get(), nullptr); }
    py::bytes controller_state() { return controller && !U::cast<IEditController>(component) ? state_of(nullptr, controller.get()) : py::bytes(); }

    py::bytes state_of(IComponent* comp, IEditController* ctrl) {
        std::string data;
        {
            py::gil_scoped_release free;
            MemoryStream stream;
            tresult result = comp ? comp->getState(&stream) : ctrl->getState(&stream);
            if (result == kResultOk || result == kResultTrue)
                data.assign(stream.getData(), (size_t)stream.getSize());
        }
        return py::bytes(data);
    }

    // (main thread) the processor's settings, then the window's (empty: leave it as it is)
    void set_state(py::bytes comp_state, py::bytes ctrl_state) {
        std::string comp(comp_state), ctrl(ctrl_state);
        py::gil_scoped_release free;
        {
            std::lock_guard<std::mutex> hold(process_lock);
            if (!comp.empty()) {
                MemoryStream stream(comp.data(), (TSize)comp.size());
                component->setState(&stream);
            }
        }
        if (controller && !comp.empty()) {
            MemoryStream stream(comp.data(), (TSize)comp.size());
            controller->setComponentState(&stream);
        }
        if (controller && !ctrl.empty() && !U::cast<IEditController>(component)) {
            MemoryStream stream(ctrl.data(), (TSize)ctrl.size());
            controller->setState(&stream);
        }
        latency_frames = (int)processor->getLatencySamples();
    }

    // --- its window (main thread) ---

    // attach the plugin's view into a window (its HWND); returns its size in physical pixels, or None
    py::object open_editor(uintptr_t hwnd, double scale) {
        if (!attach_editor(hwnd, scale))
            return py::none();
        return py::make_tuple(wanted_w, wanted_h);
    }

    bool attach_editor(uintptr_t hwnd, double scale) {
        py::gil_scoped_release free;
        close_editor();
        if (!controller)
            return false;
        view = owned(controller->createView(ViewType::kEditor));
        if (!view || view->isPlatformTypeSupported(kPlatformTypeHWND) != kResultTrue) {
            view = nullptr;
            return false;
        }
        view->setFrame(frame);
        if (auto scaling = U::cast<IPlugViewContentScaleSupport>(view))
            scaling->setContentScaleFactor((float)scale);
        if (view->attached((void*)hwnd, kPlatformTypeHWND) != kResultOk) {
            view->setFrame(nullptr);
            view = nullptr;
            return false;
        }
        ViewRect r;
        view->getSize(&r);
        wanted_w = r.getWidth();
        wanted_h = r.getHeight();
        return true;
    }

    void close_editor() {
        if (view) {
            view->removed();
            view->setFrame(nullptr);
            view = nullptr;
        }
    }

    bool editor_open() const { return view != nullptr; }
    bool editor_resizable() { return view && view->canResize() == kResultTrue; }

    // the size the plugin asked for (it changed its own window), once: (w, h) or None
    py::object requested_size() {
        if (!resize_wanted.exchange(false))
            return py::none();
        return py::make_tuple(wanted_w, wanted_h);
    }

    // the window's inside is now w x h (physical pixels); returns the size the plugin takes
    py::tuple editor_resized(int w, int h) {
        if (!view)
            return py::make_tuple(w, h);
        ViewRect r(0, 0, w, h);
        if (view->canResize() == kResultTrue)
            view->checkSizeConstraint(&r);
        view->onSize(&r);
        return py::make_tuple(r.getWidth(), r.getHeight());
    }

    void plugin_resized(ViewRect* size) {
        wanted_w = size->getWidth();
        wanted_h = size->getHeight();
        resize_wanted = true;
        if (view)
            view->onSize(size);  // (the window follows with the next requested_size())
    }

    void restart(int32 flags) { restart_flags |= flags; }

private:
    VST3::Hosting::Module::Ptr module;
    VST3::Hosting::ClassInfo info;
    IPtr<Provider> provider;
    IPtr<IComponent> component;
    IPtr<IAudioProcessor> processor;
    IPtr<IEditController> controller;
    IPtr<ComponentHandler> handler;
    IPtr<PlugFrame> frame;
    IPtr<IPlugView> view;
    int main_in = -1, aux_in = -1, main_out = -1;
    std::vector<AudioBusBuffers> in_buses, out_buses;
    std::vector<std::vector<float*>> in_pointers, out_pointers;
    std::vector<float> zeros;
    std::vector<std::vector<float>> scratch;
    ParameterChanges input_changes, output_changes;
    EventList events;
    ProcessContext context{};
    std::vector<std::tuple<int, bool, int, int, int>> pending_notes;
    std::bitset<16 * 128> held;
    std::atomic<bool> notes_off_wanted{false};
    std::mutex process_lock;
    bool active = false;
    double rate = 48000.0;
    std::atomic<int> latency_frames{0};
    std::atomic<int> restart_flags{0};
    ChangeQueue to_processor, to_controller, from_processor;
    std::vector<Change> changes_now;
    std::mutex edits_lock;
    std::vector<ParamID> edits;
    int wanted_w = 0, wanted_h = 0;
    std::atomic<bool> resize_wanted{false};
};

tresult PLUGIN_API ComponentHandler::performEdit(ParamID id, ParamValue value) {
    plugin->performed(id, value);
    return kResultOk;
}

tresult PLUGIN_API ComponentHandler::restartComponent(int32 flags) {
    plugin->restart(flags);
    return kResultOk;
}

tresult PLUGIN_API PlugFrame::resizeView(IPlugView*, ViewRect* size) {
    if (!size)
        return kInvalidArgument;
    plugin->plugin_resized(size);
    return kResultTrue;
}

py::list classes(const std::string& path) {
    VST3::Hosting::Module::Ptr module;
    {
        py::gil_scoped_release free;
        host_context();
        module = load_module(path);
    }
    py::list result;
    for (const auto& info : module->getFactory().classInfos()) {
        if (info.category() != kVstAudioEffectClass)
            continue;
        py::dict d;
        d["name"] = info.name();
        d["vendor"] = info.vendor();
        d["subcategories"] = info.subCategoriesString();
        d["instrument"] = info.subCategoriesString().find("Instrument") != std::string::npos;
        d["version"] = info.version();
        result.append(d);
    }
    return result;
}

}  // namespace

void register_vst2(py::module_& m);  // (vst2plugin.cpp: VST2 plugins, with the same methods)

PYBIND11_MODULE(vst3host, m) {
    m.doc() = "Tiny DAW's plugin host: VST3 (Plugin) and VST2 (Vst2Plugin) plugins (see native/vst3host.cpp)";
    register_vst2(m);
    m.def("classes", &classes, py::arg("path"), "The plugins in a .vst3 file: [{name, vendor, subcategories, instrument, version}]");
    py::class_<Plugin>(m, "Plugin")
        .def(py::init<const std::string&, const std::string&>(), py::arg("path"), py::arg("name") = "",
             py::call_guard<py::gil_scoped_release>())
        .def_property_readonly("name", &Plugin::name)
        .def_property_readonly("vendor", &Plugin::vendor)
        .def_property_readonly("subcategories", &Plugin::subcategories)
        .def_property_readonly("is_instrument", &Plugin::is_instrument)
        .def_property_readonly("has_sidechain", &Plugin::has_sidechain)
        .def_property_readonly("latency", &Plugin::latency)
        .def_property_readonly("sample_rate", &Plugin::sample_rate)
        .def("prepare", &Plugin::prepare, py::arg("sample_rate"), py::call_guard<py::gil_scoped_release>())
        .def("reset", &Plugin::reset, py::call_guard<py::gil_scoped_release>())
        .def("set_transport", &Plugin::set_transport, py::arg("tempo"), py::arg("numerator"), py::arg("denominator"),
             py::arg("playing"), py::arg("position"))
        .def("process", &Plugin::process, py::arg("input"), py::arg("sidechain"), py::arg("output"))
        .def("render", &Plugin::render, py::arg("notes"), py::arg("output"))
        .def("notes_off", &Plugin::notes_off)
        .def("holding", &Plugin::holding)
        .def("parameters", &Plugin::parameters)
        .def("get_param", &Plugin::get_param)
        .def("set_param", &Plugin::set_param)
        .def("param_text", &Plugin::param_text)
        .def("edited", &Plugin::edited)
        .def("window_edit", &Plugin::window_edit)
        .def("idle", &Plugin::idle, py::call_guard<py::gil_scoped_release>())
        .def("component_state", &Plugin::component_state)
        .def("controller_state", &Plugin::controller_state)
        .def("set_state", &Plugin::set_state, py::arg("component"), py::arg("controller"))
        .def("open_editor", &Plugin::open_editor, py::arg("hwnd"), py::arg("scale") = 1.0)
        .def("close_editor", &Plugin::close_editor, py::call_guard<py::gil_scoped_release>())
        .def_property_readonly("editor_open", &Plugin::editor_open)
        .def("editor_resizable", &Plugin::editor_resizable)
        .def("requested_size", &Plugin::requested_size)
        .def("editor_resized", &Plugin::editor_resized);
}
