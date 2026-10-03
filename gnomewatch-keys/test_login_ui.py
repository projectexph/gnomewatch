"""Opens the real login page against the local key server and drives it: bad key, good key, welcome. Saves screenshots of
this program's own window only.   python test_login_ui.py [outdir]"""
import ctypes
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
sys.path.insert(0, APP)
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.gettempdir()
PORT = 8792
ADMIN = "GW-TEST-ADMIN-KEY"
tmp = tempfile.mkdtemp(prefix="gw_ui_")
srv = subprocess.Popen([sys.executable, os.path.join(HERE, "dev_server.py"), "--port", str(PORT), "--admin-key", ADMIN,
                        "--file", os.path.join(tmp, "store.json")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2).read()
            break
        except Exception:
            time.sleep(0.25)
    key = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/dev/getkey?id=4242&name=Kyle", timeout=5).read())["key"]
    app = os.path.join(tmp, "app")
    shutil.copytree(APP, app, ignore=shutil.ignore_patterns("profiles", "backups", "__pycache__", "*.log", "gnomewatch-keys", ".env", "secrets.env",
                                                            "session.json", "diag", "*.zip"))
    json.dump({"api": f"http://127.0.0.1:{PORT}", "invite": "https://discord.gg/example"}, open(os.path.join(app, "gw_config.json"), "w"))
    sys.path.insert(0, app)
    from PIL import ImageGrab
    import gw_login_ui as G
    import gw_license as L

    win = G.LoginWindow(app, mode="boot")
    r = win.root
    log = []

    def shot(tag):
        x, y, w, h = r.winfo_rootx(), r.winfo_rooty(), r.winfo_width(), r.winfo_height()
        ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(os.path.join(OUT, f"login_{tag}.png"))
        log.append(tag)

    def type_key(k):
        win.var.set(k)

    steps = [
        (1400, lambda: shot("1_form")),
        (1600, lambda: (type_key("GW-WRNG-WRNG-WRNG"), win.submit())),
        (2600, lambda: shot("2_bad_key")),
        (2800, lambda: (type_key(key), win.submit())),
        (3300, lambda: shot("3_checking")),
        (4600, lambda: shot("4_welcome")),
        (6800, lambda: None),
    ]
    for ms, fn in steps:
        r.after(ms, fn)
    sess = win.run()
    print("shots:", log)
    print("session:", {k: (v if k != "key" else "<hidden>") for k, v in (sess or {}).items() if k in ("id", "name", "admin", "data_dir", "key")})
    print("remembered key saved:", L.load_saved(app) == key)
    print("profile folder:", os.path.isdir(sess["data_dir"]) if sess else None)
finally:
    srv.kill()
    shutil.rmtree(tmp, ignore_errors=True)
