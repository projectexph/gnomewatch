"""Runs the WHOLE app in a sandbox copy against the local key server:
   remembered key -> splash -> welcome -> app starts in the user's own folder -> key revoked -> lock screen.
Uses safe settings (no hotkeys, tray, dashboard, hiding or closing anything) and a separate single-instance lock,
so it can run next to your real Gnomewatch.        python test_app.py [outdir]"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.gettempdir()
PORT = 8794
ADMIN = "GW-TEST-ADMIN-KEY"
tmp = tempfile.mkdtemp(prefix="gw_app_")
sys.path.insert(0, APP)
import gw_license as L
from PIL import ImageGrab

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def get(path):
    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=5).read())


def windows_of(pid, hidden=False):
    out = []
    u = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(h, l):
        p = wt.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and (hidden or u.IsWindowVisible(h)):
            b = ctypes.create_unicode_buffer(80)
            u.GetWindowTextW(h, b, 80)
            r = wt.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            if r.right - r.left > 200 or hidden:
                out.append((b.value, (r.left, r.top, r.right, r.bottom), bool(u.IsWindowVisible(h))) if hidden else (b.value, (r.left, r.top, r.right, r.bottom)))
        return True

    u.EnumWindows(cb, 0)
    return out


srv = subprocess.Popen([sys.executable, os.path.join(HERE, "dev_server.py"), "--port", str(PORT), "--admin-key", ADMIN,
                        "--file", os.path.join(tmp, "store.json")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
app_proc = None
try:
    for _ in range(40):
        try:
            get("/")
            break
        except Exception:
            time.sleep(0.25)
    key = get("/dev/getkey?id=4242&name=Kyle")["key"]
    app = os.path.join(tmp, "app")
    shutil.copytree(APP, app, ignore=shutil.ignore_patterns("profiles", "backups", "__pycache__", "*.log", "gnomewatch-keys", ".env", "secrets.env",
                                                            "session.json", "diag", "*.zip", "lockkeeper.*", "accounts.json", "assign.json", "macros.json",
                                                            "history.jsonl", "sessions.jsonl", "known_events.json", "scenes.json", "mode_prev.json", "running.flag"))
    json.dump({"api": f"http://127.0.0.1:{PORT}", "invite": "https://discord.gg/example"}, open(os.path.join(app, "gw_config.json"), "w"))
    open(os.path.join(app, "defaults.env"), "w").write("NOTIFY=0\nTRAY=0\nDASHBOARD=0\nCLEAN_HELPERS=0\nHOTKEY=\nPANEL_HOTKEY=\nPALETTE_HOTKEY=\nPANIC_HOTKEY=\n"
                                                      "GRID_HOTKEY=\nHIDE_WINDOW=0\nHIDE_ALL=0\nKEEPALIVE=0\nAUTO_RECONNECT=0\nAUTO_REJOIN=0\nMEM_TRIM=0\n"
                                                      "MULTI_INSTANCE=0\nPANEL_MINI=0\nANIMATIONS=off\nUI_MODE=fps\n")
    L.save_key(app, key)
    env = dict(os.environ, GW_INSTANCE="_test", GW_HEARTBEAT="8", SENTINEL_NAME="gwtest", PYTHONIOENCODING="utf-8")
    for k in ("WORKER_URL", "INGEST_SECRET"):
        env.pop(k, None)
    exe = sys.executable.replace("python.exe", "pythonw.exe")
    app_proc = subprocess.Popen([exe, os.path.join(app, "sentinel.py")], cwd=app, env=env)
    pid = app_proc.pid
    time.sleep(2.5)
    import psutil
    kids = [c.pid for c in psutil.Process(pid).children(recursive=True)]
    w = [x for p_ in kids for x in windows_of(p_)]
    check("the login splash appears first, in its own process (before the app)", any(t == "Gnomewatch" and (r[2] - r[0]) < 600 for t, r in w), w)
    ImageGrab.grab(bbox=w[0][1]).save(os.path.join(OUT, "app_1_splash.png")) if w else None
    time.sleep(16)
    w = windows_of(pid)
    prof = os.path.join(app, "profiles", "4242")
    ok_console = any((r[2] - r[0]) > 1000 for t, r in w)
    check("then the real console opens", ok_console, w)
    if not ok_console:
        print("all windows incl. hidden:", windows_of(pid, True))
        print("---- sandbox log ----")
        print(open(os.path.join(prof, "sentinel.log"), encoding="utf-8", errors="replace").read()[-2500:].encode("ascii", "replace").decode())
        print("---------------------")
    check("the user's own folder exists with their settings", os.path.isfile(os.path.join(prof, ".env")) and os.path.isfile(os.path.join(prof, "sentinel.log")))
    log = open(os.path.join(prof, "sentinel.log"), encoding="utf-8", errors="replace").read()
    check("the app started and says it isn't reporting (no bot set up)", "Gnomewatch 'gwtest'" in log and "no bot set up" in log, log[-300:])
    check("nothing was written to the install folder's own data files", not os.path.exists(os.path.join(app, "accounts.json")) and not os.path.exists(os.path.join(app, ".env")))
    big = [r for t, r in w if (r[2] - r[0]) > 1000]
    if big:
        ImageGrab.grab(bbox=big[0]).save(os.path.join(OUT, "app_2_console.png"))
    ra = L.admin_call(app, ADMIN, "revoke", target=key)
    check("admin revokes the key", ra.get("ok"), ra)
    time.sleep(32)
    log = open(os.path.join(prof, "sentinel.log"), encoding="utf-8", errors="replace").read()
    check("the app notices and pauses itself", "!! key:" in log and "paused until you sign in again" in log, log[-400:])
    w = windows_of(pid)
    lock = [r for t, r in w if (r[2] - r[0]) < 600]
    check("the login page comes back over the console as a lock screen", bool(lock), w)
    if lock:
        ImageGrab.grab(bbox=lock[0]).save(os.path.join(OUT, "app_3_lock.png"))
finally:
    if app_proc:
        app_proc.kill()
    srv.kill()
    time.sleep(0.5)
    shutil.rmtree(tmp, ignore_errors=True)
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
