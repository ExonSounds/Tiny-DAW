"""Is there a newer version on GitHub? No Qt; the GUI runs check() in a background thread.

The newest version is the repository's latest release, or, while it has none, its highest
version tag ("0.2", "v0.2.1"…). Nothing is downloaded or installed: the notice under the
master row only says so and links to the repository.
"""
import json
import re
import urllib.error
import urllib.request

from .version import REPOSITORY, VERSION

API = "https://api.github.com/repos/" + REPOSITORY
PAGE = "https://github.com/" + REPOSITORY
TIMEOUT_S = 8


def parse(version):
    """"v0.2.1" -> (0, 2, 1); None if it isn't a version number."""
    numbers = re.findall(r"\d+", version or "")
    return tuple(int(n) for n in numbers) if re.match(r"^\s*v?\d+(\.\d+)*\s*$", version or "") else None


def is_newer(candidate, current=VERSION):
    a, b = parse(candidate), parse(current)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def fetch(url):
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": f"Tiny-DAW/{VERSION}"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return json.load(response)


def latest(fetch=fetch):
    """(newest version, its page) published on GitHub, or (None, PAGE) if there is none yet.
    Raises OSError / ValueError when GitHub can't be reached or answers strangely."""
    try:
        release = fetch(API + "/releases/latest")
        tag = release.get("tag_name")
        if parse(tag):
            return tag.lstrip("v"), release.get("html_url") or PAGE + "/releases"
    except urllib.error.HTTPError as e:
        if e.code != 404:  # 404: no releases yet, look at the tags
            raise
    tags = [t.get("name") for t in fetch(API + "/tags") if parse(t.get("name"))]
    if not tags:
        return None, PAGE
    newest = max(tags, key=parse)
    return newest.lstrip("v"), f"{PAGE}/releases/tag/{newest}"


def check(fetch=fetch):
    """("newer", version, url) | ("current", version, url) | ("failed", reason, PAGE)."""
    try:
        version, url = latest(fetch)
    except (OSError, ValueError, AttributeError, TypeError) as e:
        return "failed", str(e) or type(e).__name__, PAGE
    if version and is_newer(version):
        return "newer", version, url
    return "current", version or VERSION, PAGE
