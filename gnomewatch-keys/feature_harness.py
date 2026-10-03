"""A sandbox copy of the real app (safe settings, test lock, own folder) that runs a script INSIDE the console and reports back.
Used by test_features.py. Screenshots are of the app's own windows only.

    h = Harness(profile_files={"macros.json": {...}}, env={...})
    res = h.run(script_text, wait=10, shots={"name": 8})      # res = {"result": <json the script wrote>, "shots": {...}, "log": "..."}
"""
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
sys.path.insert(0, APP)
import gw_license as L
from PIL import ImageGrab

SAFE = ("NOTIFY=0\nTRAY=0\nDASHBOARD=0\nCLEAN_HELPERS=0\nHOTKEY=\nPANEL_HOTKEY=\nPALETTE_HOTKEY=\nPANIC_HOTKEY=\nGRID_HOTKEY=\nHIDE_WINDOW=0\n"
        "HIDE_ALL=0\nKEEPALIVE=0\nAUTO_RECONNECT=0\nAUTO_REJOIN=0\nMEM_TRIM=0\nMULTI_INSTANCE=0\nPANEL_MINI=0\nANIMATIONS=off\nUI_MODE=fps\n")


def windows_of(pid, min_w=200):
    out = []
    u = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(h, l):
        p = wt.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and u.IsWindowVisible(h):
            r = wt.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            b = ctypes.create_unicode_buffer(80)
            u.GetWindowTextW(h, b, 80)
            if r.right - r.left >= min_w:
                out.append((b.value, (r.left, r.top, r.right, r.bottom)))
        return True

    u.EnumWindows(cb, 0)
    return out


class Harness:
    def __init__(self, port, profile_files=None, env=None, outdir=None, hours_user=("4242", "Kyle"), admin=False, extra_args=()):
        self.port, self.tmp = port, tempfile.mkdtemp(prefix="gw_feat_")
        self.out = outdir or tempfile.gettempdir()
        self.admin_key = "GW-TEST-ADMIN-KEY"
        self.srv = subprocess.Popen([sys.executable, os.path.join(HERE, "dev_server.py"), "--port", str(port), "--admin-key", self.admin_key,
                                     "--file", os.path.join(self.tmp, "store.json"), *extra_args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2).read()
                break
            except Exception:
                time.sleep(0.25)
        uid, name = hours_user
        self.key = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/dev/getkey?id={uid}&name={name}", timeout=5).read())["key"]
        if admin:                                   # sign in with the admin key instead (profile folder: admin)
            self.key, uid = self.admin_key, "admin"
        self.uid = uid
        self.app = os.path.join(self.tmp, "app")
        shutil.copytree(APP, self.app, ignore=shutil.ignore_patterns("profiles", "backups", "__pycache__", "*.log", "gnomewatch-keys", ".env", "secrets.env",
                                                                    "session.json", "diag", "*.zip", "lockkeeper.*", "accounts.json", "assign.json", "macros.json",
                                                                    "history.jsonl", "sessions.jsonl", "known_events.json", "scenes.json", "mode_prev.json",
                                                                    "running.flag", "packaging", "dist", "build"))
        json.dump({"api": f"http://127.0.0.1:{port}", "invite": "https://discord.gg/example"}, open(os.path.join(self.app, "gw_config.json"), "w"))
        open(os.path.join(self.app, "defaults.env"), "w").write(SAFE)
        L.save_key(self.app, self.key)
        self.prof = os.path.join(self.app, "profiles", uid)
        os.makedirs(self.prof, exist_ok=True)
        for fn, data in (profile_files or {}).items():
            with open(os.path.join(self.prof, fn), "w", encoding="utf-8") as f:
                f.write(data if isinstance(data, str) else json.dumps(data))
        self.extra_env = env or {}

    def run(self, script, wait=12, shots=None, script_delay=5000, kill=True):
        """shots: {name: seconds after start}. Returns dict(result, shots, log, windows)."""
        sp = os.path.join(self.tmp, "script.py")
        open(sp, "w", encoding="utf-8").write(script)
        outp = os.path.join(self.tmp, "result.json")
        env = dict(os.environ, GW_INSTANCE="_test", GW_HEARTBEAT="300", SENTINEL_NAME="gwtest", PYTHONIOENCODING="utf-8", GW_TEST_SCRIPT=sp,
                   GW_TEST_DELAY=str(script_delay), GW_TEST_OUT=outp, GW_TEST_SHOTS=self.out, **self.extra_env)
        for k in ("WORKER_URL", "INGEST_SECRET"):
            env.pop(k, None)
        exe = sys.executable.replace("python.exe", "pythonw.exe")
        self.proc = subprocess.Popen([exe, os.path.join(self.app, "sentinel.py")], cwd=self.app, env=env)
        t0 = time.time()
        got = {}
        while time.time() - t0 < wait and not os.path.exists(outp):         # the script says it is finished by writing its result file
            time.sleep(0.5)
        time.sleep(0.5)
        res = None
        try:
            res = json.load(open(outp, encoding="utf-8"))
        except (OSError, ValueError):
            pass
        log = ""
        try:
            log = open(os.path.join(self.prof, "sentinel.log"), encoding="utf-8", errors="replace").read()
        except OSError:
            pass
        out = {"result": res, "shots": got, "log": log, "windows": windows_of(self.proc.pid)}
        if kill:
            self.proc.kill()
        return out

    def close(self):
        try:
            self.proc.kill()
        except Exception:
            pass
        self.srv.kill()
        time.sleep(0.5)
        shutil.rmtree(self.tmp, ignore_errors=True)
