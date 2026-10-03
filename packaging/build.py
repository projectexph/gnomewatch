"""Builds the one-click installer:   python packaging/build.py     ->  packaging/dist/Gnomewatch-Setup.exe
1) PyInstaller turns the app into a folder (Gnomewatch.exe + everything it needs, no console window)
2) that folder is zipped, and a small installer exe (setup_app.py) carries the zip.
Only program files go in - never anyone's profiles, keys, logs or secrets."""
import json
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
WORK = os.path.join(HERE, "build")
DIST = os.path.join(HERE, "dist")
PY = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN"]
SEP = os.pathsep

shutil.rmtree(WORK, ignore_errors=True)
shutil.rmtree(DIST, ignore_errors=True)
os.makedirs(WORK)
os.makedirs(DIST)

# the only config that ships: where the key server is and the Discord invite (both public)
if not os.path.isfile(os.path.join(APP, "gw_config.json")):
    sys.exit("gw_config.json is missing. Copy gw_config.example.json to gw_config.json and put in your own key server address and Discord invite "
             "(see gnomewatch-keys/README.md).")
cfg = json.load(open(os.path.join(APP, "gw_config.json"), encoding="utf-8"))
if not str(cfg.get("api", "")).startswith("https://") or "YOUR-" in str(cfg.get("api", "")):
    sys.exit("gw_config.json still has the placeholder key server address. Put in your own (see gnomewatch-keys/README.md).")
stage = os.path.join(WORK, "stage")
os.makedirs(stage)
json.dump({"api": cfg["api"], "invite": cfg.get("invite", "")}, open(os.path.join(stage, "gw_config.json"), "w"), indent=1)
shutil.copytree(os.path.join(APP, "icons"), os.path.join(stage, "seed_icons"))

mods = ["console", "console_v1", "panel", "dashboard", "lockkeeper", "gw_boot", "gw_license", "gw_update", "gw_version", "gw_ghost", "gw_share", "gw_overlay", "gw_wizard", "gw_presence", "cs_help", "win32clipboard", "win32con", "gw_login_ui", "gw_logs", "ui_kit",
        "cs_common", "cs_overview", "cs_instances", "cs_accounts", "cs_autoplace", "cs_performance", "cs_activity", "cs_settings",
        "cs_palette", "cs_admin", "handles", "pystray._win32", "win32timezone", "win32com.client", "certifi"]
data = [(os.path.join(APP, "assets"), "assets"), (os.path.join(stage, "seed_icons"), os.path.join("assets", "seed_icons")),
        (os.path.join(APP, "dashboard.html"), "."), (os.path.join(APP, "defaults.env"), "."), (os.path.join(APP, "gnome.ico"), "."),
        (os.path.join(stage, "gw_config.json"), ".")]
cmd = PY + ["--windowed", "--name", "Gnomewatch", "--contents-directory", ".", "--icon", os.path.join(APP, "assets", "brand", "gnomewatch.ico"),
            "--distpath", os.path.join(WORK, "app"), "--workpath", os.path.join(WORK, "pyi"), "--specpath", WORK,
            "--paths", APP, "--collect-submodules", "winsdk", "--collect-all", "windows_capture"]
for m in mods:
    cmd += ["--hidden-import", m]
for src, dst in data:
    cmd += ["--add-data", f"{src}{SEP}{dst}"]
cmd.append(os.path.join(APP, "sentinel.py"))
print("building the app ...")
subprocess.check_call(cmd, cwd=APP)

appdir = os.path.join(WORK, "app", "Gnomewatch")
for f in ("Gnomewatch.vbs", "Uninstall Gnomewatch.vbs"):
    shutil.copy(os.path.join(HERE, f), appdir)

payload = os.path.join(WORK, "payload.zip")
with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for root, _, files in os.walk(appdir):
        for f in files:
            full = os.path.join(root, f)
            z.write(full, os.path.relpath(full, appdir))
print("payload:", round(os.path.getsize(payload) / 1e6, 1), "MB")

# The app is its own download (Gnomewatch-App.zip); the installer is a small program that fetches it and checks its SHA-256. Packing the whole
# app inside one exe is what antivirus tools flagged as a virus. --offline still builds the big all-in-one installer, for no-internet use.
import hashlib
sys.path.insert(0, APP)
from gw_version import VERSION
app_zip = os.path.join(DIST, "Gnomewatch-App.zip")
shutil.copy(payload, app_zip)
zsha = hashlib.sha256(open(app_zip, "rb").read()).hexdigest()
repo = os.environ.get("GW_REPO") or (json.load(open(os.path.join(HERE, "release.json"))).get("repo") if os.path.exists(os.path.join(HERE, "release.json")) else "")
offline = "--offline" in sys.argv or not repo
data_args = []
if offline:
    print("(offline installer: the app is packed inside it, so it is large)")
    data_args += ["--add-data", f"{payload}{SEP}."]
else:
    man = os.path.join(WORK, "payload.json")
    json.dump({"url": f"https://github.com/{repo}/releases/download/v{VERSION}/Gnomewatch-App.zip", "sha256": zsha, "size": os.path.getsize(app_zip),
               "version": VERSION}, open(man, "w"))
    data_args += ["--add-data", f"{man}{SEP}."]
print("building the installer ...")
cmd = PY + ["--windowed", "--onefile", "--name", "Gnomewatch-Setup", "--icon", os.path.join(APP, "assets", "brand", "gnomewatch.ico"),
            "--distpath", DIST, "--workpath", os.path.join(WORK, "pyi2"), "--specpath", WORK, "--hidden-import", "psutil", "--hidden-import", "win32com.client",
            "--hidden-import", "pythoncom", "--exclude-module", "numpy", "--exclude-module", "cv2", "--exclude-module", "PIL",
            *data_args, "--add-data", f"{os.path.join(APP, 'assets', 'brand', 'logo_256.png')}{SEP}.",
            "--add-data", f"{os.path.join(APP, 'assets', 'brand', 'gnomewatch.ico')}{SEP}.", os.path.join(HERE, "setup_app.py")]
subprocess.check_call(cmd, cwd=APP)
print("done:", os.path.join(DIST, "Gnomewatch-Setup.exe"), round(os.path.getsize(os.path.join(DIST, "Gnomewatch-Setup.exe")) / 1e6, 1), "MB")
