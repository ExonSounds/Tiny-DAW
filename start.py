"""Start Tiny DAW with its files up to date:  python start.py [project.json | audio and MIDI files to add...]
(or double-click "Start Tiny DAW.bat").

Before the program starts, each of its files is compared with the repository on GitHub (the main
branch of REPOSITORY), and only the files that changed there are downloaded: no whole release to
download. Every file is checked against the repository's own fingerprint of it (its git blob hash)
after downloading, and only then put in place. Files the repository no longer has are removed, if
they came from it. A file you changed here yourself is kept (and said so): delete it to get the
repository's again. The repository's front page (README.md) and licence aren't the program's
files; its GUIDE.md is this folder's README.md.

.tinydaw-files.json (next to this file) remembers each file's fingerprint as it was last put here,
and which state of the repository that was (its commit and time). Without it (a release .zip just
unpacked) every file that differs is updated. GitHub's answers can be up to a minute old: an answer
older than the state already here is ignored, so files never go back to an older version.

No connection, or GitHub not answering: the program starts with the files it has. If
requirements.txt changed, the Python packages it lists are installed (pip) before starting.
  --no-update   start without looking for updates
  --check       only say which files would change, then stop
"""
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

REPOSITORY, BRANCH = "ExonSounds/Tiny-DAW", "main"
HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, ".tinydaw-files.json")
NOT_PROGRAM = {"README.md", "LICENSE", ".gitignore"}  # the repository's own, not the program's
RENAMED = {"GUIDE.md": "README.md"}  # repository path -> path here
TIMEOUT_S = 10
API = f"https://api.github.com/repos/{REPOSITORY}"


def fingerprint(data):
    """Git's blob hash of a file's bytes: what the repository's file list gives for each file."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def fetch(url, accept="application/vnd.github+json"):
    request = urllib.request.Request(url, headers={"User-Agent": "Tiny-DAW-updater", "Accept": accept})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return response.read()


def remote_files():
    """(commit, its time, {path here: (fingerprint, path there)}) of the program's files on the repository's
    main branch. The time is ISO 8601 in UTC ("2026-11-01T10:00:00Z"), so times compare as text."""
    head = json.loads(fetch(f"{API}/commits/{BRANCH}"))
    commit, date = head["sha"], head["commit"]["committer"]["date"]
    tree = json.loads(fetch(f"{API}/git/trees/{commit}?recursive=1"))
    if tree.get("truncated"):
        raise RuntimeError("the repository's file list came back incomplete")
    files = {}
    for entry in tree["tree"]:
        path = entry["path"]
        if entry["type"] != "blob" or path in NOT_PROGRAM or not safe(path):
            continue
        files[RENAMED.get(path, path)] = (entry["sha"], path)
    return commit, date, files


def safe(path):
    """A plain relative path inside this folder (nothing from elsewhere, nothing of git's)."""
    parts = path.split("/")
    return (path and not path.startswith("/") and ":" not in path and "\\" not in path
            and all(p not in ("", ".", "..") for p in parts) and parts[0] not in (".git", ".publish"))


def local_fingerprint(path):
    try:
        with open(os.path.join(HERE, path), "rb") as f:
            return fingerprint(f.read())
    except OSError:
        return None


def load_manifest():
    """{"commit", "date", "files": {path: fingerprint}}, or None (nothing remembered yet)."""
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) and isinstance(data.get("files"), dict) else None
    except (OSError, ValueError):
        return None


def save_manifest(manifest):
    try:
        with open(MANIFEST + ".tmp", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=1, sort_keys=True)
        os.replace(MANIFEST + ".tmp", MANIFEST)
    except OSError:
        pass


def plan(files, manifest):
    """(to download, to remove, kept: changed here) — paths here. manifest: the remembered fingerprints."""
    download, kept = [], []
    for path, (sha, _) in sorted(files.items()):
        mine = local_fingerprint(path)
        if mine == sha:
            continue
        if mine is None or manifest is None or manifest.get(path) == mine:
            download.append(path)  # missing, a fresh unpacked release, or untouched since it came
        else:
            kept.append(path)
    remove = [p for p, sha in sorted((manifest or {}).items())
              if p not in files and safe(p) and local_fingerprint(p) == sha]  # gone there, untouched here
    return download, remove, kept


