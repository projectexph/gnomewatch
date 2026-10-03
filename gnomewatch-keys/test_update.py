"""Updates end to end, against the local key server:
   1. the server tells an old app about a newer version (optional / required)
   2. the downloader checks the SHA-256 and refuses wrong or missing checksums
   3. the login page turns into 'Update now' for a copy that is too old (screenshots of its own window)
   4. the real console shows the update in Settings (screenshot of its own window)
        python test_update.py [outdir]"""
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.gettempdir()
PORT, FILEPORT = 8798, 8799
ADMIN = "GW-TEST-ADMIN-KEY"
tmp = tempfile.mkdtemp(prefix="gw_upd_")
sys.path.insert(0, APP)
fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


# a fake installer to download
payload = os.urandom(300_000)
want = hashlib.sha256(payload).hexdigest()
files = os.path.join(tmp, "files")
os.makedirs(files)
open(os.path.join(files, "setup.exe"), "wb").write(payload)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=files, **k)

    def log_message(self, *a):
        pass


fs = http.server.ThreadingHTTPServer(("127.0.0.1", FILEPORT), Quiet)
threading.Thread(target=fs.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{FILEPORT}/setup.exe"


def start_server(*extra, port=PORT):
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "dev_server.py"), "--port", str(port), "--admin-key", ADMIN, "--file",
                          os.path.join(tmp, f"store{port}.json"), *extra], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2).read()
            return p
        except Exception:
            time.sleep(0.25)
    raise SystemExit("dev server didn't start")


def getkey(port, uid="4242", name="Kyle"):
    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/dev/getkey?id={uid}&name={name}", timeout=5).read())["key"]


