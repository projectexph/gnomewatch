"""Gnomewatch one-click installer. Extracts the app to %LOCALAPPDATA%\\Gnomewatch, adds Desktop + Start-menu shortcuts that start it
with no console window, and opens it. Running it again updates the program and keeps everyone's settings."""
import os
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import zipfile
from tkinter import ttk

NOWIN = 0x08000000
BG, CARD, TEXT, DIM, ACCENT = "#0a0c11", "#131723", "#f1f4fa", "#9ba7bf", "#ff7a45"
KEEP = ("profiles", "session.json")                       # never overwritten on an update
TEST = os.environ.get("GW_SETUP_TEST")                    # test mode: everything goes into this folder, nothing touches the real PC
DEST = os.path.join(TEST, "Gnomewatch") if TEST else os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Gnomewatch")


def res(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def hidden(cmd):
    return subprocess.run(cmd, creationflags=NOWIN, capture_output=True, text=True)


def stop_running():
    """Close a running Gnomewatch (and its restarter) so its files can be replaced."""
    try:
        os.remove(os.path.join(DEST, "running.flag"))                      # a deliberate stop is not a crash
    except OSError:
        pass
    hidden(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='wscript.exe'\" | Where-Object { $_.CommandLine -like '*Gnomewatch.vbs*' } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"])
    hidden(["taskkill", "/F", "/IM", "Gnomewatch.exe"])


def make_shortcut(path, target, args, icon, workdir):
    vbs = (f'Set s = CreateObject("WScript.Shell")\nSet l = s.CreateShortcut("{path}")\nl.TargetPath = "{target}"\n'
           f'l.Arguments = "{args}"\nl.WorkingDirectory = "{workdir}"\nl.IconLocation = "{icon}"\nl.Description = "Gnomewatch"\nl.Save\n')
    f = os.path.join(tempfile.gettempdir(), "gw_lnk.vbs")
    with open(f, "w", encoding="utf-8") as h:
        h.write(vbs)
    hidden(["wscript.exe", "//B", f])
    os.remove(f)


def install(progress):
    progress(2, "Closing Gnomewatch if it is running...")
    if not TEST:
        stop_running()
    zf = zipfile.ZipFile(res("payload.zip"))
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
            progress(5 + 80 * i / len(names), "Installing...")
    progress(88, "Adding shortcuts...")
    ico = os.path.join(DEST, "assets", "brand", "gnomewatch.ico")
    vbs = os.path.join(DEST, "Gnomewatch.vbs")
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    wscript = os.path.join(sysroot, "System32", "wscript.exe")
    desktop = os.path.join(TEST, "Desktop") if TEST else subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"], capture_output=True, text=True,
                             creationflags=NOWIN).stdout.strip() or os.path.join(os.path.expanduser("~"), "Desktop")
    start = os.path.join(TEST, "Start") if TEST else os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Gnomewatch")
    os.makedirs(start, exist_ok=True)
    os.makedirs(desktop, exist_ok=True)
    make_shortcut(os.path.join(desktop, "Gnomewatch.lnk"), wscript, f'//B \\"{vbs}\\"'.replace('\\"', '""'), ico, DEST)
    make_shortcut(os.path.join(start, "Gnomewatch.lnk"), wscript, f'//B \\"{vbs}\\"'.replace('\\"', '""'), ico, DEST)
    progress(94, "Registering the uninstaller...")
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
        install(lambda v, m: None)
        if not TEST:
            launch()
    else:
        Setup(updating="--update" in sys.argv).run()
