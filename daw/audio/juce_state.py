"""Reading plugin settings saved by Tiny DAW versions up to 0.3.1, which hosted plugins through
pedalboard (JUCE): a VST3 plugin's state was JUCE's binary XML, "VC2!" + length + XML holding the
processor's state (<IComponent>) and the window's (<IEditController>), each in JUCE's own base64
("<byte count>.<characters>", six bits per character, lowest bits first). The new host keeps the
two parts as they are (plugins.py); this turns an old state into them.
"""
import re
import struct

MAGIC = 0x21324356  # "VC2!"
ALPHABET = ".ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+"
VALUE = {c: i for i, c in enumerate(ALPHABET)}


def is_juce_state(blob):
    return blob is not None and len(blob) >= 8 and struct.unpack_from("<I", blob)[0] == MAGIC


def decode_base64(text):
    """JUCE's MemoryBlock::fromBase64Encoding."""
    count, _, chars = text.strip().partition(".")
    size = int(count)
    out = bytearray(size)
    bit = 0
    for c in chars:
        value = VALUE[c]
        for i in range(6):
            if bit >= size * 8:
                return bytes(out)
            if value >> i & 1:
                out[bit >> 3] |= 1 << (bit & 7)
            bit += 1
    return bytes(out)


def split(blob):
    """(processor state, window state) from an old JUCE state; either may be b"". Raises ValueError
    if it isn't one."""
    if not is_juce_state(blob):
        raise ValueError("not a JUCE plugin state")
    size = struct.unpack_from("<I", blob, 4)[0]
    xml = blob[8:8 + size].split(b"\0")[0].decode("utf-8", "replace")

    def part(tag):
        found = re.search(rf"<{tag}>([^<]*)</{tag}>", xml)
        return decode_base64(found.group(1)) if found and found.group(1).strip() else b""

    return part("IComponent"), part("IEditController")
