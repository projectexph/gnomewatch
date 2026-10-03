"""Gnomewatch installer. Downloads the app (checking its SHA-256), puts it in %LOCALAPPDATA%\\Gnomewatch, adds Desktop + Start-menu shortcuts that
start it with no console window, and opens it. Running it again updates the program and keeps everyone's settings.

This is a SMALL program on purpose: the app itself is a separate zip on the release page. (Packing the whole app inside one exe is what made
antivirus tools call it a virus.) For no-internet installs, put Gnomewatch-App.zip next to this file and it uses that instead."""
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import urllib.request
import uuid
import zipfile
from ctypes import wintypes
from tkinter import ttk

NOWIN = 0x08000000
BG, CARD, TEXT, DIM, ACCENT = "#0a0c11", "#131723", "#f1f4fa", "#9ba7bf", "#ff7a45"
KEEP = ("profiles", "session.json")                       # never overwritten on an update
TEST = os.environ.get("GW_SETUP_TEST")                    # test mode: everything goes into this folder, nothing touches the real PC
DEST = os.path.join(TEST, "Gnomewatch") if TEST else os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Gnomewatch")


def res(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def manifest():
    """{"url", "sha256", "size", "version"} for the app zip when this is the small web installer; None when the app is bundled instead."""
    path = (os.environ.get("GW_PAYLOAD_JSON") if TEST else None) or res("payload.json")
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def get_payload(progress):
    """Path to the app zip: downloaded and verified (web installer), found next to this file (offline), or bundled."""
    man = manifest()
    if man:
        url = str(man["url"])
        if not (url.startswith("https://") or (TEST and url.startswith("http://127.0.0.1"))):
            raise RuntimeError("The download link isn't a secure link.")
        folder = os.path.join(tempfile.gettempdir(), "gnomewatch-setup")
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, f"Gnomewatch-App-{man.get('version', 'x')}.zip")
        want = str(man["sha256"]).lower()
        if os.path.isfile(dest) and sha256_file(dest) == want:                      # a retry: it's already here and correct
            return dest
        part = dest + ".part"
        h, done = hashlib.sha256(), 0
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Gnomewatch-Setup"})
            with urllib.request.urlopen(req, timeout=30) as r, open(part, "wb") as f:
                total = int(r.headers.get("Content-Length") or man.get("size") or 0)
                while True:
                    chunk = r.read(1 << 18)
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    progress(6 + 54 * (done / total if total else 0.5),
                             f"Downloading Gnomewatch...  {done / 1e6:.0f} of {total / 1e6:.0f} MB" if total else "Downloading Gnomewatch...")
        except Exception:
            _remove(part)
            raise RuntimeError("Couldn't download Gnomewatch. Check your internet connection and try again.")
        if h.hexdigest() != want:
            _remove(part)
            raise RuntimeError("The download didn't match what was published, so it was thrown away. Try again.")
        os.replace(part, dest)
        return dest
    for cand in (os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "Gnomewatch-App.zip"), res("payload.zip")):
        if os.path.isfile(cand):
            return cand
    raise RuntimeError("This installer has no app inside and no download address.")


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def stop_running():
    """Close a running Gnomewatch (and its restarter) so its files can be replaced. Only touches programs that live in the install folder."""
    _remove(os.path.join(DEST, "running.flag"))                            # a deliberate stop is not a crash
    try:
        import psutil
    except ImportError:
        return
    mine = os.path.normcase(os.path.abspath(DEST))
    victims = []
    for p in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            name = (p.info["name"] or "").lower()
            if name == "wscript.exe" and any("gnomewatch.vbs" in (c or "").lower() for c in (p.info["cmdline"] or [])):
                victims.append(p)                                          # the restarter goes first, or it would start the app again
            elif name == "gnomewatch.exe" and os.path.normcase(os.path.abspath(p.info["exe"] or "")).startswith(mine):
                victims.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    victims.sort(key=lambda p: (p.info["name"] or "").lower() != "wscript.exe")
    for p in victims:
        try:
            p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    psutil.wait_procs(victims, timeout=6)


def known_folder(folder_id, fallback):
    """A Windows special folder (like the real Desktop, even when OneDrive moved it), without running any script."""
    try:
        g = uuid.UUID(folder_id)

        class GUID(ctypes.Structure):
            _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

        gid = GUID(g.time_low, g.time_mid, g.time_hi_version, (ctypes.c_ubyte * 8).from_buffer_copy(g.bytes[8:]))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(gid), 0, None, ctypes.byref(out)) == 0:
            path = out.value
            ctypes.windll.ole32.CoTaskMemFree(out)
            if path:
                return path
    except Exception:
        pass
    return fallback


def make_shortcut(path, target, args, icon, workdir):
    """A .lnk file, made through Windows' own shortcut object (no script is written or run)."""
    import win32com.client
    sh = win32com.client.Dispatch("WScript.Shell")
    lnk = sh.CreateShortcut(path)
    lnk.TargetPath = target
    lnk.Arguments = args
    lnk.WorkingDirectory = workdir
    lnk.IconLocation = icon
    lnk.Description = "Gnomewatch"
    lnk.Save()