def download(commit, path, repo_path, sha):
    """Put the repository's file in place; False if it couldn't be (the old one stays)."""
    url = f"https://raw.githubusercontent.com/{REPOSITORY}/{commit}/{urllib.parse.quote(repo_path)}"
    data = fetch(url, accept="*/*")
    if fingerprint(data) != sha:
        raise RuntimeError("it arrived damaged")
    target = os.path.join(HERE, path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target + ".download", "wb") as f:
        f.write(data)
    try:
        os.replace(target + ".download", target)
    except OSError:
        os.remove(target + ".download")
        raise RuntimeError("it is in use (is Tiny DAW already running?)")
    return True


def update(check_only=False):
    """Bring the files up to date. Returns True if requirements.txt changed."""
    print(f"Tiny DAW: looking for updated files on GitHub ({REPOSITORY})...")
    try:
        commit, date, files = remote_files()
    except (urllib.error.URLError, OSError, ValueError, KeyError, RuntimeError) as e:
        print(f"  Couldn't look ({getattr(e, 'reason', e)}); starting with the files here.")
        return False
    remembered = load_manifest()
    if remembered and str(remembered.get("date", "")) > date:
        print("  GitHub answered with an older state than this folder already has (its answers can be a minute "
              "old); starting with the files here.")
        return False
    manifest = remembered["files"] if remembered else None
    to_get, to_remove, kept = plan(files, manifest)
    for path in kept:
        print(f"  {path}: changed on this computer, so it is kept (delete it to get the repository's)")
    if check_only:
        print(f"  {len(to_get)} to download: {', '.join(to_get) or '-'}\n  {len(to_remove)} to remove: "
              f"{', '.join(to_remove) or '-'}")
        return False
    manifest = dict(manifest or {})
    got, failed = [], []
    for i, path in enumerate(to_get, 1):
        sha, repo_path = files[path]
        print(f"  ({i}/{len(to_get)}) {path}")
        try:
            download(commit, path, repo_path, sha)
            got.append(path)
        except (urllib.error.URLError, OSError, RuntimeError) as e:
            failed.append(path)
            print(f"    not updated: {getattr(e, 'reason', e)}")
    for path in to_remove:
        try:
            os.remove(os.path.join(HERE, path))
            print(f"  removed {path} (no longer part of Tiny DAW)")
        except OSError:
            continue
        manifest.pop(path, None)
    for path, (sha, _) in files.items():  # what is here now as the repository has it
        if local_fingerprint(path) == sha:
            manifest[path] = sha
    save_manifest({"commit": commit, "date": date, "files": manifest})
    if got:
        print(f"  {len(got)} file{'' if len(got) == 1 else 's'} updated.")
    elif not failed:
        print("  Everything is up to date.")
    return "requirements.txt" in got


def install_requirements():
    print("  requirements.txt changed: installing the Python packages it lists...")
    result = subprocess.run([sys.executable, "-m", "pip", "install", "-r", os.path.join(HERE, "requirements.txt")])
    if result.returncode:
        print("  pip couldn't install them; Tiny DAW may not start. Try: python -m pip install -r requirements.txt")


def main():
    args = sys.argv[1:]
    if "--check" in args:
        update(check_only=True)
        return
    if "--no-update" in args:
        args.remove("--no-update")
    elif update():
        install_requirements()
    if sys.version_info[:2] != (3, 12):
        print(f"  Note: this is Python {sys.version_info[0]}.{sys.version_info[1]}; Tiny DAW's plugin host is "
              "built for Python 3.12, so plugins won't load.")
    os.chdir(HERE)
    sys.path.insert(0, HERE)
    sys.argv = [os.path.join(HERE, "main.py"), *args]
    import runpy
    runpy.run_path(sys.argv[0], run_name="__main__")  # (main.py and the daw package: as updated just now)


if __name__ == "__main__":
    main()
