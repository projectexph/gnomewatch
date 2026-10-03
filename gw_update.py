"""Updating Gnomewatch: the key server says a newer version exists (with a download link and its SHA-256), this downloads the
installer, checks it is exactly the file that was published, and starts it. The installer closes this program, replaces the
program files (never anyone's settings), and opens Gnomewatch again.

An update only ever runs because the person pressed 'Update now'.
"""
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.request
import webbrowser

from gw_license import _ssl
from gw_version import VERSION

STATE = {"phase": "idle", "pct": 0, "msg": ""}            # idle | download | verify | launch | done | error  (read by the screens)
_lock = threading.Lock()


class UpdateError(Exception):
    pass


def packaged():
    return bool(getattr(sys, "frozen", False))


def _secure(url):
    """https, or plain http to this PC itself (used by the tests)."""
    return str(url).startswith("https://") or str(url).startswith(("http://127.0.0.1", "http://localhost"))


def usable(upd):
    """A well-formed update note from the server (https link, and a checksum when we can install it ourselves)."""
    if not upd or not isinstance(upd, dict) or not upd.get("latest"):
        return False
    return _secure(upd.get("url", ""))


def _set(**kw):
    STATE.update(kw)


def download(upd, dest_dir=None):
    """Download the installer to a temp folder and verify its SHA-256. Returns the file path."""
    url = str(upd.get("url", ""))
    want = str(upd.get("sha256", "")).lower()
    if not _secure(url):
        raise UpdateError("The update link isn't a secure link.")
    if not re.fullmatch(r"[0-9a-f]{64}", want):
        raise UpdateError("The update has no checksum, so it can't be installed safely.")
    folder = dest_dir or os.path.join(tempfile.gettempdir(), "gnomewatch-update")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, f"Gnomewatch-Setup-{re.sub(r'[^0-9A-Za-z.]', '', str(upd['latest']))}.exe")
    part = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": f"Gnomewatch/{VERSION}"})
    h = hashlib.sha256()
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl()) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(1 << 18)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                _set(phase="download", pct=int(done * 100 / total) if total else 0, msg="Downloading…")
    except Exception as e:
        _remove(part)
        raise UpdateError(f"Couldn't download the update ({str(e)[:60]}).")
    _set(phase="verify", msg="Checking the download…")
    if h.hexdigest() != want:
        _remove(part)
        raise UpdateError("The download didn't match what was published, so it was thrown away.")
    _remove(dest)
    os.replace(part, dest)
    return dest


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def launch(path):
    """Start the installer in update mode. It closes Gnomewatch itself, so the caller should be ready to go."""
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")          # lets a packaged program start another packaged program
    subprocess.Popen([path, "--update"], close_fds=True, env=env, creationflags=0x00000008)     # DETACHED_PROCESS


def start(upd, on_ready):
    """Press-the-button entry point. Runs in the background; STATE shows progress. When the installer is running, on_ready()
    is called (the caller then quits). From source, it just opens the download page."""
    with _lock:
        if STATE["phase"] in ("download", "verify", "launch"):
            return
        _set(phase="download", pct=0, msg="Starting…")

    def work():
        try:
            if not packaged():
                webbrowser.open(str(upd.get("url", "")))
                _set(phase="done", msg="Opened the download page in your browser.")
                return
            path = download(upd)
            _set(phase="launch", msg="Installing…")
            launch(path)
            _set(phase="done", msg="Installing…")
            on_ready()
        except UpdateError as e:
            _set(phase="error", msg=str(e))
        except Exception as e:
            _set(phase="error", msg=f"The update failed ({str(e)[:60]}).")

    threading.Thread(target=work, daemon=True).start()
