"""Control panel for the sentinel (Tk). Runs in its own thread.

`S` is the sentinel module itself, so a setting changed here takes effect at once (they are read live)
and is saved to .env by S._set_setting. Three tabs: Status, Accounts, Settings.

Accounts = browser profiles. Each account stays logged in to Roblox inside its own Edge/Chrome profile;
we only keep a label + the profile folder name (accounts.json). No passwords or cookies are ever read or stored.
"""
import ctypes
import json
import math
import os
import queue
import subprocess
import time
import tkinter as tk
from tkinter import filedialog

GLASS = os.environ.get("GLASS", "1") == "1"        # frosted-glass panel (Windows 11); GLASS=0 for a solid panel
# pure black is what Windows renders as "see-through glass" once the frame is extended into the window
BG, CARD, EDGE = ("#000000" if GLASS else "#0e1116"), "#171b22", "#252b35"
TEXT, DIM, FAINT = "#e8ecf2", "#8a94a3", "#586171"
GREEN, BLUE, AMBER, RED = "#3ddc97", "#5b9dff", "#f5b942", "#ff6b6b"
GREEN_BG, GREEN_HI = "#12362a", "#184636"
RED_BG, RED_HI = "#3a1a1f", "#4b2229"
AMBER_BG, AMBER_HI = "#3a2f14", "#4a3b18"
BTN, BTN_HI = "#222833", "#2d3543"
F = "Segoe UI"
W = 344

PRESETS = {
    "Quiet":    {"POLL_SECONDS": 5.0, "PARK_CORES": 1, "IDLE_BACKOFF": True},
    "Balanced": {"POLL_SECONDS": 2.0, "PARK_CORES": 1, "IDLE_BACKOFF": True},
    "Sharp":    {"POLL_SECONDS": 1.0, "PARK_CORES": 2, "IDLE_BACKOFF": False},
}


def lerp(c1, c2, t):
    """Blend two #rrggbb colours (t = 0 gives c1, t = 1 gives c2)."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def rr(c, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
           x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class Btn(tk.Canvas):
    """Flat rounded button with a hover state."""
    def __init__(self, parent, text, cmd, w=96, h=30, fill=BTN, hover=BTN_HI, fg=TEXT, bg=BG, bold=False, size=9):
        super().__init__(parent, width=w, height=h, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.cmd, self.fill, self.hover = cmd, fill, hover
        self.cur, self.job = fill, None
        self.bg_item = rr(self, 1, 1, w - 1, h - 1, 9, fill=fill, outline="")
        self.txt = self.create_text(w / 2, h / 2, text=text, fill=fg, font=(F, size, "bold" if bold else "normal"))
        self.bind("<Enter>", lambda e: self.fade(self.hover))
        self.bind("<Leave>", lambda e: self.fade(self.fill))
        self.bind("<ButtonRelease-1>", lambda e: self.cmd())

    def fade(self, target, steps=5):
        """Ease the fill colour toward `target` over a few frames instead of snapping."""
        try:
            if self.job:
                self.after_cancel(self.job)
        except tk.TclError:
            return
        start = self.cur

        def step(i=1):
            try:
                self.cur = lerp(start, target, i / steps)
                self.itemconfig(self.bg_item, fill=self.cur)
                self.job = self.after(14, step, i + 1) if i < steps else None
            except tk.TclError:
                pass                                    # the button was destroyed mid-fade

        step()

    def style(self, fill, hover, fg):
        self.fill, self.hover = fill, hover
        if fill != self.cur and not self.job:
            self.fade(fill, steps=4)
        self.itemconfig(self.txt, fill=fg)

    def label(self, text):
        self.itemconfig(self.txt, text=text)


class Switch(tk.Canvas):
    """Small on/off slider."""
    def __init__(self, parent, on_change, bg=CARD):
        super().__init__(parent, width=44, height=24, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.on, self.cb, self.t, self.job = None, on_change, 0.0, None
        self.track = rr(self, 1, 1, 43, 23, 11, fill=BTN, outline="")
        self.knob = self.create_oval(3, 3, 21, 21, fill=TEXT, outline="")
        self.bind("<ButtonRelease-1>", lambda e: self.cb(not self.on))

    def draw(self, t):
        self.t = t
        x = 3 + 20 * t
        self.itemconfig(self.track, fill=lerp(BTN, GREEN, t))
        self.coords(self.knob, x, 3, x + 18, 21)

    def set(self, on):
        if on == self.on:
            return
        first = self.on is None
        self.on = on
        if first:
            self.draw(1.0 if on else 0.0)               # start in the right place without animating
            return
        try:
            if self.job:
                self.after_cancel(self.job)
        except tk.TclError:
            return
        start, end = self.t, (1.0 if on else 0.0)

        def step(i=1):
            try:
                k = i / 7
                k = k * k * (3 - 2 * k)                # smoothstep easing
                self.draw(start + (end - start) * k)
                self.job = self.after(16, step, i + 1) if i < 7 else None
            except tk.TclError:
                pass

        step()


def same(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return a == b


class Seg(tk.Frame):
    """Segmented control: a row of buttons, the current value highlighted."""
    def __init__(self, parent, choices, get, pick, seg_w=52, bg=CARD):
        super().__init__(parent, bg=bg)
        self.get, self.btns = get, []
        for label, value in choices:
            b = Btn(self, label, lambda v=value: pick(v), w=seg_w, h=26, bg=bg, size=8)
            b.pack(side="left", padx=2)
            self.btns.append((b, value))
        self.sync()

    def sync(self):
        cur = self.get()
        for b, v in self.btns:
            on = same(cur, v)
            b.style(GREEN_BG if on else BTN, GREEN_HI if on else BTN_HI, GREEN if on else TEXT)


# --------------------------------------------------------------------------- accounts (browser profiles)

def browser_exe(kind):
    rel = {"edge": r"Microsoft\Edge\Application\msedge.exe", "chrome": r"Google\Chrome\Application\chrome.exe"}[kind]
    for base in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""),
                 os.environ.get("LOCALAPPDATA", "")):
        p = os.path.join(base, rel)
        if base and os.path.exists(p):
            return p
    return None


def browser_profiles(kind):
    """[(folder, display name)] from the browser's own profile list. Names only - no logins."""
    root = {"edge": r"Microsoft\Edge\User Data", "chrome": r"Google\Chrome\User Data"}[kind]
    path = os.path.join(os.environ.get("LOCALAPPDATA", ""), root, "Local State")
    try:
        cache = json.load(open(path, encoding="utf-8"))["profile"]["info_cache"]
    except (OSError, ValueError, KeyError):
        return []
    return [(d, (info.get("name") or d)) for d, info in cache.items()]


