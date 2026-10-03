"""Help page: how to get going, how each feature works, what to do when something is wrong, shortcuts, and about.
Everything here is read-only text plus buttons that jump to the right place (setup guide, health check, Discord)."""
import os
import subprocess
import threading
import time
import tkinter as tk
import webbrowser

import ui_kit as U
from ui_kit import K, font, lerp
import cs_common as C
from cs_common import CW, BODY_H, SCROLL_W

COLW = (SCROLL_W - 16) // 2
INNER = COLW - 40


def build(ctx):
    S, root, theme, get = ctx.S, ctx.root, ctx.theme, ctx.get
    page = ctx.pages["Help"]
    sf = U.ScrollFrame(page, CW, BODY_H, bg=K.BG, theme=theme)
    sf.pack(anchor="w")
    ctx.scrollers.append(sf)
    cols = tk.Frame(sf.inner, bg=K.BG)
    cols.pack(anchor="w")
    colL = tk.Frame(cols, bg=K.BG)
    colL.pack(side="left", anchor="n")
    colR = tk.Frame(cols, bg=K.BG)
    colR.pack(side="left", anchor="n", padx=(16, 0))
    tk.Frame(sf.inner, bg=K.BG, height=24).pack()

    def section(parent, title, icon):
        c = U.AutoCard(parent, COLW, title=title, icon=icon)
        c.pack(pady=(0, 16))
        return c.inner

    def para(parent, text, fg=None, pady=(0, 8)):
        tk.Label(parent, text=text, bg=K.CARD, fg=fg or K.DIM, font=font(13), wraplength=INNER, justify="left", anchor="w").pack(fill="x", pady=pady)

    def step(parent, n, title, text):
        r = tk.Frame(parent, bg=K.CARD)
        r.pack(fill="x", pady=(0, 10))
        dot = tk.Canvas(r, width=28, height=28, bg=K.CARD, highlightthickness=0, bd=0)
        dot.create_image(14, 14, image=U.circle_photo(24, lerp(K.CARD, theme.cur, .22), theme.cur, 2))
        dot.create_text(14, 14, text=str(n), fill=theme.cur, font=font(11, "bold"))
        dot.pack(side="left", anchor="n", padx=(0, 12))
        col = tk.Frame(r, bg=K.CARD)
        col.pack(side="left", fill="x")
        tk.Label(col, text=title, bg=K.CARD, fg=K.TEXT, font=font(14, "semibold"), anchor="w").pack(anchor="w")
        tk.Label(col, text=text, bg=K.CARD, fg=K.DIM, font=font(12), wraplength=INNER - 44, justify="left", anchor="w").pack(anchor="w")

    def qa(parent, q, a):
        tk.Label(parent, text=q, bg=K.CARD, fg=K.TEXT, font=font(13, "semibold"), wraplength=INNER, justify="left", anchor="w").pack(fill="x", pady=(6, 0))
        tk.Label(parent, text=a, bg=K.CARD, fg=K.DIM, font=font(12), wraplength=INNER, justify="left", anchor="w").pack(fill="x", pady=(1, 4))

    def btn_row(parent, *items):
        r = tk.Frame(parent, bg=K.CARD)
        r.pack(fill="x", pady=(6, 4))
        for label, icon, fn, kind in items:
            U.Btn(r, label, fn, theme, icon=icon, kind=kind, h=34, size=12, bg=K.CARD).pack(side="left", padx=(0, 8))

    # ================================================================== getting started
    gs = section(colL, "Getting started", "rocket")
    step(gs, 1, "Add your account", "Accounts > Add. Pick the Edge or Chrome profile that is signed in to Roblox. Gnomewatch never asks for your password.")
    step(gs, 2, "Choose where to join", "Settings > New instance opens. A fresh install opens Roll A Gnome; add a private server link if you use one.")
    step(gs, 3, "Launch it", "The play button on an account opens Roblox in that profile. Launch several and they tile and stay light.")
    step(gs, 4, "Teach it your move", "Auto-place > Record under an event (START), do your clicks once, then press Ctrl+Shift+F8. It replays when that event starts.")
    step(gs, 5, "Switch it on", "On the event's card, pick the account and turn the switch on. Use the eye (dry run) first to see where it will click.")
    btn_row(gs, ("Open the setup guide", "rocket", lambda: ctx.open_wizard(), "primary"), ("Go to Auto-place", "wand-sparkles", lambda: ctx.show("Auto-place"), "secondary"))

    # ================================================================== auto-place
    ap = section(colL, "Auto-place, in short", "wand-sparkles")
    qa(ap, "Record, Test, eye, arrow", "Record captures your clicks and keys. Test replays them for real. The eye is a dry run: it only shows where it would click. "
           "The arrow copies a share code.")
    qa(ap, "Dry run", "With the account's window open it draws numbered markers over the game, in time, without clicking anything. With no window it shows a preview.")
    qa(ap, "Stop everything", "Press your panic key (Settings) or Stop on the red bar. A replay never keeps a key or button pressed.")
    qa(ap, "It says 'nothing on screen changed'", "Gnomewatch checks the game reacted. If your move legitimately changes nothing, turn off Check it worked on the Auto-place page.")

    # ================================================================== sharing
    sh = section(colL, "Sharing macros", "upload")
    para(sh, "Click the arrow on an event's recording and a code is copied. Send it in Discord or anywhere; it is one line starting with GW2-.")
    para(sh, "To use a friend's, press Import a macro on the Auto-place page and paste it. You see what it does first, it arrives switched off, "
             "and it only holds key presses and clicks. Never your account or settings.")
    qa(sh, "My friend's code won't import", "Copy the whole code, from GW2- to the end. It can sit inside a message or in backticks. Codes from version 1.1.0 (GW1-) still work.")
    qa(sh, "The event isn't on my Auto-place page", "Importing adds that event for you, so a card appears. Pick an account on it, then switch it on.")

    # ================================================================== fixing problems
    fx = section(colR, "Something isn't working", "heart-pulse")
    para(fx, "The health check finds the usual causes (launch link, browser, profile, key, key server) and offers a fix.")
    btn_row(fx, ("Run the checks", "heart-pulse", lambda: (ctx.show("Settings"), root.after(250, lambda: ctx.run_health())), "primary"),
            ("Save a picture of this window", "image", lambda: save_picture(), "secondary"))
    note = tk.Label(fx, text="", bg=K.CARD, fg=K.FAINT, font=font(11), wraplength=INNER, justify="left", anchor="w")
    note.pack(fill="x")
    qa(fx, "Nothing opens when I launch", "Run the checks. The most common cause is no launch link, or a browser profile that was removed.")
    qa(fx, "'Gnomewatch is already running'", "Another copy is still alive, maybe hidden. Choose Yes in the box that appears and it closes the old one.")
    qa(fx, "It says my key ended", "Keys last 24 hours. Run /getkey in the Discord channel and paste the new one. Your settings are kept.")
    qa(fx, "The window looks wrong", "Save a picture of this window (button above) and send it, with what you pressed. That's the quickest way to get it fixed.")
    qa(fx, "Roblox uses too much memory", "Set a Memory cap on the account (Accounts > edit). Over the cap, idle memory is handed back to Windows.")

    # ================================================================== shortcuts
    sc = section(colR, "Shortcuts", "keyboard")
    sc_rows = {}

    def key_row(title, name, fixed=None):
        r = tk.Frame(sc, bg=K.CARD)
        r.pack(fill="x", pady=3)
        tk.Label(r, text=title, bg=K.CARD, fg=K.DIM, font=font(13)).pack(side="left")
        v = tk.Label(r, text=fixed or "", bg=K.CARD2, fg=K.TEXT, font=font(12, "semibold"), padx=10, pady=3)
        v.pack(side="right")
        if name:
            sc_rows[name] = v

    key_row("Command bar", None, "Ctrl + K")
    key_row("Stop a recording", None, "Ctrl + Shift + F8")
    key_row("Command palette (anywhere)", "PALETTE_HOTKEY")
    key_row("Show / hide the console", "PANEL_HOTKEY")
    key_row("Panic: hide everything", "PANIC_HOTKEY")
    key_row("Tile the Roblox windows", "GRID_HOTKEY")
    para(sc, "Change these in Settings > Hotkeys.", fg=K.FAINT, pady=(6, 0))

    # ================================================================== about
    ab = section(colR, "About", "info")
    about = tk.Label(ab, text="", bg=K.CARD, fg=K.DIM, font=font(13), wraplength=INNER, justify="left", anchor="w")
    about.pack(fill="x", pady=(0, 8))
    invite = ""
    try:
        import gw_license as L
        invite = L.config(S.HERE).get("invite") or ""
    except Exception:
        pass
    btn_row(ab, *([("Join the Discord", "external-link", lambda: webbrowser.open(invite), "secondary")] if invite else []),
            ("Open my data folder", "folder", lambda: os.startfile(S.DATA), "secondary"))

    # ------------------------------------------------------------------ actions
    def save_picture():
        """A picture of just this console window, saved to the Desktop, for sending along with a problem."""
        try:
            from PIL import ImageGrab
            root.update_idletasks()
            x, y, w, h = root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height()
            desk = os.path.join(os.path.expanduser("~"), "Desktop")
            if not os.path.isdir(desk):
                desk = os.path.expanduser("~")
            path = os.path.join(desk, "Gnomewatch-" + time.strftime("%Y%m%d-%H%M%S") + ".png")
            ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(path)
            note.config(text="Saved to your Desktop: " + os.path.basename(path), fg=K.OK)
            subprocess.Popen(["explorer", "/select,", path])
        except Exception as e:
            note.config(text=f"Couldn't save it ({str(e)[:50]})", fg=K.BAD)

    def refresh():
        for name, lbl in sc_rows.items():
            val = str(get(name) or "").strip()
            lbl.config(text=val.upper().replace("+", " + ") if val else "off", fg=K.TEXT if val else K.FAINT)
        sess = S.GW_SESSION or {}
        try:
            from gw_version import VERSION
        except Exception:
            VERSION = "?"
        left = S._license_left()
        key = "no expiry (admin)" if sess.get("admin") else ("expired" if S.LOCKED.is_set() else f"{int(left // 3600)}h {int(left % 3600 // 60)}m left") if left is not None else "-"
        about.config(text=f"Gnomewatch {VERSION}\nSigned in as {sess.get('name', '-')}  ·  key: {key}")

    class Page:
        always = False

        def slow(self):
            refresh()

        def on_show(self):
            sf.to_top()
            refresh()

    refresh()
    return Page()
