"""Discord Rich Presence for Gnomewatch.

It talks to the Discord app on THIS PC (a local named pipe), so it needs no password or token, and it only lasts while it runs.
Discord must be open, and Settings > Activity Privacy > "Share my detected activity" must be on.

What your profile shows depends on what is happening (smart switching):
  * an event is on        ->  Watching Blizzard        a live "02:03 left" countdown      "2 of 4 accounts in game"
  * the next event is near->  Next: Aurora             "starts in 01:12"
  * accounts are running  ->  Watching 2 Roblox accounts
  * you are in an editor  ->  Coding Gnomewatch  (developer mode only: the name of the front program is only looked at here, never sent)
  * you are away          ->  nothing (the status is cleared)

Two ways to run it:
  Gnomewatch itself (Settings > "Show Gnomewatch on my Discord profile", off by default) - the same engine, fed from the running app.
  The developer tool:   pythonw gw_presence.py   (see start_presence.vbs)  /  python gw_presence.py --stop  /  --once  (a check)

presence.json (next to this file) holds the lines, buttons, which programs count as "coding" and how long before "away".
Event pictures: run make_discord_art.py, upload the PNGs in the Developer Portal (Rich Presence > Art Assets) - they are picked up by themselves.
"""
import ctypes
import json
import msvcrt
import os
import re
import struct
import sys
import time
import urllib.request
import uuid
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_ID = "1555468397213519892"                                  # the Gnomewatch application (its name and icon are what Discord shows)
CONFIG = os.path.join(HERE, "presence.json")
PIDFILE = os.path.join(HERE, "presence.pid")
DASHBOARD = "http://127.0.0.1:8765/api/state"                      # the running Gnomewatch's own local page: where the developer tool learns what is happening
DEFAULTS = {
    "details": "Coding Gnomewatch",
    "states": ["Building a weather watcher for Roll A Gnome", "Teaching OCR to read one more countdown", "Making Roblox multi-instance behave",
               "Shipping {version}", "Reading the sky so you don't have to"],
    "buttons": [{"label": "Get Gnomewatch", "url": "https://github.com/projectexph/gnomewatch"},
                {"label": "Join the Discord", "url": "https://discord.gg/gpjTJej39"}],
    "rotate_seconds": 60,
    "idle_minutes": 10,
    "coding_apps": ["Code.exe", "Cursor.exe", "claude.exe", "WindowsTerminal.exe", "powershell.exe", "cmd.exe", "pycharm64.exe", "idea64.exe",
                    "devenv.exe", "notepad++.exe", "sublime_text.exe", "conhost.exe"],
}
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.PeekNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
_k32.PeekNamedPipe.restype = wintypes.BOOL


def settings():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.load(open(CONFIG, encoding="utf-8")))
    except (OSError, ValueError):
        try:
            json.dump(DEFAULTS, open(CONFIG, "w", encoding="utf-8"), indent=2)          # write the defaults once so they are easy to edit
        except OSError:
            pass
    return cfg


def version():
    try:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        from gw_version import VERSION
        return "v" + VERSION
    except Exception:
        return ""


# ================================================================================================ talking to Discord
class Discord:
    """A minimal Discord IPC client: handshake, SET_ACTIVITY, and answering Discord's pings. One thread only: on Windows a read that is waiting
    blocks every write on the same pipe handle, so data is only read when PeekNamedPipe says it is already there."""
    def __init__(self):
        self.pipe, self.alive, self.replies = None, False, {}

    def connect(self):
        last = None
        for n in range(10):
            try:
                self.pipe = open(chr(92) * 2 + "." + chr(92) + "pipe" + chr(92) + f"discord-ipc-{n}", "r+b", buffering=0)
                break
            except OSError as e:
                last = e
        else:
            raise ConnectionError(f"Discord isn't open ({last})")
        self._send(0, {"v": 1, "client_id": CLIENT_ID})
        deadline = time.time() + 8
        while time.time() < deadline:
            if self._available():
                op, data = self._recv()
                if data.get("evt") == "READY":
                    self.alive = True
                    return
                raise ConnectionError("Discord didn't accept the connection")
            time.sleep(0.05)
        raise ConnectionError("Discord didn't answer")

    def _available(self):
        n = wintypes.DWORD(0)
        if not _k32.PeekNamedPipe(msvcrt.get_osfhandle(self.pipe.fileno()), None, 0, None, ctypes.byref(n), None):
            raise ConnectionError("Discord closed the connection")
        return n.value

    def _send(self, op, payload):
        raw = json.dumps(payload).encode()
        self.pipe.write(struct.pack("<II", op, len(raw)) + raw)

    def _recv(self):
        head = self.pipe.read(8)
        if len(head) < 8:
            raise ConnectionError("Discord closed the connection")
        op, n = struct.unpack("<II", head)
        body = b""
        while len(body) < n:
            chunk = self.pipe.read(n - len(body))
            if not chunk:
                raise ConnectionError("Discord closed the connection")
            body += chunk
        return op, (json.loads(body.decode()) if body else {})

    def pump(self):
        """Handle whatever Discord has sent (answer pings, keep replies). Raises ConnectionError if the pipe is gone."""
        try:
            while self._available() >= 8:
                op, data = self._recv()
                if op == 3:                                       # PING -> PONG
                    self._send(4, data)
                elif op == 2:                                     # CLOSE
                    raise ConnectionError("Discord closed the connection")
                elif data.get("nonce"):
                    self.replies[data["nonce"]] = data
        except (OSError, ValueError) as e:
            self.alive = False
            raise ConnectionError(str(e))

    def set_activity(self, activity):
        nonce = str(uuid.uuid4())
        self._send(1, {"cmd": "SET_ACTIVITY", "args": {"pid": os.getpid(), "activity": activity}, "nonce": nonce})
        for _ in range(60):
            self.pump()
            if nonce in self.replies:
                return self.replies.pop(nonce)
            time.sleep(0.1)
        return {}

    def close(self):
        self.alive = False
        try:
            self.pipe.close()
        except Exception:
            pass