def run(S):
    import win32process

    root = tk.Tk()
    root.title("Sentinel")
    root.configure(bg=BG)
    root.attributes("-topmost", True)
    root.resizable(False, False)
    root.geometry("+40+40")
    try:
        root.iconbitmap(S._gnome_ico())
    except Exception:
        pass

    accounts_file = os.path.join(S.DATA, "accounts.json")

    def dark_title(win):
        try:
            win.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            v = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), 4)   # dark title bar
        except Exception:
            pass

    def apply_glass(win):
        """Acrylic frosted glass behind the panel (Windows 11). Black areas of the window become see-through."""
        if not GLASS:
            return
        try:
            win.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            kind = ctypes.c_int(3)                                                    # 3 = acrylic, 2 = mica
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(kind), 4)   # system backdrop type

            class MARGINS(ctypes.Structure):
                _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]

            m = MARGINS(-1, -1, -1, -1)
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(m))
        except Exception:
            pass

    def get(name):
        return getattr(S, S._GLOBAL_OF.get(name, name))

    syncers = []                                  # callables run each tick so widgets follow the real state

    def label(parent, text, bg=BG, fg=DIM, size=9, bold=False, **kw):
        return tk.Label(parent, text=text, bg=bg, fg=fg, font=(F, size, "bold" if bold else "normal"), **kw)

    def section(parent, text):
        label(parent, text, fg=FAINT, size=8, bold=True, anchor="w").pack(fill="x", padx=16, pady=(12, 2))

    def box(parent):
        b = tk.Frame(parent, bg=CARD, highlightbackground=EDGE, highlightthickness=1)
        b.pack(fill="x", padx=14)
        return b

    def switch_row(parent, text, name, note=None):
        r = tk.Frame(parent, bg=CARD)
        r.pack(fill="x", padx=12, pady=5)
        col = tk.Frame(r, bg=CARD)
        col.pack(side="left")
        label(col, text, bg=CARD, fg=TEXT).pack(anchor="w")
        if note:
            label(col, note, bg=CARD, fg=FAINT, size=8).pack(anchor="w")
        sw = Switch(r, lambda v, n=name: S._set_setting(n, v))
        sw.pack(side="right")
        syncers.append(lambda: sw.set(bool(get(name))))

    # ------------------------------------------------------------------ log window (Show/Hide logs)
    logs = {"win": None, "text": None, "last": None}

    def show_logs():
        if logs["win"] is None:
            w = tk.Toplevel(root)
            w.title("Sentinel log")
            w.geometry("700x320+40+330")
            w.configure(bg=BG)
            w.attributes("-topmost", True)
            t = tk.Text(w, bg="#0a0d11", fg="#c5ccd6", font=("Consolas", 9), wrap="none", bd=0, padx=10, pady=8,
                        state="disabled", insertbackground=TEXT, selectbackground="#26324a", relief="flat")
            sb = tk.Scrollbar(w, command=t.yview, bg=BG, troughcolor=BG, bd=0, relief="flat")
            t.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            t.pack(side="left", fill="both", expand=True)
            for tag, colour in (("ok", GREEN), ("next", BLUE), ("start", AMBER), ("bad", RED), ("dim", FAINT)):
                t.tag_configure(tag, foreground=colour)
            w.protocol("WM_DELETE_WINDOW", w.withdraw)
            dark_title(w)
            logs.update(win=w, text=t, last=None)
        logs["win"].deiconify()
        logs["last"] = None

    def hide_logs():
        if logs["win"] is not None:
            logs["win"].withdraw()

    def toggle_panel():
        if root.state() == "withdrawn":
            root.deiconify()
        else:
            root.withdraw()

    # ------------------------------------------------------------------ header + tabs
    head = tk.Frame(root, bg=BG)
    head.pack(fill="x", padx=14, pady=(12, 2))
    label(head, "Sentinel", fg=TEXT, size=13, bold=True).pack(side="left")
    state_lbl = label(head, "", fg=GREEN)
    state_lbl.pack(side="right")
    mini_btn = Btn(head, "–", lambda: set_mini(True), w=28, h=24, fill=BG, hover=BTN, fg=DIM, size=11)
    mini_btn.pack(side="right", padx=(0, 10))

    tabbar = tk.Frame(root, bg=BG)
    tabbar.pack(padx=10, pady=(8, 4))
    pages, tabbtn = {}, {}
    current = ["Status"]

    def show(name):
        current[0] = name
        for n, f in pages.items():
            f.pack_forget()
        pages[name].pack(fill="both", before=foot)
        for n, b in tabbtn.items():
            on = n == name
            b.style(GREEN_BG if on else BG, GREEN_HI if on else BTN, GREEN if on else DIM)

    for n in ("Status", "Accounts", "Settings"):
        b = Btn(tabbar, n, lambda n=n: show(n), w=104, h=28, fill=BG, bold=True)
        b.pack(side="left", padx=3)
        tabbtn[n] = b
        pages[n] = tk.Frame(root, bg=BG)

    def confirm_button(parent, text, sure, action, w, h, bg=CARD):
        """A red button that needs two clicks (the second within 3 seconds) before it does anything."""
        b = Btn(parent, text, lambda: None, w=w, h=h, fill=RED_BG, hover=RED_HI, fg=RED, bg=bg, bold=True, size=8)
        armed = [False]

        def disarm():
            if armed[0] and b.winfo_exists():
                armed[0] = False
                b.label(text)
                b.style(RED_BG, RED_HI, RED)

        def click():
            if armed[0]:
                armed[0] = False
                b.label(text)
                action()
            else:
                armed[0] = True
                b.label(sure)
                b.style(RED, RED, BG)
                root.after(3000, disarm)

        b.cmd = click
        return b

    # ================================================================== STATUS
    st = pages["Status"]
    section(st, "ROBLOX WINDOWS")
    rows = tk.Frame(st, bg=BG)
    rows.pack(fill="x", padx=10)
    allrow = tk.Frame(st, bg=BG)
    allrow.pack(padx=10, pady=(6, 0))
    Btn(allrow, "Show all", lambda: S._set_show(True), w=150, h=30).pack(side="left", padx=4)
    Btn(allrow, "Hide all", lambda: S._set_show(False), w=150, h=30).pack(side="left", padx=4)
    extra = tk.Frame(st, bg=BG)
    extra.pack(padx=10, pady=(6, 0))
    Btn(extra, "Dashboard", S._open_dashboard, w=150, h=30, fill=GREEN_BG, hover=GREEN_HI, fg=GREEN,
        bold=True).pack(side="left", padx=4)
    Btn(extra, "New instance", S._launch_instance, w=150, h=30, fill=AMBER_BG, hover=AMBER_HI, fg=AMBER,
        bold=True).pack(side="left", padx=4)
    closerow = tk.Frame(st, bg=BG)
    closerow.pack(padx=10, pady=(6, 0))
    confirm_button(closerow, "Close all Roblox", "Sure? Click again", S._close_all, w=308, h=30,
                   bg=BG).pack(padx=4)

    section(st, "USAGE  (of one CPU core)")
    meterbox = box(st)
    meters = {}
    for key, text in (("sentinel", "Sentinel"), ("hidden", "Hidden Roblox"), ("other", "Other Roblox")):
        r = tk.Frame(meterbox, bg=CARD)
        r.pack(fill="x", padx=12, pady=4)
        label(r, text, bg=CARD, fg=TEXT, width=13, anchor="w").pack(side="left")
        c = tk.Canvas(r, width=120, height=10, bg=CARD, highlightthickness=0, bd=0)
        c.pack(side="left", padx=(0, 8))
        rr(c, 0, 0, 120, 10, 5, fill=BTN, outline="")
        bar = c.create_rectangle(0, 0, 3, 10, fill=GREEN, width=0)
        val = label(r, "–", bg=CARD, fg=DIM, size=8, width=17, anchor="e")
        val.pack(side="right")
        meters[key] = (c, bar, val)

    section(st, "LOGS")
    logrow = tk.Frame(st, bg=BG)
    logrow.pack(padx=10)
    Btn(logrow, "Show logs", show_logs, w=150, h=30).pack(side="left", padx=4)
    Btn(logrow, "Hide logs", hide_logs, w=150, h=30).pack(side="left", padx=4)

    # ================================================================== ACCOUNTS
    ac = pages["Accounts"]

    def load_accounts():
        try:
            return json.load(open(accounts_file, encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def save_accounts(a):
        try:
            json.dump(a, open(accounts_file, "w", encoding="utf-8"), indent=1)
        except OSError as e:
            print("couldn't save accounts:", e)

    note = {"text": ""}

    def launch_account(a):
        exe = browser_exe(a["browser"])
        url = S._launch_url()
        if not exe or not url:
            note["text"] = "Browser or launch link not found"
            return
        try:
            subprocess.Popen([exe, f"--profile-directory={a['profile']}", url])
            note["text"] = f"Opening as {a['label']}…"
            print(f"account: launching as '{a['label']}' ({a['browser']} / {a['profile']})")
        except OSError as e:
            note["text"] = f"Couldn't launch: {e}"

    def remove_account(i):
        a = load_accounts()
        if 0 <= i < len(a):
            a.pop(i)
            save_accounts(a)

    section(ac, "YOUR ACCOUNTS")
    acct_rows = tk.Frame(ac, bg=BG)
    acct_rows.pack(fill="x", padx=10)
    acct_state = {"sig": None}

    def rebuild_accounts():
        a = load_accounts()
        sig = json.dumps(a)
        if sig == acct_state["sig"]:
            return
        acct_state["sig"] = sig
        for ch in acct_rows.winfo_children():
            ch.destroy()
        if not a:
            c = tk.Canvas(acct_rows, width=W - 4, height=50, bg=BG, highlightthickness=0, bd=0)
            rr(c, 1, 1, W - 5, 49, 12, fill=CARD, outline=EDGE)
            c.create_text((W - 4) / 2, 25, text="No accounts yet – add one below", fill=DIM, font=(F, 9))
            c.pack(pady=3)
        for i, acct in enumerate(a):
            c = tk.Canvas(acct_rows, width=W - 4, height=54, bg=BG, highlightthickness=0, bd=0)
            rr(c, 1, 1, W - 5, 53, 12, fill=CARD, outline=EDGE)
            c.create_oval(16, 21, 26, 31, fill=BLUE, outline="")
            c.create_text(38, 19, anchor="w", text=acct["label"], fill=TEXT, font=(F, 10, "bold"))
            c.create_text(38, 36, anchor="w", text=f"{acct['browser'].title()} · {acct['profile']}", fill=DIM, font=(F, 8))
            c.create_window(W - 4 - 92, 27, window=Btn(c, "Launch", lambda x=acct: launch_account(x), w=70, h=28,
                                                        fill=GREEN_BG, hover=GREEN_HI, fg=GREEN, bg=CARD, bold=True))
            c.create_window(W - 4 - 28, 27, window=Btn(c, "×", lambda k=i: remove_account(k), w=28, h=28, bg=CARD, size=11))
            c.pack(pady=3)

    section(ac, "ADD AN ACCOUNT")
    form = box(ac)
    fr = tk.Frame(form, bg=CARD)
    fr.pack(fill="x", padx=12, pady=(8, 4))
    label(fr, "Name", bg=CARD, fg=TEXT, width=8, anchor="w").pack(side="left")
    name_var = tk.StringVar()
    tk.Entry(fr, textvariable=name_var, bg=BTN, fg=TEXT, insertbackground=TEXT, relief="flat", font=(F, 9),
             width=26).pack(side="left", ipady=4)

    fr2 = tk.Frame(form, bg=CARD)
    fr2.pack(fill="x", padx=12, pady=4)
    label(fr2, "Browser", bg=CARD, fg=TEXT, width=8, anchor="w").pack(side="left")
    kind_var = tk.StringVar(value="edge" if browser_exe("edge") else "chrome")

    def style_menu(m):
        m.config(bg=BTN, fg=TEXT, activebackground=BTN_HI, activeforeground=TEXT, relief="flat", bd=0,
                 highlightthickness=0, font=(F, 9), anchor="w")
        m["menu"].config(bg=BTN, fg=TEXT, activebackground=BTN_HI, activeforeground=TEXT, bd=0, font=(F, 9))

    prof_choices = {}
    prof_var = tk.StringVar(value="")
    fr3 = tk.Frame(form, bg=CARD)
    fr3.pack(fill="x", padx=12, pady=4)
    label(fr3, "Profile", bg=CARD, fg=TEXT, width=8, anchor="w").pack(side="left")
    prof_menu = tk.OptionMenu(fr3, prof_var, "")
    style_menu(prof_menu)
    prof_menu.pack(side="left", ipady=2)

    def fill_profiles(*_):
        menu = prof_menu["menu"]
        menu.delete(0, "end")
        prof_choices.clear()
        found = browser_profiles(kind_var.get())
        for d, n in found:
            text = f"{n}  ({d})"
            prof_choices[text] = d
            menu.add_command(label=text, command=lambda t=text: prof_var.set(t))
        prof_var.set(next(iter(prof_choices), "no profiles found"))

    kind_menu = tk.OptionMenu(fr2, kind_var, "edge", "chrome", command=fill_profiles)
    style_menu(kind_menu)
    kind_menu.pack(side="left", ipady=2)

    fill_profiles()

    def add_account():
        nm = name_var.get().strip()
        d = prof_choices.get(prof_var.get())
        if not nm or not d:
            note["text"] = "Give it a name and pick a profile"
            return
        a = load_accounts()
        a.append({"label": nm, "browser": kind_var.get(), "profile": d})
        save_accounts(a)
        name_var.set("")
        note["text"] = f"Added {nm}"

    Btn(form, "Add account", add_account, w=W - 60, h=30, fill=GREEN_BG, hover=GREEN_HI, fg=GREEN, bg=CARD,
        bold=True).pack(pady=(6, 8))
    ac_note = label(ac, "", fg=AMBER, size=8, wraplength=W - 20)
    ac_note.pack(pady=(6, 0))
    label(ac, "Each profile must already be signed in to Roblox in that browser.\nOnly the name and profile "
              "folder are saved – never a password.", fg=FAINT, size=8, justify="left",
          wraplength=W - 24).pack(padx=16, pady=(4, 6), anchor="w")

    # ================================================================== SETTINGS (scrollable)
    se = pages["Settings"]
    canvas = tk.Canvas(se, bg=BG, highlightthickness=0, bd=0, width=W, height=460)
    inner = tk.Frame(canvas, bg=BG)
    canvas.create_window(0, 0, window=inner, anchor="nw", width=W)
    canvas.pack()
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

    def wheel(e):
        if current[0] == "Settings":
            canvas.yview_scroll(int(-e.delta / 120) * 2, "units")

    root.bind_all("<MouseWheel>", wheel)

    # ---- performance
    section(inner, "PERFORMANCE")
    pb = box(inner)
    pr = tk.Frame(pb, bg=CARD)
    pr.pack(fill="x", padx=12, pady=(8, 2))
    label(pr, "Preset", bg=CARD, fg=TEXT).pack(anchor="w")
    preset_btns = {}
    prow = tk.Frame(pb, bg=CARD)
    prow.pack(fill="x", padx=10, pady=(2, 6))

    def apply_preset(nm):
        for k, v in PRESETS[nm].items():
            S._set_setting(k, v)

    for nm in PRESETS:
        b = Btn(prow, nm, lambda n=nm: apply_preset(n), w=92, h=28, bg=CARD, bold=True)
        b.pack(side="left", padx=2)
        preset_btns[nm] = b

    def sync_presets():
        for nm, vals in PRESETS.items():
            on = all(same(get(k), v) for k, v in vals.items())
            preset_btns[nm].style(GREEN_BG if on else BTN, GREEN_HI if on else BTN_HI, GREEN if on else TEXT)

    syncers.append(sync_presets)

    r = tk.Frame(pb, bg=CARD)
    r.pack(fill="x", padx=12, pady=(6, 2))
    label(r, "Read speed", bg=CARD, fg=TEXT).pack(anchor="w")
    label(r, "How often it looks at the game", bg=CARD, fg=FAINT, size=8).pack(anchor="w")
    sp = Seg(pb, [("1 s", 1.0), ("2 s", 2.0), ("5 s", 5.0)], lambda: get("POLL_SECONDS"),
             lambda v: S._set_setting("POLL_SECONDS", v), seg_w=92)
    sp.pack(anchor="w", padx=10, pady=(2, 6))
    syncers.append(sp.sync)

    r = tk.Frame(pb, bg=CARD)
    r.pack(fill="x", padx=12, pady=(4, 2))
    label(r, "Hidden Roblox may use", bg=CARD, fg=TEXT).pack(anchor="w")
    label(r, "CPU cores while parked (fewer = cooler, slower game)", bg=CARD, fg=FAINT, size=8).pack(anchor="w")
    cs = Seg(pb, [("All", 0), ("1", 1), ("2", 2), ("3", 3), ("4", 4)], lambda: get("PARK_CORES"),
             lambda v: S._set_setting("PARK_CORES", v), seg_w=52)
    cs.pack(anchor="w", padx=10, pady=(2, 6))
    syncers.append(cs.sync)
    switch_row(pb, "Low CPU for hidden Roblox", "THROTTLE_PARKED", "Lower priority + efficiency mode")
    switch_row(pb, "Slow down when board is away", "IDLE_BACKOFF", "Looks less often if it can't see the sign")

    # ---- alerts
    section(inner, "ALERTS")
    ab = box(inner)
    switch_row(ab, "Alerts", "NOTIFY", "Master switch for this PC")
    switch_row(ab, "Sound", "NOTIFY_SOUND")
    switch_row(ab, "Pop-up notification", "NOTIFY_TOAST")
    switch_row(ab, "\"Next event\" notice", "NOTIFY_NEXT", "Also alert when a new next event appears")
    label(ab, "Alert me for these events", bg=CARD, fg=TEXT).pack(anchor="w", padx=12, pady=(8, 0))
    evbox = tk.Frame(ab, bg=CARD)
    evbox.pack(fill="x")
    ev_state = {"sig": None}
    ev_syncers = []

    def rebuild_events():
        names = tuple(sorted(S.KNOWN))
        if names == ev_state["sig"]:
            return
        ev_state["sig"] = names
        ev_syncers.clear()
        for ch in evbox.winfo_children():
            ch.destroy()
        for n in names:
            rw = tk.Frame(evbox, bg=CARD)
            rw.pack(fill="x", padx=12, pady=3)
            glyph, colour, _emoji = S._event_style(n)
            tk.Label(rw, text=glyph, bg=CARD, fg=colour, font=("Segoe UI Symbol", 11)).pack(side="left", padx=(8, 4))
            label(rw, n.title(), bg=CARD, fg=TEXT, size=9).pack(side="left")
            sw = Switch(rw, lambda v, k=n: S._set_alert(k, v))
            sw.pack(side="right")
            sw.set(n not in S.ALERT_OFF)
            ev_syncers.append(lambda s_=sw, k=n: s_.set(k not in S.ALERT_OFF))

    # ---- windows
    section(inner, "WINDOWS")
    wb = box(inner)
    switch_row(wb, "Hide the board window", "HIDE_WINDOW")
    switch_row(wb, "Hide all instances", "HIDE_ALL", "Every Roblox window, not just the board's")
    switch_row(wb, "Clean up Roblox helpers", "CLEAN_HELPERS", "Closes window-less tray launchers")

    def entry_row(parent, text, name):
        rw = tk.Frame(parent, bg=CARD)
        rw.pack(fill="x", padx=12, pady=5)
        label(rw, text, bg=CARD, fg=TEXT, width=14, anchor="w").pack(side="left")
        var = tk.StringVar(value=str(get(name)))
        e = tk.Entry(rw, textvariable=var, bg=BTN, fg=TEXT, insertbackground=TEXT, relief="flat", font=(F, 9), width=18)
        e.pack(side="left", ipady=3)

        def commit(_=None):
            if var.get().strip() != str(get(name)):
                S._set_setting(name, var.get().strip())

        e.bind("<Return>", commit)
        e.bind("<FocusOut>", commit)
        def sync_entry():
            try:
                typing = root.focus_get() is e
            except KeyError:
                typing = False
            if not typing and var.get() != str(get(name)):
                var.set(str(get(name)))

        syncers.append(sync_entry)

    entry_row(wb, "Board window", "WINDOW_TITLE")
    entry_row(wb, "Other windows", "OTHER_TITLE")

    ir = tk.Frame(wb, bg=CARD)
    ir.pack(fill="x", padx=12, pady=5)
    label(ir, "Window icon", bg=CARD, fg=TEXT, width=14, anchor="w").pack(side="left")
    icon_var = tk.StringVar()
    ICON_G, ICON_N, ICON_C = "Gnome (built-in)", "Roblox's own", "Choose a file…"

    def icon_pick(choice):
        if choice == ICON_G:
            S._set_setting("ROBLOX_ICON", "gnome")
        elif choice == ICON_N:
            S._set_setting("ROBLOX_ICON", "")
        else:
            p = filedialog.askopenfilename(parent=root, title="Choose an .ico file", filetypes=[("Icon", "*.ico")])
            if p:
                S._set_setting("ROBLOX_ICON", p)

    icon_menu = tk.OptionMenu(ir, icon_var, ICON_G, ICON_N, ICON_C, command=icon_pick)
    style_menu(icon_menu)
    icon_menu.config(width=16)
    icon_menu.pack(side="left", ipady=1)

    def sync_icon():
        cur = S.ROBLOX_ICON
        icon_var.set(ICON_G if cur.lower() == "gnome" else ICON_N if not cur else os.path.basename(cur))

    syncers.append(sync_icon)

    # ---- keep-alive
    section(inner, "STAY CONNECTED")
    kb = box(inner)
    switch_row(kb, "Click to stay connected", "KEEPALIVE", "Clicks the middle of each Roblox window")
    label(kb, "How often", bg=CARD, fg=TEXT).pack(anchor="w", padx=12, pady=(4, 0))
    kseg = Seg(kb, [("5 min", 5.0), ("10 min", 10.0), ("15 min", 15.0)], lambda: get("KEEPALIVE_MINUTES"),
               lambda v: S._set_setting("KEEPALIVE_MINUTES", v), seg_w=92)
    kseg.pack(anchor="w", padx=10, pady=(2, 4))
    syncers.append(kseg.sync)
    label(kb, "Waits until you've been idle a few seconds, brings a hidden window\nback for about a second, clicks, then hides it again.",
          bg=CARD, fg=FAINT, size=8, justify="left").pack(padx=12, pady=(2, 8), anchor="w")

    # ---- hotkeys
    section(inner, "HOTKEYS")
    hb = box(inner)

    def capture_hotkey(name, what):
        w = tk.Toplevel(root)
        w.title("Set hotkey")
        w.configure(bg=BG)
        w.attributes("-topmost", True)
        w.geometry(f"+{root.winfo_rootx() + 30}+{root.winfo_rooty() + 120}")
        label(w, f"Press the keys for “{what}”", fg=TEXT, size=10, bold=True).pack(padx=24, pady=(18, 4))
        msg = label(w, "Hold Ctrl / Shift / Alt, then press a key.  Esc cancels.", fg=DIM, size=8)
        msg.pack(padx=24, pady=(0, 18))
        dark_title(w)
        w.focus_force()
        w.grab_set()

        def on_key(e):
            ks = e.keysym.lower()
            if ks in ("shift_l", "shift_r", "control_l", "control_r", "alt_l", "alt_r", "win_l", "win_r", "caps_lock"):
                return
            if ks == "escape":
                w.destroy()
                return
            mods = (["ctrl"] if e.state & 0x4 else []) + (["alt"] if e.state & 0x20000 else []) + \
                   (["shift"] if e.state & 0x1 else [])
            if not mods:
                msg.config(text="Add Ctrl, Shift or Alt too – a bare key would fire while you type", fg=AMBER)
                return
            key = ks if (len(ks) == 1 or (ks.startswith("f") and ks[1:].isdigit())) else None
            if key is None:
                msg.config(text="Use a letter, a number or an F-key", fg=AMBER)
                return
            combo = "+".join(mods + [key])
            w.destroy()
            S._set_setting(name, combo)

        w.bind("<KeyPress>", on_key)

    for text, name in (("Show / hide Roblox", "HOTKEY"), ("Show / hide this panel", "PANEL_HOTKEY")):
        rw = tk.Frame(hb, bg=CARD)
        rw.pack(fill="x", padx=12, pady=5)
        label(rw, text, bg=CARD, fg=TEXT).pack(side="left")
        b = Btn(rw, get(name), lambda n=name, t=text: capture_hotkey(n, t), w=132, h=26, bg=CARD, size=8, bold=True)
        b.pack(side="right")
        syncers.append(lambda b_=b, n=name: b_.label(str(get(n))))

    # ---- launch
    section(inner, "NEW INSTANCE OPENS")
    lb = box(inner)
    ls = Seg(lb, [("Game page", "game"), ("Private server", "private")], lambda: get("LAUNCH_TARGET"),
             lambda v: S._set_setting("LAUNCH_TARGET", v), seg_w=136)
    ls.pack(padx=10, pady=(8, 2))
    label(lb, "The game page lets you pick an account first;\nthe private server joins straight away.",
          bg=CARD, fg=FAINT, size=8, justify="left").pack(padx=12, pady=(2, 8), anchor="w")
    syncers.append(ls.sync)

    Btn(inner, "Open settings file (.env)", lambda: os.startfile(S._env_path), w=W - 30, h=28,
        size=8).pack(pady=(14, 14))

    # ------------------------------------------------------------------ footer
    foot = tk.Frame(root, bg=BG)
    foot.pack(fill="x", padx=14, pady=(8, 10))
    hint = label(foot, "", fg=FAINT, size=8)
    hint.pack(side="left")
    Btn(foot, "Hide panel", toggle_panel, w=84, h=26, fill=BG, hover=BTN, fg=DIM).pack(side="right")

    # ---- mini mode: just the current event and its countdown
    mini = tk.Frame(root, bg=BG)
    tk.Frame(mini, width=W - 14, height=1, bg=BG).pack()
    mrow = tk.Frame(mini, bg=BG)
    mrow.pack(fill="x", padx=12, pady=(4, 10))
    mglyph = tk.Label(mrow, text="✦", bg=BG, fg=GREEN, font=("Segoe UI Symbol", 20))
    mglyph.pack(side="left", padx=(0, 10))
    mcol = tk.Frame(mrow, bg=BG)
    mcol.pack(side="left")
    mname = label(mcol, "Watching", fg=TEXT, size=11, bold=True)
    mname.pack(anchor="w")
    msub = label(mcol, "", fg=DIM, size=8)
    msub.pack(anchor="w")
    Btn(mrow, "▢", lambda: set_mini(False), w=28, h=24, fill=BG, hover=BTN, fg=DIM, size=10).pack(side="right")
    mtime = tk.Label(mrow, text="--:--", bg=BG, fg=TEXT, font=(F, 18, "bold"))
    mtime.pack(side="right", padx=(0, 10))
    mini_on = [False]

    def set_mini(on, save=True):
        mini_on[0] = on
        if on:
            for w_ in (head, tabbar, pages[current[0]], foot):
                w_.pack_forget()
            mini.pack(fill="x")
        else:
            mini.pack_forget()
            head.pack(fill="x", padx=14, pady=(12, 2))
            tabbar.pack(padx=10, pady=(8, 4))
            foot.pack(fill="x", padx=14, pady=(8, 10))
            show(current[0])
        if save:
            S._set_setting("PANEL_MINI", on)

    def mini_tick():
        kind, name, left = S._event_summary()
        if S.PAUSED.is_set():
            mglyph.config(text="❚❚", fg=AMBER)
            mname.config(text="Paused")
            msub.config(text="not reading the game")
            mtime.config(text="--:--", fg=DIM)
        elif kind == "none":
            mglyph.config(text="✦", fg=GREEN)
            mname.config(text="Watching")
            msub.config(text="no event on the sign")
            mtime.config(text="--:--", fg=DIM)
        else:
            glyph, colour, _emoji = S._event_style(name)
            mglyph.config(text=glyph, fg=colour)
            mname.config(text=name.title())
            msub.config(text="ACTIVE  ·  time left" if kind == "active" else "NEXT  ·  starts in", fg=colour)
            mtime.config(text=S._fmt_left(left), fg=colour)
        root.after(250, mini_tick)

    pulse = {"mode": "static", "colour": GREEN}

    def pulse_loop():
        """Gentle breathing on the status dot while everything is fine."""
        if pulse["mode"] == "live":
            k = (math.sin(time.time() * 2.6) + 1) / 2
            state_lbl.config(fg=lerp(lerp(BG if BG != "#000000" else "#0e1116", pulse["colour"], 0.45), pulse["colour"], k))
        root.after(80, pulse_loop)

    root.protocol("WM_DELETE_WINDOW", root.withdraw)
    show("Status")
    if get("PANEL_MINI"):
        set_mini(True, save=False)
    root.update_idletasks()
    dark_title(root)
    apply_glass(root)
    shown = {"sig": None}

    # ------------------------------------------------------------------ window cards (Status tab)
    def card(hwnd, role, title, hidden, mini):
        c = tk.Canvas(rows, width=W - 4, height=54, bg=BG, highlightthickness=0, bd=0)
        rr(c, 1, 1, W - 5, 53, 12, fill=CARD, outline=EDGE)
        is_board = role == "board"
        dot = AMBER if mini else (GREEN if is_board else BLUE)
        c.create_oval(16, 21, 26, 31, fill=dot, outline="")
        c.create_text(38, 19, anchor="w", text=("Weather board" if is_board else "Roblox") + " window",
                      fill=TEXT, font=(F, 10, "bold"))
        state = "minimised" if mini else ("hidden" if hidden else "visible")
        c.create_text(38, 36, anchor="w", text=f"{(title or '?')[:16]}  ·  {state}", fill=DIM, font=(F, 8))
        if mini:
            b = Btn(c, "Restore", lambda h=hwnd: ctypes.windll.user32.ShowWindowAsync(h, 9), w=74, h=28,
                    fill=AMBER_BG, hover=AMBER_HI, fg=AMBER, bg=CARD, bold=True)
        elif hidden:
            b = Btn(c, "Show", lambda h=hwnd: S._set_window_show(h, True), w=74, h=28,
                    fill=GREEN_BG, hover=GREEN_HI, fg=GREEN, bg=CARD, bold=True)
        else:
            b = Btn(c, "Hide", lambda h=hwnd: S._set_window_show(h, False), w=74, h=28, bg=CARD)
        c.create_window(W - 4 - 12 - 37, 27, window=b)
        c.create_window(W - 4 - 12 - 74 - 6 - 24, 27,
                        window=confirm_button(c, "Close", "Sure?", lambda h=hwnd: S._close_window(h), w=48, h=28))
        c.pack(pady=3)

    def rebuild_windows():
        snap = list(S._snapshot)
        sig = tuple(snap)
        if sig == shown["sig"]:
            return
        shown["sig"] = sig
        for ch in rows.winfo_children():
            ch.destroy()
        if not snap:
            c = tk.Canvas(rows, width=W - 4, height=54, bg=BG, highlightthickness=0, bd=0)
            rr(c, 1, 1, W - 5, 53, 12, fill=CARD, outline=EDGE)
            c.create_text((W - 4) / 2, 27, text="No Roblox window found", fill=DIM, font=(F, 9))
            c.pack(pady=3)
        for item in snap:
            card(*item)

    # ------------------------------------------------------------------ usage meter
    import psutil
    procs = {}
    meter_err = []
    last_meter = [0.0]
    ncpu = psutil.cpu_count() or 1

    def proc(pid):
        p = procs.get(pid)
        if p is None:
            try:
                p = procs[pid] = psutil.Process(pid)
                p.cpu_percent(None)
            except psutil.Error:
                return None
        return p

    def group_cpu(pids):
        tot = 0.0
        for pid in pids:
            p = proc(pid)
            if p is None:
                continue
            try:
                tot += p.cpu_percent(None)
            except psutil.Error:
                procs.pop(pid, None)
        return tot

    def update_meters():
        if time.time() - last_meter[0] < 2.0:
            return
        last_meter[0] = time.time()
        hidden, other = set(), set()
        for hwnd, role, title, is_hidden, mini in list(S._snapshot):
            try:
                pid = win32process.GetWindowThreadProcessId(hwnd)[1]
            except Exception:
                continue
            (hidden if is_hidden else other).add(pid)
        vals = {"sentinel": group_cpu([os.getpid()]), "hidden": group_cpu(hidden), "other": group_cpu(other)}
        for key, v in vals.items():
            c, bar, lbl = meters[key]
            frac = min(1.0, v / 400.0)
            c.coords(bar, 0, 0, max(3, int(120 * frac)), 10)
            c.itemconfig(bar, fill=GREEN if v < 120 else AMBER if v < 250 else RED)
            lbl.config(text=f"{v:.0f}%  ({v / ncpu:.1f}% PC)")

    # ------------------------------------------------------------------ tick
    def tick():
        if S.QUIT.is_set():
            root.destroy()
            return
        try:
            while True:
                cmd = S.PANEL_Q.get_nowait()
                if cmd == "toggle":
                    toggle_panel()
                elif cmd == "mini":
                    set_mini(not mini_on[0])
        except queue.Empty:
            pass
        S._apply_visibility()                  # auto-detect windows even if the main loop is busy
        if S.PAUSED.is_set():
            pulse["mode"] = "static"
            state_lbl.config(text="❚❚ Paused", fg=AMBER)
        elif S.MUTED.is_set():
            pulse["mode"] = "static"
            state_lbl.config(text="● Muted", fg=DIM)
        else:
            kind_, name_, _left = S._event_summary()
            pulse["mode"] = "live"
            pulse["colour"] = S._event_style(name_)[1] if kind_ == "active" else GREEN
            state_lbl.config(text="● " + (name_.title() + " on" if kind_ == "active" else "Watching"))
        hint.config(text=f"{S.HOTKEY} · {S.PANEL_HOTKEY}")
        ac_note.config(text=note["text"])
        rebuild_windows()
        rebuild_accounts()
        rebuild_events()
        for fn in syncers + ev_syncers:
            try:
                fn()
            except tk.TclError:
                pass
        try:
            update_meters()
        except Exception as e:
            if not meter_err:
                meter_err.append(1)
                print("usage meter error:", repr(e))
        w = logs["win"]
        if w is not None and w.state() != "withdrawn":
            text = S._tail(S.LOG_PATH)
            if text != logs["last"]:
                logs["last"] = text
                t = logs["text"]
                t.configure(state="normal")
                t.delete("1.0", "end")
                for line in text.splitlines(True):
                    low = line.lower()
                    tag = ("bad" if ("error" in low or "!!" in low or "couldn't" in low or "unknown" in low)
                           else "ok" if " active" in low else "start" if " start " in low
                           else "next" if " next " in low else "dim" if "parked" in low or "roblox window" in low else "")
                    t.insert("end", line, tag)
                t.see("end")
                t.configure(state="disabled")
        root.after(700, tick)

    pulse_loop()
    mini_tick()
    tick()
    root.mainloop()
