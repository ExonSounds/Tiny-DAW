"""Finding output devices, grouped by Windows driver type (PortAudio "host API")."""
import sounddevice as sd

# Driver types in the order they are offered, with a short name and what they are good for.
DRIVER_TYPES = {
    "ASIO": ("ASIO", "The audio interface's own driver: lowest delay. Usually one program at a time."),
    "Windows WASAPI": ("WASAPI", "Windows' modern audio, shared with other programs. Exclusive mode gives lower "
                                 "delay but takes the device for this program alone."),
    "Windows WDM-KS": ("WDM-KS", "Talks to the hardware directly: low delay. Takes the device for this program alone."),
    "Windows DirectSound": ("DirectSound", "Older Windows audio: higher delay."),
    "MME": ("MME", "The oldest Windows audio: works with everything, but has the highest delay."),
}


# Least total output delay (seconds) a driver type keeps up with. DirectSound and MME starve when
# asked for a single small buffer (measured: under 30 % of real time delivered at 512 frames).
MIN_LATENCY = {"Windows DirectSound": 0.04, "MME": 0.04}


def label(driver_type):
    return DRIVER_TYPES.get(driver_type, (driver_type,))[0]


def driver_types():
    """Driver types that have at least one output device, in DRIVER_TYPES order."""
    present = {sd.query_hostapis(d["hostapi"])["name"] for d in sd.query_devices() if d["max_output_channels"] > 0}
    return [t for t in DRIVER_TYPES if t in present] + sorted(present - set(DRIVER_TYPES))


def outputs(driver_type):
    """(device index, name) for every output device of one driver type."""
    apis = [i for i, h in enumerate(sd.query_hostapis()) if h["name"] == driver_type]
    return [(i, d["name"]) for i, d in enumerate(sd.query_devices())
            if d["hostapi"] in apis and d["max_output_channels"] > 0]


def default_output(driver_type):
    """The driver type's own default output device, or its first one, or None."""
    for api in sd.query_hostapis():
        if api["name"] == driver_type and api["default_output_device"] >= 0:
            return api["default_output_device"]
    devices = outputs(driver_type)
    return devices[0][0] if devices else None


def default_input(driver_type):
    """The driver type's default input device (index), or None if it has none."""
    for api in sd.query_hostapis():
        if api["name"] == driver_type:
            index = api["default_input_device"]
            return index if index >= 0 else None
    return None


def driver_type_of(index):
    return sd.query_hostapis(sd.query_devices(index)["hostapi"])["name"]


def device_info(index):
    """sounddevice's info dict: name, max_output_channels, default_samplerate, ..."""
    return sd.query_devices(index)
