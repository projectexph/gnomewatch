"""The small web installer, in test mode (nothing touches your real Desktop, Start menu, registry or install):
it downloads the app zip from a local pretend release page, checks the SHA-256, installs, makes shortcuts, and refuses a tampered download.
    python test_setup.py        (needs a built packaging/dist: Gnomewatch-Setup.exe + Gnomewatch-App.zip)"""
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

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(os.path.dirname(HERE), "packaging", "dist")
EXE, ZIP = os.path.join(DIST, "Gnomewatch-Setup.exe"), os.path.join(DIST, "Gnomewatch-App.zip")
fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   {str(extra)[:300]}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


if not (os.path.isfile(EXE) and os.path.isfile(ZIP)):
    sys.exit("build first: python packaging/build.py")

tmp = tempfile.mkdtemp(prefix="gw_setup_")
serve = os.path.join(tmp, "serve")
os.makedirs(serve)
shutil.copy(ZIP, os.path.join(serve, "Gnomewatch-App.zip"))
PORT = 8831


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=serve, **k)

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
sha = hashlib.sha256(open(ZIP, "rb").read()).hexdigest()


def run_setup(manifest, name):
    root = os.path.join(tmp, name)
    os.makedirs(root)
    mpath = os.path.join(tmp, name + ".json")
    json.dump(manifest, open(mpath, "w"))
    env = dict(os.environ, GW_SETUP_TEST=root, GW_PAYLOAD_JSON=mpath)
    p = subprocess.run([EXE, "--silent"], env=env, capture_output=True, timeout=300)
    return root, p.returncode


try:
    check("the installer is small (it only carries the downloader)", os.path.getsize(EXE) < 30e6, round(os.path.getsize(EXE) / 1e6, 1))
    src = open(os.path.join(os.path.dirname(HERE), "packaging", "setup_app.py"), encoding="utf-8").read()
    check("it runs no PowerShell and writes no script to make shortcuts", "powershell" not in src.lower().replace("(no script", "") and ".vbs\"" not in src.split("def make_shortcut")[1].split("def install")[0])

    root, rc = run_setup({"url": f"http://127.0.0.1:{PORT}/Gnomewatch-App.zip", "sha256": sha, "size": os.path.getsize(ZIP), "version": "t1"}, "good")
    app = os.path.join(root, "Gnomewatch")
    check("a good download installs (exit 0)", rc == 0 and os.path.isfile(os.path.join(app, "Gnomewatch.exe")), (rc, os.listdir(root)))
    check("the launcher, uninstaller, icon and assets came with it", all(os.path.exists(os.path.join(app, p)) for p in ("Gnomewatch.vbs", "Uninstall Gnomewatch.vbs",
          os.path.join("assets", "brand", "gnomewatch.ico"), "gw_config.json")))
    check("both shortcuts were made", os.path.isfile(os.path.join(root, "Desktop", "Gnomewatch.lnk")) and os.path.isfile(os.path.join(root, "Start", "Gnomewatch.lnk")),
          os.listdir(root))
    try:
        import win32com.client
        s = win32com.client.Dispatch("WScript.Shell").CreateShortcut(os.path.join(root, "Desktop", "Gnomewatch.lnk"))
        check("the shortcut starts the hidden launcher with no console window", s.TargetPath.lower().endswith("wscript.exe") and "gnomewatch.vbs" in s.Arguments.lower(), (s.TargetPath, s.Arguments))
    except Exception as e:
        check("the shortcut can be read back", False, e)
    check("no settings, keys or profiles were shipped", not os.path.exists(os.path.join(app, "profiles")) and not os.path.exists(os.path.join(app, "session.json")))

    # an update keeps the person's data
    os.makedirs(os.path.join(app, "profiles", "42"))
    open(os.path.join(app, "profiles", "42", ".env"), "w").write("KEEP=me\n")
    open(os.path.join(app, "session.json"), "w").write('{"key": "x"}')
    env = dict(os.environ, GW_SETUP_TEST=root, GW_PAYLOAD_JSON=os.path.join(tmp, "good.json"))
    rc2 = subprocess.run([EXE, "--silent"], env=env, capture_output=True, timeout=300).returncode
    check("running it again keeps profiles and the saved sign-in", rc2 == 0 and open(os.path.join(app, "profiles", "42", ".env")).read() == "KEEP=me\n"
          and open(os.path.join(app, "session.json")).read() == '{"key": "x"}')

    # a download that isn't what was published
    root2, rc3 = run_setup({"url": f"http://127.0.0.1:{PORT}/Gnomewatch-App.zip", "sha256": "0" * 64, "size": os.path.getsize(ZIP), "version": "t2"}, "bad")
    check("a download with the wrong checksum is refused, and nothing is installed", rc3 != 0 and not os.path.exists(os.path.join(root2, "Gnomewatch", "Gnomewatch.exe")), (rc3, os.listdir(root2)))
    root3, rc4 = run_setup({"url": f"http://127.0.0.1:{PORT}/missing.zip", "sha256": sha, "size": 1, "version": "t3"}, "gone")
    check("an unreachable download fails cleanly, and nothing is installed", rc4 != 0 and not os.path.exists(os.path.join(root3, "Gnomewatch", "Gnomewatch.exe")), (rc4, os.listdir(root3)))
    root4, rc5 = run_setup({"url": "http://example.com/evil.zip", "sha256": sha, "size": 1, "version": "t4"}, "plain")
    check("a link that isn't https is refused", rc5 != 0 and not os.path.exists(os.path.join(root4, "Gnomewatch", "Gnomewatch.exe")), (rc5,))
finally:
    srv.shutdown()
    time.sleep(0.5)
    shutil.rmtree(tmp, ignore_errors=True)
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.stdout.flush()
sys.exit(1 if fails else 0)