# ================================================================================================ what to show (pure: easy to test)
def asset_key(name):
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def pretty(name):
    return " ".join(w.capitalize() for w in str(name).split())


def fmt_left(sec):
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def activity_for(ctx, cfg, assets, start, tick, mode="dev"):
    """The Rich Presence card for what is happening, or None to clear it.
    ctx: {"event": (kind 'active'|'next', name, end_epoch or None) or None, "running": int, "total": int, "coding": bool, "idle": seconds}
    assets: the picture names Discord actually has for the app (only those are used).  mode: 'dev' (can say Coding) or 'user'."""
    now = ctx.get("now") or time.time()
    if ctx.get("idle", 0) > float(cfg.get("idle_minutes", 10)) * 60:
        return None
    ver = version()
    ev, running, total = ctx.get("event"), int(ctx.get("running") or 0), int(ctx.get("total") or 0)
    a = {}
    if mode == "dev" and ctx.get("coding"):
        states = cfg.get("states") or DEFAULTS["states"]
        a = {"details": str(cfg.get("details", "Coding Gnomewatch")), "state": str(states[tick % len(states)]).replace("{version}", ver or "the next update"),
             "timestamps": {"start": int(start)}}
        if "gnome" in assets:
            a["assets"] = {"large_image": "gnome", "large_text": f"Gnomewatch {ver}".strip()}
    elif ev:
        kind, name, end = ev
        title = pretty(name)
        if kind == "active":
            a["details"] = f"Watching {title}"
            a["state"] = f"{running} of {total} accounts in game" if total and running else (f"{running} Roblox window{'s' if running != 1 else ''} open" if running else "Roll A Gnome weather")
        else:
            a["details"] = f"Next: {title}"
            a["state"] = "starting soon" if (end and end - now < 60) else "the countdown is on"
        if end and end > now:
            a["timestamps"] = {"end": int(end)}                       # Discord shows its own live "02:03 left"
        else:
            a["timestamps"] = {"start": int(start)}
        key = asset_key(name)
        art = {}
        if key in assets:
            art = {"large_image": key, "large_text": title}
            if "gnome" in assets:
                art.update(small_image="gnome", small_text=f"Gnomewatch {ver}".strip())
        elif "gnome" in assets:
            art = {"large_image": "gnome", "large_text": f"Gnomewatch {ver}".strip()}
        if art:
            a["assets"] = art
    elif running:
        a = {"details": f"Watching {running} Roblox account{'s' if running != 1 else ''}", "state": f"Gnomewatch {ver}".strip(), "timestamps": {"start": int(start)}}
        if "gnome" in assets:
            a["assets"] = {"large_image": "gnome", "large_text": f"Gnomewatch {ver}".strip()}
    elif mode == "dev":                                               # nothing else going on: still the developer's "coding" card
        states = cfg.get("states") or DEFAULTS["states"]
        a = {"details": str(cfg.get("details", "Coding Gnomewatch")), "state": str(states[tick % len(states)]).replace("{version}", ver or "the next update"),
             "timestamps": {"start": int(start)}}
        if "gnome" in assets:
            a["assets"] = {"large_image": "gnome", "large_text": f"Gnomewatch {ver}".strip()}
    else:
        return None
    if total and running and ev and ev[0] == "active":
        a["party"] = {"id": "gnomewatch", "size": [min(running, total), total]}
    btns = [b for b in (cfg.get("buttons") or []) if b.get("label") and str(b.get("url", "")).startswith("https://")][:2]
    if btns:
        a["buttons"] = [{"label": str(b["label"])[:32], "url": b["url"]} for b in btns]
    for k in ("details", "state"):                                    # Discord wants 2 to 128 characters
        a[k] = (a[k] + "  ")[:128] if len(a[k]) < 2 else a[k][:128]
    return a


def signature(a):
    """What makes a card different enough to be worth sending (a small drift in the countdown isn't)."""
    if a is None:
        return None
    s = json.loads(json.dumps(a))
    ts = s.get("timestamps", {})
    ts.pop("end", None)                                               # the countdown's end is compared separately (see end_drift)
    s["timestamps"] = ts
    s.get("assets", {}).pop("large_text", None)
    return json.dumps(s, sort_keys=True)


