"""Finding out whether plugins are instruments or effects, without the user loading each one.

Run as its own process by ui/plugin_host.py (KindFinder):  python -m daw.audio.plugin_probe <list.json>
so a plugin that crashes or hangs while it is looked at can't take the program down with it. The
list is the plugin files whose kind isn't known yet. For each, one line goes to stdout as it starts
({"start": path}) and one when it is done ({"path": path, "classes": [[name, kind], ...]} or
{"path": path, "error": text}); each line begins with MARK, so whatever plugins print themselves is
told apart. If the process dies, the last "start" without a result names the plugin to blame.

VST3: the plugin file's factory lists its plugins with their sub-categories ("Instrument|Synth",
"Fx|EQ"…), read without creating any of them; only a plugin whose sub-categories say neither is
created to ask it. VST2: the plugin is created (its entry point run) and asked; nothing is processed
and no window opens.
"""
import json
import os
import sys

MARK = "\x1eTINYDAW "
INSTRUMENT, EFFECT = "instrument", "effect"  # (as audio/plugins.py)


def kind_of(subcategories):
    """INSTRUMENT, EFFECT, or None if a plugin's VST3 sub-categories don't say."""
    if "Instrument" in subcategories:
        return INSTRUMENT
    return EFFECT if "Fx" in subcategories or "Analyzer" in subcategories else None


def probe(path, vst3host):
    """[[name, kind], ...] of the plugins in one file."""
    if path.lower().endswith(".dll"):
        plugin = vst3host.Vst2Plugin(path)
        return [[plugin.name, INSTRUMENT if plugin.is_instrument else EFFECT]]
    found = []
    for c in vst3host.classes(path):
        kind = kind_of(c["subcategories"])
        if kind is None:  # (rare) create it and ask
            kind = INSTRUMENT if vst3host.Plugin(path, c["name"]).is_instrument else EFFECT
        found.append([c["name"], kind])
    if not found:
        raise RuntimeError("no plugins in this file")
    return found


def say(entry):
    sys.stdout.write("\n" + MARK + json.dumps(entry) + "\n")
    sys.stdout.flush()


def main(list_file):
    from . import vst3host
    for path in json.load(open(list_file, encoding="utf-8")):
        say({"start": path})
        try:
            say({"path": path, "classes": probe(path, vst3host)})
        except Exception as e:
            say({"path": path, "error": str(e) or type(e).__name__})
    os._exit(0)  # (no clean-up: some plugins misbehave when unloaded at exit)


if __name__ == "__main__":
    main(sys.argv[1])
