"""Publish a new version of Gnomewatch to everyone, in one command:

    python packaging/release.py 1.0.1                         # everyone is offered the update (a button in Settings)
    python packaging/release.py 1.0.1 --min 1.0.1             # older copies can't sign in until they update
    python packaging/release.py 1.0.1 --notes "Faster login"  # a short line shown next to the update button
    python packaging/release.py 1.0.0 --repo yourname/gnomewatch --create    # the very first time (makes the GitHub repo)

What it does: sets the version in gw_version.py, builds the installer, uploads it to a GitHub Release, and tells the key server
the new version + download link + the file's SHA-256 (the app refuses any download that doesn't match). Needs the GitHub CLI
(`gh auth login` once) and Node (for wrangler, already set up).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
KEYS = os.path.join(APP, "gnomewatch-keys")
CFG = os.path.join(HERE, "release.json")
EXE = os.path.join(HERE, "dist", "Gnomewatch-Setup.exe")
APPZIP = os.path.join(HERE, "dist", "Gnomewatch-App.zip")                     # the app itself; the installer downloads it from the release
VARS = ("LATEST_VERSION", "MIN_VERSION", "DOWNLOAD_URL", "DOWNLOAD_SHA256", "RELEASE_NOTES")


def tool(name):
    p = shutil.which(name)
    if p:
        return p
    for d in (r"C:\Program Files\nodejs", r"C:\Program Files\GitHub CLI", os.path.expandvars(r"%LOCALAPPDATA%\Programs\GitHub CLI")):
        for ext in (".cmd", ".exe", ""):
            q = os.path.join(d, name + ext)
            if os.path.isfile(q):
                return q
    sys.exit(f"Can't find '{name}'. Install it first (see the notes at the top of this file).")


def run(cmd, **kw):
    print("  $", " ".join(str(c) for c in cmd)[:140])
    subprocess.check_call(cmd, **kw)


ap = argparse.ArgumentParser()
ap.add_argument("version", help="like 1.0.1")
ap.add_argument("--min", default="", help="oldest version still allowed to sign in (omit = nobody is forced)")
ap.add_argument("--notes", default="", help='what changed, e.g. "Faster login; Overlay widget; Bug fixes" (each part becomes a bullet)')
ap.add_argument("--repo", default="", help="owner/name of the GitHub repo that holds the releases (saved after the first time)")
ap.add_argument("--create", action="store_true", help="create the (public) repo first")
ap.add_argument("--reuse", action="store_true", help="upload the installer already in dist/ instead of building again")
a = ap.parse_args()

version = a.version.lstrip("vV")
if not re.fullmatch(r"\d+\.\d+\.\d+", version):
    sys.exit("The version should look like 1.0.1")
cfg = json.load(open(CFG)) if os.path.exists(CFG) else {}
repo = a.repo or cfg.get("repo")
if not repo:
    sys.exit("Say which GitHub repo to use the first time:  --repo yourname/gnomewatch  (add --create if it doesn't exist yet)")
json.dump({"repo": repo}, open(CFG, "w"))
gh = tool("gh")
if subprocess.run([gh, "auth", "status"], capture_output=True).returncode != 0:
    sys.exit("The GitHub CLI isn't signed in. Run  gh auth login  first.")
if a.min and tuple(map(int, a.min.lstrip("vV").split("."))) > tuple(map(int, version.split("."))):
    sys.exit("--min can't be newer than the version you're publishing.")

print(f"1/5  version {version}")
raw_notes = a.notes or f"Gnomewatch {version}"
bullets = [b.strip() for b in re.split(r"[;|]", raw_notes) if b.strip()]          # "A; B; C" -> three bullets in the app's What's-new card
vf = os.path.join(APP, "gw_version.py")
open(vf, "w", encoding="utf-8").write(
    '"""The version of this copy of Gnomewatch, and what changed in it. packaging/release.py rewrites both when you publish."""\n'
    f'VERSION = "{version}"\nNOTES = {json.dumps(chr(10).join("• " + b for b in bullets))}      # shown once after an update, one bullet per line\n')

print("2/5  building the installer (a few minutes)")
if not a.reuse:
    run([sys.executable, os.path.join(HERE, "build.py")], cwd=APP)
sha = hashlib.sha256(open(EXE, "rb").read()).hexdigest()
print("     sha256", sha)

print("3/5  uploading to GitHub")
if a.create:
    run([gh, "repo", "create", repo, "--public", "--add-readme", "--description", "Gnomewatch downloads"])
tag = "v" + version
url = f"https://github.com/{repo}/releases/download/{tag}/Gnomewatch-Setup.exe"
notes = "; ".join(bullets)
assets = [EXE] + ([APPZIP] if os.path.isfile(APPZIP) else [])
run([gh, "release", "create", tag, *assets, "--repo", repo, "--title", f"Gnomewatch {version}", "--notes", notes])

print("4/5  telling the key server")
toml = os.path.join(KEYS, "wrangler.toml")
lines = [l for l in open(toml, encoding="utf-8").read().splitlines() if not any(l.startswith(v + " ") for v in VARS)]
vals = {"LATEST_VERSION": version, "MIN_VERSION": a.min.lstrip("vV"), "DOWNLOAD_URL": url, "DOWNLOAD_SHA256": sha, "RELEASE_NOTES": notes}
i = next(n for n, l in enumerate(lines) if l.startswith("MAX_DEVICES"))
for v in reversed(VARS):
    lines.insert(i + 1, f"{v} = {json.dumps(vals[v])}")
open(toml, "w", encoding="utf-8").write("\n".join(lines) + "\n")
run([tool("npx"), "wrangler", "deploy"], cwd=KEYS)

print("5/5  done")
print(f"  Published Gnomewatch {version}. Everyone on an older version is offered it (Settings > Updates)" + (f"; versions below {a.min} are locked out until they update." if a.min else "."))
print(f"  Link for NEW people (always the newest installer):  https://github.com/{repo}/releases/latest/download/Gnomewatch-Setup.exe")