def end_of(a):
    return (a or {}).get("timestamps", {}).get("end")


def end_drift(a, b):
    """Seconds the countdown's end moved between two cards (0 if either has no countdown). Small corrections aren't worth resending."""
    x, y = end_of(a), end_of(b)
    return abs(x - y) if x is not None and y is not None else 0


# ================================================================================================ where the facts come from
def fetch_assets():
    """The picture names the Gnomewatch application has on Discord (public). Only these are used, so a missing picture never shows as a broken one."""
    try:
        with urllib.request.urlopen(f"https://discord.com/api/v10/oauth2/applications/{CLIENT_ID}/assets", timeout=6) as r:
            return {str(x.get("name")) for x in json.loads(r.read().decode())}
    except Exception:
        return set()


def idle_seconds():
    class LII(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
    info = LII()
    info.cbSize = ctypes.sizeof(info)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0


def front_program():
    """The file name of the program in front (e.g. 'Code.exe'). Only used on this PC to decide the status; it is never sent anywhere."""
    try:
        import psutil
        h = ctypes.windll.user32.GetForegroundWindow()
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        return psutil.Process(pid.value).name()
    except Exception:
        return ""


def dev_context(cfg):
    """For the developer tool: ask the running Gnomewatch (its local dashboard) what is happening, and see what is in front."""
    ctx = {"event": None, "running": 0, "total": 0, "idle": idle_seconds(), "now": time.time(),
           "coding": front_program().lower() in {a.lower() for a in cfg.get("coding_apps", [])}}
    try:
        with urllib.request.urlopen(DASHBOARD, timeout=1.5) as r:
            d = json.loads(r.read().decode())
        b = d.get("board") or {}
        if b.get("kind") in ("active", "next") and b.get("name"):
            ctx["event"] = (b["kind"], b["name"], b.get("target"))
        ctx["running"] = len(d.get("instances") or [])
    except Exception:
        pass
    return ctx


# ================================================================================================ the loop (used by the developer tool and by Gnomewatch)
class Presenter:
    """Keeps the Discord status in step with get_context(). step() is called every few seconds; it only talks to Discord when the card changes
    (and never more than once per 15 s)."""
    def __init__(self, get_context, mode="dev", cfg=None, connect=Discord):
        self.get_context, self.mode, self.connect = get_context, mode, connect
        self.cfg = cfg or settings()
        self.d, self.start, self.tick = None, time.time(), 0
        self.sig, self.last_set, self.last_cfg, self.assets, self.assets_t, self.rot = "never", 0.0, 0.0, set(), 0.0, 0.0
        self.last_card = None

    def step(self, now=None):
        now = now or time.time()
        if self.d is None or not self.d.alive:
            self.d = self.connect()
            self.d.connect()
            self.sig = "never"
        self.d.pump()
        if now - self.assets_t > 600:
            self.assets, self.assets_t = fetch_assets(), now
        if self.mode == "dev" and now - self.last_cfg > 60:
            self.cfg, self.last_cfg = settings(), now
        if now - self.rot >= max(15, int(self.cfg.get("rotate_seconds", 60))):
            self.tick, self.rot = self.tick + 1, now                  # the changing line under "Coding Gnomewatch"
        ctx = self.get_context()
        card = activity_for(ctx, self.cfg, self.assets, self.start, self.tick, self.mode)
        sig = signature(card)
        changed = sig != self.sig or end_drift(card, self.last_card) > 6          # a new card, or the countdown moved by more than a few seconds
        if changed and now - self.last_set >= 15:
            res = self.d.set_activity(card)
            self.sig, self.last_set, self.last_card = sig, now, card
            return res
        return None

    def clear(self):
        try:
            if self.d is not None and self.d.alive:
                self.d.set_activity(None)
                self.d.close()
        except Exception:
            pass


def stop():
    try:
        pid = int(open(PIDFILE).read().strip())
        import psutil
        p = psutil.Process(pid)
        if "python" in (p.name() or "").lower():
            p.terminate()
            print("stopped; your Discord status is cleared")
        os.remove(PIDFILE)
    except Exception:
        print("it wasn't running")


def run(once=False):
    if not once:
        try:
            open(PIDFILE, "w").write(str(os.getpid()))
        except OSError:
            pass
    cfg = settings()
    pr = Presenter(lambda: dev_context(cfg), "dev", cfg)
    while True:
        try:
            res = pr.step()
            if once:
                ok = bool(res) and res.get("evt") != "ERROR"
                print("status set" if ok else f"Discord said no: {res.get('data') if res else 'no answer'}")
                if ok:
                    print("card:", json.dumps(pr.last_card)[:300])
                time.sleep(2 if ok else 0)
                pr.clear()
                return 0 if ok else 1
        except ConnectionError as e:
            if once:
                print(e)
                return 1
            pr.d = None
            time.sleep(10)
            continue
        except Exception as e:
            if once:
                print("error:", e)
                return 1
            pr.d = None
            time.sleep(10)
            continue
        time.sleep(4)


if __name__ == "__main__":
    if "--stop" in sys.argv:
        stop()
    else:
        sys.exit(run(once="--once" in sys.argv) or 0)
