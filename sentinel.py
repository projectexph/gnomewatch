#!/usr/bin/env python3
"""
Roll A Gnome weather sentinel — macOS and Windows.

Watches YOUR OWN Roblox window (screen pixels only — nothing is injected, read
from memory, or intercepted), OCRs the weather billboard, and posts what it sees
to the bot's /ingest endpoint with the exact countdown.

macOS 13+:  pip3 install pyobjc-framework-Quartz pyobjc-framework-Vision
            (allow Terminal under System Settings → Privacy & Security → Screen Recording)
Windows 10+: pip install winsdk mss pygetwindow
            (uses the built-in Windows OCR; install the English language pack if asked)

Usage:
  WORKER_URL=https://roll-a-gnome-weather.exph.workers.dev INGEST_SECRET=... python3 sentinel.py
  (Windows PowerShell:  $env:WORKER_URL="..."; $env:INGEST_SECRET="..."; python sentinel.py)
"""
import json
import os
import re
import sys
import time
import ssl
import urllib.error
import urllib.request

if getattr(sys, "frozen", False) and len(sys.argv) > 1 and sys.argv[1] in ("--gw-login", "--gw-keeper"):
    # the packaged app has no python.exe to hand helper jobs to, so it runs them itself
    _mode = sys.argv.pop(1)
    if _mode == "--gw-login":
        import gw_boot
        gw_boot._child(sys.argv[1], sys.argv[2])
    else:
        import lockkeeper
        lockkeeper.main()                        # the mutex name is sys.argv[1]
    sys.exit(0)

# python.org builds on macOS ship without root certificates; use certifi's bundle if present.
try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")
if IS_MAC:
    try:
        import Quartz
        import Vision
        from Foundation import NSURL  # noqa: F401  (pyobjc bridge warm-up)
    except ImportError:
        sys.exit("Missing deps. Run:  pip3 install pyobjc-framework-Quartz pyobjc-framework-Vision")
elif IS_WIN:
    try:
        import asyncio
        import io
        import mss
        import pygetwindow as gw
        from PIL import Image
        from winsdk.windows.graphics.imaging import BitmapDecoder
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.storage.streams import DataWriter, InMemoryRandomAccessStream
    except ImportError:
        sys.exit("Missing deps. Run:  pip install winsdk mss pygetwindow pillow")
else:
    sys.exit("This sentinel supports macOS and Windows.")

import queue
import subprocess
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
def _self_cmdline():
    """The command that starts this program again (quoted for cmd)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" "{os.path.abspath(__file__)}"'


GW_SESSION = None                         # who is signed in (set by the login page below)
if __name__ == "__main__" or os.environ.get("GW_BOOT") == "1":      # imports (tests, tools) skip the login page
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import gw_boot
    GW_SESSION = gw_boot.run(HERE)        # shows the login page, or signs in with the remembered key; exits if closed
DATA = (GW_SESSION or {}).get("data_dir") or HERE        # everything this user sets up lives here (profiles/<discord id>)
os.makedirs(DATA, exist_ok=True)
LOG_PATH = os.path.join(DATA, "sentinel.log")


class _Tee:
    """Everything printed also goes to sentinel.log (and works under pythonw, which has no console)."""
    def __init__(self, console):
        self.console = console
        self.log = open(LOG_PATH, "a", buffering=1, encoding="utf-8")
        self.q = queue.Queue()
        if console is not None:
            # A separate thread writes to the console, so a console that's paused (e.g. text selected
            # by clicking in it) can never freeze the sentinel or the panel.
            threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        while True:
            s = self.q.get()
            try:
                self.console.write(s)
                self.console.flush()
            except Exception:
                pass

    def write(self, s):
        if self.console is not None and self.q.qsize() < 2000:
            self.q.put(s)
        self.log.write(s)

    def flush(self):
        pass


def _disable_quickedit():
    """Clicking inside a Windows console normally pauses the program; turn that off."""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-10)                       # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, (mode.value & ~0x0040) | 0x0080)   # clear QUICK_EDIT, set EXTENDED_FLAGS
    except Exception:
        pass


if sys.platform.startswith("win"):
    _disable_quickedit()


sys.stdout = _Tee(sys.stdout)
try:
    import faulthandler
    faulthandler.enable(file=sys.stdout.log, all_threads=True)      # a native crash writes its call stacks into sentinel.log
except Exception:
    pass
sys.stderr = sys.stdout

PAUSED, MUTED, SHOW_ROBLOX, QUIT = (threading.Event() for _ in range(4))
PANIC = threading.Event()               # panic key: everything hidden, muted, no clicks

# Settings can live in a .env file next to this script (KEY=value per line); real environment
# variables win over the file.
_env_path = os.path.join(DATA, ".env")
_secrets_path = os.path.join(DATA, "secrets.env")
SECRET_KEYS = {"INGEST_SECRET", "LAUNCH_URL_PRIVATE", "LAUNCH_URL_CUSTOM", "DASHBOARD_KEY", "WEBHOOK_URL"}     # kept out of .env
for _p in (_secrets_path, _env_path):
    if os.path.exists(_p):
        for _line in open(_p, encoding="utf-8-sig"):
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

WORKER_URL = os.environ.get("WORKER_URL", "").rstrip("/")
NOTIFY = os.environ.get("NOTIFY", "0") == "1"
ACCENT = os.environ.get("ACCENT", "event").strip()                  # event = follow the event, or a fixed #rrggbb
ANIMATIONS = os.environ.get("ANIMATIONS", "normal").strip().lower()   # off | low | normal (console motion)
PANEL_MINI = os.environ.get("PANEL_MINI", "0") == "1"               # panel shown as a slim strip
SELFCHECK = os.environ.get("SELFCHECK", "1") == "1"                 # warn when something is wrong (Roblox closed, bot link down...)
SELFCHECK_TOAST = os.environ.get("SELFCHECK_TOAST", "1") == "1"     # ...also as a pop-up, even when event alerts are off
KEEPALIVE = os.environ.get("KEEPALIVE", "0") == "1"                 # click the middle of each Roblox window now and then
KEEPALIVE_MINUTES = float(os.environ.get("KEEPALIVE_MINUTES", "10"))
NOTIFY_SOUND = os.environ.get("NOTIFY_SOUND", "1") == "1"        # beep with an alert
NOTIFY_TOAST = os.environ.get("NOTIFY_TOAST", "1") == "1"        # pop-up notification with an alert
IDLE_BACKOFF = os.environ.get("IDLE_BACKOFF", "1") == "1"        # read less often while the board is out of view
ALERT_OFF = {x.strip().lower() for x in os.environ.get("ALERT_OFF", "").split(",") if x.strip()}   # events that never alert
LAUNCH_TARGET = os.environ.get("LAUNCH_TARGET", "game").strip().lower()   # game | private
LAUNCH_URL_GAME = os.environ.get("LAUNCH_URL_GAME", "").strip()
if not LAUNCH_URL_GAME:                                       # a fresh install opens the game's own page until you pick something else
    try:
        LAUNCH_URL_GAME = "https://www.roblox.com/games/%s" % json.load(open(os.path.join(HERE, "assets", "game.json"), encoding="utf-8"))["placeId"]
    except (OSError, ValueError, KeyError):
        pass
LAUNCH_URL_PRIVATE = os.environ.get("LAUNCH_URL_PRIVATE", "").strip()
PRESENCE = os.environ.get("PRESENCE", "0") == "1"                # show what Gnomewatch is watching on your Discord profile (needs Discord open)
OVERLAY = os.environ.get("OVERLAY", "0") == "1"                  # the small always-on-top event + countdown pill
OVERLAY_LOCK = os.environ.get("OVERLAY_LOCK", "0") == "1"        # locked = clicks pass through it to the game
OVERLAY_NEXT = os.environ.get("OVERLAY_NEXT", "1") == "1"        # show the next event's countdown when nothing is on
OVERLAY_X = int(os.environ.get("OVERLAY_X", "-1"))
OVERLAY_Y = int(os.environ.get("OVERLAY_Y", "-1"))
LAUNCH_URL_CUSTOM = os.environ.get("LAUNCH_URL_CUSTOM", "").strip()         # a link or a place ID you type in
LAUNCH_DELAY = float(os.environ.get("LAUNCH_DELAY", "0"))                    # 0 = smart (next account when the last window is ready), else seconds between accounts
NOTIFY_NEXT = os.environ.get("NOTIFY_NEXT", "0") == "1"    # also toast when a new "next event" appears
WINDOW_TITLE = os.environ.get("WINDOW_TITLE", "").strip()  # title for the window that has the weather board
OTHER_TITLE = os.environ.get("OTHER_TITLE", "").strip()    # title for every other Roblox window
HOTKEY = os.environ.get("HOTKEY", "ctrl+shift+r")          # show/hide the parked Roblox window(s)
HIDE_ALL = os.environ.get("HIDE_ALL", "0") == "1"          # park every Roblox window at startup, not just the board's
TRAY = os.environ.get("TRAY", "1") == "1"
DASHBOARD = os.environ.get("DASHBOARD", "1") == "1"           # local web dashboard (127.0.0.1 only)
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8765"))
DASHBOARD_OPEN = os.environ.get("DASHBOARD_OPEN", "0") == "1"  # open it in the browser on start
DASHBOARD_LAN = os.environ.get("DASHBOARD_LAN", "0") == "1"    # also reachable from other devices on your Wi-Fi
DASHBOARD_LAN_CONTROL = os.environ.get("DASHBOARD_LAN_CONTROL", "0") == "1"   # let those devices press buttons too
THROTTLE_PARKED = os.environ.get("THROTTLE_PARKED", "0") == "1"   # hidden Roblox windows: lower priority + efficiency mode
PARK_CORES = int(os.environ.get("PARK_CORES", "2"))                # hidden Roblox may only use this many cores (0 = no limit)
LOW_PRIORITY = os.environ.get("LOW_PRIORITY", "1") == "1"       # run the sentinel itself at below-normal priority
CLEAN_HELPERS = os.environ.get("CLEAN_HELPERS", "0") == "1"    # close window-less Roblox tray launchers
MULTI_INSTANCE = os.environ.get("MULTI_INSTANCE", "0") == "1"  # hold Roblox's singleton lock so more copies can start
LAUNCH_URL = os.environ.get("LAUNCH_URL", "").strip()          # what "New instance" opens (e.g. your private server link)
SECRET = os.environ.get("INGEST_SECRET", "")
NAME = os.environ.get("SENTINEL_NAME", "mac")
POLL_S = float(os.environ.get("POLL_SECONDS", "2"))
REPOST_S = float(os.environ.get("REPOST_SECONDS", "30"))   # refresh the countdown this often
VERBOSE = os.environ.get("VERBOSE", "0") == "1"
def _direct_bot():
    """Do you have a weather bot of your own set up (address + password in Settings)?"""
    return bool(WORKER_URL and SECRET)


def _relay_ready():
    """Signed in with a working key server: event starts are reported through it. (The server forwards the first copy to the weather bot and ignores
    duplicates from other people, and it keeps the bot's password so the app never needs it.)"""
    try:
        return bool(GW_SESSION and GW_SESSION.get("key") and not LOCKED.is_set() and _lic.config(HERE).get("api"))
    except Exception:
        return False


def _reporting():
    """Does Gnomewatch report events anywhere? Either to a bot of your own, or through the Gnomewatch server. Neither: it still works fully."""
    return _direct_bot() or _relay_ready()

# OCR sometimes garbles the stylised font ("EventLA&tive:"); be forgiving.
# Billboard names ("Lightning Storm") differ from mutation names ("Charged").
# We send the raw name; the bot resolves it through its alias table and tells us
# when it doesn't recognise one, so it can be added with /aliasevent.
NEXT_RE = re.compile(r"n\W*e\W*x\W*t\W{0,3}e\W*v\W*e\W*n\W*t", re.I)
UI_NOISE = {"sell", "garden", "shop", "pets", "gnomes", "auto sell", "index", "clans", "rebirth", "upgrades", "weekly deal"}
ACTIVE_RE = re.compile(r"e\W*v\W*e\W*n\W*t\W{0,3}a\W*c?\W*t\W*i\W*v\W*e", re.I)
TIMER_RE = re.compile(r"(?:(\d+)\s*m)?\s*(\d+)\s*s\b|(\d+):(\d{2})", re.I)


# ----------------------------------------------------------------- capture (macOS)

def roblox_window_mac():
    """Return (window id, bounds) of the Roblox window, or None."""
    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []:
        if w.get("kCGWindowOwnerName") == "Roblox" and w.get("kCGWindowLayer", 0) == 0:
            b = w.get("kCGWindowBounds", {})
            if b.get("Width", 0) > 300 and b.get("Height", 0) > 200:
                return w["kCGWindowNumber"], b
    return None


def capture_mac(window_id):
    img = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull,
        Quartz.kCGWindowListOptionIncludingWindow,
        window_id,
        Quartz.kCGWindowImageBoundsIgnoreFraming | Quartz.kCGWindowImageNominalResolution,
    )
    return img


# --------------------------------------------------------------------- OCR (macOS)

def ocr_mac(cgimage):
    """Return a list of (text, x, y, w, h) with y measured from the TOP, normalised 0..1."""
    results = []

    def handler(request, error):
        for obs in request.results() or []:
            cand = obs.topCandidates_(1)
            if not cand:
                continue
            box = obs.boundingBox()  # origin bottom-left, normalised
            x, y, w, h = box.origin.x, box.origin.y, box.size.width, box.size.height
            results.append((cand[0].string(), x, 1.0 - y - h, w, h))

    req = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(handler)
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    handler_ = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cgimage, None)
    ok, err = handler_.performRequests_error_([req], None)
    if not ok and VERBOSE:
        print("OCR error:", err)
    return results


# --------------------------------------------------------- capture + OCR (Windows)

_renamed = set()     # windows whose title we changed, so we can still recognise them


def _scan_roblox(include_minimised=False):
    """Roblox windows as (left, top, width, height, hwnd). Matched by window class, so renaming
    the title doesn't lose them."""
    import win32gui
    out = []

    def cb(h, _):
        try:
            if not win32gui.IsWindowVisible(h):
                return True
            if win32gui.IsIconic(h) and not include_minimised:
                return True
            if win32gui.GetClassName(h) == "WINDOWSCLIENT":
                l, t, r, b = win32gui.GetWindowRect(h)
                if win32gui.IsIconic(h) or (r - l > 300 and b - t > 200):
                    out.append((l, t, r - l, b - t, h))
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return out


def roblox_windows_win():
    """Every open (un-minimised) Roblox window."""
    return _scan_roblox()


def roblox_window_win():
    wins = roblox_windows_win()
    return wins[0] if wins else None


# Windows Graphics Capture reads the window's own surface, so it works while other
# windows cover it (it still can't see a minimised window). One capture per window.
_caps = {}


def _wgc_start(hwnd):
    from windows_capture import WindowsCapture
    cap = WindowsCapture(window_hwnd=hwnd, minimum_update_interval=max(250, CAPTURE_MS))   # ~2 frames/s (1/s in Eco), not 60
    state = {"hwnd": hwnd, "ctl": None, "frame": None, "closed": False}
    _caps[hwnd] = state

    @cap.event
    def on_frame_arrived(frame, ctl):
        now = time.time()
        if now - state.get("t", 0) >= CAPTURE_MS / 1000.0:      # frames arrive ~60/s; keep the latest one now and then
            state["t"] = now
            state["frame"] = frame.frame_buffer.copy()

    @cap.event
    def on_closed():
        state["closed"] = True

    state["ctl"] = cap.start_free_threaded()


HIDE_WINDOW = os.environ.get("HIDE_WINDOW", "0") == "1"
_moved = {}          # hwnd -> the on-screen spot to return to
_parked = set()      # windows currently parked off-screen by us
_hooked = []         # becomes non-empty once the exit hook is registered
_SWP = 0x0001 | 0x0004 | 0x0010 | 0x4000        # NOSIZE | NOZORDER | NOACTIVATE | ASYNCWINDOWPOS (never wait on the other app)


def _window_left(hwnd):
    import ctypes
    from ctypes import wintypes
    r = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top


import ctypes as _ct
from ctypes import wintypes as _wt


class _PowerThrottle(_ct.Structure):
    _fields_ = [("Version", _wt.DWORD), ("ControlMask", _wt.DWORD), ("StateMask", _wt.DWORD)]


_throttle_warned = []


def _throttle_window(hwnd, on):
    """Hidden Roblox: below-normal priority + Windows 'efficiency mode' (fewer/slower CPU cycles).
    Reversed when the window is shown again. Ordinary OS settings; nothing inside Roblox is touched."""
    if on and not THROTTLE_PARKED:
        return
    try:
        import psutil
        import win32process
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        proc = psutil.Process(pid)
        proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if on else psutil.NORMAL_PRIORITY_CLASS)
        cpus = list(range(psutil.cpu_count()))
        want = cpus[-PARK_CORES:] if (on and PARK_CORES and len(cpus) > PARK_CORES) else cpus   # last few cores, or all
        if sorted(proc.cpu_affinity()) != want:
            proc.cpu_affinity(want)
        k = _ct.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = _wt.HANDLE
        k.SetProcessInformation.argtypes = [_wt.HANDLE, _ct.c_int, _ct.c_void_p, _wt.DWORD]
        h = k.OpenProcess(0x0200, False, pid)                        # PROCESS_SET_INFORMATION
        if h:
            st = _PowerThrottle(1, 1, 1 if on else 0)               # execution-speed throttling on/off
            k.SetProcessInformation(h, 4, _ct.byref(st), _ct.sizeof(st))
            k.CloseHandle(h)
    except Exception as e:
        if not _throttle_warned:
            _throttle_warned.append(1)
            print("couldn't change Roblox's priority:", e)


def _park_offscreen(hwnd):
    """Move a Roblox window off-screen; it keeps rendering and stays capturable."""
    import atexit, ctypes
    if hwnd in _parked:
        return
    left, top = _window_left(hwnd)
    if left > -20000:
        _moved[hwnd] = (left, top)
    else:                                       # already off-screen (force-killed earlier run)
        _moved.setdefault(hwnd, (100, 100))
    if not _hooked:
        _hooked.append(True)
        atexit.register(lambda: [_unpark(h) for h in list(_parked)])
    ctypes.windll.user32.SetWindowPos(hwnd, 0, -30000, -30000, 0, 0, _SWP)
    _taskbar_hidden(hwnd, True)
    _parked.add(hwnd)
    _throttle_window(hwnd, True)
    print(f"Roblox window parked off-screen and hidden from the taskbar ({HOTKEY} or the tray menu shows it).")


def _unpark(hwnd):
    import ctypes
    if hwnd in _parked:
        _taskbar_hidden(hwnd, False)
        x, y = _moved.get(hwnd, (100, 100))
        ctypes.windll.user32.SetWindowPos(hwnd, 0, x, y, 0, 0, _SWP)
        _parked.discard(hwnd)
        _throttle_window(hwnd, False)


def _taskbar_hidden(hwnd, hide):
    """Drop the window from the taskbar and Alt+Tab (tool-window style), or put it back."""
    import ctypes
    u = ctypes.windll.user32
    ex = u.GetWindowLongW(hwnd, -20)                                    # GWL_EXSTYLE
    new = (ex | 0x80) & ~0x40000 if hide else (ex & ~0x80) | 0x40000    # TOOLWINDOW / APPWINDOW
    if new != ex:
        u.ShowWindowAsync(hwnd, 0)                                           # style changes apply on re-show
        u.SetWindowLongW(hwnd, -20, new)
        u.ShowWindowAsync(hwnd, 4)                                           # SW_SHOWNOACTIVATE


ROBLOX_ICON = os.environ.get("ROBLOX_ICON", "")     # path to a .ico; "gnome" = built-in green icon
_iconed = set()
_iconed_at = [0.0]