def install(progress):
    progress(2, "Getting ready...")
    zpath = get_payload(progress)                                           # the old Gnomewatch keeps running while this downloads
    progress(62, "Closing Gnomewatch if it is running...")
    if not TEST:
        stop_running()
    zf = zipfile.ZipFile(zpath)
    names = [n for n in zf.namelist() if not n.endswith("/")]
    os.makedirs(DEST, exist_ok=True)
    for i, n in enumerate(names):
        top = n.replace("\\", "/").split("/")[0]
        target = os.path.join(DEST, n)
        if top in KEEP and os.path.exists(target):
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(n) as src, open(target, "wb") as out:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
        if i % 12 == 0:
            progress(64 + 24 * i / len(names), "Installing...")
    progress(90, "Adding shortcuts...")
    ico = os.path.join(DEST, "assets", "brand", "gnomewatch.ico")
    vbs = os.path.join(DEST, "Gnomewatch.vbs")
    wscript = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "wscript.exe")
    desktop = os.path.join(TEST, "Desktop") if TEST else known_folder("B4BFCC3A-DB2C-424C-B029-7FE99A87C641", os.path.join(os.path.expanduser("~"), "Desktop"))
    start = os.path.join(TEST, "Start") if TEST else os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Gnomewatch")
    os.makedirs(start, exist_ok=True)
    os.makedirs(desktop, exist_ok=True)
    make_shortcut(os.path.join(desktop, "Gnomewatch.lnk"), wscript, f'//B "{vbs}"', ico, DEST)
    make_shortcut(os.path.join(start, "Gnomewatch.lnk"), wscript, f'//B "{vbs}"', ico, DEST)
    progress(96, "Registering the uninstaller...")
    try:
        if TEST:
            raise OSError
        import winreg
        k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Gnomewatch")
        for name, val in (("DisplayName", "Gnomewatch"), ("DisplayIcon", ico), ("InstallLocation", DEST), ("Publisher", "Gnomewatch"),
                          ("UninstallString", f'"{wscript}" "{os.path.join(DEST, "Uninstall Gnomewatch.vbs")}"')):
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
        winreg.CloseKey(k)
    except OSError:
        pass
    progress(100, "Done")


def launch():
    wscript = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "wscript.exe")
    subprocess.Popen([wscript, "//B", os.path.join(DEST, "Gnomewatch.vbs")], cwd=DEST, creationflags=NOWIN, close_fds=True)


class Setup:
    def __init__(self, updating=False):
        self.updating = updating                           # started by Gnomewatch's own "Update now": no clicking, reopens when done
        self.root = r = tk.Tk()
        r.title("Updating Gnomewatch" if updating else "Gnomewatch Setup")
        r.configure(bg=BG)
        r.resizable(False, False)
        w, h = 440, 400
        r.geometry(f"{w}x{h}+{(r.winfo_screenwidth() - w) // 2}+{(r.winfo_screenheight() - h) // 2}")
        try:
            r.iconbitmap(res("gnomewatch.ico"))
        except tk.TclError:
            pass
        try:
            self.logo = tk.PhotoImage(file=res("logo_256.png")).subsample(2, 2)
            tk.Label(r, image=self.logo, bg=BG).pack(pady=(26, 6))
        except tk.TclError:
            pass
        tk.Label(r, text="Gnomewatch", bg=BG, fg=TEXT, font=("Segoe UI", 20, "bold")).pack()
        self.sub = tk.Label(r, text="Weather watcher for Roll A Gnome", bg=BG, fg=DIM, font=("Segoe UI", 10))
        self.sub.pack(pady=(0, 18))
        st = ttk.Style(r)
        st.theme_use("clam")
        st.configure("G.Horizontal.TProgressbar", troughcolor=CARD, background=ACCENT, bordercolor=BG, lightcolor=ACCENT, darkcolor=ACCENT)
        self.bar = ttk.Progressbar(r, style="G.Horizontal.TProgressbar", length=320, maximum=100)
        self.status = tk.Label(r, text=f"Installs to {DEST}", bg=BG, fg=DIM, font=("Segoe UI", 9), wraplength=380)
        self.btn = tk.Button(r, text="Install", command=self.go, bg=ACCENT, fg="#1a0d05", activebackground="#ff946a", relief="flat", bd=0,
                             font=("Segoe UI", 11, "bold"), width=18, pady=8, cursor="hand2")
        self.btn.pack(pady=(4, 12))
        self.status.pack()
        self.bar.pack(pady=(10, 0))
        self.bar.pack_forget()
        self.err = None
        if updating:
            self.sub.config(text="Updating — Gnomewatch reopens by itself")
            self.btn.pack_forget()
            self.status.config(text="Starting...")
            r.after(400, self.go)

    def progress(self, v, msg):
        self.root.after(0, lambda: (self.bar.config(value=v), self.status.config(text=msg)))

    def go(self):
        self.btn.config(state="disabled", text="Installing...")
        self.bar.pack(pady=(10, 0))
        self.status.pack_forget()
        self.status.pack(pady=(10, 0))
        threading.Thread(target=self.work, daemon=True).start()

    def work(self):
        try:
            install(self.progress)
        except Exception as e:                                  # shown to the person, not swallowed
            self.err = str(e)
        self.root.after(0, self.finished)

    def finished(self):
        if self.err:
            self.status.config(text=f"Something went wrong: {self.err}", fg="#ff5470")
            self.btn.pack(pady=(10, 12))
            self.btn.config(state="normal", text="Try again", command=self.go)
            return
        if self.updating:
            self.status.config(text="Updated. Opening Gnomewatch...", fg=DIM)
            if not TEST:
                launch()
            self.root.after(1500, self.root.destroy)
            return
        self.status.config(text="Installed. A Gnomewatch shortcut is on your Desktop and in the Start menu.", fg=DIM)
        self.btn.config(state="normal", text="Open Gnomewatch", command=lambda: (launch(), self.root.destroy()))

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if "--silent" in sys.argv:                                  # no window: install, then start it (not in test mode)
        try:
            install(lambda v, m: None)
        except Exception as e:                                  # a windowed program would otherwise pop up a crash box
            sys.stderr.write("install failed: " + str(e) + chr(10))
            sys.exit(1)
        if not TEST:
            launch()
    else:
        Setup(updating="--update" in sys.argv).run()
