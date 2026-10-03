"""Runs the PACKAGED app in the sandbox and reports whether it reads the weather sign from open Roblox windows."""
import json, os, shutil, subprocess, sys, tempfile, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.dirname(HERE)
sys.path.insert(0, APP)
import gw_license as L
tmp = tempfile.mkdtemp(prefix="gw_probe_")
port = 8820
srv = subprocess.Popen([sys.executable, os.path.join(HERE, "dev_server.py"), "--port", str(port), "--admin-key", "GW-TEST-ADMIN-KEY", "--file", os.path.join(tmp, "store.json")],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2).read(); break
        except Exception:
            time.sleep(0.25)
    key = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/dev/getkey?id=777&name=Probe", timeout=5).read())["key"]
    app = os.path.join(tmp, "app")
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(APP, "packaging", "build", "app", "Gnomewatch")
    frozen = os.path.isfile(os.path.join(src, "Gnomewatch.exe"))
    shutil.copytree(src, app, ignore=shutil.ignore_patterns("profiles", "__pycache__", "*.log", "gnomewatch-keys", "packaging", "dist", "build", ".env", "secrets.env", "session.json"))
    json.dump({"api": f"http://127.0.0.1:{port}"}, open(os.path.join(app, "gw_config.json"), "w"))
    open(os.path.join(app, "defaults.env"), "w").write("NOTIFY=0\nTRAY=0\nDASHBOARD=0\nCLEAN_HELPERS=0\nHOTKEY=\nPANEL_HOTKEY=\nPALETTE_HOTKEY=\nPANIC_HOTKEY=\nGRID_HOTKEY=\nHIDE_WINDOW=0\nHIDE_ALL=0\nKEEPALIVE=0\nAUTO_RECONNECT=0\nAUTO_REJOIN=0\nMEM_TRIM=0\nMULTI_INSTANCE=0\nPANEL=0\nVERBOSE=1\n")
    L.save_key(app, key)
    env = dict(os.environ, GW_INSTANCE="_probe", GW_HEARTBEAT="300", SENTINEL_NAME="probe", PYTHONIOENCODING="utf-8")
    exe = [os.path.join(app, "Gnomewatch.exe")] if frozen else [sys.executable.replace("python.exe", "pythonw.exe"), os.path.join(app, "sentinel.py")]
    p = subprocess.Popen(exe, cwd=app, env=env)
    time.sleep(75)
    log = ""
    try:
        log = open(os.path.join(app, "profiles", "777", "sentinel.log"), encoding="utf-8", errors="replace").read()
    except OSError:
        pass
    p.kill()
    print("frozen:", frozen)
    for ln in log.splitlines():
        if any(k in ln for k in ("ACTIVE", "NEXT", "error", "Error", "no billboard", "Roblox window", "capture", "Traceback")):
            print("  ", ln[:170].encode("ascii", "replace").decode())
    print("log lines:", len(log.splitlines()))
finally:
    srv.kill(); time.sleep(0.5); shutil.rmtree(tmp, ignore_errors=True)