def _send_timeout(hwnd, msg, wparam, text=None, lparam=0, ms=1000):
    """SendMessage that gives up instead of hanging forever if the other program is frozen."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                      wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    res = ctypes.c_size_t()
    buf = ctypes.create_unicode_buffer(text) if text is not None else None
    lp = ctypes.cast(buf, ctypes.c_void_p).value if buf is not None else lparam
    return u.SendMessageTimeoutW(hwnd, msg, wparam, lp or 0, 0x0002, ms, ctypes.byref(res))   # SMTO_ABORTIFHUNG


def _gnome_ico():
    """Path to the built-in green gnome icon (created on first use)."""
    from PIL import ImageDraw
    path = os.path.join(HERE, "gnome.ico")
    if not os.path.exists(path):
        im = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.ellipse((8, 8, 248, 248), fill=(46, 160, 67, 255))
        d.polygon([(128, 20), (60, 150), (196, 150)], fill=(220, 40, 40, 255))   # gnome hat
        d.ellipse((88, 140, 168, 220), fill=(245, 222, 190, 255))
        im.save(path, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
    return path


def _cosmetics():
    """Every ~10s: give each Roblox window the custom icon and neutral title (also catches new ones)."""
    import ctypes
    if time.time() - _iconed_at[0] < 10:
        return
    _iconed_at[0] = time.time()
    for w in _scan_roblox(include_minimised=True):
        hwnd = w[4]
        if ROBLOX_ICON and hwnd not in _iconed:
            _iconed.add(hwnd)
            try:
                _set_window_icon(hwnd)
            except Exception as e:
                print("couldn't set icon:", e)
        title = WINDOW_TITLE if hwnd == _board[0] else OTHER_TITLE
        if title:
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.InternalGetWindowText(hwnd, buf, 256)     # reads it without messaging the window
            if buf.value != title:
                _renamed.add(hwnd)
                _send_timeout(hwnd, 0x000C, 0, title)                      # WM_SETTEXT, gives up after 1s


def _set_window_icon(hwnd):
    """Swap the taskbar/title-bar icon of the Roblox window (cosmetic; resets if Roblox restarts)."""
    import ctypes
    path = _gnome_ico() if ROBLOX_ICON.lower() == "gnome" else ROBLOX_ICON
    u = ctypes.windll.user32
    for size, which in ((16, 0), (32, 1)):          # ICON_SMALL, ICON_BIG
        h = u.LoadImageW(None, path, 1, size, size, 0x0010)   # IMAGE_ICON, LR_LOADFROMFILE
        if h:
            _send_timeout(hwnd, 0x0080, which, None, h)         # WM_SETICON (gives up after 1s)
            u.SetClassLongPtrW(hwnd, -14 if which else -34, h)  # GCLP_HICON / GCLP_HICONSM
    # The Windows 11 taskbar takes its icon from the window's AppUserModelID, so give the window
    # its own ID plus a relaunch icon; otherwise the taskbar keeps showing Roblox's own icon.
    try:
        from win32com.propsys import propsys, pscon
        store = propsys.SHGetPropertyStoreForWindow(hwnd, propsys.IID_IPropertyStore)
        V = propsys.PROPVARIANTType
        store.SetValue(pscon.PKEY_AppUserModel_ID, V(f"GnomeSentinel.Roblox.{hwnd}"))
        store.SetValue(pscon.PKEY_AppUserModel_RelaunchIconResource, V(path + ",0"))
        store.SetValue(pscon.PKEY_AppUserModel_RelaunchCommand, V("explorer.exe roblox-player:"))
        store.SetValue(pscon.PKEY_AppUserModel_RelaunchDisplayNameResource, V("Roblox"))
        store.Commit()
    except Exception as e:
        print("couldn't set taskbar app id:", e)


CAPTURE_STALE_S = 6.0         # a live game window delivers a fresh frame every fraction of a second; this long without one means the capture stalled


def _capture_stale(st, now=None):
    """Has this capture stopped delivering frames (it keeps handing back its last picture forever, so the board looks like it vanished)?"""
    now = now or time.time()
    if st is None or st.get("closed") or st.get("frame") is None:
        return False
    wait = min(300, 10 * 2 ** st.get("restarts", 0))              # a window that really is static isn't restarted over and over
    return now - st.get("t", now) > CAPTURE_STALE_S and now - st.get("restarted", 0) > wait


def _restart_capture(hwnd, st):
    try:
        if st.get("ctl") is not None:
            st["ctl"].stop()
    except Exception:
        pass
    n = st.get("restarts", 0) + 1
    print(f"{time.strftime('%H:%M:%S')}  capture: window {hwnd} stopped sending pictures - started a fresh capture")
    _wgc_start(hwnd)
    _caps[hwnd].update(restarted=time.time(), restarts=n)


def capture_win(win):
    hwnd = win[4]
    st = _caps.get(hwnd)
    if _capture_stale(st):
        _restart_capture(hwnd, st)
        st = _caps[hwnd]
    if st is None or st["closed"]:
        if st is not None and st["ctl"] is not None:
            try:
                st["ctl"].stop()
            except Exception:
                pass
        _wgc_start(hwnd)
        st = _caps[hwnd]
    for _ in range(50):                      # wait up to ~5s for the first frame
        if st["frame"] is not None:
            break
        time.sleep(0.1)
    arr = st["frame"]
    if arr is None:
        return None
    return Image.fromarray(arr[:, :, [2, 1, 0]])   # BGRA -> RGB


async def _ocr_win_async(png_bytes):
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream.get_output_stream_at(0))
    writer.write_bytes(png_bytes)
    await writer.store_async()
    await writer.flush_async()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError("Windows OCR has no language installed — add English under Settings → Language.")
    result = await engine.recognize_async(bitmap)
    W, H = bitmap.pixel_width, bitmap.pixel_height
    out = []
    for line in result.lines:
        words = list(line.words)
        if not words:
            continue
        x0 = min(w.bounding_rect.x for w in words)
        y0 = min(w.bounding_rect.y for w in words)
        x1 = max(w.bounding_rect.x + w.bounding_rect.width for w in words)
        y1 = max(w.bounding_rect.y + w.bounding_rect.height for w in words)
        out.append((line.text, x0 / W, y0 / H, (x1 - x0) / W, (y1 - y0) / H))
    return out


def ocr_win(image):
    if image.width < 1600:                       # the stylised sign font reads much better at 2x
        image = image.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return asyncio.run(_ocr_win_async(buf.getvalue()))


# ------------------------------------------------------------------ dispatch

def roblox_window():
    return roblox_window_mac() if IS_MAC else roblox_window_win()


def capture(win):
    return capture_mac(win[0]) if IS_MAC else capture_win(win)


def ocr(img):
    return ocr_mac(img) if IS_MAC else ocr_win(img)


_OCR_DIGITS = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"})
TIMER_NOISY_RE = re.compile(r"(\d+)\s*m\s*(\d{2})[\dsS$]?\b")            # "1m 298" = 1m 29s with the 's' read as a digit


def parse_timer(text):
    m = TIMER_RE.search(text)
    if not m:
        # OCR reads a 0 as O, a 1 as l, and the trailing s as a digit: tidy up and try once more
        fixed = text.translate(_OCR_DIGITS) if any(ch.isdigit() for ch in text) else text
        m2 = TIMER_RE.search(fixed)
        if m2 is None:
            n = TIMER_NOISY_RE.search(fixed)
            if n and int(n.group(2)) < 60:
                return int(n.group(1)) * 60 + int(n.group(2))
            return None
        text, m = fixed, m2
    if m.group(4) is not None:            # M:SS
        mins, secs = int(m.group(3)), int(m.group(4))
    else:
        mins = int(m.group(1)) if m.group(1) else 0
        secs = int(m.group(2))
    if secs >= 60:                        # "3m 83s" is a misread, not a time
        return None
    return mins * 60 + secs


_sign_boxes = []      # normalised boxes (label, name, timer) of the last billboard that was read


def _billboard(boxes, label_re):
    """
    The billboard reads:   <Weather name>
                           <label>          ("Event Active:" or "Next Event in:")
                           <timer>
    Only accept a name that sits directly ABOVE the label and overlaps it
    horizontally — nameplates also say 'Aurora' but never have a label under them.
    Returns (raw name, seconds) or None.
    """
    for text, x, y, w, h in boxes:
        if not label_re.search(text):
            continue
        cx = x + w / 2
        name = None
        name_box = None
        timer_box = None
        best = 9
        for t2, x2, y2, w2, h2 in boxes:
            if y2 + h2 <= y + 0.005 and (y - (y2 + h2)) < 0.14 and x2 <= cx <= x2 + w2:
                clean = re.sub(r"[^a-z ]", "", t2.lower())
                clean = " ".join(tok for tok in clean.split() if len(tok) > 1)   # drop stray 1-letter OCR noise ("l solar flare")
                if clean and clean not in UI_NOISE and (y - (y2 + h2)) < best and len(clean) <= 30:
                    best = y - (y2 + h2)
                    name = clean
                    name_box = (x2, y2, w2, h2)
        if not name:
            continue
        # timer: same line as 'Event Active: 10s' or the nearest box below
        remaining = parse_timer(text)
        if remaining is None:
            for t3, x3, y3, w3, h3 in boxes:
                if y3 >= y + h - 0.005 and (y3 - (y + h)) < 0.12 and x3 <= cx <= x3 + w3:
                    remaining = parse_timer(t3)
                    if remaining is not None:
                        timer_box = (x3, y3, w3, h3)
                        break
        _sign_boxes[:] = [b for b in ((x, y, w, h), name_box, timer_box) if b]
        return name, remaining
    return None


def find_weather(boxes):
    """Active event → (name, seconds remaining)."""
    hit = _billboard(boxes, ACTIVE_RE)
    if hit and hit[1] is None:
        return hit[0], hud_timer(boxes)
    return hit


def find_upcoming(boxes):
    """'Next Event in' → (name, seconds until start)."""
    return _billboard(boxes, NEXT_RE)


def _reread_timer(img, label_box):
    """The sign's countdown is stylised white or yellow text on brown that Windows OCR sometimes skips ('no time-left readable'). Look again
    at just the strip around the label: bigger, and only the brightest pixels kept (dark text on white, which OCR reads far better).
    Only the strict settings are used, and a result is accepted only if they don't disagree. Returns seconds or None."""
    from PIL import ImageOps
    W, H = img.size
    x, y, w, h = label_box
    x0, x1 = max(0, int((x - 0.05) * W)), min(W, int((x + w + 0.05) * W))
    y0, y1 = max(0, int((y - 0.005) * H)), min(H, int((y + h + 0.11) * H))
    if x1 - x0 < 30 or y1 - y0 < 15:
        return None
    value = img.crop((x0, y0, x1, y1)).convert("HSV").split()[2]          # brightness: the countdown is the brightest thing on the sign
    big = value.resize((value.width * 3, value.height * 3), Image.Resampling.LANCZOS)
    found = []
    for thr in (240, 225):
        bw = ImageOps.expand(big.point(lambda p, t=thr: 0 if p >= t else 255), border=20, fill=255)
        for text, *_ in ocr(bw.convert("RGB")):
            r = parse_timer(text)
            if r is not None:
                found.append(r)
                break
    if found and max(found) - min(found) <= 3:
        return found[0]
    return None


def hud_timer(boxes):
    """The HUD shows the same countdown as M:SS in the bottom-right corner."""
    for t, x, y, w, h in boxes:
        if x > 0.85 and y > 0.85 and re.fullmatch(r"\d{1,2}:\d{2}", t.strip()):
            return parse_timer(t)
    return None


# ------------------------------------------------------------------ report

def _parse_hotkey(spec):
    mods, vk = 0x4000, None                       # MOD_NOREPEAT
    for part in spec.lower().replace(" ", "").split("+"):
        if part in ("alt",):
            mods |= 0x0001
        elif part in ("ctrl", "control"):
            mods |= 0x0002
        elif part == "shift":
            mods |= 0x0004
        elif part in ("win", "windows"):
            mods |= 0x0008
        elif len(part) == 1:
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit():
            vk = 0x70 + int(part[1:]) - 1
    return mods, vk


PANEL = os.environ.get("PANEL", "1") == "1"
PANEL_HOTKEY = os.environ.get("PANEL_HOTKEY", "ctrl+shift+p")
GRID_HOTKEY = os.environ.get("GRID_HOTKEY", "ctrl+alt+shift+g")      # tiles all Roblox windows into a grid; again to put them back
PANIC_HOTKEY = os.environ.get("PANIC_HOTKEY", "ctrl+alt+shift+h")     # hides everything at once; press again to bring it back
HUD_LEARN = os.environ.get("HUD_LEARN", "1") == "1"                   # learn each mutation's corner icon from the board
PALETTE_HOTKEY = os.environ.get("PALETTE_HOTKEY", "")              # optional global hotkey for the command palette (off by default)
PANEL_STYLE = os.environ.get("PANEL_STYLE", "console").strip().lower()   # console | classic
PANEL_Q = queue.Queue()

_board = [None]          # hwnd of the Roblox window that has the weather board
_override = {}           # hwnd -> True (show) / False (hide), chosen from the panel, hotkey or tray
_snapshot = []           # what the panel lists: (hwnd, role, title, hidden, minimised)
_vis_lock = threading.Lock()
_hold = set()            # windows the keep-alive is working on right now: leave them where they are


def _want_hidden(hwnd):
    """Default rules: the board window is parked, every other window is left alone (HIDE_ALL parks them too).
    Anything chosen in the panel / hotkey overrides that."""
    if hwnd in _override:
        return not _override[hwnd]
    if hwnd == _board[0]:
        return HIDE_WINDOW or HIDE_ALL
    return HIDE_ALL


def _title_of(hwnd):
    import ctypes
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.InternalGetWindowText(hwnd, buf, 256)
    return buf.value


def _apply_visibility():
    """Detect every Roblox window, park/unpark each per the rules + overrides, refresh the panel list."""
    if not IS_WIN:
        return
    import ctypes
    with _vis_lock:
        snap = []
        for l, t, w, h, hwnd in _scan_roblox(include_minimised=True):
            minimised = bool(ctypes.windll.user32.IsIconic(hwnd))
            if not minimised and hwnd not in _hold:
                try:
                    if _want_hidden(hwnd):
                        _park_offscreen(hwnd)
                    elif hwnd in _parked:
                        _unpark(hwnd)
                    elif l <= -20000:              # left off-screen by an old, force-killed run
                        _moved.setdefault(hwnd, (100, 100))
                        _parked.add(hwnd)
                        _unpark(hwnd)
                except Exception as e:
                    print("visibility error:", e)
            snap.append((hwnd, "board" if hwnd == _board[0] else "other", _title_of(hwnd),
                         hwnd in _parked, minimised))
        _snapshot[:] = snap


def _set_show(value):
    """Show (True) or hide (False) every Roblox window."""
    for w in _scan_roblox():
        _override[w[4]] = value
    _apply_visibility()
    print("Roblox windows:", "shown" if value else "hidden")


def _set_window_show(hwnd, value):
    _override[hwnd] = value
    _apply_visibility()
    print(f"Roblox window {hwnd}:", "shown" if value else "hidden")


def _toggle_show():
    _set_show(bool(_parked))          # anything hidden -> show all, otherwise hide all


def _persist_env(key, value):
    """Write KEY=value into .env (or secrets.env for private keys), replacing the line or adding one."""
    path = _secrets_path if key in SECRET_KEYS else _env_path
    try:
        lines = open(path, encoding="utf-8-sig").read().splitlines() if os.path.exists(path) else []
        done = False
        for i, line in enumerate(lines):
            if re.match(rf"\s*{re.escape(key)}\s*=", line):
                lines[i], done = f"{key}={value}", True
        if not done:
            lines.append(f"{key}={value}")
        open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    except OSError as e:
        print("couldn't save the setting to .env:", e)


# Setting name in .env -> the module variable that holds it (when they differ), and how to convert text.
_GLOBAL_OF = {"POLL_SECONDS": "POLL_S", "INGEST_SECRET": "SECRET"}
_CASTS = {"POLL_SECONDS": float, "PARK_CORES": int, "KEEPALIVE_MINUTES": float, "LAUNCH_DELAY": float, "MACRO_RETRIES": int, "CAPTURE_MS": int, "MACRO_DELAY": float, "AUTO_SCENES_MIN": float, "MEM_TRIM_MIN": float, "WEBHOOK_SHOT_MIN": float, "RULE_AWAY_MIN": float, "OVERLAY_X": int, "OVERLAY_Y": int}


def _set_setting(name, value):
    """Change a setting right now (they are read live) and remember it in .env."""
    if isinstance(value, bool):
        raw = "1" if value else "0"
    else:
        value = _CASTS.get(name, str)(value)
        raw = str(value)
    globals()[_GLOBAL_OF.get(name, name)] = value
    os.environ[name] = raw
    _persist_env(name, raw)
    print(f"setting {name} = {raw}")
    if name in ("THROTTLE_PARKED", "PARK_CORES"):
        for h_ in list(_parked):                          # re-apply (or undo) on windows that are already hidden
            _throttle_window(h_, True)
    elif name in ("HIDE_WINDOW", "HIDE_ALL"):
        _apply_visibility()
    elif name in ("WINDOW_TITLE", "OTHER_TITLE"):
        _iconed_at[0] = 0.0                               # rename on the next pass
    elif name == "ROBLOX_ICON":
        _iconed.clear()
        _iconed_at[0] = 0.0
    elif name in ("HOTKEY", "PANEL_HOTKEY", "PALETTE_HOTKEY", "PANIC_HOTKEY", "GRID_HOTKEY"):
        _hotkey_reload()


def _set_alert(event, on):
    """Turn alerts for one event on/off (kept in .env as ALERT_OFF=a,b)."""
    (ALERT_OFF.discard if on else ALERT_OFF.add)(event)
    raw = ",".join(sorted(ALERT_OFF))
    os.environ["ALERT_OFF"] = raw
    _persist_env("ALERT_OFF", raw)
    print(f"alerts for '{event}': {'on' if on else 'off'}")


def _launch_url():
    """What 'New instance' opens: the game page or the private server, per the Settings choice."""
    if LAUNCH_TARGET == "custom" and LAUNCH_URL_CUSTOM:
        c = LAUNCH_URL_CUSTOM.strip()
        return f"https://www.roblox.com/games/{c}" if c.isdigit() else c          # a bare number is a place ID
    if LAUNCH_TARGET == "private" and LAUNCH_URL_PRIVATE:
        return LAUNCH_URL_PRIVATE
    if LAUNCH_TARGET == "game" and LAUNCH_URL_GAME:
        return LAUNCH_URL_GAME
    return LAUNCH_URL or LAUNCH_URL_GAME or LAUNCH_URL_PRIVATE


def _tail(path, max_bytes=40000):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - max_bytes))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _panel_thread():
    """The control panel lives in panel.py; Tk runs entirely in this thread."""
    me = sys.modules[__name__]
    try:
        if PANEL_STYLE != "classic":
            for mod in (("console_v1",) if PANEL_STYLE == "v1" else ("console", "console_v1")):
                try:
                    print(f"panel: opening {mod}")
                    __import__(mod).run(me)
                    print(f"panel: {mod} closed")
                    return
                except Exception as e:
                    import traceback
                    print(f"console error in {mod}, trying the next panel:", e)
                    print(traceback.format_exc())
        import panel
        panel.run(me)
    except Exception as e:
        print("panel error:", e)
    finally:
        import gc
        gc.collect()


_hotkey_tid = [0]


def _hotkey_reload():
    """Ask the hotkey thread to re-register after HOTKEY / PANEL_HOTKEY changed."""
    import ctypes
    if _hotkey_tid[0]:
        ctypes.windll.user32.PostThreadMessageW(_hotkey_tid[0], 0x8001, 0, 0)


def _hotkey_thread():
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    msg = wintypes.MSG()
    u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)              # create this thread's message queue
    _hotkey_tid[0] = ctypes.windll.kernel32.GetCurrentThreadId()

    def register():
        for hid in (1, 2, 3, 4, 5):
            u.UnregisterHotKey(None, hid)
        for hid, spec in ((1, HOTKEY), (2, PANEL_HOTKEY), (3, PALETTE_HOTKEY), (4, PANIC_HOTKEY), (5, GRID_HOTKEY)):
            if not spec:
                continue                                        # blank = that hotkey is switched off
            mods, vk = _parse_hotkey(spec)
            if vk is None or not u.RegisterHotKey(None, hid, mods, vk):
                print(f"couldn't register hotkey '{spec}' (another program may be using it) — use the tray menu")

    register()
    while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == 0x0312:                 # WM_HOTKEY
            if msg.wParam == 1:
                _toggle_show()
            elif msg.wParam == 2:
                PANEL_Q.put("toggle")
            elif msg.wParam == 3:
                PANEL_Q.put("palette")
            elif msg.wParam == 4:
                threading.Thread(target=_panic_toggle, daemon=True).start()
            elif msg.wParam == 5:
                threading.Thread(target=_grid_toggle, daemon=True).start()
        elif msg.message == 0x8001:               # our own "re-register" request
            register()


# ------------------------------------------------------------------ dashboard glue

DASH_URL = f"http://127.0.0.1:{DASHBOARD_PORT}/"
_lock_handle = []


MUTEX_NAME = "ROBLOX_singletonMutex"
_mx = {"h": None}


def _mutex_exists(k, name):
    """Does some process already own the named mutex? (Opening it succeeds only if it exists.)"""
    from ctypes import wintypes
    k.OpenMutexW.restype = wintypes.HANDLE
    k.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    h = k.OpenMutexW(0x00100000, False, name)          # SYNCHRONIZE
    if h:
        k.CloseHandle(h)
        return True
    return False


def _hold_singleton_legacy():
    """The Bloxstrap / Fishstrap / Voidstrap method for several Roblox copies at once: own Roblox's
    ROBLOX_singletonMutex ourselves (only the mutex - nothing else) BEFORE Roblox starts. Roblox then finds it
    already taken and lets every copy run. Nothing inside Roblox is touched.

    This thread takes the mutex the moment nobody else has it, keeps it while MULTI_INSTANCE is on, and lets go
    when it's switched off. If a Roblox that started earlier already owns it, we wait until all Roblox is closed."""
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = wintypes.HANDLE
    k.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.OpenMutexW.restype = wintypes.HANDLE
    k.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    while not QUIT.is_set():
        try:
            if MULTI_INSTANCE:
                if _mx["h"] is None:
                    if _mutex_exists(k, MUTEX_NAME):
                        # The lock still exists because the running Roblox windows hold handles to it. If its owner was an
                        # earlier run of this program, nobody owns it any more ("abandoned"): take it over, no need to close Roblox.
                        h = k.OpenMutexW(0x00100000 | 0x0001, False, MUTEX_NAME)       # SYNCHRONIZE | MUTEX_MODIFY_STATE
                        if h:
                            r = k.WaitForSingleObject(h, 0)
                            if r in (0x0, 0x80):                                           # WAIT_OBJECT_0 / WAIT_ABANDONED_0
                                _mx["h"] = h
                                STATE["lock"] = "held"
                                print("multi-instance: took over Roblox's lock again - your open windows stay as they are")
                                continue
                            k.CloseHandle(h)
                        if STATE["lock"] != "waiting":
                            print("multi-instance: Roblox already owns its lock - close ALL Roblox windows and it will switch on by itself")
                        STATE["lock"] = "waiting"
                    else:
                        h = k.CreateMutexW(None, True, MUTEX_NAME)            # owned from the start, like Bloxstrap
                        if h and ctypes.get_last_error() != 183:
                            _mx["h"] = h
                            STATE["lock"] = "held"
                            print("multi-instance: holding Roblox's single-instance lock - open as many copies as you like")
                        else:
                            if h:
                                k.CloseHandle(h)
                            STATE["lock"] = "waiting"
                else:
                    STATE["lock"] = "held"
            else:
                if _mx["h"] is not None:
                    k.CloseHandle(_mx["h"])
                    _mx["h"] = None
                    print("multi-instance: released the lock")
                STATE["lock"] = "off"
        except Exception as e:
            STATE["lock"] = "failed"
            print("multi-instance error:", e)
        time.sleep(1.5)


KEEPER_PATH = "lockkeeper.py"


def _keeper_file(name):
    return os.path.join(HERE, name)


def _keeper_info():
    """(state dict, is the keeper process alive?)"""
    d = _json_file("lockkeeper.json", {})
    pid, alive = d.get("pid"), False
    if pid:
        try:
            import psutil
            p = psutil.Process(int(pid))
            alive = p.is_running() and "lockkeeper" in " ".join(p.cmdline()).lower()
        except Exception:
            alive = False
    return d, alive


def _keeper_touch():
    try:
        with open(_keeper_file("lockkeeper.beat"), "w") as f:
            f.write(str(time.time()))
    except OSError:
        pass


def _keeper_stop():
    try:
        with open(_keeper_file("lockkeeper.stop"), "w") as f:
            f.write("1")
    except OSError:
        pass


