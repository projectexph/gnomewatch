"""Makes a clean copy of the source that is safe to publish, in  ../gnomewatch-source  (next to this project's folder).

    python packaging/export_source.py

It copies only an ALLOWLIST of files (never anyone's settings, accounts, keys, logs or secrets), writes the README / LICENSE /
requirements / CI files, then scans every exported file for secrets and personal paths. It stops with a list if anything looks wrong.
Publishing is a separate step (git push from that folder) so nothing leaves this PC until you decide.
"""
import glob
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
OUT = os.path.join(os.path.dirname(APP), "gnomewatch-source")

KEEP_DIRS = ["assets/ui", "assets/brand", "icons", "gnomewatch-keys/src"]
KEEP_GLOBS = ["*.py", "dashboard.html", "defaults.env", "gnome.ico", "start.bat", "start_hidden.vbs", "start_presence.vbs",
              "assets/game.json", "packaging/*.py", "packaging/*.vbs", "gnomewatch-keys/*.py", "gnomewatch-keys/README.md"]
NEVER = ("fixtures", "__pycache__", ".pyc", "dev_store", "wrangler.toml", "release.json", "presence.pid", "presence.json", "session.json",
         "secrets.env", "gw_config.json")


def copy_allowlist():
    if os.path.isdir(OUT):
        for name in os.listdir(OUT):
            if name == ".git":
                continue
            p = os.path.join(OUT, name)
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    os.makedirs(OUT, exist_ok=True)
    files = set()
    for pat in KEEP_GLOBS:
        files.update(glob.glob(os.path.join(APP, pat)))
    for d in KEEP_DIRS:
        for root, _dirs, fs in os.walk(os.path.join(APP, d)):
            for f in fs:
                files.add(os.path.join(root, f))
    n = 0
    for src in sorted(files):
        rel = os.path.relpath(src, APP).replace("\\", "/")
        if any(bad in rel for bad in NEVER) or not os.path.isfile(src):
            continue
        dst = os.path.join(OUT, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    return n


LICENSE = """MIT License

Copyright (c) 2026 projectexph

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

The Gnomewatch name, logo and brand artwork (assets/brand) are not covered by this
license: please don't present a modified copy as the official Gnomewatch.
"""

README = """# Gnomewatch

A Windows helper for **Roll A Gnome** (Roblox). It watches the weather sign for you, keeps several Roblox accounts running without eating your PC,
and can replay moves you recorded when an event starts.

- **Weather watching:** reads the in-game weather sign and shows the current event with a live countdown (and can announce event starts to a Discord bot).
- **Accounts and instances:** launches accounts one after another from their own browser profiles, tiles the windows, hides or parks them, trims memory, and rejoins after a crash.
- **Auto-place:** record clicks and key presses once for an event, replay them when it starts, preview them with a dry run, and share them as a short code.
- **Extras:** an always-on-top countdown overlay, Discord Rich Presence, a setup guide, a built-in health check, and in-app updates.

## Install (most people)

Download **Gnomewatch-Setup.exe** from the [latest release](../../releases/latest), run it, and follow the setup guide that opens.
You need a key from the Gnomewatch Discord to sign in (type `/getkey`).

### "Windows protected your PC" / my antivirus flags it

Gnomewatch is not code-signed yet, and it is a brand-new program built with PyInstaller. Windows Defender and some antivirus tools flag exactly that
combination with a generic guess (for example `Trojan:Win32/Wacatac.C!ml`). It is a false positive. You do not have to take our word for it:

- The whole source is in this repository, and `packaging/build.py` builds the installer from it. You can build it yourself.
- Each release lists the installer's SHA-256 so you can check what you downloaded.
- It never asks for, reads or stores a Roblox password or cookie.

If you'd rather not run the installer, build from source (below).

## What it does and doesn't do

- It **reads pixels** of your own Roblox windows (Windows screen capture) and uses Windows' built-in text recognition. Nothing is injected into Roblox.
- It opens Roblox the way you would: your own Edge or Chrome profile opens the game link. Only a label and a browser profile name are stored per account.
- Auto-place and the anti-idle click send ordinary mouse and keyboard input to the Roblox window you choose. Automation may be against a game's rules:
  use it at your own risk. This project is not affiliated with Roblox or the creators of Roll A Gnome.
- Sign-in keys come from a small Cloudflare Worker (`gnomewatch-keys/`). Your settings, accounts and macros stay on your PC, in `profiles/<your Discord id>/`.

## Build from source

Windows 10/11 and Python 3.12.

```powershell
pip install -r requirements.txt
copy gw_config.example.json gw_config.json     # your own key server address (see gnomewatch-keys/README.md), and your Discord invite
python sentinel.py                             # run it from source
python packaging/build.py                      # build the installer into packaging/dist
```

The game's own banner artwork is not part of this repository, so source builds show a plain background on the little dashboard page.

Tests live in `gnomewatch-keys/` (`python test_logic.py`, `python test_share.py`, and so on). Many of them start a sandbox copy of the real app.

## Your own key server

Sign-in needs the Worker in `gnomewatch-keys/`. Setup, step by step (free Cloudflare plan): `gnomewatch-keys/README.md`.
Copy `gnomewatch-keys/wrangler.example.toml` to `wrangler.toml` and fill in your own values. Secrets (admin key hash, Discord keys, the weather bot password)
are set with `wrangler secret put` and are never stored in this repository.

## License

MIT, see [LICENSE](LICENSE). Third-party fonts and icons are listed in [THIRD_PARTY.md](THIRD_PARTY.md).
"""

THIRD_PARTY = """# Third-party material

- **Inter** typeface (assets/ui/fonts) - SIL Open Font License 1.1, see `assets/ui/fonts/Inter-OFL-LICENSE.txt`.
- **Lucide** icons (assets/ui/icons) - ISC License, see `assets/ui/icons/LUCIDE-LICENSE.txt`.
- Python packages (see `requirements.txt`) keep their own licenses: Pillow (HPND), psutil (BSD-3), pywin32 (PSF), winsdk (MIT), mss (MIT),
  PyGetWindow (BSD), windows-capture (MIT), NumPy (BSD-3), OpenCV (Apache-2.0), pystray (LGPL-3), certifi (MPL-2.0), PyInstaller (GPL-2 with a bootloader exception).
- "Roblox" and "Roll A Gnome" belong to their owners. This project is unofficial.
"""

REQUIREMENTS = """# Gnomewatch runs on Windows 10/11, Python 3.12
pillow>=12
psutil>=7
pywin32>=312
winsdk>=1.0.0b10
mss>=10
PyGetWindow>=0.0.9
windows-capture>=2.0
numpy>=2
opencv-python>=4.10
pystray>=0.19
certifi
pyinstaller>=6.10          # only to build the installer
quickjs>=1.19              # only for the key-server tests
"""

GITIGNORE = """# your own data and secrets: never commit these
profiles/
backups/
diag/
*.log
.env
secrets.env
session.json
running.flag
lockkeeper.*
accounts.json
assign.json
macros.json
history.jsonl
sessions.jsonl
known_events.json
scenes.json
mode_prev.json
presence.json
presence.pid
state.json
gw_config.json
gnomewatch-keys/wrangler.toml
gnomewatch-keys/fixtures/
gnomewatch-keys/dev_store.json
packaging/build/
packaging/dist/
packaging/release.json
discord_art/
__pycache__/
*.pyc
"""

ENV_EXAMPLE = """# Copy to .env (or just use the Settings page). Private values go in secrets.env, never here.
# See the Settings page for what each one does.
POLL_SECONDS=2.0
IDLE_BACKOFF=1
NOTIFY=1
KEEPALIVE=0
AUTO_RECONNECT=1
AUTO_REJOIN=0
HIDE_WINDOW=0
HIDE_ALL=0
MULTI_INSTANCE=0
DASHBOARD=1
DASHBOARD_PORT=8765
LAUNCH_TARGET=game
# secrets.env (kept out of git): LAUNCH_URL_PRIVATE=...  WEBHOOK_URL=...  INGEST_SECRET=...
"""

GW_CONFIG_EXAMPLE = """{
  "api": "https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev",
  "invite": "https://discord.gg/YOUR-INVITE"
}
"""

WRANGLER_EXAMPLE = """name = "gnomewatch-keys"
main = "src/index.js"
compatibility_date = "2025-01-01"

[vars]
KEY_HOURS = "24"
MAX_DEVICES = "1"
# optional: only /getkey in this channel (right-click the channel in Discord > Copy Channel ID)
ALLOWED_CHANNEL_ID = ""
# optional: forward event starts to a weather bot (its password is the secret WEATHER_SECRET)
WEATHER_URL = ""

# npx wrangler kv namespace create KEYS   -> paste the id below
[[kv_namespaces]]
binding = "KEYS"
id = "PASTE_KV_NAMESPACE_ID_HERE"
"""

WORKFLOW = """name: build
on:
  workflow_dispatch:
  push:
    tags: ["v*"]
jobs:
  windows:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - name: Config (public values only)
        shell: pwsh
        run: |
          '{"api": "${{ vars.GW_API }}", "invite": "${{ vars.GW_INVITE }}"}' | Out-File -Encoding utf8 gw_config.json
      - run: python packaging/build.py
      - name: SHA-256
        shell: pwsh
        run: Get-FileHash packaging/dist/Gnomewatch-Setup.exe -Algorithm SHA256 | Format-List
      - uses: actions/upload-artifact@v4
        with:
          name: Gnomewatch-Setup
          path: packaging/dist/Gnomewatch-Setup.exe
"""

TOKEN = re.compile(r"[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6,7}\.[A-Za-z0-9_-]{27,}")
CHECKS = [
    ("a Discord bot token", TOKEN),
    ("a webhook link", re.compile(r"discord(?:app)?\.com/api/webhooks/\d+/[\w-]+")),
    ("an ingest/secret value", re.compile(r"(?:INGEST_SECRET|WEATHER_SECRET|DISCORD_TOKEN)\s*=\s*[\"']?[A-Za-z0-9_\-./+]{12,}")),
    ("a Gnomewatch key", re.compile(r"\bGW-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}(?:-[A-HJ-NP-Z2-9]{4})?\b")),
    ("a long hex string (hash or key?)", re.compile(r"\b[0-9a-f]{64}\b")),
    ("a private-server link", re.compile(r"roblox\.com/share\?code=[0-9a-f]{16,}")),
    ("a personal path or name", re.compile(r"(?i)C:\\\\Users\\\\seque|/Users/Kyles|sequeira|kyle\.|OneDrive\\\\Desktop")),
    ("an email address", re.compile(r"[\w.+-]+@(?!example\.)[\w-]+\.(?:com|net|org|io)\b")),
]
ALLOWED_LINES = ("GW-TEST-ADMIN", "GW-ADMIN-TESTKEY", "GW-WRNG", "GW-NOPE", "GW-FAKE", "GW-XXXX", "GW-AAAA-BBBB-CCCC")


def scan():
    problems = []
    for root, _d, fs in os.walk(OUT):
        if ".git" in root.split(os.sep):
            continue
        for f in fs:
            path = os.path.join(root, f)
            rel = os.path.relpath(path, OUT).replace("\\", "/")
            if rel == "packaging/export_source.py":                  # holds the scanner's own patterns
                continue
            if os.path.splitext(f)[1].lower() in (".png", ".jpg", ".jpeg", ".ico", ".ttf", ".zip", ".exe"):
                continue
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if any(a in line for a in ALLOWED_LINES):
                    continue
                for what, rx in CHECKS:
                    if rx.search(line):
                        problems.append((rel, i, what, line.strip()[:110]))
    return problems


def write(name, text):
    path = os.path.join(OUT, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main():
    n = copy_allowlist()
    write("LICENSE", LICENSE)
    write("README.md", README)
    write("THIRD_PARTY.md", THIRD_PARTY)
    write("requirements.txt", REQUIREMENTS)
    write(".gitignore", GITIGNORE)
    write(".env.example", ENV_EXAMPLE)
    write("gw_config.example.json", GW_CONFIG_EXAMPLE)
    write("gnomewatch-keys/wrangler.example.toml", WRANGLER_EXAMPLE)
    write(".github/workflows/build.yml", WORKFLOW)
    print(f"copied {n} files, wrote the README / LICENSE / CI files -> {OUT}")
    bad = scan()
    if bad:
        print(f"\nSTOP: {len(bad)} thing(s) that look private. Fix them (or add the safe test value to ALLOWED_LINES) and run again:")
        for rel, i, what, line in bad[:40]:
            print(f"  {rel}:{i}  [{what}]  {line}")
        return 1
    total = sum(os.path.getsize(os.path.join(r, f)) for r, _d, fs in os.walk(OUT) for f in fs if ".git" not in r.split(os.sep))
    print(f"scan clean. {round(total / 1e6, 1)} MB in total.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