procs = []
try:
    import gw_license as L
    import gw_update as UP
    from gw_version import VERSION
    app = os.path.join(tmp, "app")
    os.makedirs(app)

    # ---------------------------------------------------------------- 1. the server tells the app
    s1 = start_server("--latest", "9.9.9", "--url", URL, "--sha256", want, "--notes", "Faster login", port=PORT)
    procs.append(s1)
    json.dump({"api": f"http://127.0.0.1:{PORT}"}, open(os.path.join(app, "gw_config.json"), "w"))
    key = getkey(PORT)
    r = L.login(app, key)
    check("an older app signs in and is told about the new version", r.get("ok") and r["update"]["latest"] == "9.9.9" and r["update"]["forced"] is False
          and r["update"]["sha256"] == want and r["update"]["notes"] == "Faster login", r)
    check("the session carries the update note", L.make_session(app, key, r)["update"]["latest"] == "9.9.9")
    r = L.check(app, key)
    check("the heartbeat carries it too", r.get("ok") and r["update"]["latest"] == "9.9.9", r)
    procs.pop().kill()

    s1 = start_server("--latest", "9.9.9", "--min", "9.0.0", "--url", URL, "--sha256", want, port=PORT)
    procs.append(s1)
    key = getkey(PORT)
    r = L.login(app, key)
    check("below the minimum: refused with 'update', and an update note to act on", r.get("ok") is False and r["update"]["forced"] is True and "update" in r["error"].lower(), r)
    r = L.admin_call(app, ADMIN, "list")
    check("(the admin key still works for managing keys)", r.get("ok") is True, r)

    # ---------------------------------------------------------------- 2. the downloader
    upd = {"latest": "9.9.9", "url": URL, "sha256": want}
    check("a good update note is accepted", UP.usable(upd))
    check("a note with a non-secure link is not", not UP.usable({"latest": "9.9.9", "url": "http://evil.example/x.exe", "sha256": want}))
    path = UP.download(upd, os.path.join(tmp, "dl"))
    check("the download is saved and matches the published checksum", os.path.isfile(path) and hashlib.sha256(open(path, "rb").read()).hexdigest() == want)
    try:
        UP.download({"latest": "9.9.9", "url": URL, "sha256": "0" * 64}, os.path.join(tmp, "dl2"))
        check("a wrong checksum is refused", False)
    except UP.UpdateError as e:
        left = [f for f in os.listdir(os.path.join(tmp, "dl2"))]
        check("a wrong checksum is refused and the file thrown away", "didn't match" in str(e) and not left, (str(e), left))
    try:
        UP.download({"latest": "9.9.9", "url": URL, "sha256": ""}, os.path.join(tmp, "dl3"))
        check("a missing checksum is refused", False)
    except UP.UpdateError as e:
        check("a missing checksum is refused", "checksum" in str(e))

    # ---------------------------------------------------------------- 3. the login page for a copy that is too old
    from PIL import ImageGrab
    shutil.copytree(APP, os.path.join(tmp, "ui"), ignore=shutil.ignore_patterns("profiles", "backups", "__pycache__", "*.log", "gnomewatch-keys", ".env",
                                                                               "secrets.env", "session.json", "diag", "*.zip", "packaging", "dist", "build"))
    uiapp = os.path.join(tmp, "ui")
    json.dump({"api": f"http://127.0.0.1:{PORT}", "invite": "https://discord.gg/example"}, open(os.path.join(uiapp, "gw_config.json"), "w"))
    sys.path.insert(0, uiapp)
    import webbrowser
    opened = []
    webbrowser.open = lambda u, *a, **k: opened.append(u)
    import gw_login_ui as G
    win = G.LoginWindow(uiapp, mode="boot")
    r_ = win.root
    log = {}

    def shot(tag):
        x, y, w, h = r_.winfo_rootx(), r_.winfo_rooty(), r_.winfo_width(), r_.winfo_height()
        ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(os.path.join(OUT, f"update_login_{tag}.png"))

    def probe(tag):
        log[tag] = {"btn": win.go.text, "upd": bool(win.upd), "msg": win.cv.itemcget(win.msg, "text")}

    steps = [
        (1400, lambda: (win.var.set(key), win.submit())),
        (3000, lambda: (shot("1_update_needed"), probe("needed"))),
        (3200, win.submit),
        (4300, lambda: (shot("2_after_click"), probe("clicked"))),
        (4800, win._close),
    ]
    for ms, fn in steps:
        r_.after(ms, fn)
    win.run()
    check("the page switches to 'Update now' with an explanation", log["needed"]["btn"] == "Update now" and log["needed"]["upd"] and "9.9.9" in log["needed"]["msg"], log)
    check("(from source) 'Update now' opens the download page", opened == [URL], opened)

    # ---------------------------------------------------------------- 4. the real console shows the update in Settings
    procs.pop().kill()
    s2 = start_server("--latest", "9.9.9", "--url", URL, "--sha256", want, "--notes", "Faster login", port=PORT)
    procs.append(s2)
    k2 = getkey(PORT, "4243", "Sam")
    app2 = os.path.join(tmp, "app2")
    shutil.copytree(uiapp, app2)
    json.dump({"api": f"http://127.0.0.1:{PORT}", "invite": "https://discord.gg/example"}, open(os.path.join(app2, "gw_config.json"), "w"))
    L.save_key(app2, k2)
    open(os.path.join(app2, "defaults.env"), "w").write("NOTIFY=0\nTRAY=0\nDASHBOARD=0\nCLEAN_HELPERS=0\nHOTKEY=\nPANEL_HOTKEY=\nPALETTE_HOTKEY=\nPANIC_HOTKEY=\n"
                                                      "GRID_HOTKEY=\nHIDE_WINDOW=0\nHIDE_ALL=0\nKEEPALIVE=0\nAUTO_RECONNECT=0\nAUTO_REJOIN=0\nMEM_TRIM=0\n"
                                                      "MULTI_INSTANCE=0\nPANEL_MINI=0\nANIMATIONS=off\nUI_MODE=fps\n")
    env = dict(os.environ, GW_INSTANCE="_test", GW_HEARTBEAT="300", SENTINEL_NAME="gwtest", GW_START_PAGE="Settings", PYTHONIOENCODING="utf-8")
    for k_ in ("WORKER_URL", "INGEST_SECRET"):
        env.pop(k_, None)
    exe = sys.executable.replace("python.exe", "pythonw.exe")
    ap = subprocess.Popen([exe, os.path.join(app2, "sentinel.py")], cwd=app2, env=env)
    time.sleep(19)
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    wins = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(h, l):
        p = wt.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == ap.pid and u.IsWindowVisible(h):
            r = wt.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            if r.right - r.left > 1000:
                wins.append((r.left, r.top, r.right, r.bottom))
        return True

    u.EnumWindows(cb, 0)
    check("the console opened on the Settings page", bool(wins), wins)
    if wins:
        ImageGrab.grab(bbox=wins[0]).save(os.path.join(OUT, "update_settings.png"))
    ap.kill()
finally:
    for p in procs:
        p.kill()
    fs.shutdown()
    time.sleep(0.5)
    shutil.rmtree(tmp, ignore_errors=True)
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