def _keeper_spawn():
    """Start the keeper process and wait for it to say whether it got the lock. Returns 'held', 'waiting' or None."""
    try:
        os.remove(_keeper_file("lockkeeper.stop"))
    except OSError:
        pass
    _keeper_touch()
    exe = sys.executable
    if exe.lower().endswith("python.exe") and os.path.exists(exe[:-10] + "pythonw.exe"):
        exe = exe[:-10] + "pythonw.exe"                                  # no console window
    t0 = time.time()
    try:
        cmd = [exe, "--gw-keeper", MUTEX_NAME] if getattr(sys, "frozen", False) else [exe, _keeper_file(KEEPER_PATH), MUTEX_NAME]
        subprocess.Popen(cmd, cwd=HERE, close_fds=True,
                         creationflags=0x08000000 | 0x00000008)           # CREATE_NO_WINDOW | DETACHED_PROCESS
    except OSError as e:
        print("multi-instance: couldn't start the lock keeper:", e)
        return None
    while time.time() - t0 < 5:
        d = _json_file("lockkeeper.json", {})
        if d.get("t", 0) >= t0 - 0.5 and d.get("state") in ("held", "waiting", "failed"):
            return d["state"] if d["state"] != "failed" else None
        time.sleep(0.15)
    return None


def _hold_singleton():
    """The Bloxstrap / Fishstrap / Voidstrap method for several Roblox copies at once: own Roblox's ROBLOX_singletonMutex
    BEFORE Roblox starts (only the mutex - nothing inside Roblox is touched). A separate tiny process (lockkeeper.py) owns it,
    so the lock survives the sentinel restarting or crashing. If a Roblox that started earlier already owns the lock, we
    wait until all Roblox is closed."""
    if not os.path.exists(_keeper_file(KEEPER_PATH)):
        return _hold_singleton_legacy()
    last_try, said = 0.0, {"held": False, "waiting": False}
    try:
        while not QUIT.is_set():
            try:
                if MULTI_INSTANCE:
                    d, alive = _keeper_info()
                    if alive and d.get("state") == "held":
                        _keeper_touch()
                        try:
                            os.remove(_keeper_file("lockkeeper.stop"))
                        except OSError:
                            pass
                        if STATE["lock"] != "held" and not said["held"]:
                            print("multi-instance: holding Roblox's single-instance lock - open as many copies as you like")
                        said["held"], said["waiting"] = True, False
                        STATE["lock"] = "held"
                    elif time.time() - last_try >= 6:
                        last_try = time.time()
                        st = _keeper_spawn()
                        if st == "held":
                            STATE["lock"] = "held"
                            said["held"], said["waiting"] = True, False
                            print("multi-instance: holding Roblox's single-instance lock - open as many copies as you like"
                                  " (kept by a helper, so it survives restarts)")
                        elif st == "waiting":
                            if not said["waiting"]:
                                print("multi-instance: Roblox already owns its lock - close ALL Roblox windows and it will switch on by itself")
                            said["waiting"], said["held"] = True, False
                            STATE["lock"] = "waiting"
                        else:
                            STATE["lock"] = "failed"
                else:
                    d, alive = _keeper_info()
                    if alive:
                        _keeper_stop()
                        print("multi-instance: released the lock")
                    said["held"] = said["waiting"] = False
                    STATE["lock"] = "off"
            except Exception as e:
                STATE["lock"] = "failed"
                print("multi-instance error:", e)
            time.sleep(1.5)
    finally:
        _keeper_stop()                                               # a normal quit lets go; a crash or kill leaves it holding


def _launch_instance():
    url = _launch_url()
    if not url.lower().startswith(("https://", "http://", "roblox://", "roblox-player:")):
        print("launch: set LAUNCH_URL_GAME in .env (an https or roblox:// link) to use this button")
        return {"ok": False, "error": "launch link not set"}
    os.startfile(url)
    print("launch: opened the game link — Roblox will start (use the account you switched to)")
    return {"ok": True, "message": "Opening the game link…"}


def _open_dashboard():
    """Open the dashboard as its own app-style window (Edge/Chrome --app: no tabs, no address bar)."""
    import shutil
    import subprocess
    if not DASHBOARD:
        print("dashboard is off (DASHBOARD=0)")
        return
    pf = [os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""), os.environ.get("LOCALAPPDATA", "")]
    cands = [shutil.which("msedge"), shutil.which("chrome")]
    for base in pf:
        cands += [os.path.join(base, r"Microsoft\Edge\Application\msedge.exe"),
                  os.path.join(base, r"Google\Chrome\Application\chrome.exe")]
    for exe in cands:
        if exe and os.path.exists(exe):
            try:
                subprocess.Popen([exe, f"--app={DASH_URL}", "--window-size=1120,860", "--window-position=140,60"])
                return
            except OSError:
                continue
    import webbrowser
    webbrowser.open(DASH_URL)                        # no Edge/Chrome found: fall back to the default browser


def _dash_state():
    now = time.time()
    a, n = STATE["active"], STATE["next"]
    if a:
        board = {"kind": "active", "name": a[0], "target": a[1], "seen": a[2]}
    elif n:
        board = {"kind": "next", "name": n[0], "target": n[1], "seen": n[2]}
    else:
        board = {"kind": "none"}
    gj = {}
    try:
        gj = json.load(open(os.path.join(HERE, "assets", "game.json"), encoding="utf-8"))
    except Exception:
        pass
    w = STATE["worker"]
    return {
        "now": now,
        "sentinel": NAME,
        "game": {"name": gj.get("name", "Roll A Gnome"), "placeId": gj.get("placeId")},
        "board": board,
        "instances": [{"hwnd": h, "role": r, "title": t, "hidden": hid, "minimised": mini}
                      for (h, r, t, hid, mini) in list(_snapshot)],
        "can_launch": bool(_launch_url()),
        "health": {"worker_ok": w["ok"], "worker_msg": w["msg"], "worker_last": w["last"],
                   "last_read": STATE["last_read"], "board_seen": STATE["board_seen"],
                   "started": STATE["started"], "paused": PAUSED.is_set(), "muted": MUTED.is_set(),
                   "lock": STATE["lock"], "hotkey": HOTKEY, "panel_hotkey": PANEL_HOTKEY},
    }


def _dash_frame():
    img = _last_frame[0]
    if img is None:
        return None
    im = img.copy()
    im.thumbnail((720, 720))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=72)
    return buf.getvalue()


def _known_hwnd(p):
    h = p.get("hwnd")
    if h not in [x[0] for x in _snapshot]:
        raise ValueError("unknown window")
    return h


def _a_show(p):
    _set_window_show(_known_hwnd(p), True)
    return {"ok": True, "message": "Window shown"}


def _a_hide(p):
    _set_window_show(_known_hwnd(p), False)
    return {"ok": True, "message": "Window hidden"}


def _a_restore(p):
    import ctypes
    ctypes.windll.user32.ShowWindowAsync(_known_hwnd(p), 9)
    return {"ok": True, "message": "Window restored"}


def _a_pause(p):
    PAUSED.clear() if PAUSED.is_set() else PAUSED.set()
    return {"ok": True, "message": "Paused" if PAUSED.is_set() else "Resumed"}


def _a_mute(p):
    MUTED.clear() if MUTED.is_set() else MUTED.set()
    return {"ok": True, "message": "Alerts muted" if MUTED.is_set() else "Alerts on"}


def _user_idle_seconds():
    """Seconds since you last touched the keyboard or mouse."""
    import ctypes
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0


def _mouse_to(x, y):
    """Move the pointer to (x, y) so the GAME sees it arrive. A bare SetCursorPos isn't always noticed by Roblox, which then registers
    the next click where it last saw the pointer (somewhere random in the window). A tiny real move out and back fixes that.
    Returns True only when the pointer really is on the spot."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    pt = wintypes.POINT()
    for _ in range(3):
        u.SetCursorPos(x, y)
        time.sleep(0.05)
        u.mouse_event(0x0001, 1, 1, 0, 0)                   # MOUSEEVENTF_MOVE, relative: out one pixel...
        time.sleep(0.03)
        u.mouse_event(0x0001, -1, -1, 0, 0)                 # ...and back
        time.sleep(0.05)
        u.SetCursorPos(x, y)                                # pointer acceleration can leave it a pixel off: put it exactly there
        time.sleep(0.12)                                    # let the game take in the new position before anything is pressed
        u.GetCursorPos(ctypes.byref(pt))
        if abs(pt.x - x) <= 1 and abs(pt.y - y) <= 1:
            return True
    return False


def _click_center(hwnd):
    """Bring the window to the front, left-click the middle of it, then put everything back.
    Only clicks if the window really is in front and that spot belongs to it. Returns True if it clicked."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.WindowFromPoint.argtypes = [wintypes.POINT]
    u.WindowFromPoint.restype = wintypes.HWND
    u.GetAncestor.restype = wintypes.HWND
    was_parked = hwnd in _parked
    prev_fg = u.GetForegroundWindow()
    old = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(old))
    _hold.add(hwnd)
    try:
        if was_parked:
            _unpark(hwnd)                                   # bring it back on screen for a moment
            time.sleep(0.8)
        u.keybd_event(0x12, 0, 0, 0)                        # tap Alt so Windows lets us change the front window
        u.keybd_event(0x12, 0, 2, 0)
        u.SetForegroundWindow(hwnd)
        time.sleep(0.4)
        if u.GetForegroundWindow() != hwnd:
            return False
        def centre():                                       # the exact middle of the game area, read fresh each time (windows can move)
            rc = wintypes.RECT()
            u.GetClientRect(hwnd, ctypes.byref(rc))
            p_ = wintypes.POINT(rc.right // 2, rc.bottom // 2)
            u.ClientToScreen(hwnd, ctypes.byref(p_))
            return p_

        pt = centre()
        if u.GetAncestor(u.WindowFromPoint(pt), 2) != hwnd:   # GA_ROOT: is that spot really this window?
            return False
        if not _mouse_to(pt.x, pt.y):
            return False
        pt2 = centre()
        if abs(pt2.x - pt.x) > 2 or abs(pt2.y - pt.y) > 2 or u.GetForegroundWindow() != hwnd:      # it moved or lost focus: never click blind
            return False
        cur = wintypes.POINT()
        u.GetCursorPos(ctypes.byref(cur))
        if abs(cur.x - pt2.x) > 2 or abs(cur.y - pt2.y) > 2:
            return False
        u.mouse_event(0x0002, 0, 0, 0, 0)                   # left down, exactly in the middle
        time.sleep(0.06)
        u.mouse_event(0x0004, 0, 0, 0, 0)                   # left up
        time.sleep(0.3)
        return True
    finally:
        u.SetCursorPos(old.x, old.y)
        _mark_own()
        if prev_fg and prev_fg != hwnd:
            u.SetForegroundWindow(prev_fg)
        _hold.discard(hwnd)
        if was_parked:
            _apply_visibility()                             # hide it again


def _keepalive_loop():
    """KEEPALIVE: every KEEPALIVE_MINUTES, click the middle of each Roblox window so it never looks idle.
    Waits for a quiet moment (you not using the PC) so it doesn't steal focus while you type."""
    last = {}
    while not QUIT.is_set():
        try:
            if KEEPALIVE and not PAUSED.is_set() and not PANIC.is_set() and not LOCKED.is_set():
                now = time.time()
                every = max(1.0, KEEPALIVE_MINUTES) * 60
                live = [e for e in list(_snapshot) if not e[4]]
                for pid_ in list(last):
                    if pid_ not in [e[0] for e in live]:
                        del last[pid_]
                for hwnd, role, title, hidden, mini in live:
                    due = last.setdefault(hwnd, now) + every
                    if now < due:
                        continue
                    if _user_idle_seconds() < 8 and now < due + 300:
                        continue                            # you're busy: wait for a quiet moment (up to 5 min)
                    ok = _click_center(hwnd)
                    last[hwnd] = time.time() if ok else time.time() - every + 60      # a miss retries in a minute
                    print(f"{time.strftime('%H:%M:%S')}  keep-alive click on {title or hwnd}: "
                          f"{'done' if ok else 'skipped (window not ready)'}")
                    time.sleep(1)
        except Exception as e:
            print("keep-alive error:", e)
        time.sleep(5)


# ====================================================================== auto-reconnect: Roblox disconnect dialog -> click Reconnect
AUTO_RECONNECT = os.environ.get("AUTO_RECONNECT", "1") == "1"
_reco = {}                       # hwnd -> {"seen": scans in a row with the button, "tries", "last", "since", "bad"}
_DISC_WORDS = ("disconnected", "lost connection", "error code", "you were kicked", "connection attempt failed", "shut down", "shutdown", "server restart", "restarting")


def _scan_disconnect(hwnd):
    """OCR the middle of that window's latest frame. Returns (disconnected?, (fx, fy) of the Reconnect button or None)."""
    st = _caps.get(hwnd)
    arr = st.get("frame") if st else None
    if arr is None:
        return False, None
    img = Image.fromarray(arr[:, :, [2, 1, 0]])
    w, h = img.size
    x0, y0, x1, y1 = int(w * 0.12), int(h * 0.12), int(w * 0.88), int(h * 0.88)
    lines = ocr(img.crop((x0, y0, x1, y1)))
    cw, ch = x1 - x0, y1 - y0
    text = " ".join(t.lower() for t, *_ in lines)
    btn = None
    for t, fx, fy, fw, fh in lines:
        low = t.lower().strip()
        if "reconnect" in low and len(low) <= 16:                     # the button label, not a sentence
            btn = ((x0 + (fx + fw / 2) * cw) / w, (y0 + (fy + fh / 2) * ch) / h)
            break
    disc = btn is not None or any(k in text for k in _DISC_WORDS)
    return disc, btn


def _click_frame_point(hwnd, fx, fy, frame_size):
    """Bring the window forward (wherever it is), click a spot given as fractions of its captured picture, put it back."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    old = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(old))
    prev, was_parked = _bring_forward(hwnd)
    try:
        if u.GetForegroundWindow() != hwnd:
            return False
        rc = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(rc))
        cx, cy, cw, chh = _client_rect(hwnd)
        fw_px, fh_px = frame_size
        if abs(fw_px - (rc.right - rc.left)) <= abs(fw_px - cw) and abs(fh_px - (rc.bottom - rc.top)) <= abs(fh_px - chh):
            ox, oy, ww, hh = rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top      # the picture includes the title bar
        else:
            ox, oy, ww, hh = cx, cy, cw, chh
        if not _mouse_to(int(ox + fx * ww), int(oy + fy * hh)):
            return False
        u.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.06)
        u.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.3)
        return True
    finally:
        u.SetCursorPos(old.x, old.y)
        _mark_own()
        _put_back(hwnd, prev, was_parked)


def _reconnect_loop():
    """AUTO_RECONNECT: every ~10s look at each Roblox window for the disconnect dialog and press Reconnect.
    Light on purpose: one small OCR per window per pass, and nothing at all while paused / in panic mode."""
    while not QUIT.is_set():
        try:
            if AUTO_RECONNECT and IS_WIN and not PAUSED.is_set() and not PANIC.is_set() and not LOCKED.is_set():
                live = [e for e in list(_snapshot) if not e[4]]
                for hwnd in [h for h in _reco if h not in [e[0] for e in live]]:
                    del _reco[hwnd]
                for hwnd, role, title, hidden, mini in live:
                    _ensure_capture(hwnd)
                    r = _reco.setdefault(hwnd, {"seen": 0, "tries": 0, "last": 0.0, "since": 0.0, "bad": False, "gone": True})
                    disc, btn = _scan_disconnect(hwnd)
                    now = time.time()
                    if not disc:
                        if r["tries"] and not r["gone"]:
                            print(f"{time.strftime('%H:%M:%S')}  reconnect: {title or hwnd} is back")
                            notify("✔ Roblox reconnected", title or "window", event=None)
                        r.update(seen=0, tries=0, since=0.0, bad=False, gone=True)
                        continue
                    r["gone"] = False
                    r["since"] = r["since"] or now
                    if btn is None:
                        if not r["bad"]:
                            r["bad"] = True
                            print(f"{time.strftime('%H:%M:%S')}  reconnect: {title or hwnd} is disconnected but has no Reconnect button")
                            notify("⚠ Roblox disconnected", "No Reconnect button (kicked or banned?) - look at the window", event=None)
                        continue
                    r["seen"] += 1
                    if r["seen"] < 2 or now - r["last"] < 30:          # same dialog on two scans in a row, and not too often
                        continue
                    if _user_idle_seconds() < 5 and now - r["since"] < 120:
                        continue                                        # you're typing: wait a moment
                    if r["tries"] >= 6:
                        continue
                    if r["tries"] == 0:
                        notify("⚠ Roblox disconnected", f"Clicking Reconnect on {title or 'a window'}", event=None)
                    r["tries"] += 1
                    r["last"] = now
                    st = _caps.get(hwnd) or {}
                    fr = st.get("frame")
                    ok = fr is not None and _click_frame_point(hwnd, btn[0], btn[1], (fr.shape[1], fr.shape[0]))
                    print(f"{time.strftime('%H:%M:%S')}  reconnect: clicked Reconnect on {title or hwnd} (try {r['tries']}): {'done' if ok else 'window not ready'}")
                    if r["tries"] >= 6:
                        notify("✖ Couldn't reconnect", f"{title or 'a window'} still shows the dialog after 6 tries", event=None)
        except Exception as e:
            print("reconnect error:", e)
        time.sleep(10)


# ====================================================================== performance modes
# Smooth: everything animated.  FPS friendly: nothing moves, hidden Roblox is throttled, lowest impact on a game in front.
# Stealth: nothing visible or audible.  Eco: slow reads, slow redraws, for long AFK stretches.
UI_MODE = os.environ.get("UI_MODE", "smooth").strip().lower()
CAPTURE_MS = int(os.environ.get("CAPTURE_MS", "500"))
MODE_PROFILES = {
    "smooth":  {"priority": "normal", "settings": {"ANIMATIONS": "normal", "POLL_SECONDS": 2.0, "IDLE_BACKOFF": True, "CAPTURE_MS": 500}},
    "fps":     {"priority": "below",  "settings": {"ANIMATIONS": "off", "POLL_SECONDS": 3.0, "IDLE_BACKOFF": True, "THROTTLE_PARKED": True,
                                                   "PARK_CORES": 1, "CAPTURE_MS": 500}},
    "stealth": {"priority": "below",  "settings": {"ANIMATIONS": "off", "POLL_SECONDS": 3.0, "IDLE_BACKOFF": True, "THROTTLE_PARKED": True,
                                                   "PARK_CORES": 1, "CAPTURE_MS": 500}},
    "eco":     {"priority": "idle",   "settings": {"ANIMATIONS": "off", "POLL_SECONDS": 5.0, "IDLE_BACKOFF": True, "THROTTLE_PARKED": True,
                                                   "PARK_CORES": 1, "CAPTURE_MS": 1000}},
}
if UI_MODE not in MODE_PROFILES:
    UI_MODE = "smooth"
MODE_PREV = os.path.join(DATA, "mode_prev.json")
_STEALTH_KEYS = ("NOTIFY", "NOTIFY_SOUND", "NOTIFY_TOAST", "HIDE_WINDOW", "HIDE_ALL")
_mode_lock = threading.Lock()


def _apply_priority(level):
    """Run the sentinel itself at a lower priority so a game in front always wins."""
    try:
        import psutil
        psutil.Process().nice({"normal": psutil.NORMAL_PRIORITY_CLASS, "below": psutil.BELOW_NORMAL_PRIORITY_CLASS,
                               "idle": psutil.IDLE_PRIORITY_CLASS}[level])
    except Exception as e:
        print("couldn't change the sentinel's priority:", e)


def _recycle_captures():
    """Stop the running window captures; they restart by themselves at the new rate."""
    for st in list(_caps.values()):
        try:
            if st.get("ctl") is not None:
                st["ctl"].stop()
        except Exception:
            pass
        st["closed"] = True


def _set_mode(name):
    """Switch performance mode: changes the settings that matter, the process priority and (through the console) the look."""
    name = str(name).strip().lower()
    prof = MODE_PROFILES.get(name)
    if not prof:
        return False
    with _mode_lock:
        old, old_cap = UI_MODE, CAPTURE_MS
        if name == "stealth" and old != "stealth":
            try:
                json.dump({k: bool(globals()[k]) for k in _STEALTH_KEYS}, open(MODE_PREV, "w"))      # what to give back afterwards
            except OSError:
                pass
        for k, v in prof["settings"].items():
            _set_setting(k, v)
        if name == "stealth":
            for k in ("NOTIFY", "NOTIFY_SOUND", "NOTIFY_TOAST"):
                _set_setting(k, False)
            _set_setting("HIDE_WINDOW", True)
            _set_setting("HIDE_ALL", True)
            _set_show(False)
            _tray_visible(False)
        elif old == "stealth":
            try:
                prev = json.load(open(MODE_PREV, encoding="utf-8"))
            except (OSError, ValueError):
                prev = {}
            for k in _STEALTH_KEYS:
                if k in prev:
                    _set_setting(k, bool(prev[k]))
            _tray_visible(True)
            _override.clear()
            _apply_visibility()
        _set_setting("UI_MODE", name)
        _apply_priority(prof["priority"])
        if CAPTURE_MS != old_cap:
            _recycle_captures()
        PANEL_Q.put("restyle")
    print(f"mode: {name}")
    return True


# ====================================================================== clients: what each Roblox window is doing
import gw_logs as _gwl

SESSIONS_FILE_NAME = "sessions.jsonl"
_sess_n = [0]
_clients = {}                    # pid -> {"state", "place", "job", "ip", "rss", "cpu", "log", "reason", ...}
_procs = {}
_tails = {}
_last_disc = {}
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip()
WEBHOOK_FAILS = os.environ.get("WEBHOOK_FAILS", "1") == "1"
WEBHOOK_REJOINS = os.environ.get("WEBHOOK_REJOINS", "1") == "1"
WEBHOOK_DISCONNECTS = os.environ.get("WEBHOOK_DISCONNECTS", "1") == "1"
WEBHOOK_EVENTS = os.environ.get("WEBHOOK_EVENTS", "0") == "1"
WEBHOOK_SHOT_MIN = float(os.environ.get("WEBHOOK_SHOT_MIN", "0"))
MEM_TRIM = os.environ.get("MEM_TRIM", "0") == "1"
MEM_TRIM_MIN = float(os.environ.get("MEM_TRIM_MIN", "10"))
_mem = {"last": 0.0, "msg": "", "freed": 0.0}


def _session_add(account, kind, detail=""):
    """One line in the per-account history (sessions.jsonl): launches, joins, disconnects, rejoins, auto-place results."""
    try:
        path = os.path.join(DATA, SESSIONS_FILE_NAME)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": round(time.time()), "account": account or "", "kind": kind, "detail": detail}) + "\n")
        _sess_n[0] += 1
        if _sess_n[0] % 100 == 0 and os.path.getsize(path) > 1_500_000:
            keep = open(path, encoding="utf-8", errors="replace").read().splitlines()[-3000:]
            open(path, "w", encoding="utf-8").write("\n".join(keep) + "\n")
    except OSError:
        pass


def _sessions_load(account=None, limit=30):
    """Newest first."""
    path = os.path.join(DATA, SESSIONS_FILE_NAME)
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 400_000))
            lines = f.read().decode("utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if account is None or r.get("account") == account:
            out.append(r)
            if len(out) >= limit:
                break
    return out


def _hwnd_pid(hwnd):
    try:
        import win32process
        return win32process.GetWindowThreadProcessId(hwnd)[1]
    except Exception:
        return 0


def _account_of_hwnd(hwnd):
    return _json_file("assign.json", {}).get(str(hwnd))


def _account_of_pid(pid):
    for e in list(_snapshot):
        if _hwnd_pid(e[0]) == pid:
            return _account_of_hwnd(e[0])
    return None


def _client_for_hwnd(hwnd):
    """The live info for the Roblox client that owns this window (RAM, CPU, game, state), or None."""
    return _clients.get(_hwnd_pid(hwnd))


def _client_event(pid, ev, info, replay=False):
    kind = ev["kind"]
    acct = _account_of_pid(pid)
    if kind == "join":
        info.update(place=ev["place"], job=ev["job"], ip=ev["ip"], state="in game", joined=time.time(), reason=None)
        if not replay:
            _session_add(acct, "join", f"place {ev['place']} · server {ev['job'][:8]}")
    elif kind == "server":
        info["server"] = f"{ev['ip']}:{ev['port']}"
    elif kind == "disconnect":
        code, now = ev.get("code"), time.time()
        last = _last_disc.get(pid)
        if last and last[0] == code and now - last[1] < 8:
            return                                              # the log repeats this line several times
        _last_disc[pid] = (code, now)
        if ev.get("clean"):
            info.update(state="left", reason=None)
            if not replay:
                _session_add(acct, "left", "left the game")
            return
        name = _gwl.error_name(code)
        info.update(state="disconnected", reason=f"{name} (code {code})")
        if not replay:
            _session_add(acct, "disconnect", f"{name} (code {code})")
            print(f"{time.strftime('%H:%M:%S')}  {acct or 'a Roblox window'} disconnected: {name} (code {code})")
            _hook(f"{acct or 'A Roblox window'} disconnected", f"Reason: {name} (code {code})", "bad", "disconnect")
    elif kind == "lost":
        if info.get("state") != "disconnected":
            info["state"] = "reconnecting"
    elif kind == "left":
        if info.get("state") not in ("disconnected",):
            info["state"] = "left"


def _roblox_player_pids():
    import psutil
    out = []
    for p in psutil.process_iter(["pid", "name"]):
        if (p.info["name"] or "").lower() == "robloxplayerbeta.exe":
            out.append(p.pid)
    return out


def _clients_pass(full):
    import psutil
    pids = set(_roblox_player_pids())
    for pid in [p for p in _clients if p not in pids]:
        _clients.pop(pid, None)
        _procs.pop(pid, None)
        _tails.pop(pid, None)
    maps = _gwl.client_logs() if (full or any(_clients.get(p, {}).get("log") is None for p in pids)) else {}
    for pid in pids:
        info = _clients.setdefault(pid, {"pid": pid, "state": "starting", "place": None, "job": None, "reason": None, "log": None,
                                         "rss": 0, "cpu": 0.0, "primed": False})
        try:
            proc = _procs.get(pid) or _procs.setdefault(pid, psutil.Process(pid))
            info["rss"] = proc.memory_info().rss
            info["cpu"] = proc.cpu_percent(None) / max(1, psutil.cpu_count() or 1)
            info.setdefault("start", proc.create_time())
        except psutil.Error:
            continue
        if pid in maps and info["log"] != maps[pid]:
            info["log"] = maps[pid]
            _tails[pid] = _gwl.Tail(maps[pid])
        tail = _tails.get(pid)
        if tail:
            replay = not info["primed"]
            for line in tail.lines():
                ev = _gwl.parse_line(line)
                if ev:
                    _client_event(pid, ev, info, replay)
            info["primed"] = True
        # Roblox's log didn't say "joined" (or the log couldn't be matched), but its window has been up well over a minute: it is in the game
        if info.get("state") == "starting" and time.time() - info.get("start", time.time()) > 75 and not info.get("reason"):
            info["state"] = "in game"
            info["assumed"] = True


def _trim_pid(pid):
    import ctypes
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = ctypes.c_void_p
    h = k.OpenProcess(0x0100 | 0x0400, False, pid)               # PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION
    if not h:
        return False
    try:
        return bool(ctypes.windll.psapi.EmptyWorkingSet(ctypes.c_void_p(h)))
    finally:
        k.CloseHandle(ctypes.c_void_p(h))


def _trim_background(force=False):
    """Hand idle memory of background Roblox clients back to Windows. Returns (clients trimmed, MB freed).
    Background = hidden, parked or minimised (force: every client except the one in front)."""
    import ctypes
    import psutil
    fg = ctypes.windll.user32.GetForegroundWindow()
    n, freed = 0, 0
    for hwnd, role, title, hidden, mini in list(_snapshot):
        if hwnd == fg or hwnd in _hold or not (hidden or mini or force):
            continue
        pid = _hwnd_pid(hwnd)
        try:
            before = psutil.Process(pid).memory_info().rss
            if _trim_pid(pid):
                after = psutil.Process(pid).memory_info().rss
                n += 1
                freed += max(0, before - after)
        except psutil.Error:
            continue
    _mem.update(last=time.time(), freed=freed / 1e6, msg=f"trimmed {n} client(s), freed about {freed / 1e6:.0f} MB")
    print(f"{time.strftime('%H:%M:%S')}  memory: {_mem['msg']}")
    return n, freed / 1e6


RAMCAP = {}                    # account label -> {"mb", "cap", "over", "trims", "warned"}: what the cap sees right now (the Accounts page shows it)
_ramcap_seen = {}              # (label, pid) -> {"trimmed": t, "over_since": t, "warned": t}
RAMCAP_RETRIM_S, RAMCAP_WARN_AFTER_S, RAMCAP_WARN_EVERY_S = 90, 60, 900


def _ramcap_pass(caps=None, assign=None, now=None):
    """Per-account memory cap (set on the Accounts page). Over the cap: hand idle memory back to Windows (never closes anything);
    still over a minute after a trim: tell the person, at most every 15 minutes. caps/assign/now are only for tests."""
    caps = caps if caps is not None else {a.get("label"): int(a.get("ram_cap") or 0) for a in _json_file("accounts.json", []) if int(a.get("ram_cap") or 0) > 0}
    if not caps:
        RAMCAP.clear()
        _ramcap_seen.clear()
        return
    assign = assign if assign is not None else _json_file("assign.json", {})
    now = now or time.time()
    live = set()
    for h, lbl in assign.items():
        cap = caps.get(lbl)
        if not cap:
            continue
        try:
            pid = _hwnd_pid(int(h))
        except (TypeError, ValueError):
            continue
        info = _clients.get(pid) if pid else None
        if not info:
            continue
        key = (lbl, pid)
        live.add(key)
        mb = info.get("rss", 0) / 1e6
        st = _ramcap_seen.setdefault(key, {"trimmed": 0.0, "over_since": 0.0, "warned": 0.0, "trims": 0})
        row = RAMCAP.setdefault(lbl, {"mb": 0, "cap": cap, "over": False, "trims": 0, "warned": False})
        row.update(mb=mb, cap=cap, trims=st["trims"])
        if mb <= cap:
            st["over_since"] = 0.0
            row.update(over=False, warned=False)
            continue
        row["over"] = True
        st["over_since"] = st["over_since"] or now
        if now - st["trimmed"] >= RAMCAP_RETRIM_S:
            freed, before = 0.0, None
            try:
                import psutil
                before = psutil.Process(pid).memory_info().rss
            except Exception:
                pass
            if _trim_pid(pid) and before:
                try:
                    freed = max(0, before - psutil.Process(pid).memory_info().rss) / 1e6
                except Exception:
                    pass
            st["trimmed"] = now
            st["trims"] += 1
            row["trims"] = st["trims"]
            print(f"{time.strftime('%H:%M:%S')}  memory cap: {lbl} is at {mb:.0f} MB (cap {cap}) - trimmed, freed about {freed:.0f} MB")
        elif now - st["over_since"] >= RAMCAP_WARN_AFTER_S and now - st["trimmed"] >= RAMCAP_WARN_AFTER_S and now - st["warned"] >= RAMCAP_WARN_EVERY_S:
            st["warned"] = now
            row["warned"] = True
            msg = f"Using {mb / 1000:.1f} GB, over its {cap / 1000:.1f} GB cap even after trimming"
            print(f"{time.strftime('%H:%M:%S')}  memory cap: {lbl}: {msg}")
            _session_add(lbl, "memory", msg)
            notify(f"Memory cap: {lbl}", msg + ". Restarting that window would free it.")
    for key in [k for k in _ramcap_seen if k not in live]:
        _ramcap_seen.pop(key, None)
    for lbl in [l for l in RAMCAP if l not in caps or not any(k[0] == l for k in live)]:
        RAMCAP.pop(lbl, None)


def _clients_loop():
    """Every 3 s: RAM/CPU of each Roblox client and what its own log says; trims background memory if switched on."""
    time.sleep(8)
    n = 0
    while not QUIT.is_set():
        try:
            if IS_WIN:
                _clients_pass(full=(n % 5 == 0))
                n += 1
                if n % 5 == 1:                                   # every ~15 s
                    _ramcap_pass()
                if MEM_TRIM and time.time() - _mem["last"] >= max(1.0, MEM_TRIM_MIN) * 60 and not _play_lock.locked() and not _lq["running"]:
                    _trim_background()
        except Exception as e:
            print("clients error:", e)
        time.sleep(3)


# ====================================================================== Discord webhook
_hook_q = queue.Queue()
_HOOK_COLOURS = {"info": 0x7C6CFF, "ok": 0x2EE59D, "bad": 0xFF5470, "warn": 0xFFB547}


def _hook_url_ok(url=None):
    u = (url if url is not None else WEBHOOK_URL).strip().lower()
    return u.startswith("https://") and "/api/webhooks/" in u and any(h in u for h in ("discord.com", "discordapp.com"))


def _hook(title, text="", level="info", kind="", image=None):
    """Queue a message for the Discord webhook (if one is set and this kind of message is switched on)."""
    on = {"fail": WEBHOOK_FAILS, "rejoin": WEBHOOK_REJOINS, "disconnect": WEBHOOK_DISCONNECTS, "event": WEBHOOK_EVENTS}.get(kind, True)
    if WEBHOOK_URL and on and _hook_url_ok():
        _hook_q.put((title, text, level, image))


def _hook_post(title, text, level, image=None, url=None):
    """Send one embed (optionally with a picture). Returns (ok, message)."""
    import uuid
    payload = {"username": "Gnomewatch", "embeds": [{"title": title[:250], "description": (text or "")[:1900],
               "color": _HOOK_COLOURS.get(level, _HOOK_COLOURS["info"]), "footer": {"text": f"Gnomewatch · {NAME}"},
               "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}]}
    headers = {"User-Agent": "Gnomewatch/2.0"}
    if image:
        payload["embeds"][0]["image"] = {"url": "attachment://board.png"}
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\nContent-Type: application/json\r\n\r\n"
                + json.dumps(payload) + f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; filename=\"board.png\"\r\n"
                "Content-Type: image/png\r\n\r\n").encode() + image + f"\r\n--{boundary}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    else:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url or WEBHOOK_URL, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as r:
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        if e.code == 429:
            try:
                time.sleep(min(10, float(json.loads(e.read()).get("retry_after", 2))))
            except Exception:
                time.sleep(2)
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)[:80]


def _hook_test():
    if not _hook_url_ok():
        return False, "that isn't a Discord webhook link"
    return _hook_post("Gnomewatch is connected", "This is a test message. Failures, rejoins and disconnects will show up here.", "ok")


def _hook_loop():
    last_shot = time.time()
    while not QUIT.is_set():
        try:
            try:
                title, text, level, image = _hook_q.get(timeout=5)
                _hook_post(title, text, level, image)
                time.sleep(1.5)
            except queue.Empty:
                pass
            if WEBHOOK_SHOT_MIN > 0 and WEBHOOK_URL and _hook_url_ok() and time.time() - last_shot >= WEBHOOK_SHOT_MIN * 60:
                last_shot = time.time()
                fr = _last_frame[0]
                if fr is not None:
                    import io as _io
                    im = fr.copy()
                    im.thumbnail((960, 960))
                    buf = _io.BytesIO()
                    im.save(buf, format="PNG")
                    kind, name, left = _event_summary()
                    _hook_post("Board snapshot", f"{name.title()} · {_fmt_left(left)}" if kind != "none" else "No event on the sign", "info", buf.getvalue())
        except Exception as e:
            print("webhook error:", e)
            time.sleep(5)


# ====================================================================== window grid
_grid = {"saved": {}, "on": False}


def _arrange_grid():
    """Tile every Roblox window across the main monitor (showing hidden ones). Returns how many were arranged."""
    import ctypes
    import win32api
    u = ctypes.windll.user32
    wins = _scan_roblox(include_minimised=False)
    if not wins:
        return 0
    for l, t, w, h, hwnd in wins:
        _override[hwnd] = True                                  # un-park them, and keep them out of the parking rules
    _apply_visibility()
    time.sleep(0.9)
    wins = sorted(_scan_roblox(include_minimised=False), key=lambda w: w[4])
    mon = win32api.GetMonitorInfo(win32api.MonitorFromPoint((0, 0), 1))["Work"]
    ml, mt, mr, mb = mon
    W, H, gap, n = mr - ml, mb - mt, 6, len(wins)
    best = (0, 1, 1)
    for cols in range(1, n + 1):
        rows = -(-n // cols)
        cw, ch = (W - gap * (cols + 1)) / cols, (H - gap * (rows + 1)) / rows
        fit = min(cw, ch * 16 / 9)                              # biggest 16:9 picture that fits the cell
        if fit > best[0]:
            best = (fit, cols, rows)
    _f, cols, rows = best
    cw, ch = int((W - gap * (cols + 1)) / cols), int((H - gap * (rows + 1)) / rows)
    _grid["saved"] = {}
    for i, (l, t, w, h, hwnd) in enumerate(wins):
        _grid["saved"][hwnd] = (l, t, w, h)
        x, y = ml + gap + (i % cols) * (cw + gap), mt + gap + (i // cols) * (ch + gap)
        u.SetWindowPos(hwnd, 0, x, y, cw, ch, 0x0004 | 0x0010 | 0x4000)      # NOZORDER | NOACTIVATE | ASYNCWINDOWPOS
    _grid["on"] = True
    print(f"{time.strftime('%H:%M:%S')}  windows: tiled {n} Roblox window(s) in a {cols}x{rows} grid")
    return n


def _restore_grid():
    import ctypes
    u = ctypes.windll.user32
    for hwnd, (l, t, w, h) in list(_grid["saved"].items()):
        if u.IsWindow(hwnd):
            u.SetWindowPos(hwnd, 0, l, t, w, h, 0x0004 | 0x0010 | 0x4000)
        _override.pop(hwnd, None)
    _grid.update(saved={}, on=False)
    _apply_visibility()
    print(f"{time.strftime('%H:%M:%S')}  windows: put back where they were")


def _grid_toggle():
    if _grid["on"]:
        _restore_grid()
    else:
        _arrange_grid()


# ====================================================================== low-resource launches (Roblox's own settings file)
RBX_SETTINGS = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Roblox", "GlobalBasicSettings_13.xml")
_rbx = {"changed": False, "blocked": ""}


def _rbx_locked():
    """Is Roblox's settings file read-only? (Something - you, or another tool - locked it on purpose; we leave it alone.)"""
    import stat
    try:
        return os.path.exists(RBX_SETTINGS) and not (os.stat(RBX_SETTINGS).st_mode & stat.S_IWRITE)
    except OSError:
        return False


def _writable(path):
    """A read-only flag on the file would block our edit (and Roblox's own saves): clear it."""
    try:
        import stat
        if os.path.exists(path):
            os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
    except OSError:
        pass


def _rbx_apply(fps=None, quality=None):
    """Write a frame-rate cap and graphics level into Roblox's own settings file (read when a client starts).
    The first time, the original is saved next to it so it can always be put back. Returns True if it was changed."""
    import re as _re
    path = RBX_SETTINGS
    if not os.path.exists(path):
        _rbx["blocked"] = "Roblox's settings file wasn't found"
        return False
    if _rbx_locked():
        _rbx["blocked"] = "Roblox's settings file is read-only (locked), so it was left alone"
        return False
    _rbx["blocked"] = ""
    bak = path + ".gnomewatch-backup"
    if not os.path.exists(bak):
        import shutil
        shutil.copy2(path, bak)
    text = open(path, encoding="utf-8", errors="replace").read()
    if fps:
        text = _re.sub(r'(<int name="FramerateCap">)-?\d+(</int>)', rf"\g<1>{int(fps)}\g<2>", text)
    if quality:
        q = max(1, min(10, int(quality)))
        text = _re.sub(r'(<token name="SavedQualityLevel">)\d+(</token>)', rf"\g<1>{q}\g<2>", text)
        text = _re.sub(r'(<int name="GraphicsQualityLevel">)\d+(</int>)', rf"\g<1>{round(1 + (q - 1) * 20 / 9)}\g<2>", text)
    _writable(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    _rbx["changed"] = True
    return True


def _rbx_restore():
    """Put Roblox's settings file back to how it was before Gnomewatch first changed it."""
    bak = RBX_SETTINGS + ".gnomewatch-backup"
    if not os.path.exists(bak) or _rbx_locked():
        return False
    import shutil
    _writable(RBX_SETTINGS)
    shutil.copy2(bak, RBX_SETTINGS)
    _rbx["changed"] = False
    return True


def _apply_account_profile(a):
    """Before opening an account: low-resource accounts get the lower FPS/quality, everyone else the original settings."""
    try:
        lr = a.get("lowres") or {}
        if lr.get("on"):
            _rbx_apply(lr.get("fps") or 20, lr.get("quality") or 2)
        elif _rbx["changed"]:
            _rbx_restore()
    except Exception as e:
        print("low-resource profile:", e)


# ====================================================================== per-account join target
def _launch_url_for(a):
    """Where this account joins: its own choice if it has one, otherwise the global setting."""
    t, u = (a.get("target") or "").lower(), (a.get("url") or "").strip()
    if t == "custom" and u:
        return f"https://www.roblox.com/games/{u}" if u.isdigit() else u
    if t == "private" and LAUNCH_URL_PRIVATE:
        return LAUNCH_URL_PRIVATE
    if t == "game" and LAUNCH_URL_GAME:
        return LAUNCH_URL_GAME
    return _launch_url()


# ====================================================================== auto-rejoin: relaunch an account whose window vanished
AUTO_REJOIN = os.environ.get("AUTO_REJOIN", "0") == "1"
REJOIN_GRACE = 20            # seconds a window must stay gone before it counts as a crash
REJOIN_MAX = 4               # relaunches per account per hour, then it gives up and tells you
_pending_launch = []         # (account label, time): browsers we just asked to open Roblox; the console assigns the next new window
_expected_close = {}         # hwnd -> time: windows we closed on purpose
_rj = {}


def _json_file(name, default):
    try:
        return json.load(open(os.path.join(DATA, name), encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _launch_account(a, retry=False):
    """Open Roblox for one saved account (browser profile + the launch link from the Settings). Returns (ok, message).
    retry=True opens the link again without repeating the account's bookkeeping (the history line, the last-launched time)."""
    kind = a.get("browser", "edge")
    rel = {"edge": r"Microsoft\Edge\Application\msedge.exe", "chrome": r"Google\Chrome\Application\chrome.exe"}.get(kind)
    exe = None
    for base in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""), os.environ.get("LOCALAPPDATA", "")):
        p = os.path.join(base, rel or "")
        if base and rel and os.path.exists(p):
            exe = p
            break
    url = _launch_url_for(a)
    if not exe:
        return False, f"{'Chrome' if kind == 'chrome' else 'Edge'} isn't installed (this account uses it)"
    if not url:
        return False, "no launch link is set - add your game or private-server link under Settings > New instance opens"
    if not retry:
        _apply_account_profile(a)
    try:
        subprocess.Popen([exe, f"--profile-directory={a['profile']}", url])
    except OSError as e:
        return False, str(e)
    if not retry:
        lst = _json_file("accounts.json", [])
        for x in lst:
            if x.get("label") == a.get("label"):
                x["last"] = time.time()
        try:
            tmp = os.path.join(DATA, "accounts.json.tmp")
            json.dump(lst, open(tmp, "w", encoding="utf-8"), indent=1)
            os.replace(tmp, os.path.join(DATA, "accounts.json"))
        except OSError:
            pass
        _session_add(a.get("label"), "launch", (a.get("target") or "default") + " link")
    _pending_launch[:] = [p for p in _pending_launch if p[0] != a.get("label")]
    _pending_launch.append((a.get("label"), time.time()))
    return True, "ok"


def _rejoin_pass():
    now = time.time()
    accounts = _json_file("accounts.json", [])
    assign = _json_file("assign.json", {})
    alive = {e[0] for e in list(_snapshot)}
    live_by = {}
    for h, lbl in assign.items():
        try:
            h = int(h)
        except (TypeError, ValueError):
            continue
        if h in alive:
            live_by.setdefault(lbl, set()).add(h)
    for a in accounts:
        lbl = a.get("label")
        r = _rj.setdefault(lbl, {"seen": False, "lost": 0.0, "hw": set(), "times": [], "wait": 0.0, "gaveup": False})
        up = live_by.get(lbl)
        if up:
            r.update(seen=True, lost=0.0, hw=set(up), wait=0.0, gaveup=False)
            continue
        if r["wait"]:                                           # we relaunched it: give the window time to appear
            if now - r["wait"] < 180:
                continue
            r.update(wait=0.0, seen=True, lost=now - REJOIN_GRACE)   # still nothing after 3 minutes: it counts as lost again
        if not r["seen"]:
            continue                                            # never had a window (or it was closed on purpose)
        if not r["lost"]:
            r["lost"] = now
        if now - r["lost"] < REJOIN_GRACE:
            continue
        if any(now - _expected_close.get(h, 0) < 300 for h in r["hw"]):
            r.update(seen=False, lost=0.0)
            continue
        r["times"] = [t for t in r["times"] if now - t < 3600]
        if len(r["times"]) >= REJOIN_MAX:
            if not r["gaveup"]:
                r["gaveup"] = True
                print(f"{time.strftime('%H:%M:%S')}  rejoin: giving up on '{lbl}' after {REJOIN_MAX} tries this hour")
                notify("✖ Couldn't keep " + str(lbl) + " in game", f"Roblox closed {REJOIN_MAX} times in an hour. Look at the account.")
                _hook(f"Gave up on {lbl}", f"Roblox closed {REJOIN_MAX} times in an hour.", "bad", "fail")
            continue
        res = _launch_queue([a])
        ok, msg = (res[0][1], res[0][2]) if res else (False, "no result")
        r["times"].append(now)
        r.update(seen=False, lost=0.0, wait=now if ok else 0.0)
        print(f"{time.strftime('%H:%M:%S')}  rejoin: '{lbl}' window vanished - relaunching: {'ok' if ok else msg}")
        _session_add(lbl, "rejoin", "its window vanished" if ok else "relaunch failed: " + str(msg))
        _hook(f"Rejoining {lbl}", "Its Roblox window closed unexpectedly." if ok else str(msg), "warn", "rejoin")
        notify("↻ Rejoining " + str(lbl), "Its Roblox window closed unexpectedly" if ok else msg)
        if S_LOCK_NOT_HELD():
            print("  (multi-instance is off, so opening this may close another Roblox window)")


def S_LOCK_NOT_HELD():
    return STATE.get("lock") != "held" and len([a for a in _json_file("accounts.json", [])]) > 1


def _rejoin_loop():
    """AUTO_REJOIN: if a window that belonged to an account disappears and nobody closed it, open that account again."""
    time.sleep(25)                                              # let the first scans and the console's assignment settle
    while not QUIT.is_set():
        try:
            if AUTO_REJOIN and IS_WIN and not PAUSED.is_set() and not PANIC.is_set() and not LOCKED.is_set():
                _rejoin_pass()
        except Exception as e:
            print("rejoin error:", e)
        time.sleep(5)


# ====================================================================== launch queue: open accounts one after another, fast and safe
LAUNCH_SMART_GAP = 2.0                                          # seconds between accounts in Smart mode (after the window is ready)
_lq = {"running": False, "cancel": False, "items": [], "msg": "", "n": 0, "done_msg": "", "done_t": 0.0}
_lq_lock = threading.Lock()
_assign_now = []                                                # (hwnd, account label): the console assigns these windows at once


def _roblox_hwnds():
    return {w[4] for w in _scan_roblox(include_minimised=True)}


def _window_ready(hwnd):
    """Is that Roblox window past its splash screen? Needs a capture frame that isn't black or one flat colour."""
    try:
        _ensure_capture(hwnd)
        st = _caps.get(hwnd)
        arr = st.get("frame") if st else None
        if arr is None:
            return False
        sm = arr[::24, ::24, :3].astype("int16")
        return bool(sm.std() > 7 and sm.mean() > 6)
    except Exception:
        return False


def _window_loaded(hwnd):
    """Is that Roblox window up and the game running? Roblox's own log saying 'in game' is the quickest and surest sign; the picture check
    (not black, not one flat colour) is the fallback. Also true when the window has gone, so the queue never waits on a dead window."""
    import ctypes
    try:
        if not ctypes.windll.user32.IsWindow(hwnd):
            return True
        if (_client_for_hwnd(hwnd) or {}).get("state") == "in game":
            return True
    except Exception:
        pass
    return _window_ready(hwnd)


def _lq_wait(seconds, stop=None):
    t_end = time.time() + seconds
    while time.time() < t_end and not _lq["cancel"] and not QUIT.is_set():
        if stop and stop():
            return True
        time.sleep(0.4)
    return bool(stop and stop())


def _start_account(a, it, before_pids, before_hw):
    """Open the account's browser link and wait for Roblox to actually START (a new Roblox process or window). If nothing starts, clear what can
    be in the way (stale tray helpers) and open the link again, up to 3 tries: it is exactly what pressing Play a second time used to do by hand,
    and it worked every time. Roblox's own launcher still busy (updating, starting) counts as progress, not as failure. Returns (ok, message)."""
    lbl = a.get("label")
    for attempt in (1, 2, 3):
        it["state"] = "launching" if attempt == 1 else "retrying"
        if attempt > 1:
            _lq["msg"] = f"Opening {lbl} again (try {attempt} of 3)…"
        ok, msg = _launch_account(a, retry=attempt > 1)
        if not ok:
            return False, msg
        it["state"] = "waiting for window"
        limit = 30 if attempt < 3 else 120
        t0 = time.time()
        hard = t0 + 170
        while time.time() < t0 + limit and time.time() < hard and not _lq["cancel"] and not QUIT.is_set():
            if (set(_roblox_player_pids()) - before_pids) or (_roblox_hwnds() - before_hw):
                return True, "started"
            if _roblox_launcher_busy():
                t0 = max(t0, time.time() - (limit - 12))                    # Roblox is working on it: keep at least 12 s on the clock
            time.sleep(0.5)
        if _lq["cancel"] or QUIT.is_set():
            return False, "cancelled"
        cleared = _kill_stale_helpers(6)
        print(f"{time.strftime('%H:%M:%S')}  launch: nothing started for {lbl} after {int(time.time() - t0)} s (try {attempt} of 3)"
              + (f"; ended {cleared} stale helper(s)" if cleared else "") + ("; trying again" if attempt < 3 else "; giving up"))
    return False, "Roblox didn't start. If your browser asked to open Roblox, tick 'Always allow' and press Open, then try again"


def _launch_queue(accounts, close_first=False):
    """Open each account in turn. Smart mode (LAUNCH_DELAY = 0) starts the next one as soon as the previous window has
    opened and finished loading; otherwise it waits LAUNCH_DELAY seconds from each launch. Returns [(label, ok, message)]."""
    n = len(accounts)
    if LOCKED.is_set():
        return [(a.get("label"), False, "sign in again first") for a in accounts]
    if not _lq_lock.acquire(blocking=False):
        return [(a.get("label"), False, "another launch is already running") for a in accounts]
    results = []
    t_all = time.time()
    try:
        _lq.update(running=True, cancel=False, n=n, msg="", fail_msg="", items=[{"label": a.get("label"), "state": "queued"} for a in accounts])
        smart = float(LAUNCH_DELAY or 0) <= 0
        for i, a in enumerate(accounts):
            lbl, it = a.get("label"), _lq["items"][i]
            if _lq["cancel"] or QUIT.is_set():
                it["state"] = "cancelled"
                results.append((lbl, False, "cancelled"))
                continue
            if close_first:                                                  # rejoin: close its window(s), and wait for the Roblox process itself to go
                assign = _json_file("assign.json", {})
                mine = [int(h) for h, l in assign.items() if l == lbl and int(h) in _roblox_hwnds()]
                if mine:
                    pids = {_hwnd_pid(h) for h in mine}
                    _lq["msg"] = f"Closing {lbl}'s window…"
                    for h in mine:
                        _close_window(h)
                    _lq_wait(14, lambda: not (set(mine) & _roblox_hwnds()))
                    try:
                        import psutil
                        _lq_wait(10, lambda: not any(psutil.pid_exists(p) for p in pids if p))
                    except ImportError:
                        pass
                    _kill_stale_helpers(3)                                   # a leftover helper would swallow the new launch
            if MULTI_INSTANCE and STATE.get("lock") != "held":
                _lq["msg"] = "Waiting for the multi-instance lock…"
                _lq_wait(15, lambda: STATE.get("lock") == "held")
            if i > 0:                                                        # the last launch's Roblox launcher must be finished, or it swallows this one
                _lq["msg"] = "Waiting for Roblox to finish starting the last one…"
                _lq_wait(30, lambda: not _roblox_launcher_busy())
            before = _roblox_hwnds()
            before_pids = set(_roblox_player_pids())
            it["state"] = "launching"
            _lq["msg"] = f"Opening {lbl}  ({i + 1} of {n})…"
            t_launch = time.time()
            ok, msg = _start_account(a, it, before_pids, before)
            if not ok:
                it["state"] = "cancelled" if msg == "cancelled" else "failed"
                results.append((lbl, False, msg))
                continue
            it["state"] = "waiting for window"
            _lq["msg"] = f"{lbl}: Roblox is starting…"
            new = []

            def find_window():
                new.extend(sorted(_roblox_hwnds() - before))
                if not new:                                                  # hidden / parked windows aren't "visible": find them by their new process
                    for pid_, hws in _windows_of_roblox_processes().items():
                        if pid_ not in before_pids:
                            new.extend(hws)
                return bool(new)

            _lq_wait(150, find_window)
            if not new:
                it["state"] = "no window appeared"
                results.append((lbl, False, "no window appeared"))
                _pending_launch[:] = [p for p in _pending_launch if p[0] != lbl]
                continue
            hw = new[0]
            _assign_now.append((hw, lbl))
            for j, p in enumerate(_pending_launch):                           # assigned directly, so the "next window" guess isn't needed
                if p[0] == lbl:
                    _pending_launch.pop(j)
                    break
            it["state"] = "loading"
            _lq["msg"] = f"{lbl}: window is up, loading the game…"
            _lq_wait(60, lambda: _window_loaded(hw))
            it["state"] = "in game"
            results.append((lbl, True, "ok"))
            if i < n - 1:
                gap = LAUNCH_SMART_GAP if smart else max(0.0, float(LAUNCH_DELAY) - (time.time() - t_launch))
                _lq["msg"] = f"{lbl} is in. Next account in {int(gap)} s…" if gap > 1 else f"{lbl} is in."
                _lq_wait(gap)
        good = sum(1 for r in results if r[1])
        _lq["done_msg"] = f"Opened {good} of {n} account(s) in {int(time.time() - t_all)} s" + ("" if good == n else " - " + next((m for _, ok_, m in results if not ok_), "see the log"))
        _lq["done_t"] = time.time()
        print(f"{time.strftime('%H:%M:%S')}  launch: {_lq['done_msg']}")
        for lbl_, ok_, msg_ in results:
            if not ok_:
                print(f"    {lbl_}: {msg_}")
        _lq["fail_msg"] = next((m for _, ok_, m in results if not ok_ and m != "cancelled"), "")
        return results
    finally:
        try:
            if _rbx["changed"]:
                _rbx_restore()                                            # later manual launches start with your normal settings
        except Exception:
            pass
        _lq.update(running=False, msg="")
        _lq_lock.release()


def _launch_queue_async(accounts, close_first=False):
    threading.Thread(target=_launch_queue, args=(accounts, close_first), daemon=True).start()


# ====================================================================== backup and restore
BACKUP_DIR = os.path.join(DATA, "backups")
BACKUP_FILES = [".env", "secrets.env", "accounts.json", "scenes.json", "macros.json", "known_events.json", "history.jsonl"]
BACKUP_SECRET = {"secrets.env"}


def _backup_members(with_secrets=True):
    out = []
    for name in BACKUP_FILES:
        if name in BACKUP_SECRET and not with_secrets:
            continue
        if os.path.exists(os.path.join(DATA, name)):
            out.append(name)
    icons = os.path.join(DATA, "icons")
    if os.path.isdir(icons):
        out += ["icons/" + f for f in sorted(os.listdir(icons)) if f.lower().endswith(".png")]
    return out


def _backup_make(path, with_secrets=True):
    """Zip the settings, accounts, scenes, recordings and learned icons into one file. Returns the number of files."""
    import zipfile
    names = _backup_members(with_secrets)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.write(os.path.join(DATA, n), n)
        z.writestr("backup_info.json", json.dumps({"made": time.strftime("%Y-%m-%d %H:%M:%S"), "secrets": bool(with_secrets),
                                                     "files": len(names)}))
    os.replace(tmp, path)
    return len(names)


def _backup_restore(path):
    """Put a backup's files back (only known file names; the current ones are saved first). Returns (ok, message)."""
    import zipfile
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        return False, f"not a usable backup file: {e}"
    with z:
        allowed = []
        for n in z.namelist():
            if n in BACKUP_FILES or (n.startswith("icons/") and n.lower().endswith(".png") and n.count("/") == 1 and ".." not in n):
                allowed.append(n)
        if not allowed:
            return False, "that file has no Gnomewatch settings in it"
        os.makedirs(BACKUP_DIR, exist_ok=True)
        _backup_make(os.path.join(BACKUP_DIR, "before-restore-" + time.strftime("%Y%m%d-%H%M%S") + ".zip"), True)
        for n in allowed:
            dest = os.path.join(DATA, *n.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(z.read(n))
    return True, f"restored {len(allowed)} file(s)"


def _backup_auto():
    """Once a day keep a copy in backups/ (without secrets: this folder may be synced to the cloud). Keeps the last 7."""
    try:
        today = os.path.join(BACKUP_DIR, "auto-" + time.strftime("%Y%m%d") + ".zip")
        if not os.path.exists(today):
            n = _backup_make(today, with_secrets=False)
            print(f"auto-backup saved ({n} files): {os.path.basename(today)}")
        olds = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("auto-") and f.endswith(".zip"))
        for f in olds[:-7]:
            os.remove(os.path.join(BACKUP_DIR, f))
    except Exception as e:
        print("auto-backup:", e)


def _restart_self():
    """Start the sentinel again (used after a restore). Works under the supervisor scripts and on its own."""
    try:
        os.remove(RUN_FLAG)                                     # a deliberate restart is not a crash
    except OSError:
        pass
    if os.environ.get("SENTINEL_SUPERVISED") != "1":
        exe = sys.executable
        subprocess.Popen(f'cmd /c ping -n 4 127.0.0.1 >nul & start "" {_self_cmdline()}',
                         creationflags=0x08000000, cwd=HERE)
    os._exit(3)                                                 # non-zero: the supervisor starts it again



# ====================================================================== auto scenes by activity
AUTO_SCENES = os.environ.get("AUTO_SCENES", "0") == "1"
AUTO_SCENES_MIN = float(os.environ.get("AUTO_SCENES_MIN", "10"))
AUTO_SCENE_AWAY = os.environ.get("AUTO_SCENE_AWAY", "AFK")
AUTO_SCENE_BACK = os.environ.get("AUTO_SCENE_BACK", "Working")
_own_input = [0.0]            # when the sentinel itself last pressed a key / clicked (keep-alive, reconnect, auto-place)
_auto = {"away": False, "real": None}


def _mark_own():
    _own_input[0] = time.time()


def _real_idle_seconds():
    """Seconds since YOU last used the keyboard or mouse. The sentinel's own clicks and key presses are ignored,
    otherwise the keep-alive click every few minutes would make you look present forever."""
    now = time.time()
    last = now - _user_idle_seconds()
    if _auto["real"] is None or abs(last - _own_input[0]) > 4.0:
        _auto["real"] = last                       # genuine input (or nothing new since we last looked)
    return now - _auto["real"]


def _autoscene_loop():
    """AUTO_SCENES: after AUTO_SCENES_MIN minutes without you, switch to the away scene; when you come back, switch back."""
    time.sleep(30)
    while not QUIT.is_set():
        try:
            if AUTO_SCENES and IS_WIN and not PANIC.is_set() and not LOCKED.is_set():
                idle = _real_idle_seconds()
                if not _auto["away"] and idle >= max(1.0, AUTO_SCENES_MIN) * 60:
                    _auto["away"] = True
                    print(f"{time.strftime('%H:%M:%S')}  auto scene: away for {int(idle // 60)} min -> {AUTO_SCENE_AWAY}")
                    if AUTO_SCENE_AWAY:
                        PANEL_Q.put("scene:" + AUTO_SCENE_AWAY)
                elif _auto["away"] and idle < 5:
                    _auto["away"] = False
                    print(f"{time.strftime('%H:%M:%S')}  auto scene: you're back -> {AUTO_SCENE_BACK}")
                    if AUTO_SCENE_BACK:
                        PANEL_Q.put("scene:" + AUTO_SCENE_BACK)
            else:
                _auto["away"] = False
        except Exception as e:
            print("auto scenes error:", e)
        time.sleep(5)


# ====================================================================== auto-switch rules for the performance mode
RULE_FULLSCREEN = os.environ.get("RULE_FULLSCREEN", "").strip().lower()     # mode while some other app is fullscreen ("" = no change)
RULE_AWAY_MIN = float(os.environ.get("RULE_AWAY_MIN", "20"))
RULE_AWAY = os.environ.get("RULE_AWAY", "").strip().lower()                 # mode after RULE_AWAY_MIN minutes without you
RULE_BACK = os.environ.get("RULE_BACK", "").strip().lower()                 # mode when you come back ("" = the one from before)
WALL_SIZE = os.environ.get("WALL_SIZE", "m").strip().lower()                # Instances wall tile size: s | m | l
_rules = {"prev": None, "by": None, "fs_since": 0.0, "away": False}


def _fullscreen_other():
    """Is some other app (not Roblox, not us) covering a whole monitor right now?"""
    import ctypes
    from ctypes import wintypes
    try:
        u = ctypes.windll.user32
        fg = u.GetForegroundWindow()
        if not fg:
            return False
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(fg, ctypes.byref(pid))
        if pid.value == os.getpid():
            return False
        buf = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(fg, buf, 64)
        if buf.value in ("WINDOWSCLIENT", "Progman", "WorkerW", "Shell_TrayWnd", "TkTopLevel"):
            return False                                          # Roblox itself, the desktop, the taskbar, our own windows
        rc = wintypes.RECT()
        u.GetWindowRect(fg, ctypes.byref(rc))
        mon = u.MonitorFromWindow(fg, 2)

        class MI(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("mon", wintypes.RECT), ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

        mi = MI()
        mi.cb = ctypes.sizeof(MI)
        u.GetMonitorInfoW(mon, ctypes.byref(mi))
        return rc.left <= mi.mon.left and rc.top <= mi.mon.top and rc.right >= mi.mon.right and rc.bottom >= mi.mon.bottom
    except Exception:
        return False


def _rule_apply(target, why):
    """Switch to a mode because of a rule, remembering what to go back to."""
    if target not in MODE_PROFILES or target == UI_MODE:
        return False
    if _rules["prev"] is None:
        _rules["prev"] = UI_MODE
    _rules["by"] = target
    print(f"{time.strftime('%H:%M:%S')}  mode rule: {why} -> {target}")
    _set_mode(target)
    return True


def _rule_release(target=None):
    """The reason is over: go back to the mode from before (or the one named, if the rule says so)."""
    prev, _rules["prev"], _rules["by"] = _rules["prev"], None, None
    dest = target or prev
    if dest and dest in MODE_PROFILES and dest != UI_MODE:
        print(f"{time.strftime('%H:%M:%S')}  mode rule: back to {dest}")
        _set_mode(dest)


def _moderules_loop():
    """Rules from the Performance page: fullscreen game -> a light mode, away for a while -> eco, back -> normal."""
    time.sleep(40)
    while not QUIT.is_set():
        try:
            if IS_WIN and not PANIC.is_set():
                if _rules["by"] and UI_MODE != _rules["by"]:      # you switched the mode yourself: stop managing it
                    _rules.update(prev=None, by=None)
                now = time.time()
                if RULE_FULLSCREEN:
                    if _fullscreen_other():
                        _rules["fs_since"] = _rules["fs_since"] or now
                        if now - _rules["fs_since"] >= 10 and _rules["by"] != RULE_FULLSCREEN and not _rules["away"]:
                            _rule_apply(RULE_FULLSCREEN, "a fullscreen app is in front")
                    else:
                        if _rules["fs_since"] and _rules["by"] == RULE_FULLSCREEN:
                            _rule_release()
                        _rules["fs_since"] = 0.0
                if RULE_AWAY:
                    idle = _real_idle_seconds()
                    if not _rules["away"] and idle >= max(1.0, RULE_AWAY_MIN) * 60:
                        _rules["away"] = True
                        _rule_apply(RULE_AWAY, f"away for {int(idle // 60)} min")
                    elif _rules["away"] and idle < 5:
                        _rules["away"] = False
                        _rule_release(RULE_BACK or None)
                else:
                    _rules["away"] = False
        except Exception as e:
            print("mode rules error:", e)
        time.sleep(5)


# ====================================================================== license: who is signed in, and when the key runs out
import gw_license as _lic

LOCKED = threading.Event()               # set when the key expired / was revoked: everything pauses until a new key is entered
_lic_state = {"warned": False, "reason": ""}


def _license_left():
    """Seconds left on this key (None = never expires or not signed in)."""
    return _lic.seconds_left(GW_SESSION) if GW_SESSION else None


def _license_lock(reason, update=None):
    if not LOCKED.is_set():
        LOCKED.set()
        _lic_state["reason"] = reason
        _lic_state["update"] = update
        print(f"{time.strftime('%H:%M:%S')}  !! key: {reason} - Gnomewatch is paused until you sign in again")
        PANEL_Q.put("license:locked")


def _license_unlock(sess):
    """A valid key was entered on the lock screen."""
    if GW_SESSION is not None and sess:
        GW_SESSION.update(sess)
    _lic_state.update(warned=False, reason="")
    LOCKED.clear()
    print(f"{time.strftime('%H:%M:%S')}  key accepted - Gnomewatch is running again")


def _quit_for_update():
    """The installer is about to replace the program: stop cleanly (a deliberate stop, not a crash)."""
    try:
        os.remove(RUN_FLAG)
    except OSError:
        pass
    print(f"{time.strftime('%H:%M:%S')}  updating - closing so the new version can be installed")
    QUIT.set()


def _sign_out():
    """Forget the key on this PC and start again at the login page."""
    _lic.forget(HERE)
    _restart_self()


def _presence_context():
    """What the Discord status needs to know right now: the event on the board, how many accounts are in game, and whether you're away."""
    now = time.time()
    ev = None
    a, n = STATE["active"], STATE["next"]
    if a and now - a[2] < 90:
        ev = ("active", a[0], a[1])
    elif n and n[1] and n[1] > now and now - n[2] < 600:
        ev = ("next", n[0], n[1])
    accts = _json_file("accounts.json", [])
    assign = _json_file("assign.json", {})
    alive = {e[0] for e in list(_snapshot)}
    running = len({lbl for h, lbl in assign.items() if str(h).isdigit() and int(h) in alive})
    return {"event": ev, "running": running, "total": len(accts), "idle": _user_idle_seconds(), "now": now}


def _presence_loop():
    """PRESENCE: keep your Discord status in step with the board (see gw_presence.py). Quiet when Discord isn't open."""
    pr = None
    while not QUIT.is_set():
        try:
            if PRESENCE and IS_WIN and not LOCKED.is_set() and not PANIC.is_set():
                if pr is None:
                    import gw_presence
                    pr = gw_presence.Presenter(_presence_context, "user", dict(gw_presence.DEFAULTS))
                pr.step()
            elif pr is not None:
                pr.clear()
                pr = None
        except ConnectionError:
            if pr is not None:
                pr.d = None
            QUIT.wait(15)                                    # Discord isn't open (or just closed): look again in a bit
        except Exception as e:
            print("discord status error:", e)
            if pr is not None:
                pr.d = None
            QUIT.wait(30)
        QUIT.wait(4)
    if pr is not None:
        pr.clear()


def _license_loop():
    """Checks the key with the server every 5 minutes; warns 30 minutes before it ends; locks the app when it's over."""
    if not GW_SESSION:
        return
    last_check = time.time()
    while not QUIT.is_set():
        try:
            left = _license_left()
            if left is not None and left <= 0 and not LOCKED.is_set():
                _license_lock("your key has expired")
            if left is not None and 0 < left <= 1800 and not _lic_state["warned"]:
                _lic_state["warned"] = True
                notify("⏳ Your key ends soon", f"About {_lic.fmt_left(left)} left. Run /getkey in Discord for a new one.")
                PANEL_Q.put("license:warn")
            if time.time() - last_check >= float(os.environ.get("GW_HEARTBEAT", "300")) and not LOCKED.is_set():
                last_check = time.time()
                res = _lic.check(HERE, GW_SESSION["key"])
                if res.get("ok"):
                    GW_SESSION["update"] = res.get("update")             # a newer version exists (or None)
                    GW_SESSION["expires"] = res.get("expires")
                    if res.get("serverTime"):
                        GW_SESSION["skew"] = res["serverTime"] / 1000.0 - time.time()
                elif not res.get("network"):                   # the server answered and said no (revoked, expired, other PC)
                    _license_lock(res.get("error") or "the key was rejected", res.get("update"))
        except Exception as e:
            print("license check error:", e)
        time.sleep(20)


# ====================================================================== panic key
_panic_state = {"muted": False}


def _dash_windows():
    """The dashboard's app-style browser window(s). Matched by class and title, read without messaging the window."""
    import win32gui
    icons_ = tuple(v[2] for v in EVENT_STYLE.values())
    out = []

    def cb(h, _):
        try:
            if win32gui.GetClassName(h) == "Chrome_WidgetWin_1":
                t = _title_of(h)
                if t.startswith("Gnome Sentinel") or any(t.startswith(e) for e in icons_):
                    out.append(h)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return out


def _panic_toggle():
    """Panic key: hide every Roblox window, the console, the tray icon and the dashboard; mute alerts; pause clicks.
    Press it again to bring everything back."""
    import ctypes
    u = ctypes.windll.user32
    if not PANIC.is_set():
        PANIC.set()
        _panic_state["muted"] = MUTED.is_set()
        MUTED.set()
        _set_show(False)
        _tray_visible(False)
        for h in _dash_windows():
            u.ShowWindowAsync(h, 0)
        PANEL_Q.put("panic:on")
        print(f"{time.strftime('%H:%M:%S')}  PANIC: everything hidden")
    else:
        PANIC.clear()
        if not _panic_state["muted"]:
            MUTED.clear()
        _override.clear()
        _apply_visibility()
        _tray_visible(True)
        for h in _dash_windows():
            u.ShowWindowAsync(h, 5)
        PANEL_Q.put("panic:off")
        print(f"{time.strftime('%H:%M:%S')}  panic off: everything back")


# ====================================================================== corner icons (the mutation HUD, bottom right)
ICON_DIR = os.path.join(DATA, "icons")
HUD_MATCH = float(os.environ.get("HUD_MATCH", "0.5"))
_icons = {"loaded": 0.0, "t": {}}
_hud_streak = {"name": None, "n": 0, "t": 0.0}


def _icons_load():
    """name -> small picture of that mutation's corner icon, from icons/<name>.png."""
    if time.time() - _icons["loaded"] < 30:
        return _icons["t"]
    import cv2
    _icons["loaded"] = time.time()
    found = {}
    try:
        names = os.listdir(ICON_DIR)
    except OSError:
        names = []
    for f in names:
        if f.lower().endswith(".png"):
            im = cv2.imread(os.path.join(ICON_DIR, f), cv2.IMREAD_COLOR)
            if im is not None:
                if max(im.shape[:2]) > 140:
                    k_ = 140 / max(im.shape[:2])
                    im = cv2.resize(im, None, fx=k_, fy=k_, interpolation=cv2.INTER_AREA)
                found[f[:-4].lower()] = im
    _icons["t"] = found
    return found


def _hud_crop(img, x, y, w, h):
    """The icon sits right above its 'M:SS' timer; crop that area (x, y, w, h = the timer's normalised box)."""
    W, H = img.size
    cx, tw, th, top = (x + w / 2) * W, w * W, h * H, y * H
    box = (int(max(0, cx - tw)), int(max(0, top - th * 3.95)), int(min(W, cx + tw)), int(max(1, top - th * 0.1)))
    if box[2] - box[0] < 12 or box[3] - box[1] < 10:
        return None
    return img.crop(box)


def _small(crop, size=(40, 30)):
    import cv2
    import numpy as np
    return cv2.resize(cv2.cvtColor(np.asarray(crop.convert("RGB")), cv2.COLOR_RGB2BGR), size, interpolation=cv2.INTER_AREA)


def _hud_scan(img, boxes):
    """Every 'M:SS' timer in the bottom-right corner, with the mutation its icon matches (or None).
    The stored icon is scaled to the timer text's size and slid over a wider area above the timer, so a few pixels
    of jitter or a different window size don't matter."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return []
    out = []
    tpl = _icons_load()
    W, H = img.size
    for text, x, y, w, h in boxes:
        t = text.strip()
        if not re.fullmatch(r"\d{1,2}:\d{2}", t) or x < .68 or y < .5:
            continue
        left, crop = parse_timer(t), _hud_crop(img, x, y, w, h)
        if left is None or crop is None:
            continue
        tw, th, cx, top = w * W, h * H, (x + w / 2) * W, y * H
        tw_, th_ = max(14, int(tw * 2.0)), max(12, int(th * 3.9))
        box = (int(max(0, cx - tw * 1.55)), int(max(0, top - th * 5.4)), int(min(W, cx + tw * 1.55)), int(min(H, top + th * 0.5)))
        search = cv2.cvtColor(np.asarray(img.crop(box).convert("RGB")), cv2.COLOR_RGB2BGR)
        best = (None, -1.0)
        for name, tp in tpl.items():
            for k_ in (0.8, 0.9, 1.0, 1.12, 1.25):                # the text box is only accurate to a pixel or two
                tt = cv2.resize(tp, (max(8, int(tw_ * k_)), max(8, int(th_ * k_))), interpolation=cv2.INTER_AREA)
                if search.shape[0] < tt.shape[0] or search.shape[1] < tt.shape[1]:
                    continue
                sc = float(cv2.matchTemplate(search, tt, cv2.TM_CCOEFF_NORMED).max())
                if sc > best[1]:
                    best = (name, sc)
        out.append({"name": best[0] if best[1] >= HUD_MATCH else None, "score": best[1], "left": left, "crop": crop})
    return out


def _hud_learn(name, left, hud_hits):
    """The board says `name` is active and exactly one corner timer agrees with it: remember that icon."""
    if not HUD_LEARN or not hud_hits:
        return
    try:
        if name in _icons_load():
            return
    except ImportError:
        return
    cands = [h for h in hud_hits if left is None or abs(h["left"] - left) <= 6]
    if len(cands) != 1:
        return
    try:
        os.makedirs(ICON_DIR, exist_ok=True)
        cands[0]["crop"].save(os.path.join(ICON_DIR, f"{name}.png"))
        _icons["loaded"] = 0.0
        print(f"learned the corner icon for '{name}'")
    except OSError:
        pass


def _hud_pick(hud_hits):
    """The event named by the corner icons - only after two readings in a row agree."""
    best = None
    for h in hud_hits:
        if h["name"] and (best is None or h["score"] > best["score"]):
            best = h
    now = time.time()
    if not best:
        _hud_streak.update(name=None, n=0)
        return None
    if _hud_streak["name"] == best["name"] and now - _hud_streak["t"] < 20:
        _hud_streak["n"] += 1
    else:
        _hud_streak.update(name=best["name"], n=1)
    _hud_streak["t"] = now
    return (best["name"], best["left"]) if _hud_streak["n"] >= 2 else None


# ====================================================================== macros: record once, replay when a mutation starts
MACROS_FILE = os.path.join(DATA, "macros.json")
MACRO_RETRIES = max(1, int(os.environ.get("MACRO_RETRIES", "3")))          # tries when nothing could be sent (focus, covered window...)
MACRO_VERIFY = os.environ.get("MACRO_VERIFY", "1") == "1"                  # look at the screen afterwards to check something changed
MACRO_ALERT = os.environ.get("MACRO_ALERT", "1") == "1"                    # tell me when it fails for good
MACRO_DELAY = float(os.environ.get("MACRO_DELAY", "0"))                    # seconds to wait after an event starts before placing
MACRO_SKIP_LATE = os.environ.get("MACRO_SKIP_LATE", "1") == "1"            # don't start a placement when the event is nearly over
_rec = {"on": False}
_macro_last = {}
_MACRO_KEYS = list(range(0x30, 0x3A)) + list(range(0x41, 0x5B)) + [0x20, 0x0D, 0x09, 0x1B, 0x25, 0x26, 0x27, 0x28]   # 0-9 A-Z space enter tab esc arrows


_macros_cache = {"mtime": None, "data": {}}
_macros_lock = threading.RLock()
MACRO_MAX_STEPS, MACRO_MAX_SECONDS = 400, 90.0


def _clean_steps(steps):
    """Only keep steps that are safe to replay: known keys, sane times, clicks inside the window, in time order.
    Anything still pressed at the end gets a release, so a replay can never leave a key or button stuck."""
    out = []
    if not isinstance(steps, list):
        return out
    for st in steps:
        try:
            t = float(st["t"])
            if not (0 <= t <= MACRO_MAX_SECONDS):
                continue
            d = 1 if int(st["d"]) else 0
            if "k" in st:
                vk = int(st["k"])
                if vk in _MACRO_KEYS:
                    out.append({"t": round(t, 3), "k": vk, "d": d})
            elif st.get("m") in ("L", "R"):
                x, y = float(st["x"]), float(st["y"])
                if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
                    out.append({"t": round(t, 3), "m": st["m"], "d": d, "x": round(x, 4), "y": round(y, 4)})
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda r: r["t"])
    out = out[:MACRO_MAX_STEPS]
    held_k, held_m = {}, {}
    for r in out:
        if "k" in r:
            held_k[r["k"]] = r["d"]
        else:
            held_m[r["m"]] = (r["d"], r["x"], r["y"])
    end = out[-1]["t"] if out else 0.0
    for vk, d in held_k.items():
        if d:
            out.append({"t": end, "k": vk, "d": 0})
    for nm, (d, x, y) in held_m.items():
        if d:
            out.append({"t": end, "m": nm, "d": 0, "x": x, "y": y})
    return out


def _macros_load():
    """macros.json as a dict; tolerant of a missing, half-written or hand-edited file. Re-read only when it changed."""
    with _macros_lock:
        try:
            mt = os.path.getmtime(MACROS_FILE)
        except OSError:
            return {}
        if mt == _macros_cache["mtime"]:
            return json.loads(json.dumps(_macros_cache["data"]))
        try:
            raw = json.load(open(MACROS_FILE, encoding="utf-8"))
        except (OSError, ValueError):
            return json.loads(json.dumps(_macros_cache["data"]))      # keep the last good copy
        data = {}
        if isinstance(raw, dict):
            for ev, m in raw.items():
                if isinstance(m, dict):
                    data[str(ev)] = {"account": m.get("account") if isinstance(m.get("account"), str) else None,
                                     "enabled": bool(m.get("enabled")),
                                     "steps": _clean_steps(m.get("steps")), "end_steps": _clean_steps(m.get("end_steps"))}
        _macros_cache.update(mtime=mt, data=data)
        return json.loads(json.dumps(data))


def _phase_key(phase):
    return "end_steps" if phase == "end" else "steps"


def _macro_set(event, **kw):
    with _macros_lock:
        m = _macros_load()
        e = m.setdefault(event, {"account": None, "enabled": False, "steps": [], "end_steps": []})
        for k, v in kw.items():
            e[k] = _clean_steps(v) if k in ("steps", "end_steps") else v
        tmp = MACROS_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(m, f, indent=1)
            os.replace(tmp, MACROS_FILE)                  # never leaves a half-written file behind
            _macros_cache["mtime"] = None
        except OSError as ex:
            print("couldn't save macros:", ex)


def _macro_window(account):
    """The live Roblox window assigned to that account (assign.json), or None."""
    import ctypes
    if not account:
        return None
    try:
        assign = json.load(open(os.path.join(DATA, "assign.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return None
    alive = {e[0] for e in list(_snapshot)}
    u = ctypes.windll.user32
    for h, lbl in assign.items():
        try:
            if lbl == account and int(h) in alive and u.IsWindow(int(h)):
                return int(h)
        except (TypeError, ValueError):
            continue
    return None


def _client_rect(hwnd):
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    rc = wintypes.RECT()
    u.GetClientRect(hwnd, ctypes.byref(rc))
    pt = wintypes.POINT(0, 0)
    u.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y, max(1, rc.right), max(1, rc.bottom)


def _focus(hwnd, prev=None):
    """Get keyboard focus for a window even though Windows guards against focus stealing. Tries gentler ways first."""
    import ctypes
    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    prev = prev or u.GetForegroundWindow()
    my_tid = k.GetCurrentThreadId()
    fg_tid = u.GetWindowThreadProcessId(prev, None) if prev else 0
    for method in range(3):
        if u.GetForegroundWindow() == hwnd:
            return True
        if method == 0:                                          # share the front window's input queue for a moment
            if fg_tid and fg_tid != my_tid:
                u.AttachThreadInput(my_tid, fg_tid, True)
            u.AllowSetForegroundWindow(-1)
            u.BringWindowToTop(hwnd)
            u.SetForegroundWindow(hwnd)
            if fg_tid and fg_tid != my_tid:
                u.AttachThreadInput(my_tid, fg_tid, False)
        elif method == 1:
            u.SwitchToThisWindow(hwnd, True)
        else:                                                    # last resort: a tap of Alt lets Windows change the front window
            u.keybd_event(0x12, 0, 0, 0)
            u.keybd_event(0x12, 0, 2, 0)
            u.SetForegroundWindow(hwnd)
        time.sleep(0.25)
    return u.GetForegroundWindow() == hwnd


def _bring_forward(hwnd):
    """Put a (possibly hidden) Roblox window in front. Returns (previous front window, was it parked)."""
    import ctypes
    u = ctypes.windll.user32
    prev = u.GetForegroundWindow()
    was_parked = hwnd in _parked
    _hold.add(hwnd)
    if was_parked:
        _unpark(hwnd)
        time.sleep(0.8)
    st_ = {"iconic": False, "rect": None}
    _bf_state[hwnd] = st_
    if u.IsIconic(hwnd):                                # minimised: restore it without stealing focus first
        st_["iconic"] = True
        u.ShowWindowAsync(hwnd, 9)
        time.sleep(0.6)
    if not was_parked:                                  # off every monitor (or dragged half away): pull it on-screen
        from ctypes import wintypes
        rc = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(rc))
        if not u.MonitorFromPoint(wintypes.POINT((rc.left + rc.right) // 2, (rc.top + rc.bottom) // 2), 0):     # centre isn't on any monitor
            st_["rect"] = (rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)
            u.SetWindowPos(hwnd, 0, 60, 60, rc.right - rc.left, rc.bottom - rc.top, 0x0004 | 0x0010 | 0x4000)
            time.sleep(0.4)
    if prev != hwnd:
        _focus(hwnd, prev)
    u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)   # topmost while we work: above our own always-on-top console too
    u.SetForegroundWindow(hwnd)
    time.sleep(0.5)
    return prev, was_parked


_bf_state = {}


def _put_back(hwnd, prev, was_parked):
    import ctypes
    u = ctypes.windll.user32
    st_ = _bf_state.pop(hwnd, None) or {}
    u.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)   # not topmost any more
    if st_.get("rect"):                                 # it was somewhere off-screen: send it back
        x, y, w, h = st_["rect"]
        u.SetWindowPos(hwnd, 0, x, y, w, h, 0x0004 | 0x0010 | 0x4000)
    if st_.get("iconic"):
        u.ShowWindowAsync(hwnd, 6)
    if prev and prev != hwnd:
        u.SetForegroundWindow(prev)
    _hold.discard(hwnd)
    if was_parked:
        _apply_visibility()


def _macro_record(event, hwnd, done=None, max_seconds=90, phase="start"):
    """Record the keys and clicks made in that window until Ctrl+Shift+F8 is pressed (or max_seconds)."""
    if _rec["on"] or _play_lock.locked():
        if done:
            done(-1)                                        # busy: another recording or a replay is running
        return

    def run():
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        steps, down, mdown = [], set(), {"L": False, "R": False}
        _rec.update(on=True, event=event, phase=phase, n=0, t0=time.time())
        prev, was_parked = None, False
        ok = False
        try:
            prev, was_parked = _bring_forward(hwnd)
            ok = u.RegisterHotKey(None, 9, 0x4006, 0x77)               # Ctrl+Shift+F8 finishes the recording
            msg = wintypes.MSG()
            t0 = time.time()
            _rec["t0"] = t0
            print("macro: recording - press Ctrl+Shift+F8 when you're done")
            while _rec["on"] and time.time() - t0 < max_seconds and not QUIT.is_set() and not PANIC.is_set():
                if u.PeekMessageW(ctypes.byref(msg), None, 0x0312, 0x0312, 1) and msg.wParam == 9:
                    break
                if (u.GetAsyncKeyState(0x11) & 0x8000 and u.GetAsyncKeyState(0x10) & 0x8000
                        and u.GetAsyncKeyState(0x77) & 0x8000):        # same combo, polled: works even if the hotkey couldn't register
                    break
                if not u.IsWindow(hwnd):
                    print("macro: the window closed while recording")
                    break
                _rec["n"] = len(steps)
                fg = u.GetForegroundWindow()
                if fg == hwnd or u.GetAncestor(fg, 2) == hwnd:
                    t = round(time.time() - t0, 3)
                    for vk in _MACRO_KEYS:
                        st = bool(u.GetAsyncKeyState(vk) & 0x8000)
                        if st != (vk in down):
                            (down.add if st else down.discard)(vk)
                            steps.append({"t": t, "k": vk, "d": int(st)})
                    for nm, vk in (("L", 1), ("R", 2)):
                        st = bool(u.GetAsyncKeyState(vk) & 0x8000)
                        if st != mdown[nm]:
                            pt = wintypes.POINT()
                            u.GetCursorPos(ctypes.byref(pt))
                            x0, y0, w, h = _client_rect(hwnd)      # measured now, so it doesn't matter where the window sits
                            fx, fy = (pt.x - x0) / w, (pt.y - y0) / h
                            if st and not (0 <= fx <= 1 and 0 <= fy <= 1):
                                continue                           # a press outside the window isn't part of the macro
                            mdown[nm] = st
                            steps.append({"t": t, "m": nm, "d": int(st), "x": round(min(1, max(0, fx)), 4), "y": round(min(1, max(0, fy)), 4)})
                time.sleep(0.008)
            t_end = round(time.time() - t0, 3)
            for vk in list(down):                                       # anything still held is released at the end
                steps.append({"t": t_end, "k": vk, "d": 0})
            for nm, st in mdown.items():
                if st:
                    steps.append({"t": t_end, "m": nm, "d": 0, "x": steps[-1].get("x", 0.5) if steps and "x" in steps[-1] else 0.5,
                                  "y": steps[-1].get("y", 0.5) if steps and "y" in steps[-1] else 0.5})
        except Exception as e:
            print("macro: recording error:", e)
        finally:
            if ok:
                u.UnregisterHotKey(None, 9)
            _rec["on"] = False
            try:
                _put_back(hwnd, prev, was_parked)
            except Exception as e:
                print("macro: couldn't put the window back:", e)
            clean = _clean_steps(steps)
            if clean:
                _macro_set(event, **{_phase_key(phase): clean})
            print(f"macro: recorded {len(clean)} {phase} step(s) for '{event}'")
            if done:
                done(len(clean))

    threading.Thread(target=run, daemon=True).start()


_pressed = set()          # keys and buttons the replay itself is holding (so it only ever releases its own)


def _send_key(vk, down):
    import ctypes
    u = ctypes.windll.user32
    scan = u.MapVirtualKeyW(vk, 0)
    flags = 0x0008 | (0 if down else 0x0002) | (0x0001 if vk in (0x25, 0x26, 0x27, 0x28) else 0)   # scancode (+extended for arrows)
    u.keybd_event(vk, scan, flags, 0)
    (_pressed.add if down else _pressed.discard)(("k", vk))


_play_lock = threading.Lock()


def _macro_play(event, test=False, phase="start"):
    with _play_lock:                                    # one replay at a time
        return _macro_play_locked(event, test, phase)


MACRO_STATUS = {}          # (event, phase) -> {"t", "ok", "msg"} : what the console shows under each recording
_MACRO_STAMP = {}


def _macro_note(event, phase, ok, msg):
    MACRO_STATUS[(event, phase)] = {"t": time.time(), "ok": ok, "msg": msg}
    try:
        acct = (_macros_load().get(event) or {}).get("account")
        if ok or not msg.startswith("skipped"):
            _session_add(acct, "auto-place", f"{event} ({phase}): {'ok' if ok else 'failed - ' + msg}")
        if not ok and not msg.startswith("skipped"):
            _hook(f"Auto-place failed: {event.title()} ({phase})", msg, "bad", "fail")
    except Exception:
        pass
    print(f"{time.strftime('%H:%M:%S')}  macro '{event}' ({phase}): {'OK' if ok else 'FAILED'} - {msg}")


def _frame_cells(hwnd):
    """A coarse grey picture of the window's latest frame (for the did-it-change check), or None."""
    st = _caps.get(hwnd)
    arr = st.get("frame") if st else None
    if arr is None:
        return None, 0.0
    return arr[::16, ::16, :3].astype("int16").sum(axis=2) // 3, st.get("t", 0.0)


def _frame_changed(before, after):
    if before is None or after is None or before.shape != after.shape:
        return None
    return int((abs(after - before) > 28).sum())


def _macro_still_wanted(event, phase):
    """Is it still sensible to run this by the time we get a quiet moment? Returns a reason to skip, or None."""
    act = STATE.get("active")
    now = time.time()
    on = bool(act and act[0] == event and now - act[2] < 60)           # seen on the board / corner recently
    if phase == "start":
        if not on:
            return "the event is already over"
        if act[1] and act[1] - now < 20:
            return "the event is nearly over"
    elif act and act[0] == event and now - act[2] < 15:
        return "the event is back on"
    return None


def _macro_attempt(event, phase, steps, hwnd, test):
    """One try. Returns (ok, message, safe_to_retry). Nothing is sent unless the window is in front and uncovered."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.WindowFromPoint.argtypes = [wintypes.POINT]
    u.WindowFromPoint.restype = wintypes.HWND
    u.GetAncestor.restype = wintypes.HWND
    if not u.IsWindow(hwnd):
        return False, "the Roblox window closed", True
    _ensure_capture(hwnd)
    time.sleep(0.3)
    before, _t = _frame_cells(hwnd)
    old = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(old))
    prev, was_parked = None, False
    sent = False
    try:
        prev, was_parked = _bring_forward(hwnd)
        if u.GetForegroundWindow() != hwnd:
            time.sleep(0.5)
            u.SetForegroundWindow(hwnd)
            time.sleep(0.4)
        if u.GetForegroundWindow() != hwnd:
            return False, "couldn't bring the window to the front", True
        x0, y0, w, h = _client_rect(hwnd)
        if w < 200 or h < 150:
            return False, "the window is too small or minimised", True
        for fx_, fy_ in ((0.5, 0.5), (0.25, 0.25), (0.75, 0.75)):             # nothing may sit over the window before we start
            if u.GetAncestor(u.WindowFromPoint(wintypes.POINT(int(x0 + fx_ * w), int(y0 + fy_ * h))), 2) != hwnd:
                return False, "another window is covering Roblox", True
        t0 = time.time()
        for st in steps:
            if PANIC.is_set():
                return False, "stopped by the panic key", False
            if QUIT.is_set() or not u.IsWindow(hwnd):
                return False, "the window went away mid-way", False
            while time.time() - t0 < st["t"] and not (PANIC.is_set() or QUIT.is_set()):
                time.sleep(min(0.1, max(0.0, st["t"] - (time.time() - t0))))      # sleep in slices so the panic key stops it at once
            if PANIC.is_set():
                return False, "stopped by the panic key", False
            if u.GetForegroundWindow() != hwnd and u.GetAncestor(u.GetForegroundWindow(), 2) != hwnd:
                return False, "something took the focus away mid-way", not sent
            if "k" in st:
                _send_key(st["k"], bool(st["d"]))
                sent = True
            else:
                x0, y0, w, h = _client_rect(hwnd)
                px, py = int(x0 + st["x"] * w), int(y0 + st["y"] * h)
                u.SetCursorPos(px, py)
                u.mouse_event(0x0001, 1, 1, 0, 0)               # a real mouse-move so the game sees the pointer arrive
                u.mouse_event(0x0001, -1, -1, 0, 0)
                time.sleep(0.12)
                if st["d"] and u.GetAncestor(u.WindowFromPoint(wintypes.POINT(px, py)), 2) != hwnd:
                    return False, "another window is covering the click spot", not sent
                left = st["m"] == "L"
                u.mouse_event((0x0002 if left else 0x0008) if st["d"] else (0x0004 if left else 0x0010), 0, 0, 0, 0)
                (_pressed.add if st["d"] else _pressed.discard)(("m", st["m"]))
                sent = True
        time.sleep(1.2)
        if MACRO_VERIFY and not test:
            after, _t2 = _frame_cells(hwnd)
            n = _frame_changed(before, after)
            if n is not None and n < 4:
                return False, "nothing on screen changed afterwards", "once"      # worth one more go, but not more (it might have worked)
        return True, "done", False
    except Exception as e:
        return False, f"error: {e!r}", not sent
    finally:
        for kind, key in list(_pressed):                         # release only what this replay pressed
            try:
                if kind == "k":
                    _send_key(key, False)
                else:
                    u.mouse_event(0x0004 if key == "L" else 0x0010, 0, 0, 0, 0)
            except Exception:
                pass
        _pressed.clear()
        try:
            u.SetCursorPos(old.x, old.y)
            _mark_own()
            _put_back(hwnd, prev, was_parked)
        except Exception as e:
            print("macro: couldn't put things back:", e)


def _macro_play_locked(event, test, phase):
    import ctypes
    m = _macros_load().get(event) or {}
    steps = m.get(_phase_key(phase)) or []
    if not steps:
        _macro_note(event, phase, False, "nothing recorded yet")
        return False
    if not test:                                         # don't fight you: wait until you've stopped typing / moving
        waited = 0
        while _user_idle_seconds() < 3 and waited < 40 and not PANIC.is_set():
            time.sleep(1)
            waited += 1
        if phase == "start" and MACRO_DELAY > 0:                       # let the game settle after the event starts
            t_end = time.time() + MACRO_DELAY
            while time.time() < t_end and not (PANIC.is_set() or QUIT.is_set()):
                time.sleep(0.2)
        why = _macro_still_wanted(event, phase) if MACRO_SKIP_LATE else None
        if why:
            _macro_note(event, phase, False, f"skipped - {why}")
            return False
    last = (False, "never ran", False)
    once_used = False
    for attempt in range(1, (MACRO_RETRIES if not test else 1) + 1):
        if PANIC.is_set():
            _macro_note(event, phase, False, "stopped by the panic key")
            return False
        hwnd = _macro_window(m.get("account"))
        if hwnd is None:
            last = (False, f"no open window is assigned to '{m.get('account')}'", True)
        else:
            last = _macro_attempt(event, phase, steps, hwnd, test)
        STATE["macro"] = {"event": event, "t": time.time(), "phase": phase}
        if last[0]:
            _macro_note(event, phase, True, "done" if attempt == 1 else f"done (try {attempt})")
            return True
        print(f"macro '{event}' ({phase}): try {attempt} failed - {last[1]}")
        if not last[2] or attempt >= (MACRO_RETRIES if not test else 1):
            break
        if last[2] == "once":
            if once_used:
                break
            once_used = True
        if not test:
            why = _macro_still_wanted(event, phase) if MACRO_SKIP_LATE else None
            if why:
                last = (False, f"gave up - {why}", False)
                break
        time.sleep(4)
    _macro_note(event, phase, False, last[1])
    if not test and MACRO_ALERT and not PANIC.is_set():
        notify("✖ Auto-place failed", f"{event.title()} ({phase}): {last[1]}")
    return False


def _macro_trigger(name, phase="start"):
    """An event just started (or ended): replay that phase's steps if the macro is switched on."""
    try:
        m = _macros_load().get(name)
        if not m or not m.get("enabled") or not m.get(_phase_key(phase)) or PANIC.is_set() or LOCKED.is_set():
            return
        key = (name, phase)
        if time.time() - _macro_last.get(key, 0) < 180:
            return
        _macro_last[key] = time.time()

        def go():
            try:
                _macro_play(name, False, phase)
            except Exception as e:
                import traceback
                _macro_note(name, phase, False, f"crashed: {e!r}")
                print(traceback.format_exc())

        threading.Thread(target=go, daemon=True).start()
    except Exception as e:
        print("macro trigger error:", e)


_problems = {}                 # id -> {"text", "since"}: what the console banner and tray icon show
RUN_FLAG = os.path.join(DATA, "running.flag")


def _set_problem(pid, text):
    """Raise (text) or clear (None) one problem; each change alerts once."""
    cur = _problems.get(pid)
    if text and not cur:
        _problems[pid] = {"text": text, "since": time.time()}
        print(f"{time.strftime('%H:%M:%S')}  !! PROBLEM: {text}")
        if SELFCHECK_TOAST and not MUTED.is_set():
            _toast("⚠ Gnomewatch problem", text)
    elif text and cur:
        cur["text"] = text
    elif not text and cur:
        del _problems[pid]
        print(f"{time.strftime('%H:%M:%S')}  recovered: {cur['text']}")
        if SELFCHECK_TOAST and not MUTED.is_set() and pid != "restart":
            _toast("✔ Gnomewatch recovered", cur["text"])


def _selfcheck_loop():
    """Every 15s: is Roblox running, is the bot reachable, has the sign been seen lately?"""
    started = time.time()
    since = {"noroblox": None, "botdown": None}
    # a leftover run flag means the last run ended abnormally (crash, kill, stall-restart)
    if os.path.exists(RUN_FLAG):
        _set_problem("restart", "Gnomewatch restarted after an unexpected stop")
        threading.Timer(300, lambda: _set_problem("restart", None)).start()
    try:
        open(RUN_FLAG, "w").write(str(os.getpid()))
        import atexit
        atexit.register(lambda: os.path.exists(RUN_FLAG) and os.remove(RUN_FLAG))     # only a clean exit removes it
    except OSError:
        pass
    while not QUIT.is_set():
        try:
            now = time.time()
            if SELFCHECK and not PAUSED.is_set() and now - started > 60:
                if _snapshot:
                    since["noroblox"] = None
                    _set_problem("roblox", None)
                else:
                    since["noroblox"] = since["noroblox"] or now
                    if now - since["noroblox"] > 90:
                        _set_problem("roblox", "Roblox isn't running (no window found)")
                if STATE["worker"]["ok"] is False:
                    since["botdown"] = since["botdown"] or now
                    if now - since["botdown"] > 60:
                        _set_problem("bot", "Can't reach the bot: " + (STATE["worker"]["msg"] or "no reply"))
                else:
                    since["botdown"] = None
                    _set_problem("bot", None)
                bs = STATE["board_seen"]
                ref = bs or started
                if _snapshot and now - ref > 1200:
                    _set_problem("sign", f"The weather sign hasn't been seen for {int((now - ref) / 60)} min")
                else:
                    _set_problem("sign", None)
            elif not SELFCHECK:
                for k in [k for k in _problems if k != "restart"]:
                    _set_problem(k, None)
        except Exception as e:
            print("self-check error:", e)
        time.sleep(15)


_diag_seen = set()


def _diag_active(name):
    """An event is active but no time-left could be read: save the picture and the text the OCR saw, once per event."""
    key = (name, int(time.time() // 900))
    if key in _diag_seen or len(_diag_seen) > 12 or _last_frame[0] is None:
        return
    _diag_seen.add(key)
    try:
        d = os.path.join(DATA, "diag")
        os.makedirs(d, exist_ok=True)
        stamp = time.strftime("%H%M%S")
        _last_frame[0].save(os.path.join(d, f"active_{stamp}_{name.replace(' ', '_')}.png"))
        with open(os.path.join(d, f"active_{stamp}_{name.replace(' ', '_')}.txt"), "w", encoding="utf-8") as f:
            for t, x, y, w, h in _diag_boxes[:80]:
                f.write(f"{x:.2f},{y:.2f}  {w:.2f}x{h:.2f}  {t}\n")
        print(f"saved diagnostics for '{name}' (no time-left readable): diag/active_{stamp}_*")
    except OSError:
        pass


_diag_boxes = []


def _finish_close(hwnd, pid, wait_s):
    """After asking a window to close: give it a moment; if the Roblox process is still running, end it."""
    import ctypes
    import psutil
    end = time.time() + wait_s
    while time.time() < end and ctypes.windll.user32.IsWindow(hwnd):
        time.sleep(0.5)
    try:
        p = psutil.Process(pid)
        if p.name() == "RobloxPlayerBeta.exe":              # never end anything that isn't Roblox
            try:
                p.wait(timeout=3)
            except psutil.TimeoutExpired:
                p.terminate()
                print(f"closed Roblox (pid {pid}) - it didn't exit by itself, so it was ended")
    except psutil.NoSuchProcess:
        pass
    st_ = _caps.pop(hwnd, None)                           # tidy everything we kept for that window
    if st_ and st_.get("ctl"):
        try:
            st_["ctl"].stop()
        except Exception:
            pass
    for d_ in (_override, _moved, _last_ocr):
        d_.pop(hwnd, None)
    for s_ in (_parked, _iconed, _renamed):
        s_.discard(hwnd)
    if _board[0] == hwnd:
        _board[0] = None
    _apply_visibility()


def _close_window(hwnd):
    """Close one Roblox window (a normal close request first, then end the process if it hangs on)."""
    import ctypes
    import win32gui
    import win32process
    if win32gui.GetClassName(hwnd) != "WINDOWSCLIENT":
        return {"ok": False, "error": "not a Roblox window"}
    pid = win32process.GetWindowThreadProcessId(hwnd)[1]
    _expected_close[hwnd] = time.time()                    # closed on purpose: auto-rejoin must not bring it back
    ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    print(f"closing Roblox window {hwnd} (pid {pid})")
    threading.Thread(target=_finish_close, args=(hwnd, pid, 8), daemon=True).start()
    return {"ok": True, "message": "Closing Roblox…"}


def _close_all():
    n = 0
    for entry in list(_snapshot):
        if _close_window(entry[0]).get("ok"):
            n += 1
    return {"ok": True, "message": f"Closing {n} Roblox window(s)…"}


def _a_close(p):
    return _close_window(_known_hwnd(p))


_ACTIONS = {"close": _a_close, "close_all": lambda p: _close_all(),
    "show_all": lambda p: (_set_show(True), {"ok": True, "message": "Showing all windows"})[1],
    "hide_all": lambda p: (_set_show(False), {"ok": True, "message": "Hiding all windows"})[1],
    "show": _a_show, "hide": _a_hide, "restore": _a_restore,
    "launch": lambda p: _launch_instance(),
    "toggle_pause": _a_pause, "toggle_mute": _a_mute,
}


def _lan_ip():
    import socket
    s_ = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s_.connect(("10.255.255.255", 1))          # no packet is sent; just picks the Wi-Fi interface
        return s_.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s_.close()


def _start_dashboard():
    key, share = None, None
    if DASHBOARD_LAN:
        import secrets as _sec
        key = os.environ.get("DASHBOARD_KEY", "").strip()
        if not key:                                    # make one once and keep it, so shared links keep working
            key = _sec.token_urlsafe(12)
            try:
                with open(_env_path, "a", encoding="utf-8") as f:
                    f.write(f"\nDASHBOARD_KEY={key}\n")
            except OSError:
                pass
        share = f"http://{_lan_ip()}:{DASHBOARD_PORT}/?key={key}"
    try:
        import dashboard
        dashboard.start(DASHBOARD_PORT, _dash_state, _dash_frame, _ACTIONS, lan=DASHBOARD_LAN, key=key,
                        lan_control=DASHBOARD_LAN_CONTROL, share_url=share)
    except Exception as e:
        print("dashboard couldn't start (is the port busy?):", e)
        return
    print("Dashboard:", DASH_URL)
    if share:
        print("Wi-Fi link (view-only" + (", with controls" if DASHBOARD_LAN_CONTROL else "") + "):", share)
    if DASHBOARD_OPEN:
        _open_dashboard()


_tray_icon = []


_TRAY_COLOURS = {"ok": "#3ddc97", "active": "#4da3ff", "paused": "#f5b942", "problem": "#ff5d5d"}


def _tray_image(state):
    """The gnome icon with a small coloured status dot in the corner."""
    from PIL import ImageDraw
    base = Image.open(_gnome_ico()).convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
    d = ImageDraw.Draw(base)
    r = 12 if state == "active" else 10
    d.ellipse((64 - 2 * r - 1, 64 - 2 * r - 1, 63, 63), fill="#10151c")                       # dark ring
    d.ellipse((64 - 2 * r + 2, 64 - 2 * r + 2, 61, 61), fill=_TRAY_COLOURS.get(state, "#3ddc97"))
    return base


def _tray_state():
    """(state, tooltip) - green = watching, gold = an event is on, amber = paused/muted, red = a problem."""
    kind, name, left = _event_summary()
    tip = "Gnomewatch"
    if _problems:
        first = next(iter(_problems.values()))["text"]
        return "problem", f"Gnomewatch - problem: {first}"[:120]
    if not _snapshot or STATE["worker"]["ok"] is False or STATE["lock"] == "failed":
        why = "no Roblox window found" if not _snapshot else "can't reach the bot"
        return "problem", f"Gnomewatch - problem: {why}"
    if PAUSED.is_set():
        return "paused", "Gnomewatch - paused"
    if kind == "active":
        return "active", f"Gnomewatch - {name.title()} active" + (f", {_fmt_left(left)} left" if left is not None else "")
    if kind == "next":
        tip = f"Gnomewatch - next: {name.title()}" + (f" in {_fmt_left(left)}" if left is not None else "")
    else:
        tip = "Gnomewatch - watching"
    return ("paused" if MUTED.is_set() else "ok"), tip + (" (alerts muted)" if MUTED.is_set() else "")


def _tray_updater():
    shown = [None]
    while not QUIT.is_set():
        try:
            if _tray_icon:
                state, tip = _tray_state()
                icon = _tray_icon[0]
                if state != shown[0]:
                    icon.icon = _tray_image(state)
                    shown[0] = state
                if icon.title != tip:
                    icon.title = tip[:120]                  # Windows truncates long tooltips
        except Exception as e:
            print("tray update error:", e)
        time.sleep(2)


def _start_tray():
    try:
        import pystray
    except ImportError:
        print("tray unavailable (pip install pystray)")
        return

    def flip(ev, label):
        def cb(icon, item):
            ev.clear() if ev.is_set() else ev.set()
            print(f"{label}: {'on' if ev.is_set() else 'off'}")
        return cb

    def quit_(icon, item):
        QUIT.set()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Pause", flip(PAUSED, "paused"), checked=lambda i: PAUSED.is_set()),
        pystray.MenuItem("Mute alerts", flip(MUTED, "muted"), checked=lambda i: MUTED.is_set()),
        pystray.MenuItem("Show Roblox", lambda icon, item: _toggle_show(), checked=lambda i: not _parked),
        pystray.MenuItem("Open dashboard", lambda icon, item: _open_dashboard()),
        pystray.MenuItem("New Roblox instance", lambda icon, item: _launch_instance()),
        pystray.MenuItem("Panic - hide everything", lambda icon, item: threading.Thread(target=_panic_toggle, daemon=True).start()),
        pystray.MenuItem("Command palette", lambda icon, item: PANEL_Q.put("palette")),
        pystray.MenuItem("Show/hide panel", lambda icon, item: PANEL_Q.put("toggle")),
        pystray.MenuItem("Open log", lambda icon, item: os.startfile(LOG_PATH)),
        pystray.MenuItem("Quit", quit_),
    )
    icon = pystray.Icon("gnomewatch", _tray_image("ok"), "Gnomewatch", menu)
    _tray_icon.append(icon)
    icon.run_detached()
    threading.Thread(target=_tray_updater, daemon=True).start()


def _toast(title, message):
    """A Windows pop-up notification. Never blocks or raises."""
    import subprocess
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
        "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(1);"
        "$n=$t.GetElementsByTagName('text');"
        "$n.Item(0).AppendChild($t.CreateTextNode($env:SN_TITLE)) | Out-Null;"
        "$n.Item(1).AppendChild($t.CreateTextNode($env:SN_MSG)) | Out-Null;"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe')"
        ".Show([Windows.UI.Notifications.ToastNotification]::new($t))"
    )
    try:
        subprocess.Popen(["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                         env={**os.environ, "SN_TITLE": title, "SN_MSG": message},
                         creationflags=0x08000000)   # CREATE_NO_WINDOW
    except Exception:
        pass


def notify(title, message, event=None):
    """Sound + Windows toast on this PC (NOTIFY=1). Never blocks or raises."""
    if MUTED.is_set() or not NOTIFY or not IS_WIN:
        return
    if event and event.lower() in ALERT_OFF:
        return
    import subprocess
    if NOTIFY_SOUND:
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass
    if not NOTIFY_TOAST:
        return
    _toast(title, message)


HISTORY_FILE = os.path.join(DATA, "history.jsonl")


def _history_add(rec):
    """Append one line per event that started, so the console can show a timeline and stats."""
    try:
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def _history_load(days=7, limit=3000):
    cut = time.time() - days * 86400
    out = []
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
    except OSError:
        return out
    for line in lines:
        try:
            r = json.loads(line)
            if r.get("t", 0) >= cut:
                out.append(r)
        except ValueError:
            pass
    return out


def _ensure_capture(hwnd):
    """Start capturing a window (without waiting) so the console can show a live thumbnail of it."""
    st = _caps.get(hwnd)
    if st is None or st.get("closed") or _capture_stale(st):
        try:
            if st is not None and _capture_stale(st):
                _restart_capture(hwnd, st)
            else:
                _wgc_start(hwnd)
        except Exception:
            pass


def _tray_visible(on):
    """Show or hide the tray icon (Stealth scene)."""
    for icon in _tray_icon:
        try:
            icon.visible = bool(on)
        except Exception:
            pass


# panel glyph (a plain symbol Tk can draw), accent colour, emoji (toasts / browser)
EVENT_STYLE = {
    "aurora":          ("✧", "#5cffc4", "🌌"),
    "solar flare":     ("☀", "#ffb03c", "☀️"),
    "candy land":      ("✿", "#ff7ac8", "🍬"),
    "lightning storm": ("⚡", "#8fb8ff", "⚡"),
    "toxic":           ("☣", "#7dff4d", "☣️"),
    "cursed":          ("☠", "#b06bff", "💀"),
    "blizzard":        ("❄", "#9fd8ff", "❄️"),
}


def _event_style(name):
    return EVENT_STYLE.get((name or "").lower(), ("✦", "#8a94a3", "✨"))


def _event_summary():
    """(kind, name, seconds_left) for what the sign last showed; kind is 'active', 'next' or 'none'."""
    now = time.time()
    for kind in ("active", "next"):
        v = STATE.get(kind)
        if v:
            left = None if v[1] is None else v[1] - now
            if left is not None and left < -10:
                continue                                     # that countdown ran out a while ago
            return kind, v[0], (None if left is None else max(0.0, left))
    return "none", "", None


def _fmt_left(sec):
    if sec is None:
        return "--:--"
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


STATE = {"started": time.time(), "active": None, "next": None, "last_read": 0.0, "board_seen": 0.0,
         "worker": {"ok": None, "msg": "", "last": 0.0}, "lock": "off"}
_last_frame = [None]


def post(body):
    if not _reporting():
        STATE["worker"] = {"ok": None, "msg": "no bot set up", "last": 0.0}
        return {"ok": True, "skipped": True, "note": "no bot set up"}
    if not _direct_bot():                                  # through the Gnomewatch server
        if "upcoming" in body:                             # only event STARTS are announced
            return {"ok": True, "skipped": True, "note": "only event starts are announced"}
        res = _lic.report(HERE, GW_SESSION["key"], body)
        if res.get("skipped"):
            STATE["worker"] = {"ok": None, "msg": "the server isn't connected to the bot yet", "last": time.time()}
        else:
            STATE["worker"] = {"ok": bool(res.get("ok")), "msg": str(res.get("error") or "")[:80], "last": time.time()}
        _relay_failed[0] = not res.get("ok")
        return res
    try:
        res = _post_raw(body)
    except Exception as e:
        STATE["worker"] = {"ok": False, "msg": str(e)[:80], "last": time.time()}
        raise
    err = str(res.get("error", ""))
    bad = err.startswith("HTTP") or err.lower() in ("unauthorized", "forbidden")
    STATE["worker"] = {"ok": not bad, "msg": err if bad else "", "last": time.time()}
    return res


_relay_failed = [False]


def _post_raw(body):
    body = {**body, "name": NAME}
    req = urllib.request.Request(
        f"{WORKER_URL}/ingest",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Ingest-Secret": SECRET, "User-Agent": "gnome-weather-sentinel/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}"}


def report(kind, name, seconds, res):
    stamp = time.strftime("%H:%M:%S")
    if res.get("ok"):
        what = res.get("note") or ("already reported by another Gnomewatch" if res.get("duplicate") else None) or res.get("outcome") or (f"starts in {seconds}s" if res.get("upcoming") else "ok")
        print(f"{stamp}  {kind} {name:<18} {seconds!s:>5}s  → {what}")
    elif res.get("error") == "unknown event":
        print(f"{stamp}  {kind} {name:<18} → the bot doesn't know this name yet."
              f"  In Discord run:  /aliasevent alias:{name} event:<the mutation it gives>")
    else:
        print(f"{stamp}  {kind} {name:<18} → {res.get('error')}")


class _Sane:
    """Reject a countdown reading that doesn't fit the previous one (it should fall ~1s per second).
    After two rejections in a row the new value is accepted, so a genuine new event still gets through."""
    def __init__(self):
        self.last, self.bad = None, 0

    def reset(self):
        self.last, self.bad = None, 0

    def ok(self, value, now):
        if self.last is not None:
            expected = self.last[0] - (now - self.last[1])
            diff = value - expected
            # a new event makes the countdown jump UP (believe it after 2 reads); a sudden drop is nearly
            # always a misread like "9m 1s" read as "1s" (believe it only if it persists for 5 reads)
            if abs(diff) > 12 and self.bad < (2 if diff > 0 else 5):
                self.bad += 1
                return False
        self.last, self.bad = (value, now), 0
        return True


_sane_next, _sane_active = _Sane(), _Sane()
_last_ocr = {}


def _poll_delay():
    """Normal pace while the board is in view; slower when it hasn't been seen for a while."""
    if not IDLE_BACKOFF:
        return POLL_S
    return POLL_S if time.time() - STATE["board_seen"] < 90 else max(POLL_S, 4.0)

KNOWN_FILE = os.path.join(DATA, "known_events.json")
_SEED_EVENTS = ["aurora", "solar flare", "candy land", "lightning storm", "toxic", "cursed"]
try:
    KNOWN = set(json.load(open(KNOWN_FILE, encoding="utf-8")))
except (OSError, ValueError):
    KNOWN = set(_SEED_EVENTS)
_hits = {}


def _save_known():
    try:
        json.dump(sorted(KNOWN), open(KNOWN_FILE, "w", encoding="utf-8"), indent=1)
    except OSError:
        pass


def _canon(name):
    """Map a garbled sign reading ('candy lan', 'ightning storm', 'storm') onto a name we know."""
    import difflib
    if name in KNOWN:
        return name
    m = difflib.get_close_matches(name, KNOWN, n=1, cutoff=0.65)
    if m:
        return m[0]
    if len(name) >= 4:                                  # a fragment of a known name ('lightning', 'storm')
        for k in KNOWN:
            if name in k.split() or name in k:
                return k
    return name


def _trusted(name, now):
    """Known names are trusted at once; an unknown one must be read the same way 3 times within 30s
    (garbled reads are different every time, a real new event isn't)."""
    if name in KNOWN:
        return True
    hits = [t for t in _hits.get(name, []) if now - t < 30] + [now]
    _hits[name] = hits
    if len(_hits) > 200:
        _hits.clear()
    return len(hits) >= 3


def _learn(name, res):
    """Remember a name once the bot has accepted it."""
    if name not in KNOWN and (res.get("ok") or res.get("error") == "duplicate"):
        KNOWN.add(name)
        _save_known()
        print(f"learned new event name: {name}")
_clean_at = [0.0]
_clean_warned = [False]


def _windows_of_roblox_processes():
    """{pid: [hwnd, ...]} for every Roblox game window, visible or not (a hidden or parked window still belongs to its process)."""
    import win32gui
    import win32process
    out = {}

    def cb(h, _):
        try:
            if win32gui.GetClassName(h) == "WINDOWSCLIENT":
                out.setdefault(win32process.GetWindowThreadProcessId(h)[1], []).append(h)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return out


def _stale_roblox_helpers(min_age):
    """Window-less Roblox '--launch-to-tray' helper processes older than min_age seconds. Never one that has a game window."""
    import psutil
    with_window = set(_windows_of_roblox_processes())
    out = []
    for p in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            if p.info["name"] != "RobloxPlayerBeta.exe" or p.info["pid"] in with_window:
                continue
            if "--launch-to-tray" not in " ".join(p.info["cmdline"] or []):
                continue
            if time.time() - p.info["create_time"] < min_age:
                continue
            out.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def _kill_stale_helpers(min_age=6):
    """End the stale helpers. They can swallow the next launch (nothing opens until they're gone), so a launch that went nowhere clears them
    before it tries again. Returns how many were ended."""
    import psutil
    n = 0
    for p in _stale_roblox_helpers(min_age):
        try:
            p.terminate()
            n += 1
            print(f"{time.strftime('%H:%M:%S')}  ended a stale Roblox tray helper (pid {p.pid}) that was in the way of a launch")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return n


def _roblox_launcher_busy():
    """Is Roblox's own launcher (it updates / starts the game) still working on a launch? A second launch during that gets swallowed."""
    import psutil
    names = {"robloxplayerlauncher.exe", "robloxplayerinstaller.exe"}
    try:
        return any((p.info["name"] or "").lower() in names for p in psutil.process_iter(["name"]))
    except psutil.Error:
        return False


def _clean_roblox_helpers():
    """Close Roblox's window-less '--launch-to-tray' launcher processes. Never touches a Roblox that
    has a game window, and gives every process 30s to open one first."""
    if not CLEAN_HELPERS or time.time() - _clean_at[0] < 30:
        return
    _clean_at[0] = time.time()
    try:
        import psutil
    except ImportError:
        if not _clean_warned[0]:
            _clean_warned[0] = True
            print("clean-up unavailable (pip install psutil)")
        return
    for p in _stale_roblox_helpers(30):
        try:
            p.terminate()
            print(f"{time.strftime('%H:%M:%S')}  cleaned up a window-less Roblox tray helper (pid {p.pid})")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


_single = []


def _other_copies():
    """Other running Gnomewatch programs (not this one, not its helpers): [psutil.Process]."""
    import psutil
    out = []
    me = os.getpid()
    frozen = getattr(sys, "frozen", False)
    mine = os.path.basename(sys.executable).lower()
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if p.info["pid"] == me:
                continue
            cmd = " ".join(p.info["cmdline"] or [])
            if "--gw-keeper" in cmd or "lockkeeper" in cmd or "--gw-login" in cmd or "gw_boot.py" in cmd:
                continue                                      # helpers: the lock keeper must survive, login windows go with their owner
            name = (p.info["name"] or "").lower()
            if frozen and name == mine:
                out.append(p)
            elif not frozen and name in ("python.exe", "pythonw.exe") and "sentinel.py" in cmd:
                out.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def _close_other_copies():
    """Close the other Gnomewatch(es) and whatever would start them again, so this one can take over."""
    import psutil
    try:
        os.remove(RUN_FLAG)                                   # a deliberate close is not a crash
    except OSError:
        pass
    ancestors = set()
    try:
        ancestors = {a.pid for a in psutil.Process().parents()}
    except psutil.Error:
        pass
    for p in psutil.process_iter(["pid", "name", "cmdline"]):        # restarters (start_hidden.vbs / Gnomewatch.vbs), but never ours
        try:
            if (p.info["name"] or "").lower() == "wscript.exe" and p.info["pid"] not in ancestors:
                cmd = " ".join(p.info["cmdline"] or []).lower()
                if "start_hidden.vbs" in cmd or "gnomewatch.vbs" in cmd:
                    p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    for p in _other_copies():
        try:
            kids = [c for c in p.children(recursive=True) if "--gw-keeper" not in " ".join(c.cmdline() or []) and "lockkeeper" not in " ".join(c.cmdline() or [])]
            for c in kids:
                c.kill()
            p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def _single_instance():
    """Only one sentinel per user session. A second copy offers to close the first (which may be hidden or stuck) and take over."""
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = wintypes.HANDLE
    k.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    name = "Local\\GnomeSentinelSingleton" + os.environ.get("GW_INSTANCE", "")          # GW_INSTANCE: test copies only
    h = k.CreateMutexW(None, False, name)
    if h and ctypes.get_last_error() == 183:          # ERROR_ALREADY_EXISTS
        k.CloseHandle(h)
        print("Another Gnomewatch is already running.")
        yes = False
        if not os.environ.get("GW_INSTANCE"):          # (test copies never close anything)
            try:
                yes = ctypes.windll.user32.MessageBoxW(
                    None, "Gnomewatch is already running (it may be hidden, or still shutting down).\n\n"
                    "Close it and start this one instead?", "Gnomewatch", 0x4 | 0x20 | 0x1000) == 6        # Yes/No, question, topmost
            except Exception:
                pass
        if not yes:
            return False
        _close_other_copies()
        for _ in range(40):                            # give the old one up to ~8 s to let go of the lock
            h = k.CreateMutexW(None, False, name)
            if h and ctypes.get_last_error() != 183:
                break
            if h:
                k.CloseHandle(h)
            time.sleep(0.2)
        else:
            print("The old Gnomewatch wouldn't close - this copy is exiting.")
            return False
    _single.append(h)
    return True


_beat = [time.time()]
WATCHDOG_S = float(os.environ.get("WATCHDOG_SECONDS", "150"))


def _watchdog():
    """If the main loop stops ticking, write every thread's stack to the log, then restart the sentinel."""
    import faulthandler
    import subprocess
    dumped = False
    while True:
        time.sleep(10)
        stalled = time.time() - _beat[0]
        if stalled > WATCHDOG_S / 2 and not dumped:
            dumped = True
            print(f"{time.strftime('%H:%M:%S')}  !! main loop stalled for {int(stalled)}s — stacks follow")
            faulthandler.dump_traceback(file=sys.stdout.log, all_threads=True)
        if stalled > WATCHDOG_S:
            print(f"{time.strftime('%H:%M:%S')}  !! still stalled — restarting the sentinel")
            try:
                if os.environ.get("SENTINEL_SUPERVISED") != "1":     # a launcher script restarts us itself when it's in charge
                    exe = sys.executable
                    subprocess.Popen(                            # a plain string: cmd does its own quote handling
                        f'cmd /c ping -n 4 127.0.0.1 >nul & start "" {_self_cmdline()}',
                        creationflags=0x08000000, cwd=HERE)      # CREATE_NO_WINDOW; starts after we've exited
            finally:
                os._exit(3)
        if stalled < 5:
            dumped = False


def main():
    if IS_WIN and not _single_instance():
        return
    if IS_WIN:
        threading.Thread(target=_watchdog, daemon=True).start()
        threading.Thread(target=_keepalive_loop, daemon=True).start()
        threading.Thread(target=_rejoin_loop, daemon=True).start()
        threading.Thread(target=_autoscene_loop, daemon=True).start()
        threading.Thread(target=_clients_loop, daemon=True).start()
        threading.Thread(target=_license_loop, daemon=True).start()
        threading.Thread(target=_presence_loop, daemon=True).start()
        threading.Thread(target=_moderules_loop, daemon=True).start()
        threading.Thread(target=_hook_loop, daemon=True).start()
        threading.Thread(target=_backup_auto, daemon=True).start()
        threading.Thread(target=_reconnect_loop, daemon=True).start()
        threading.Thread(target=_selfcheck_loop, daemon=True).start()
        if LOW_PRIORITY:
            try:
                import psutil
                psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            except Exception:
                pass
    print(f"Gnomewatch '{NAME}' on {'macOS' if IS_MAC else 'Windows'} → {WORKER_URL or 'no bot set up'}  (Ctrl+C to stop)")
    print("Watching for a weather billboard… (quiet until one appears; VERBOSE=1 to see OCR)")
    last_key, last_post = None, 0.0
    last_next, last_next_post = None, 0.0
    missing_since = None
    board_hwnd = None                      # the Roblox window currently showing the weather board
    pending_start = None                   # (name, epoch when it starts) from the last countdown read
    last_start_fired = 0.0
    last_active_seen, last_start_notify = 0.0, 0.0
    open_event = None
    if IS_WIN:
        threading.Thread(target=_hotkey_thread, daemon=True).start()
        if TRAY:
            _start_tray()
        threading.Thread(target=_hold_singleton, daemon=True).start()      # checks MULTI_INSTANCE live
        if DASHBOARD:
            _start_dashboard()
        _apply_priority(MODE_PROFILES[UI_MODE]["priority"])
        if UI_MODE == "stealth":
            threading.Timer(4.0, lambda: _tray_visible(False)).start()      # the tray icon is created a moment after start
        if PANEL:
            threading.Thread(target=_panel_thread, daemon=True).start()
    while True:
        try:
            _beat[0] = time.time()
            if QUIT.is_set():
                print("bye")
                return
            if PAUSED.is_set() or LOCKED.is_set():
                time.sleep(1)
                continue
            if IS_WIN:
                _cosmetics()
                _clean_roblox_helpers()
                wins = roblox_windows_win()
            else:
                w0 = roblox_window()
                wins = [w0] if w0 else []
            if not wins:
                if missing_since is None:
                    print("Roblox window not found — waiting… (is Roblox open and not minimised?)")
                    missing_since = time.time()
                time.sleep(5)
                continue
            missing_since = None
            # Look at the window that had the board last time first; only if it's gone from there
            # do we check the other Roblox windows.
            boxes, hit = [], None
            hud_hits, hit_img = [], None
            for w in sorted(wins, key=lambda w: (w[4] if IS_WIN else 0) != board_hwnd):
                if IS_WIN and board_hwnd is not None and w[4] != board_hwnd and time.time() - _last_ocr.get(w[4], 0) < 10:
                    continue                       # windows that don't have the board only need a look every 10s
                _last_ocr[w[4]] = time.time()
                img = capture(w)
                if img is None:
                    continue
                b = ocr(img)
                if IS_WIN:
                    try:
                        hud_hits += _hud_scan(img, b)
                    except Exception as e:
                        print("corner-icon scan error:", e)
                if not boxes:
                    boxes = b
                if find_weather(b) or find_upcoming(b):
                    boxes, hit, hit_img = b, (w[4] if IS_WIN else 0), img
                    if IS_WIN:
                        _last_frame[0] = img
                        STATE["sign"] = {"boxes": list(_sign_boxes), "t": time.time()}
                    break
            if IS_WIN and hit is not None and hit != board_hwnd:
                board_hwnd = hit                   # this is the window with the board now
                _board[0] = hit
                _iconed_at[0] = 0.0                # retitle straight away (board name vs the others)
            if IS_WIN:
                _cosmetics()
                _apply_visibility()                # board window parked, the rest left free (plus panel overrides)
            now = time.time()
            STATE["last_read"] = now
            # The last "Next Event in" countdown we read tells us when the event begins, so we can
            # alert at that moment even if the board is out of view.
            if pending_start and now >= pending_start[1]:
                if now - last_start_fired > 90:                 # never twice for the same event
                    notify(f"{_event_style(pending_start[0])[2]} Event starting: {pending_start[0]}", "the weather event is starting now", event=pending_start[0])
                    print(f"{time.strftime('%H:%M:%S')}  START   {pending_start[0]}")
                    last_start_fired = now
                    _sane_next.reset()                          # the next countdown starts fresh
                pending_start, last_start_notify = None, now
            active = find_weather(boxes)
            if active and active[1] is None and hit_img is not None and _sign_boxes:       # the name was read but not the countdown: look at it again
                try:
                    t_ = _reread_timer(hit_img, _sign_boxes[0])
                except Exception as e:
                    t_ = None
                    print("timer re-read error:", e)
                if t_ is not None:
                    active = (active[0], t_)
            if active:                                   # fix garbled names; ignore names we can't trust yet
                cname = _canon(active[0])
                active = (cname, active[1]) if _trusted(cname, now) else None
            src = "board"
            if not active and IS_WIN:                        # board not visible: the icons in the bottom-right corner
                hud_active = _hud_pick(hud_hits)
                if hud_active:
                    active, src = hud_active, "icon"
            elif active and IS_WIN:
                try:
                    _hud_learn(active[0], active[1], hud_hits)
                except Exception as e:
                    print("corner-icon learn error:", e)
            STATE["source"] = src
            if active:
                name, remaining = active
                _sane_next.reset()
                bad_timer = remaining is not None and not _sane_active.ok(remaining, now)
                if bad_timer:
                    remaining = None                     # a jump like that is a misread; ignore this timer
                # a new start = a different event, or the board was inactive for a while
                is_new = name != last_key or now - last_active_seen > 45
                if open_event and open_event != name:
                    _macro_trigger(open_event, "end")        # it flipped straight to another event
                open_event = name
                last_active_seen = now
                prev_ = STATE["active"]
                target_ = now + remaining if remaining else (prev_[1] if prev_ and prev_[0] == name and prev_[1] and prev_[1] > now else None)
                STATE["active"] = (name, target_, now)
                if target_ is None:
                    _diag_boxes[:] = boxes
                    _diag_active(name)
                STATE["next"], STATE["board_seen"] = None, now
                pending_start = None
                if is_new:
                    _macro_trigger(name)
                    _history_add({"t": round(now), "name": name, "left": None if remaining is None else int(remaining)})
                if is_new and now - last_start_notify > 60:      # skip if we already alerted at the countdown
                    notify(f"{_event_style(name)[2]} Event active: {name}", f"{remaining}s left" if remaining is not None else "just started", event=name)
                    last_start_notify = now
                relay_only = _relay_ready() and not _direct_bot()           # relayed: an event START is announced once, so no refresh posts (unless it failed)
                if is_new or (not relay_only and now - last_post >= REPOST_S and not bad_timer) or (relay_only and _relay_failed[0] and now - last_post >= 15):
                    body = {"eventKey": name}
                    if remaining is not None:
                        body["remainingSeconds"] = remaining
                    res = post(body)
                    report("ACTIVE ", name, remaining, res)
                    _learn(name, res)
                    last_key, last_post = name, now
            else:
                if open_event and now - last_active_seen >= 20:      # gone for 20s: the event is over
                    _macro_trigger(open_event, "end")
                    open_event = None
                upcoming = find_upcoming(boxes)
                if upcoming and upcoming[1] is None and hit_img is not None and _sign_boxes:
                    try:
                        t_ = _reread_timer(hit_img, _sign_boxes[0])
                    except Exception as e:
                        t_ = None
                        print("timer re-read error:", e)
                    if t_ is not None:
                        upcoming = (upcoming[0], t_)
                if upcoming:
                    cname = _canon(upcoming[0])
                    upcoming = (cname, upcoming[1]) if _trusted(cname, now) else None
                if upcoming:
                    name, in_s = upcoming
                    _sane_active.reset()
                    if in_s and not _sane_next.ok(in_s, now):
                        in_s = None                      # misread countdown; wait for the next clean read
                    STATE["next"] = (name, now + in_s if in_s else None, now)
                    STATE["active"], STATE["board_seen"] = None, now
                    # The sign shows the name a beat before the timer renders; skip 0s reads.
                    if in_s:
                        # Same start time (±20s) = same event, even if OCR garbled the name this read.
                        if pending_start and abs((now + in_s) - pending_start[1]) <= 20:
                            name = pending_start[0]
                        pending_start = (name, now + in_s)
                    if in_s and (name != last_next or now - last_next_post >= REPOST_S):
                        res = post({"upcoming": {"name": name, "inSeconds": in_s}})
                        report("NEXT   ", name, in_s, res)
                        _learn(name, res)
                        if name != last_next and NOTIFY_NEXT:
                            notify(f"{_event_style(name)[2]} Next event: {name}", f"starts in {in_s}s", event=name)
                        last_next, last_next_post = name, now
                elif VERBOSE and boxes:
                    print("no billboard; saw:", " | ".join(b[0] for b in boxes[:8]))
        except KeyboardInterrupt:
            print("\nbye")
            return
        except Exception as e:  # keep running through transient errors
            print("error:", e)
            time.sleep(POLL_S)
        time.sleep(_poll_delay())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        for _i in _tray_icon:
            try:
                _i.stop()
            except Exception:
                pass
        # A leftover thread must never keep a quit Gnomewatch alive (it would still hold the one-copy lock): finish up and leave.
        import atexit
        try:
            atexit._run_exitfuncs()
        except Exception:
            pass
        try:
            sys.stdout.flush()
        except Exception:
            pass
        os._exit(0)
