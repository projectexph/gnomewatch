"""Sentinel master console (Tk). Runs in its own thread; `S` is the sentinel module itself.

Sidebar pages: Overview, Instances, Accounts, Activity, Settings.  Ctrl+K (or Ctrl+Shift+K anywhere) opens the
command palette.  The whole colour scheme follows the event that is on (the accent tweens between colours).

Accounts are browser profiles: only a label + profile folder name is stored (accounts.json) - never a password
or cookie.  Which window belongs to which account is assigned by hand (assign.json).
"""
import collections
import ctypes
import json
import math
import os
import queue
import random
import threading
import subprocess
import time
import tkinter as tk
from tkinter import filedialog, messagebox

from PIL import Image, ImageOps, ImageTk

# Each mode has its own look (palette, glass) and its own motion budget. UI_MODE is read when the console starts; changing
# the mode rebuilds the console (run() reloads this module) so every colour and default picks the new look up.
MODES = {
    "smooth": dict(label="Smooth", glyph="✦", blurb="Glass, glow and motion at full rate", glass=True,
                   pal=("#080c14", "#0f1726", "#17223a", "#22314d", "#eaf0fb", "#94a3bd", "#5a6b88", "#3dd6ff"),
                   amb_fps=24, fast=120, slow=700, tween=28, rate=.2, slide=8, slide_ms=12, hover_ms=16,
                   fade=True, burst=True, pulse=True, mono=False),
    "fps":    dict(label="FPS friendly", glyph="⚡", blurb="Flat and still. Redraws only on change", glass=False,
                   pal=("#0b0e13", "#141922", "#1c232e", "#2a3340", "#e6ebf2", "#8d99ab", "#586478", "#4cc2ff"),
                   amb_fps=0, fast=1000, slow=2000, tween=60, rate=.5, slide=0, slide_ms=0, hover_ms=0,
                   fade=False, burst=False, pulse=False, mono=False),
    "stealth": dict(label="Stealth", glyph="◍", blurb="Dark, silent, hidden. Nothing pops up", glass=False,
                    pal=("#050505", "#0b0b0c", "#121214", "#1a1a1d", "#9aa0a8", "#666b73", "#43474d", "#5a6068"),
                    amb_fps=0, fast=2000, slow=3000, tween=80, rate=1.0, slide=0, slide_ms=0, hover_ms=0,
                    fade=False, burst=False, pulse=False, mono=True),
    "eco":    dict(label="Eco", glyph="☘", blurb="Slow reads and redraws, for long AFK", glass=False,
                   pal=("#0a100c", "#101812", "#172219", "#26362a", "#e3eee5", "#8aa090", "#566a5b", "#6bd68a"),
                   amb_fps=0, fast=2000, slow=4000, tween=80, rate=1.0, slide=0, slide_ms=0, hover_ms=0,
                   fade=False, burst=False, pulse=False, mono=False),
}
UI_MODE = os.environ.get("UI_MODE", "smooth").strip().lower()
if UI_MODE not in MODES:
    UI_MODE = "smooth"
M = MODES[UI_MODE]
GLASS = M["glass"] and os.environ.get("GLASS", "1") == "1"      # frosted glass (Windows 11); GLASS=0 = solid window
_bg0, CARD, CARD2, EDGE, TEXT, DIM, FAINT, IDLE = M["pal"]
BG = "#000000" if GLASS else _bg0                     # pure black is what Windows renders as see-through glass
OK, WARN, BAD = "#39e58c", "#ffbf47", "#ff5c7a"
F, FS = "Segoe UI", "Segoe UI Symbol"
W, H, SIDE = 1000, 660, 84
CW = 876                                              # content width inside a page

FORCE_FRONT = None          # tests only: True / False pretends the console is / isn't the front window

PRESETS = {
    "Quiet":    {"POLL_SECONDS": 5.0, "PARK_CORES": 1, "IDLE_BACKOFF": True},
    "Balanced": {"POLL_SECONDS": 2.0, "PARK_CORES": 1, "IDLE_BACKOFF": True},
    "Sharp":    {"POLL_SECONDS": 1.0, "PARK_CORES": 2, "IDLE_BACKOFF": False},
}
DEFAULT_SCENES = {
    "AFK":     {"HIDE_WINDOW": True, "HIDE_ALL": True, "THROTTLE_PARKED": True, "PARK_CORES": 1, "KEEPALIVE": True,
                "NOTIFY": True, "POLL_SECONDS": 5.0, "IDLE_BACKOFF": True, "PANEL_MINI": True, "_windows": "auto"},
    "Working": {"HIDE_WINDOW": True, "HIDE_ALL": False, "NOTIFY": False, "PANEL_MINI": True, "POLL_SECONDS": 2.0,
                "_windows": "auto"},
    "Stealth": {"HIDE_WINDOW": True, "HIDE_ALL": True, "NOTIFY": False, "NOTIFY_SOUND": False, "NOTIFY_TOAST": False,
                "_tray": False, "_windows": "hide", "_panel": "hide"},
    "Full":    {"HIDE_WINDOW": False, "HIDE_ALL": False, "NOTIFY": True, "NOTIFY_SOUND": True, "NOTIFY_TOAST": True,
                "PANEL_MINI": False, "POLL_SECONDS": 2.0, "PARK_CORES": 0, "THROTTLE_PARKED": False, "_tray": True,
                "_windows": "show"},
}
SCENE_KEYS = ["HIDE_WINDOW", "HIDE_ALL", "THROTTLE_PARKED", "PARK_CORES", "KEEPALIVE", "KEEPALIVE_MINUTES", "NOTIFY",
              "NOTIFY_SOUND", "NOTIFY_TOAST", "POLL_SECONDS", "IDLE_BACKOFF", "PANEL_MINI"]


# ============================================================================================ small helpers
def lerp(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(x + (y - x) * t))) for x, y in zip(a, b))


def approach(cur, target, rate=.2):
    """Move each colour channel toward the target by a fraction, but at least 1 step, so it always arrives."""
    out = []
    for i in (1, 3, 5):
        c, t = int(cur[i:i + 2], 16), int(target[i:i + 2], 16)
        d = t - c
        if d:
            m = d * rate
            c += int(m) if abs(m) >= 1 else (1 if d > 0 else -1)
            c = min(c, t) if d > 0 else max(c, t)
        out.append(c)
    return "#%02x%02x%02x" % tuple(out)


def dist(c1, c2):
    return sum(abs(int(c1[i:i + 2], 16) - int(c2[i:i + 2], 16)) for i in (1, 3, 5))


def rr(c, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
           x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


def same(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return a == b


def browser_exe(kind):
    rel = {"edge": r"Microsoft\Edge\Application\msedge.exe", "chrome": r"Google\Chrome\Application\chrome.exe"}[kind]
    for base in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""),
                 os.environ.get("LOCALAPPDATA", "")):
        p = os.path.join(base, rel)
        if base and os.path.exists(p):
            return p
    return None


def browser_profiles(kind):
    root = {"edge": r"Microsoft\Edge\User Data", "chrome": r"Google\Chrome\User Data"}[kind]
    path = os.path.join(os.environ.get("LOCALAPPDATA", ""), root, "Local State")
    try:
        cache = json.load(open(path, encoding="utf-8"))["profile"]["info_cache"]
    except (OSError, ValueError, KeyError):
        return []
    return [(d, (info.get("name") or d)) for d, info in cache.items()]


def filter_actions(items, query):
    """items: [(label, keywords, fn)]. Every word of the query must appear in label+keywords."""
    words = query.lower().split()
    return [(l, kw, fn) for (l, kw, fn) in items if all(x in (l + " " + kw).lower() for x in words)]


def ago(sec):
    if sec is None:
        return "never"
    sec = max(0, int(sec))
    return "just now" if sec < 3 else f"{sec}s ago" if sec < 90 else f"{sec // 60} min ago" if sec < 5400 else f"{sec // 3600} h ago"


# ============================================================================================ theme (accent follows the event)
class Theme:
    def __init__(self, root):
        self.root, self.cur, self.target, self.subs, self.job = root, IDLE, IDLE, [], None

    def sub(self, fn):
        self.subs.append(fn)
        fn(self.cur)

    def go(self, colour):
        if colour == self.target:
            return
        self.target = colour
        if self.job is None:
            self.step()

    def step(self):
        self.job = None
        self.cur = approach(self.cur, self.target, M["rate"])
        if self.cur != self.target:
            self.job = self.root.after(M["tween"], self.step)
        for fn in list(self.subs):
            try:
                fn(self.cur)
            except tk.TclError:
                self.subs.remove(fn)


# ============================================================================================ widgets
class Btn(tk.Canvas):
    """Rounded button. kind: ghost | primary (accent) | ok | danger."""
    def __init__(self, parent, text, cmd, theme, w=110, h=32, kind="ghost", bg=CARD, size=9, bold=True):
        super().__init__(parent, width=w, height=h, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.cmd, self.kind, self.theme, self.cur, self.job, self.hovering = cmd, kind, theme, CARD2, None, False
        self.accent = theme.cur
        self.bg_item = rr(self, 1, 1, w - 1, h - 1, 10, fill=CARD2, outline="")
        self.txt = self.create_text(w / 2, h / 2, text=text, font=(F, size, "bold" if bold else "normal"))
        self.bind("<Enter>", lambda e: self.hover(True))
        self.bind("<Leave>", lambda e: self.hover(False))
        self.bind("<ButtonRelease-1>", lambda e: self.cmd())
        theme.sub(self.retheme)

    def palette(self):
        a = self.accent
        if self.kind == "primary":
            return lerp(CARD2, a, .30), lerp(CARD2, a, .48), lerp(a, "#ffffff", .3)
        if self.kind == "ok":
            return lerp(CARD2, OK, .22), lerp(CARD2, OK, .38), OK
        if self.kind == "danger":
            return lerp(CARD2, BAD, .22), lerp(CARD2, BAD, .40), BAD
        return CARD2, lerp(CARD2, "#ffffff", .10), TEXT

    def retheme(self, accent):
        first = not hasattr(self, "styled")
        self.styled = True
        if not first and self.kind != "primary":
            return                                     # ghost / ok / danger buttons don't use the accent
        self.accent = accent
        fill, hov, fg = self.palette()
        self.itemconfig(self.txt, fill=fg)
        target = hov if self.hovering else fill
        if not self.job:
            self.cur = target
            self.itemconfig(self.bg_item, fill=target)

    def hover(self, on):
        self.hovering = on
        fill, hov, _ = self.palette()
        self.fade(hov if on else fill)

    def fade(self, target, steps=5):
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
                pass

        step()

    def set(self, text=None, kind=None):
        if text is not None:
            self.itemconfig(self.txt, text=text)
        if kind is not None and kind != self.kind:
            self.kind = kind
            self.retheme(self.accent)


class Card(tk.Canvas):
    """A rounded panel. Put widgets in .inner, or draw straight on the canvas."""
    all = []                                             # every live card (for the hover glow)

    def __init__(self, parent, w, h, pad=14, fill=CARD, edge=EDGE, radius=18, bg=BG):
        super().__init__(parent, width=w, height=h, bg=bg, highlightthickness=0, bd=0)
        self.w, self.h, self.level, self.fill0, self.edge0 = w, h, 0.0, fill, edge
        Card.all.append(self)
        self.body = rr(self, 1, 1, w - 1, h - 1, radius, fill=fill, outline=edge)
        self.inner = None
        if pad > 0:                       # pad=0 means "draw straight on the canvas" (an inner frame would cover it)
            self.inner = tk.Frame(self, bg=fill)
            self.create_window(pad, pad, window=self.inner, anchor="nw", width=w - 2 * pad, height=h - 2 * pad)


    def glow(self, level, accent):
        """Hover glow: the border lights up in the accent colour and the fill lifts slightly."""
        self.level = level
        self.itemconfig(self.body, fill=lerp(self.fill0, lerp(self.fill0, accent, .13), level),
                        outline=lerp(self.edge0, accent, .8 * level))


class Ambient:
    """Slow, event-themed motion drawn inside one canvas, underneath its content. Kept deliberately light:
    a couple of dozen small shapes that are moved (never re-created) a few times a second."""
    KINDS = {"aurora": "curtains", "lightning storm": "rain", "solar flare": "glow", "candy land": "sprinkles",
             "toxic": "bubbles", "cursed": "mist"}

    def __init__(self, canvas, box, base, above=None):
        self.c, self.box, self.base, self.above = canvas, box, base, above
        self.key, self.parts, self.t, self.kind, self.colour = None, [], 0.0, None, IDLE
        self.flash = None
        self.flash_until = 0.0

    def clear(self):
        try:
            self.c.delete("amb")
        except tk.TclError:
            pass
        self.parts, self.key, self.flash = [], None, None

    def build(self, event, colour, level):
        self.clear()
        self.key, self.colour, self.built = (event, level), colour, colour
        x0, y0, x1, y1 = self.box
        w, h = x1 - x0, y1 - y0
        n = 1.0 if level == "normal" else 0.5
        kind = self.kind = self.KINDS.get(event, "drift")
        rnd = random.Random(11)
        c = self.c

        def mix(k):
            return lerp(self.base, colour, k)

        if kind == "curtains":
            cnt = int(4 * n) + 1
            for i in range(cnt):
                pid = c.create_polygon(0, 0, 0, 0, 0, 0, 0, 0, fill=mix(.09 + .05 * (i % 2)), outline="", tags="amb")
                self.parts.append({"id": pid, "cx": x0 + w * (i + .5) / cnt, "ph": rnd.random() * 6, "sp": .5 + rnd.random() * .4,
                                   "bw": w / cnt * .8})
        elif kind == "rain":
            for _ in range(int(14 * n) + 2):
                x, y, ln = x0 + rnd.random() * w, y0 + rnd.random() * h, 10 + rnd.random() * 14
                self.parts.append({"id": c.create_line(x, y, x - 3, y + ln, fill=mix(.30), tags="amb"), "x": x, "y": y,
                                   "vy": 90 + rnd.random() * 90, "ln": ln})
            self.flash = c.create_rectangle(x0, y0, x1, y1, fill=mix(.42), outline="", state="hidden", tags="amb")
        elif kind == "glow":
            for i in range(5):
                self.parts.append({"id": c.create_oval(0, 0, 0, 0, fill=mix(.05 + .05 * i), outline="", tags="amb"), "i": i, "k": "glow"})
            for _ in range(int(8 * n) + 1):
                self.parts.append({"id": c.create_oval(0, 0, 0, 0, fill=mix(.55), outline="", tags="amb"), "k": "dot",
                                   "x": x0 + rnd.random() * w, "y": y0 + rnd.random() * h, "vx": rnd.uniform(-6, 6),
                                   "vy": -rnd.uniform(18, 44), "r": rnd.uniform(1.2, 2.4)})
        elif kind in ("sprinkles", "bubbles", "drift"):
            pastel = ["#ff7ac8", "#6fdcff", "#ffe066", "#b58cff"]
            cnt = int({"sprinkles": 16, "bubbles": 12, "drift": 10}[kind] * n) + 2
            for i in range(cnt):
                r = rnd.uniform(1.6, 3.4) if kind == "sprinkles" else rnd.uniform(3, 7) if kind == "bubbles" else rnd.uniform(1.2, 2.4)
                col = mix(.5) if kind != "sprinkles" else lerp(self.base, pastel[i % 4], .55)
                if kind == "bubbles":
                    pid = c.create_oval(0, 0, 0, 0, outline=mix(.5), fill="", tags="amb")
                else:
                    pid = c.create_oval(0, 0, 0, 0, fill=col, outline="", tags="amb")
                vy = {"sprinkles": rnd.uniform(16, 40), "bubbles": -rnd.uniform(14, 34), "drift": rnd.uniform(-5, 5)}[kind]
                self.parts.append({"id": pid, "k": "dot", "x": x0 + rnd.random() * w, "y": y0 + rnd.random() * h,
                                   "vx": rnd.uniform(-5, 5), "vy": vy, "r": r})
        elif kind == "mist":
            for i in range(3):
                self.parts.append({"id": c.create_oval(0, 0, 0, 0, fill=mix(.07 + .03 * i), outline="", tags="amb"), "k": "mist",
                                   "ph": rnd.random() * 6, "i": i})
            self.parts.append({"id": c.create_rectangle(x0 + 2, y0 + 2, x1 - 2, y1 - 2, outline=mix(.3), width=2, tags="amb"), "k": "edge"})
        if self.above is not None:
            c.tag_raise("amb", self.above)
        else:
            c.tag_lower("amb")

    def tick(self, dt):
        if not self.parts:
            return
        self.t += dt
        t, c = self.t, self.c
        x0, y0, x1, y1 = self.box
        w, h = x1 - x0, y1 - y0
        col = self.colour

        def mix(k):
            return lerp(self.base, col, k)

        try:
            if self.kind == "curtains":
                for p in self.parts:
                    sway = math.sin(t * p["sp"] + p["ph"]) * w * .07
                    bw = p["bw"] * (.75 + .25 * math.sin(t * .7 + p["ph"]))
                    xc = p["cx"] + sway
                    c.coords(p["id"], xc - bw / 2, y0, xc + bw / 2, y0, xc + bw / 3 + sway * .5, y1, xc - bw / 3 + sway * .5, y1)
            elif self.kind == "rain":
                for p in self.parts:
                    p["y"] += p["vy"] * dt
                    if p["y"] > y1:
                        p["y"] = y0
                    c.coords(p["id"], p["x"], p["y"], p["x"] - 3, min(p["y"] + p["ln"], y1))
                if self.flash is not None:
                    if t > self.flash_until and random.random() < dt * .18:
                        self.flash_until = t + .13
                    c.itemconfig(self.flash, state="normal" if t < self.flash_until else "hidden")
            elif self.kind == "mist":
                for p in self.parts:
                    if p["k"] == "mist":
                        cx = x0 + w * (.25 + .25 * p["i"]) + math.sin(t * .35 + p["ph"]) * w * .1
                        cy = y0 + h * (.35 + .15 * p["i"]) + math.cos(t * .28 + p["ph"]) * h * .1
                        r = min(w, h) * (.35 + .08 * p["i"])
                        c.coords(p["id"], cx - r, cy - r * .7, cx + r, cy + r * .7)
                    else:
                        c.itemconfig(p["id"], outline=mix(.16 + .26 * (math.sin(t * 1.9) + 1) / 2))
            else:                                            # glow (sun) and particle kinds
                for p in self.parts:
                    k = p.get("k")
                    if k == "glow":
                        pulse = 1 + .07 * math.sin(t * 1.5 + p["i"] * .3)
                        r = (34 + p["i"] * -5) * 2.4 * pulse * (min(w, h) / 190)
                        gx, gy = x1 - w * .18, y0 + h * .18
                        c.coords(p["id"], gx - r, gy - r, gx + r, gy + r)
                    else:
                        p["x"] += p["vx"] * dt
                        p["y"] += p["vy"] * dt
                        if p["y"] > y1 - 3:
                            p["y"] = y0 + 3
                        elif p["y"] < y0 + 3:
                            p["y"] = y1 - 3
                        if p["x"] < x0 + 3:
                            p["x"] = x1 - 3
                        elif p["x"] > x1 - 3:
                            p["x"] = x0 + 3
                        r = p["r"]
                        c.coords(p["id"], p["x"] - r, p["y"] - r, p["x"] + r, p["y"] + r)
        except tk.TclError:
            self.parts = []


class Switch(tk.Canvas):
    def __init__(self, parent, on_change, theme, bg=CARD):
        super().__init__(parent, width=46, height=24, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.on, self.cb, self.t, self.job, self.accent = None, on_change, 0.0, None, theme.cur
        self.track = rr(self, 1, 1, 45, 23, 11, fill=CARD2, outline="")
        self.knob = self.create_oval(3, 3, 21, 21, fill=TEXT, outline="")
        self.bind("<ButtonRelease-1>", lambda e: self.cb(not self.on))
        theme.sub(self.retheme)

    def retheme(self, accent):
        self.accent = accent
        if self.t > 0 or self.on is None:
            self.draw(self.t)                          # an off switch has no accent in it

    def draw(self, t):
        self.t = t
        x = 3 + 22 * t
        self.itemconfig(self.track, fill=lerp(CARD2, self.accent, t * .85))
        self.coords(self.knob, x, 3, x + 18, 21)

    def set(self, on):
        if on == self.on:
            return
        first = self.on is None
        self.on = on
        if first:
            self.draw(1.0 if on else 0.0)
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
                self.draw(start + (end - start) * (k * k * (3 - 2 * k)))
                self.job = self.after(16, step, i + 1) if i < 7 else None
            except tk.TclError:
                pass

        step()


class Seg(tk.Frame):
    def __init__(self, parent, choices, get, pick, theme, seg_w=64, bg=CARD):
        super().__init__(parent, bg=bg)
        self.get, self.btns = get, []
        for label, value in choices:
            b = Btn(self, label, lambda v=value: pick(v), theme, w=seg_w, h=28, bg=bg, size=8)
            b.pack(side="left", padx=2)
            self.btns.append((b, value))
        self.sync()

    def sync(self):
        cur = self.get()
        for b, v in self.btns:
            b.set(kind="primary" if same(cur, v) else "ghost")


class Spark(tk.Canvas):
    """Rolling line graph with soft fills. series: [(name, colour, deque)]."""
    def __init__(self, parent, w, h, series, bg=CARD):
        super().__init__(parent, width=w, height=h, bg=bg, highlightthickness=0, bd=0)
        self.w, self.h, self.series, self.bgc = w, h, series, bg

    def redraw(self):
        self.delete("all")
        w, h = self.w, self.h
        peak = max([100.0] + [max(d) * 1.15 for _, _, d in self.series if d])
        for i in range(1, 4):
            y = h * i / 4
            self.create_line(0, y, w, y, fill=lerp(CARD, EDGE, .8), dash=(2, 4))
        self.create_text(4, 2, anchor="nw", text=f"{peak:.0f}%", fill=FAINT, font=(F, 7))
        for name, colour, data in self.series:
            if len(data) < 2:
                continue
            n = data.maxlen or len(data)
            pts = []
            for i, v in enumerate(data):
                x = w - (len(data) - 1 - i) * (w / (n - 1))
                y = h - 3 - (h - 8) * min(v, peak) / peak
                pts += [x, y]
            self.create_polygon([pts[0], h] + pts + [pts[-2], h], fill=lerp(self.bgc, colour, .14), outline="")
            self.create_line(pts, fill=colour, width=2, smooth=True)


# ============================================================================================ the console
def run(S):
    """Run the console; when the mode changes it is rebuilt with that mode's look (module reloaded, same thread)."""
    import importlib
    import sys
    mod = sys.modules[__name__]
    while True:
        res = mod._run_once(S)
        import gc
        gc.collect()                              # free the old window's Tk variables here, in the thread that owns them
        if res != "restyle" or S.QUIT.is_set():
            return
        try:
            mod = importlib.reload(mod)
        except Exception as e:
            print("console: couldn't reload for the new mode:", e)
            return


def _run_once(S):
    import psutil
    import win32process

    Card.all.clear()
    root = tk.Tk()
    root.title("Sentinel")
    root.configure(bg=BG)
    root.attributes("-topmost", True)
    root.resizable(False, False)
    px_, py_ = getattr(S, "_console_pos", (60, 40))
    root.geometry(f"{W}x{H}+{px_}+{py_}")
    restart = {"on": False}
    cache = {}

    def ic(cv, item, **kw):
        """canvas.itemconfig, but only when something actually changed (every call re-paints)."""
        key = (str(cv), item)
        if cache.get(key) != kw:
            cache[key] = dict(kw)
            cv.itemconfig(item, **kw)

    def lc(w, **kw):
        """widget.config, but only on change."""
        key = (str(w), "w")
        if cache.get(key) != kw:
            cache[key] = dict(kw)
            w.config(**kw)
    try:
        root.iconbitmap(S._gnome_ico())
    except Exception:
        pass
    theme = Theme(root)
    keep = []                                            # keep image references alive
    errors = set()

    def guarded(fn):
        """Run a UI update; report a problem once instead of killing the loop."""
        try:
            fn()
        except tk.TclError:
            pass
        except Exception as e:
            key = f"{fn.__name__}:{type(e).__name__}"
            if key not in errors:
                errors.add(key)
                import traceback
                print(f"console: {fn.__name__} failed: {e!r}")
                print(traceback.format_exc())

    def get(name):
        return getattr(S, S._GLOBAL_OF.get(name, name))

    # ------------------------------------------------------------------ window chrome (dark title bar + glass)
    def chrome(win, glass=True):
        try:
            win.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
            v = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), 4)
            if GLASS and glass:
                kind = ctypes.c_int(3)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(kind), 4)

                class MARGINS(ctypes.Structure):
                    _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]

                m = MARGINS(-1, -1, -1, -1)
                ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(m))
        except Exception:
            pass

    def label(parent, text, bg=CARD, fg=DIM, size=9, bold=False, **kw):
        return tk.Label(parent, text=text, bg=bg, fg=fg, font=(F, size, "bold" if bold else "normal"), **kw)

    # ------------------------------------------------------------------ scenes / accounts / assignments (files)
    def jload(name, default):
        try:
            return json.load(open(os.path.join(S.HERE, name), encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def jsave(name, data):
        try:
            json.dump(data, open(os.path.join(S.HERE, name), "w", encoding="utf-8"), indent=1)
        except OSError as e:
            print(f"couldn't save {name}: {e}")

    def load_scenes():
        sc = dict(DEFAULT_SCENES)
        sc.update(jload("scenes.json", {}))
        return sc

    def load_accounts():
        return jload("accounts.json", [])

    assign = {int(k): v for k, v in jload("assign.json", {}).items()}

    def set_assign(hwnd, acct):
        if acct:
            assign[hwnd] = acct
        else:
            assign.pop(hwnd, None)
        jsave("assign.json", {str(k): v for k, v in assign.items()})

    def apply_scene(name):
        sc = load_scenes().get(name)
        if not sc:
            return
        print(f"scene: {name}")
        for k, v in sc.items():
            if not k.startswith("_"):
                S._set_setting(k, v)
        w_ = sc.get("_windows")
        if w_ == "auto":
            S._override.clear()
            S._apply_visibility()
        elif w_ in ("show", "hide"):
            S._set_show(w_ == "show")
        if "_tray" in sc:
            S._tray_visible(sc["_tray"])
        if sc.get("_panel") == "hide":
            root.after(500, root.withdraw)
        elif sc.get("_tray") is True and root.state() == "withdrawn":
            root.deiconify()

    def scene_matches(name):
        sc = load_scenes().get(name, {})
        vals = [(k, v) for k, v in sc.items() if not k.startswith("_")]
        return bool(vals) and all(same(get(k), v) for k, v in vals)

    # ================================================================== shell: sidebar + content
    shell = tk.Frame(root, bg=BG)
    shell.pack(fill="both", expand=True)

    side = tk.Canvas(shell, width=SIDE, height=H, bg=BG, highlightthickness=0, bd=0)
    side.pack(side="left")
    main = tk.Frame(shell, bg=BG)
    main.pack(side="left", fill="both", expand=True)

    NAV = [("Overview", "◎"), ("Instances", "▣"), ("Accounts", "☻"), ("Activity", "≣"), ("Settings", "⚙")]
    nav = {"cur": "Overview", "hover": None}
    try:
        logo = ImageTk.PhotoImage(Image.open(S._gnome_ico()).convert("RGBA").resize((40, 40), Image.Resampling.LANCZOS))
        keep.append(logo)
    except Exception:
        logo = None

    def draw_side(accent=None):
        accent = accent or theme.cur
        side.delete("nav")
        if logo:
            side.create_image(SIDE / 2, 44, image=logo, tags=("logo", "nav"))
        for i, (name, glyph) in enumerate(NAV):
            y = 100 + i * 70
            on, hov = name == nav["cur"], name == nav["hover"]
            if on or hov:
                rr(side, 9, y, SIDE - 9, y + 62, 14, fill=lerp(CARD, accent, .20) if on else CARD, outline="", tags="nav")
            if on:
                rr(side, 2, y + 12, 6, y + 50, 2, fill=accent, outline="", tags="nav")
            side.create_text(SIDE / 2, y + 26, text=glyph, font=(FS, 17), fill=accent if on else (TEXT if hov else DIM), tags="nav")
            side.create_text(SIDE / 2, y + 48, text=name, font=(F, 7, "bold"), fill=TEXT if on else FAINT, tags="nav")
        y = H - 78                                        # the mode pill: click to cycle Smooth > FPS friendly > Stealth > Eco
        rr(side, 9, y, SIDE - 9, y + 58, 14, fill=CARD, outline=EDGE, tags="nav")
        side.create_text(SIDE / 2, y + 20, text=M["glyph"], font=(FS, 15), fill=accent, tags="nav")
        side.create_text(SIDE / 2, y + 42, text=M["label"].upper() if len(M["label"]) < 8 else "FPS" if UI_MODE == "fps" else M["label"].upper(),
                         font=(F, 7, "bold"), fill=DIM, tags="nav")

    def side_at(y):
        i = (y - 100) // 70
        return NAV[i][0] if 0 <= i < len(NAV) and (y - 100) % 70 < 62 else None

    side.bind("<Motion>", lambda e: (nav.update(hover=side_at(e.y)), draw_side()))
    side.bind("<Leave>", lambda e: (nav.update(hover=None), draw_side()))
    side.bind("<Button-1>", lambda e: side_at(e.y) and show(side_at(e.y)))
    side.bind("<Button-1>", lambda e: cycle_mode() if e.y > H - 82 else None, add="+")
    theme.sub(draw_side)

    # header
    hdr = tk.Frame(main, bg=BG, height=70)
    hdr.pack(fill="x", padx=20, pady=(14, 0))
    hdr.pack_propagate(False)
    title_lbl = tk.Label(hdr, text="Overview", bg=BG, fg=TEXT, font=(F, 20, "bold"))
    title_lbl.pack(side="left", anchor="n")
    sub_lbl = tk.Label(hdr, text="", bg=BG, fg=FAINT, font=(F, 9))
    sub_lbl.place(x=2, y=38)
    hb = tk.Frame(hdr, bg=BG)
    hb.pack(side="right", anchor="n")
    chip = tk.Canvas(hb, width=290, height=40, bg=BG, highlightthickness=0, bd=0)
    chip.pack(side="left", padx=(0, 10))
    Btn(hb, "–", lambda: set_mini(True), theme, w=38, h=36, bg=BG, size=12).pack(side="left")
    Btn(hb, "✕", lambda: root.withdraw(), theme, w=38, h=36, bg=BG, size=11).pack(side="left", padx=(6, 0))
    # ---- inline command bar: type here, matches drop down right under it (no pop-up window)
    cmd_wrap = tk.Canvas(hdr, width=200, height=38, bg=BG, highlightthickness=0, bd=0)
    cmd_wrap.place(x=228, y=4)
    rr(cmd_wrap, 1, 1, 199, 37, 18, fill=CARD, outline=EDGE)
    cmd_wrap.create_text(20, 19, text="⌘", fill=FAINT, font=(FS, 12))
    cmd_var = tk.StringVar()
    PH = "Type a command…"
    cmd_entry = tk.Entry(cmd_wrap, textvariable=cmd_var, bg=CARD, fg=FAINT, insertbackground=TEXT, relief="flat",
                         font=(F, 10), bd=0, highlightthickness=0)
    cmd_wrap.create_window(36, 19, window=cmd_entry, anchor="w", width=154, height=24)
    cmd_state = {"ph": True}
    cmd_shown = []
    cmd_list = tk.Listbox(root, bg=CARD, fg=TEXT, selectbackground=lerp(CARD, IDLE, .4), selectforeground=TEXT,
                          highlightthickness=1, highlightbackground=EDGE, highlightcolor=EDGE, bd=0, relief="flat",
                          font=(F, 10), activestyle="none", takefocus=0)

    def cmd_hide():
        cmd_list.place_forget()

    def cmd_reset():
        cmd_state["ph"] = True
        cmd_var.set(PH)
        cmd_entry.config(fg=FAINT)
        cmd_hide()

    def cmd_focus_in(_=None):
        if cmd_state["ph"]:
            cmd_state["ph"] = False
            cmd_var.set("")
            cmd_entry.config(fg=TEXT)

    def cmd_focus_out(_=None):
        def later():
            try:
                if root.focus_get() is not cmd_entry:
                    cmd_hide()
                    if not cmd_var.get().strip():
                        cmd_reset()
            except (tk.TclError, KeyError):
                pass
        root.after(200, later)

    def cmd_refill(*_):
        if cmd_state["ph"]:
            return
        text = cmd_var.get().strip()
        cmd_list.delete(0, "end")
        cmd_shown.clear()
        if not text:
            cmd_hide()
            return
        for label_, kw, fn in filter_actions(actions(), text)[:8]:
            cmd_shown.append(fn)
            cmd_list.insert("end", "  " + label_)
        if not cmd_shown:
            cmd_hide()
            return
        cmd_list.selection_set(0)
        root.update_idletasks()
        x = cmd_wrap.winfo_rootx() - root.winfo_rootx()
        y = cmd_wrap.winfo_rooty() - root.winfo_rooty() + 42
        cmd_list.place(x=x, y=y, width=300, height=min(8, len(cmd_shown)) * 24 + 8)
        cmd_list.lift()

    def cmd_run(_=None):
        sel = cmd_list.curselection()
        fn = cmd_shown[sel[0]] if sel and cmd_shown else None
        cmd_reset()
        root.focus_set()
        if fn:
            root.after(60, fn)
        return "break"

    def cmd_move(d):
        if cmd_shown:
            sel = cmd_list.curselection()
            i = max(0, min(len(cmd_shown) - 1, (sel[0] if sel else 0) + d))
            cmd_list.selection_clear(0, "end")
            cmd_list.selection_set(i)
            cmd_list.see(i)
        return "break"

    def cmd_escape(_=None):
        cmd_reset()
        root.focus_set()
        return "break"

    def focus_bar(_=None):
        if root.state() == "withdrawn":
            root.deiconify()
        if mini_on[0]:
            set_mini(False)
        cmd_entry.focus_force()
        return "break"

    cmd_var.trace_add("write", cmd_refill)
    cmd_entry.bind("<FocusIn>", cmd_focus_in)
    cmd_entry.bind("<FocusOut>", cmd_focus_out)
    cmd_entry.bind("<Return>", cmd_run)
    cmd_entry.bind("<Escape>", cmd_escape)
    cmd_entry.bind("<Down>", lambda e: cmd_move(1))
    cmd_entry.bind("<Up>", lambda e: cmd_move(-1))
    cmd_list.bind("<Button-1>", lambda e: (cmd_list.selection_clear(0, "end"), cmd_list.selection_set(cmd_list.nearest(e.y)),
                                           cmd_run())[-1])
    cmd_reset()

    rule = tk.Canvas(main, height=3, bg=BG, highlightthickness=0, bd=0)
    rule.pack(fill="x", padx=20, pady=(2, 8))

    rule_bits = []

    def draw_rule(accent=None):
        accent = accent or theme.cur
        n = 24
        if not rule_bits:
            for i in range(n):
                rule_bits.append(rule.create_rectangle(i * CW / n, 0, (i + 1) * CW / n + 1, 3, outline="", fill=accent))
        for i, r_ in enumerate(rule_bits):
            rule.itemconfig(r_, fill=lerp(accent, BG if GLASS else "#080c14", (i / n) ** .8))

    theme.sub(draw_rule)

    content = tk.Frame(main, bg=BG)
    content.pack(fill="both", expand=True, padx=20)
    pages = {n: tk.Frame(content, bg=BG) for n, _ in NAV}
    SUBS = {"Overview": "Live status at a glance", "Instances": "Every Roblox window, live",
            "Accounts": "Browser profiles you can launch", "Activity": "What has been happening",
            "Settings": "Everything you can tune"}

    def slide_in(f, dist=46, steps=0):
        """New page glides in from the right (a handful of frames; skipped when animations are off)."""
        steps = M["slide"]
        if get("ANIMATIONS") == "off" or not steps:
            f.place(x=0, y=0, relwidth=1, relheight=1)
            return
        f.place(x=dist, y=0, relwidth=1, relheight=1)

        def step(i=1):
            try:
                k = 1 - (1 - i / steps) ** 3
                f.place_configure(x=int(dist * (1 - k)))
                if i < steps:
                    root.after(M["slide_ms"], step, i + 1)
            except tk.TclError:
                pass

        step()

    def show(name):
        nav["cur"] = name
        for n, f in pages.items():
            f.place_forget()
        slide_in(pages[name])
        title_lbl.config(text=name)
        sub_lbl.config(text=SUBS[name])
        draw_side()
        if name == "Instances":
            guarded(rebuild_instances)

    # ================================================================== OVERVIEW
    ov = pages["Overview"]
    r1 = tk.Frame(ov, bg=BG)
    r1.pack(fill="x")
    ring_card = Card(r1, 330, 196, pad=0)
    ring_card.pack(side="left")
    RC = (100, 98, 66)                                          # ring centre x, y, radius
    cx, cy, rad = RC
    ring_bbox = (cx - rad, cy - rad, cx + rad, cy + rad)
    ring_glow = ring_card.create_arc(*[v + d for v, d in zip(ring_bbox, (-5, -5, 5, 5))], start=90, extent=0,
                                     style="arc", outline=CARD, width=16)
    ring_track = ring_card.create_arc(*ring_bbox, start=0, extent=359.9, style="arc", outline=CARD2, width=11)
    ring_prog = ring_card.create_arc(*ring_bbox, start=90, extent=0, style="arc", outline=IDLE, width=11)
    ring_time = ring_card.create_text(cx, cy - 4, text="--:--", fill=TEXT, font=(F, 21, "bold"))
    ring_cap = ring_card.create_text(cx, cy + 22, text="WAITING", fill=FAINT, font=(F, 7, "bold"))
    ring_glyph = ring_card.create_text(196, 52, text="✦", fill=IDLE, font=(FS, 26), anchor="w")
    ring_name = ring_card.create_text(196, 98, text="Watching", fill=TEXT, font=(F, 12, "bold"), anchor="w", width=126)
    ring_kind = ring_card.create_text(196, 132, text="no event on the sign", fill=DIM, font=(F, 8, "bold"), anchor="w")
    ring_seen = ring_card.create_text(196, 152, text="", fill=FAINT, font=(F, 8), anchor="w")

    statcol = tk.Frame(r1, bg=BG)
    statcol.pack(side="left", padx=(14, 0))
    stats = {}
    for i, (key, ttl) in enumerate((("inst", "INSTANCES"), ("worker", "BOT LINK"), ("read", "LAST READ"), ("up", "RUNNING FOR"))):
        c = Card(statcol, 259, 91, pad=0)
        c.grid(row=i // 2, column=i % 2, padx=(0 if i % 2 == 0 else 14, 0), pady=(0 if i < 2 else 14, 0))
        c.create_text(18, 20, text=ttl, fill=FAINT, font=(F, 7, "bold"), anchor="w")
        v = c.create_text(18, 46, text="–", fill=TEXT, font=(F, 17, "bold"), anchor="w")
        sb = c.create_text(18, 72, text="", fill=DIM, font=(F, 8), anchor="w")
        dot = c.create_oval(232, 16, 242, 26, fill=OK, outline="")
        stats[key] = (c, v, sb, dot)

    r2 = tk.Frame(ov, bg=BG)
    r2.pack(fill="x", pady=(14, 0))
    usage = Card(r2, 520, 176, pad=14)
    usage.pack(side="left")
    ul = tk.Frame(usage.inner, bg=CARD)
    ul.pack(fill="x")
    series = {"sentinel": ("Sentinel", "#7f8cff", collections.deque(maxlen=90)),
              "hidden": ("Hidden Roblox", "#ff9f43", collections.deque(maxlen=90)),
              "other": ("Other Roblox", "#3dd6ff", collections.deque(maxlen=90))}
    legend = {}
    for key, (nm, col, _d) in series.items():
        f_ = tk.Frame(ul, bg=CARD)
        f_.pack(side="left", padx=(0, 18))
        tk.Label(f_, text="●", bg=CARD, fg=col, font=(F, 9)).pack(side="left")
        lb = label(f_, f" {nm}  –", bg=CARD, fg=DIM, size=8)
        lb.pack(side="left")
        legend[key] = (nm, lb)
    spark = Spark(usage.inner, 490, 108, [(nm, col, d) for nm, col, d in series.values()])
    spark.pack(pady=(6, 0))

    scenes_card = Card(r2, 342, 176, pad=14)
    scenes_card.pack(side="left", padx=(14, 0))
    label(scenes_card.inner, "SCENES", fg=FAINT, size=7, bold=True, bg=CARD).pack(anchor="w")
    scene_box = tk.Frame(scenes_card.inner, bg=CARD)
    scene_box.pack(fill="x", pady=(8, 0))
    scene_state = {"sig": None, "btns": {}}

    def rebuild_scenes():
        sc = load_scenes()
        sig = tuple(sorted(sc))
        if sig == scene_state["sig"]:
            return
        scene_state["sig"] = sig
        for ch in scene_box.winfo_children():
            ch.destroy()
        scene_state["btns"].clear()
        for i, nm in enumerate(sc):
            b = Btn(scene_box, nm, lambda n=nm: apply_scene(n), theme, w=150, h=32, bg=CARD)
            b.grid(row=i // 2, column=i % 2, padx=(0, 8), pady=(0, 6))
            scene_state["btns"][nm] = b

    def sync_scenes():
        for nm, b in scene_state["btns"].items():
            b.set(kind="primary" if scene_matches(nm) else "ghost")

    r3 = tk.Frame(ov, bg=BG)
    r3.pack(fill="x", pady=(14, 0))
    feed_card = Card(r3, 520, 142, pad=12)
    feed_card.pack(side="left")
    sign_card = Card(r3, 342, 142, pad=0)
    sign_card.pack(side="left", padx=(14, 0))
    sign_card.create_text(16, 16, text="LIVE SIGN VIEW", fill=FAINT, font=(F, 7, "bold"), anchor="w")
    sign_img = sign_card.create_image(15, 28, anchor="nw")
    sign_msg = sign_card.create_text(171, 74, text="sign not in view", fill=FAINT, font=(F, 9))
    sign_cap = sign_card.create_text(16, 128, text="", fill=DIM, font=(F, 8, "bold"), anchor="w")
    sign_ph = [None]
    sign_last = [0.0]
    label(feed_card.inner, "LIVE ACTIVITY", fg=FAINT, size=7, bold=True, bg=CARD).pack(anchor="w")
    feed_small = tk.Text(feed_card.inner, bg=CARD, fg=DIM, font=("Consolas", 9), bd=0, relief="flat", height=6,
                         highlightthickness=0, state="disabled", wrap="none", cursor="arrow")
    feed_small.pack(fill="both", expand=True, pady=(4, 0))

    LOG_TAGS = (("ok", OK), ("next", "#6fa8ff"), ("start", WARN), ("bad", BAD), ("dim", FAINT), ("plain", DIM))

    def classify(line):
        low = line.lower()
        if "error" in low or "!!" in low or "couldn't" in low or "unknown" in low or "failed" in low:
            return "bad"
        if " active" in low:
            return "ok"
        if " start " in low or "keep-alive" in low:
            return "start"
        if " next " in low:
            return "next"
        if "parked" in low or "roblox window" in low or "setting " in low or "scene:" in low:
            return "dim"
        return "plain"

    def fill_feed(widget, lines):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        for tag, col in LOG_TAGS:
            widget.tag_configure(tag, foreground=col)
        for ln in lines:
            widget.insert("end", ln + "\n", classify(ln))
        widget.see("end")
        widget.configure(state="disabled")

    def update_sign():
        """Zoomed crop of the weather sign from the board window, with the text boxes the sentinel read outlined."""
        if nav["cur"] != "Overview" or time.time() - sign_last[0] < 1.0:
            return
        sign_last[0] = time.time()
        fr, sg = S._last_frame[0], S.STATE.get("sign")
        if fr is None or not sg or not sg["boxes"] or time.time() - sg["t"] > 25:
            sign_card.itemconfig(sign_img, image="")
            sign_card.itemconfig(sign_msg, state="normal")
            sign_card.itemconfig(sign_cap, text="")
            return
        from PIL import ImageDraw
        bx = sg["boxes"]
        x0, y0 = min(b[0] for b in bx), min(b[1] for b in bx)
        x1, y1 = max(b[0] + b[2] for b in bx), max(b[1] + b[3] for b in bx)
        W_, H_ = fr.size
        l, t = max(0.0, x0 - .07), max(0.0, y0 - .06)
        r, b_ = min(1.0, x1 + .07), min(1.0, y1 + .05)
        crop = fr.crop((int(l * W_), int(t * H_), int(r * W_), int(b_ * H_)))
        cw, ch = crop.size
        d = ImageDraw.Draw(crop)
        for (bx0, by0, bw_, bh_) in bx:
            d.rectangle([(bx0 - l) * W_, (by0 - t) * H_, (bx0 + bw_ - l) * W_, (by0 + bh_ - t) * H_], outline=theme.cur, width=2)
        crop = ImageOps.contain(crop, (312, 92), Image.Resampling.BILINEAR)
        ph = ImageTk.PhotoImage(crop)
        sign_ph[0] = ph
        sign_card.itemconfig(sign_img, image=ph)
        sign_card.coords(sign_img, 15 + (312 - crop.width) // 2, 28 + (92 - crop.height) // 2)
        sign_card.itemconfig(sign_msg, state="hidden")
        kind_, name_, left_ = S._event_summary()
        sign_card.itemconfig(sign_cap, text=f"read: {name_.title()}  ·  {S._fmt_left(left_)}" if kind_ != "none" else "read: –")

    feed_sig = {"small": None, "big": None}

    def feed_lines(n):
        txt = S._tail(S.LOG_PATH, 60000)
        lines = [l.rstrip() for l in txt.splitlines() if l.strip() and not l.startswith(("  File", "Traceback"))]
        return lines[-n:]

    # ================================================================== INSTANCES
    ip = pages["Instances"]
    bar = tk.Frame(ip, bg=BG)
    bar.pack(fill="x", pady=(0, 10))

    def confirm_btn(parent, text, sure, action, w, h, bg=CARD, size=9):
        b = Btn(parent, text, lambda: None, theme, w=w, h=h, kind="danger", bg=bg, size=size)
        armed = [False]

        def disarm():
            try:
                if armed[0] and b.winfo_exists():
                    armed[0] = False
                    b.set(text)
            except tk.TclError:
                pass

        def click():
            if armed[0]:
                armed[0] = False
                b.set(text)
                action()
            else:
                armed[0] = True
                b.set(sure)
                root.after(3000, disarm)

        b.cmd = click
        return b

    for txt, fn, kind in (("Show all", lambda: S._set_show(True), "ghost"), ("Hide all", lambda: S._set_show(False), "ghost"),
                          ("Dashboard", S._open_dashboard, "primary"), ("New instance", S._launch_instance, "primary")):
        Btn(bar, txt, fn, theme, w=130, h=34, kind=kind, bg=BG).pack(side="left", padx=(0, 8))
    confirm_btn(bar, "Close all", "Sure? Click again", S._close_all, 150, 34, bg=BG).pack(side="left")
    grid = tk.Frame(ip, bg=BG)
    grid.pack(fill="both", expand=True)
    inst = {"sig": None, "cards": {}}
    BLANK = ImageTk.PhotoImage(Image.new("RGB", (212, 134), (16, 22, 36)))
    keep.append(BLANK)

    def account_names():
        return [a["label"] for a in load_accounts()]

    def style_menu(m):
        m.config(bg=CARD2, fg=TEXT, activebackground=lerp(CARD2, "#ffffff", .1), activeforeground=TEXT, relief="flat",
                 bd=0, highlightthickness=0, font=(F, 9), anchor="w", width=14)
        m["menu"].config(bg=CARD2, fg=TEXT, activebackground=lerp(CARD2, IDLE, .3), activeforeground=TEXT, bd=0, font=(F, 9))

    def rebuild_instances():
        snap = list(S._snapshot)
        sig = (tuple(snap), tuple(sorted(assign.items())), tuple(account_names()))
        if sig == inst["sig"]:
            return
        inst["sig"] = sig
        for ch in grid.winfo_children():
            ch.destroy()
        inst["cards"].clear()
        if not snap:
            c = Card(grid, CW, 90, pad=0)
            c.pack()
            c.create_text(CW / 2, 45, text="No Roblox window found", fill=DIM, font=(F, 11))
            return
        for i, (hwnd, role, title, hidden, mini) in enumerate(snap):
            S._ensure_capture(hwnd)
            c = Card(grid, 431, 214, pad=12)
            c.grid(row=i // 2, column=i % 2, padx=(0 if i % 2 == 0 else 14, 0), pady=(0, 14))
            thumb = tk.Label(c.inner, image=BLANK, bg=CARD, bd=0)
            thumb.pack(side="left", anchor="n")
            col = tk.Frame(c.inner, bg=CARD)
            col.pack(side="left", fill="both", expand=True, padx=(12, 0))
            is_board = role == "board"
            tk.Label(col, text="●", bg=CARD, fg=OK if is_board else theme.cur, font=(F, 9)).pack(anchor="w")
            label(col, "Weather board" if is_board else "Roblox window", fg=TEXT, size=12, bold=True).pack(anchor="w")
            state = "minimised" if mini else ("hidden" if hidden else "visible")
            label(col, f"{(title or '?')[:18]} · {state}", size=8).pack(anchor="w", pady=(0, 8))
            label(col, "ACCOUNT", fg=FAINT, size=7, bold=True).pack(anchor="w")
            var = tk.StringVar(value=assign.get(hwnd, "— not set —"))
            opts = ["— not set —"] + account_names()
            om = tk.OptionMenu(col, var, *opts, command=lambda v, h=hwnd: set_assign(h, None if v.startswith("—") else v))
            style_menu(om)
            om.pack(anchor="w", pady=(2, 10), ipady=1)
            br = tk.Frame(col, bg=CARD)
            br.pack(anchor="w")
            if mini:
                Btn(br, "Restore", lambda h=hwnd: ctypes.windll.user32.ShowWindowAsync(h, 9), theme, w=86, h=32,
                    kind="primary", bg=CARD).pack(side="left")
            elif hidden:
                Btn(br, "Show", lambda h=hwnd: S._set_window_show(h, True), theme, w=86, h=32, kind="ok", bg=CARD).pack(side="left")
            else:
                Btn(br, "Hide", lambda h=hwnd: S._set_window_show(h, False), theme, w=86, h=32, bg=CARD).pack(side="left")
            confirm_btn(br, "Close", "Sure?", lambda h=hwnd: S._close_window(h), 74, 32).pack(side="left", padx=(8, 0))
            inst["cards"][hwnd] = thumb

    def update_thumbs():
        if nav["cur"] != "Instances":
            return
        for hwnd, lbl in list(inst["cards"].items()):
            st = S._caps.get(hwnd)
            arr = st.get("frame") if st else None
            if arr is None:
                continue
            im = Image.fromarray(arr[:, :, [2, 1, 0]])
            im = ImageOps.contain(im, (212, 134), Image.Resampling.BILINEAR)
            canvas_im = Image.new("RGB", (212, 134), (16, 22, 36))
            canvas_im.paste(im, ((212 - im.width) // 2, (134 - im.height) // 2))
            ph = ImageTk.PhotoImage(canvas_im)
            lbl.configure(image=ph)
            lbl.image = ph

    # ================================================================== ACCOUNTS (Roblox Account Manager style)
    from tkinter import ttk
    ap = pages["Accounts"]
    acct = {"sig": None, "note": "", "group": "All", "q": "", "sort": ("alias", False), "editing": None}
    row_ids = {}                                             # tree row id -> account alias

    def find_account(label_):
        for x in load_accounts():
            if x["label"] == label_:
                return x
        return None

    def account_windows(label_):
        return [e for e in list(S._snapshot) if assign.get(e[0]) == label_]

    def launch_account(a):
        exe, url = browser_exe(a["browser"]), S._launch_url()
        if not exe or not url:
            acct["note"] = "Browser or launch link not found"
            return
        try:
            subprocess.Popen([exe, f"--profile-directory={a['profile']}", url])
            lst = load_accounts()
            for x in lst:
                if x["label"] == a["label"]:
                    x["last"] = time.time()
            jsave("accounts.json", lst)
            acct["note"] = f"Opening as {a['label']}…"
            print(f"account: launching as '{a['label']}' ({a['browser']} / {a['profile']})")
        except OSError as e:
            acct["note"] = f"Couldn't launch: {e}"

    def launch_many(accts):
        if not accts:
            acct["note"] = "Select at least one account first"
            return
        if len(accts) > 1 and S.STATE["lock"] != "held":
            if not messagebox.askyesno("Multi-instance isn't on", "Roblox will close the earlier copies as the later ones open "
                                       "until multi-instance is on (use “Restart Roblox for multi-instance”).\n\nLaunch anyway?", parent=root):
                return
        delay = float(get("LAUNCH_DELAY") or 10)
        for i, a in enumerate(accts):
            root.after(int(i * delay * 1000), lambda x=a: launch_account(x))
        acct["note"] = f"Launching {len(accts)} account(s), {int(delay)} s apart…"

    def rejoin(a):
        wins = account_windows(a["label"])
        if wins and not messagebox.askyesno("Rejoin", f"Close {a['label']}'s Roblox window and join again?", parent=root):
            return
        for w_ in wins:
            S._close_window(w_[0])
        root.after(4500 if wins else 0, lambda: launch_account(a))
        acct["note"] = f"Rejoining as {a['label']}…"

    def restart_multi():
        alive = {h for h, *_r in S._snapshot}
        labels = []
        for h_, lbl in assign.items():
            if h_ in alive and lbl not in labels:
                labels.append(lbl)
        accts = [x for x in load_accounts() if x["label"] in labels]
        msg = "Close every Roblox window and turn multi-instance on" + (f", then relaunch: {', '.join(labels)}" if labels else "") + "?"
        if not messagebox.askyesno("Restart Roblox", msg, parent=root):
            return
        S._set_setting("MULTI_INSTANCE", True)
        S._close_all()
        acct["note"] = "Closing Roblox… waiting for the lock"
        t0 = time.time()

        def poll():
            if S.STATE["lock"] == "held" and not S._snapshot:
                acct["note"] = "Multi-instance is on."
                if accts:
                    launch_many(accts)
            elif time.time() - t0 > 45:
                acct["note"] = "Gave up waiting for Roblox to close"
            else:
                root.after(1000, poll)

        root.after(1500, poll)

    # ---- table style (dark)
    tstyle = ttk.Style()
    try:
        tstyle.theme_use("clam")
    except tk.TclError:
        pass
    tstyle.configure("RAM.Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT, rowheight=34, borderwidth=0,
                     relief="flat", font=(F, 10))
    tstyle.configure("RAM.Treeview.Heading", background=CARD2, foreground=DIM, relief="flat", borderwidth=0,
                     font=(F, 8, "bold"), padding=(10, 7))
    tstyle.map("RAM.Treeview.Heading", background=[("active", lerp(CARD2, "#ffffff", .08))])
    tstyle.layout("RAM.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    tstyle.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
        ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])          # slim bar, no arrow buttons
    tstyle.configure("Vertical.TScrollbar", background=CARD2, troughcolor=CARD, bordercolor=CARD,
                     lightcolor=CARD2, darkcolor=CARD2, gripcount=0, width=8)
    tstyle.map("Vertical.TScrollbar", background=[("active", lerp(CARD2, "#ffffff", .14)), ("!active", lerp(CARD2, "#ffffff", .05))],
               lightcolor=[("!disabled", CARD2)], darkcolor=[("!disabled", CARD2)])

    def tree_theme(accent):
        tstyle.map("RAM.Treeview", background=[("selected", lerp(CARD, accent, .38))], foreground=[("selected", TEXT)])

    theme.sub(tree_theme)

    # ---- toolbar
    tb = tk.Frame(ap, bg=BG)
    tb.pack(fill="x", pady=(0, 10))

    def selected():
        idx = {x["label"]: x for x in load_accounts()}
        return [idx[row_ids[i]] for i in tree.selection() if row_ids.get(i) in idx]

    def remove_selected():
        gone = {a["label"] for a in selected()}
        if not gone:
            return
        jsave("accounts.json", [x for x in load_accounts() if x["label"] not in gone])
        for h_ in [h for h, l_ in assign.items() if l_ in gone]:
            assign.pop(h_, None)
        jsave("assign.json", {str(k): v for k, v in assign.items()})
        acct["note"] = f"Removed {len(gone)} account(s)"
        clear_form()

    Btn(tb, "＋ Add", lambda: (clear_form(), acct.update(note="New account – fill in the panel on the right and Save")), theme, w=84,
        h=34, kind="primary", bg=BG).pack(side="left", padx=(0, 6))
    confirm_btn(tb, "Remove", "Sure?", remove_selected, 84, 34, bg=BG).pack(side="left", padx=(0, 14))
    Btn(tb, "▶ Launch", lambda: launch_account(selected()[0]) if selected() else acct.update(note="Select an account first"),
        theme, w=96, h=34, kind="ok", bg=BG).pack(side="left", padx=(0, 6))
    Btn(tb, "▶▶ Launch selected", lambda: launch_many(selected()), theme, w=150, h=34, kind="ok", bg=BG).pack(side="left", padx=(0, 6))
    Btn(tb, "↻ Rejoin", lambda: rejoin(selected()[0]) if selected() else acct.update(note="Select an account first"),
        theme, w=92, h=34, bg=BG).pack(side="left")
    search_var, grp_var = tk.StringVar(), tk.StringVar(value="All")
    sbox = tk.Frame(tb, bg=CARD, highlightbackground=EDGE, highlightthickness=1)
    sbox.pack(side="right")
    tk.Label(sbox, text="⌕", bg=CARD, fg=FAINT, font=(FS, 11)).pack(side="left", padx=(8, 2))
    tk.Entry(sbox, textvariable=search_var, bg=CARD, fg=TEXT, insertbackground=TEXT, relief="flat", font=(F, 10), width=13,
             bd=0, highlightthickness=0).pack(side="left", ipady=6, padx=(0, 8))
    grp_menu = tk.OptionMenu(tb, grp_var, "All")
    style_menu(grp_menu)
    grp_menu.config(width=9)
    grp_menu.pack(side="right", padx=(0, 8), ipady=4)

    # ---- table + details pane
    body = tk.Frame(ap, bg=BG)
    body.pack(fill="x")
    tcard = Card(body, 596, 336, pad=8)
    tcard.pack(side="left")
    COLS = (("alias", "ALIAS", 136), ("group", "GROUP", 82), ("browser", "BROWSER · PROFILE", 146), ("status", "STATUS", 100),
            ("last", "LAST LAUNCH", 96))
    tree = ttk.Treeview(tcard.inner, columns=[c_[0] for c_ in COLS], show="headings", selectmode="extended", style="RAM.Treeview")
    vsb = ttk.Scrollbar(tcard.inner, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    for key_, ttl_, w_ in COLS:
        tree.heading(key_, text=ttl_, anchor="w", command=lambda k=key_: sort_by(k))
        tree.column(key_, width=w_, anchor="w", stretch=key_ == "alias")
    tree.pack(side="left", fill="both", expand=True)
    tree.tag_configure("run", foreground=OK)

    def sort_by(key_):
        cur, rev = acct["sort"]
        acct["sort"] = (key_, (not rev) if cur == key_ else False)
        acct["sig"] = None
        rebuild_accounts()

    dcard = Card(body, 266, 336, pad=12)
    dcard.pack(side="left", padx=(14, 0))
    dp = dcard.inner
    label(dp, "ACCOUNT", fg=FAINT, size=7, bold=True).pack(anchor="w")
    alias_var, group_var, kind_var, prof_var = tk.StringVar(), tk.StringVar(), tk.StringVar(value="edge" if browser_exe("edge") else "chrome"), tk.StringVar()
    prof_choices = {}

    def frow(txt, var):
        r_ = tk.Frame(dp, bg=CARD)
        r_.pack(fill="x", pady=2)
        label(r_, txt, fg=DIM, size=8, width=8, anchor="w").pack(side="left")
        e_ = tk.Entry(r_, textvariable=var, bg=CARD2, fg=TEXT, insertbackground=TEXT, relief="flat", font=(F, 9), bd=0,
                      highlightthickness=0)
        e_.pack(side="left", fill="x", expand=True, ipady=4)
        return e_

    frow("Alias", alias_var)
    frow("Group", group_var)
    br_ = tk.Frame(dp, bg=CARD)
    br_.pack(fill="x", pady=2)
    label(br_, "Browser", fg=DIM, size=8, width=8, anchor="w").pack(side="left")
    kind_menu = tk.OptionMenu(br_, kind_var, "edge", "chrome", command=lambda *_: fill_profiles())
    style_menu(kind_menu)
    kind_menu.config(width=7)
    kind_menu.pack(side="left", ipady=1)
    pr_ = tk.Frame(dp, bg=CARD)
    pr_.pack(fill="x", pady=2)
    label(pr_, "Profile", fg=DIM, size=8, width=8, anchor="w").pack(side="left")
    prof_menu = tk.OptionMenu(pr_, prof_var, "")
    style_menu(prof_menu)
    prof_menu.config(width=17)
    prof_menu.pack(side="left", ipady=1)
    label(dp, "Notes", fg=DIM, size=8).pack(anchor="w", pady=(4, 0))
    notes_txt = tk.Text(dp, height=2, bg=CARD2, fg=TEXT, insertbackground=TEXT, relief="flat", font=(F, 9), bd=0,
                        highlightthickness=0, wrap="word")
    notes_txt.pack(fill="x", pady=(2, 4))

    def fill_profiles():
        menu = prof_menu["menu"]
        menu.delete(0, "end")
        prof_choices.clear()
        for d_, n_ in browser_profiles(kind_var.get()):
            t_ = f"{n_}  ({d_})"
            prof_choices[t_] = d_
            menu.add_command(label=t_, command=lambda t=t_: prof_var.set(t))
        if not prof_var.get() or prof_var.get() not in prof_choices:
            prof_var.set(next(iter(prof_choices), "no profiles found"))

    fill_profiles()

    def clear_form():
        acct["editing"] = None
        alias_var.set("")
        group_var.set("")
        notes_txt.delete("1.0", "end")
        fill_profiles()
        refresh_windows()

    def load_form(a):
        acct["editing"] = a["label"]
        alias_var.set(a["label"])
        group_var.set(a.get("group", ""))
        kind_var.set(a["browser"])
        fill_profiles()
        for t_, d_ in prof_choices.items():
            if d_ == a["profile"]:
                prof_var.set(t_)
        notes_txt.delete("1.0", "end")
        notes_txt.insert("1.0", a.get("notes", ""))
        refresh_windows()

    def save_form():
        nm, d_ = alias_var.get().strip(), prof_choices.get(prof_var.get())
        if not nm or not d_:
            acct["note"] = "Give it an alias and pick a browser profile"
            return
        lst = load_accounts()
        old = acct["editing"]
        if nm != old and any(x["label"] == nm for x in lst):
            acct["note"] = f"There is already an account called {nm}"
            return
        rec = {"label": nm, "browser": kind_var.get(), "profile": d_, "group": group_var.get().strip(),
               "notes": notes_txt.get("1.0", "end").strip()}
        for i, x in enumerate(lst):
            if x["label"] == old:
                rec["last"] = x.get("last")
                lst[i] = rec
                for h_, l_ in list(assign.items()):
                    if l_ == old:
                        assign[h_] = nm                     # keep its windows when the alias changes
                jsave("assign.json", {str(k): v for k, v in assign.items()})
                break
        else:
            lst.append(rec)
        jsave("accounts.json", lst)
        acct["editing"] = nm
        acct["note"] = f"Saved {nm}"
        acct["sig"] = None

    Btn(dp, "Save account", save_form, theme, w=234, h=32, kind="primary", bg=CARD).pack(pady=(0, 6))
    tk.Frame(dp, bg=EDGE, height=1).pack(fill="x", pady=(0, 4))
    label(dp, "WINDOWS", fg=FAINT, size=7, bold=True).pack(anchor="w")
    win_box = tk.Frame(dp, bg=CARD)
    win_box.pack(fill="x")
    assign_var = tk.StringVar(value="Assign a window…")
    assign_menu = tk.OptionMenu(dp, assign_var, "")
    style_menu(assign_menu)
    assign_menu.config(width=22)
    assign_menu.pack(anchor="w", pady=(4, 0), ipady=1)

    def refresh_windows():
        for ch in win_box.winfo_children():
            ch.destroy()
        lbl_ = acct["editing"]
        mine = account_windows(lbl_) if lbl_ else []
        if not lbl_:
            label(win_box, "Save the account first", fg=FAINT, size=8).pack(anchor="w", pady=2)
        for hwnd, role, title, hidden, mini in mine[:2]:
            r_ = tk.Frame(win_box, bg=CARD)
            r_.pack(fill="x", pady=2)
            label(r_, f"{(title or '?')[:9]} · {'hidden' if hidden else 'visible'}", fg=TEXT, size=8).pack(side="left")
            confirm_btn(r_, "✕", "Sure?", lambda h=hwnd: S._close_window(h), 52, 24, size=8).pack(side="right")
            Btn(r_, "Show" if hidden else "Hide", lambda h=hwnd, hd=hidden: S._set_window_show(h, hd), theme, w=52, h=24,
                bg=CARD, size=8).pack(side="right", padx=(0, 4))
        menu = assign_menu["menu"]
        menu.delete(0, "end")
        free = [e for e in list(S._snapshot) if e[0] not in assign]
        for hwnd, role, title, hidden, mini in free:
            txt = f"{(title or 'Roblox')[:14]} ({'board' if role == 'board' else 'other'}) #{hwnd % 10000}"
            menu.add_command(label=txt, command=lambda h=hwnd: (set_assign(h, lbl_) if lbl_ else None, acct.update(sig=None)))
        assign_var.set("Assign a window…" if free else "no free windows")

    def on_select(_=None):
        sel_ = selected()
        if len(sel_) == 1:
            load_form(sel_[0])

    tree.bind("<<TreeviewSelect>>", on_select)

    # ---- bottom bar: where launches go, delay, multi-instance
    bot = Card(ap, CW, 104, pad=12)
    bot.pack(pady=(10, 0))
    bi = bot.inner
    r1_ = tk.Frame(bi, bg=CARD)
    r1_.pack(fill="x")
    label(r1_, "LAUNCH TO", fg=FAINT, size=7, bold=True, width=14, anchor="w").pack(side="left")
    lt_seg = Seg(r1_, [("Game page", "game"), ("Private server", "private"), ("Custom", "custom")], lambda: get("LAUNCH_TARGET"),
                 lambda v: S._set_setting("LAUNCH_TARGET", v), theme, seg_w=116)
    lt_seg.pack(side="left")
    cust_var = tk.StringVar(value=str(get("LAUNCH_URL_CUSTOM")))
    cust = tk.Entry(r1_, textvariable=cust_var, bg=CARD2, fg=TEXT, insertbackground=TEXT, relief="flat", font=(F, 9), bd=0,
                    highlightthickness=0, width=26)
    cust.pack(side="left", padx=(12, 0), ipady=6)
    label(r1_, "link or place ID", fg=FAINT, size=8).pack(side="left", padx=6)

    def cust_commit(_=None):
        if cust_var.get().strip() != str(get("LAUNCH_URL_CUSTOM")):
            S._set_setting("LAUNCH_URL_CUSTOM", cust_var.get().strip())

    cust.bind("<Return>", cust_commit)
    cust.bind("<FocusOut>", cust_commit)
    r2_ = tk.Frame(bi, bg=CARD)
    r2_.pack(fill="x", pady=(10, 0))
    label(r2_, "DELAY BETWEEN", fg=FAINT, size=7, bold=True, width=14, anchor="w").pack(side="left")
    dl_seg = Seg(r2_, [("5 s", 5.0), ("10 s", 10.0), ("20 s", 20.0)], lambda: get("LAUNCH_DELAY"),
                 lambda v: S._set_setting("LAUNCH_DELAY", v), theme, seg_w=64)
    dl_seg.pack(side="left")
    confirm_btn(r2_, "Restart Roblox for multi-instance", "Sure? Click again", restart_multi, 250, 30).pack(side="right")
    multi_lbl = label(r2_, "", fg=DIM, size=8, bold=True)
    multi_lbl.pack(side="right", padx=12)
    acct_note = label(ap, "", bg=BG, fg=WARN, size=9)
    acct_note.pack(anchor="w", pady=(6, 0))

    def rebuild_accounts():
        lt_seg.sync()
        dl_seg.sync()
        lst = load_accounts()
        alive = {h for h, *_r in S._snapshot}
        sig = (json.dumps(lst), tuple(sorted((k, v) for k, v in assign.items() if k in alive)), tuple(sorted(alive)),
               acct["group"], acct["q"], acct["sort"], int(time.time() // 30))
        lock_txt = {"held": ("multi-instance ON", OK), "waiting": ("multi-instance: close Roblox first", WARN),
                    "off": ("multi-instance off", FAINT), "failed": ("multi-instance error", BAD)}.get(S.STATE["lock"], ("", FAINT))
        multi_lbl.config(text=lock_txt[0], fg=lock_txt[1])
        if sig == acct["sig"]:
            return
        acct["sig"] = sig
        keep_sel = {row_ids.get(i) for i in tree.selection()}
        tree.delete(*tree.get_children())
        row_ids.clear()
        groups = sorted({x.get("group", "") for x in lst if x.get("group")})
        m = grp_menu["menu"]
        m.delete(0, "end")
        for g in ["All"] + groups:
            m.add_command(label=g, command=lambda g=g: (grp_var.set(g), acct.update(group=g, sig=None), rebuild_accounts()))
        if acct["group"] not in ["All"] + groups:
            acct["group"] = "All"
            grp_var.set("All")
        rows = []
        for x in lst:
            wins = [h for h, l_ in assign.items() if l_ == x["label"] and h in alive]
            hay = f"{x['label']} {x.get('group', '')} {x.get('notes', '')}".lower()
            if acct["group"] != "All" and x.get("group", "") != acct["group"]:
                continue
            if acct["q"] and acct["q"] not in hay:
                continue
            rows.append((x, wins))
        key_, rev = acct["sort"]
        keyf = {"alias": lambda r: r[0]["label"].lower(), "group": lambda r: r[0].get("group", "").lower(),
                "browser": lambda r: (r[0]["browser"], r[0]["profile"]), "status": lambda r: -len(r[1]),
                "last": lambda r: -(r[0].get("last") or 0)}[key_]
        rows.sort(key=keyf, reverse=rev)
        for x, wins in rows:
            status = f"● In game ({len(wins)})" if wins else "○ Idle"
            last = ago(time.time() - x["last"]) if x.get("last") else "–"
            iid = tree.insert("", "end", values=(x["label"], x.get("group", ""), f"{x['browser'].title()} · {x['profile']}", status, last),
                              tags=("run",) if wins else ())
            row_ids[iid] = x["label"]
            if x["label"] in keep_sel:
                tree.selection_add(iid)
        if len(rows) > 8:                                      # only show a scrollbar when the list is longer than the table
            vsb.pack(side="right", fill="y")
        else:
            vsb.pack_forget()
        refresh_windows()

    # ================================================================== ACTIVITY
    ac_page = pages["Activity"]
    feed_big_card = Card(ac_page, 540, 520, pad=12)
    feed_big_card.pack(side="left", anchor="n")
    label(feed_big_card.inner, "LOG  (live)", fg=FAINT, size=7, bold=True).pack(anchor="w")
    feed_big = tk.Text(feed_big_card.inner, bg=CARD, fg=DIM, font=("Consolas", 9), bd=0, relief="flat",
                       highlightthickness=0, state="disabled", wrap="none", cursor="arrow")
    feed_big.pack(fill="both", expand=True, pady=(4, 0))
    hist_card = Card(ac_page, 322, 520, pad=0)
    hist_card.pack(side="left", padx=(14, 0), anchor="n")
    hist = {"sig": None}

    def rebuild_history():
        recs = S._history_load(7)
        sig = (len(recs), recs[-1]["t"] if recs else 0, tuple(sorted(S.KNOWN)), int(time.time() // 60))
        if sig == hist["sig"]:
            return
        hist["sig"] = sig
        hist_card.delete("hist")
        hist_card.create_text(20, 26, text="EVENTS THIS WEEK", fill=FAINT, font=(F, 7, "bold"), anchor="w", tags="hist")
        by = collections.defaultdict(list)
        for r in sorted(recs, key=lambda r: r["t"]):
            by[r["name"]].append(r["t"])
        names = sorted(set(by) | set(S.KNOWN), key=lambda n: -len(by.get(n, [])))
        top = max([1] + [len(v) for v in by.values()])
        y = 54
        for n in names[:9]:
            glyph, col, _e = S._event_style(n)
            times = by.get(n, [])
            hist_card.create_text(22, y, text=glyph, fill=col, font=(FS, 13), anchor="w", tags="hist")
            hist_card.create_text(48, y, text=n.title(), fill=TEXT, font=(F, 9, "bold"), anchor="w", tags="hist")
            hist_card.create_text(300, y, text=str(len(times)), fill=col, font=(F, 11, "bold"), anchor="e", tags="hist")
            rr(hist_card, 48, y + 14, 290, y + 20, 3, fill=CARD2, outline="", tags="hist")
            if times:
                rr(hist_card, 48, y + 14, 48 + max(8, 242 * len(times) / top), y + 20, 3, fill=col, outline="", tags="hist")
            if len(times) >= 2:
                gaps = [b - a for a, b in zip(times, times[1:])]
                info = f"every ~{int(sum(gaps) / len(gaps) / 60)} min · last {ago(time.time() - times[-1])}"
            elif times:
                info = f"last {ago(time.time() - times[-1])}"
            else:
                info = "not seen yet"
            hist_card.create_text(48, y + 33, text=info, fill=FAINT, font=(F, 8), anchor="w", tags="hist")
            y += 52
        hist_card.create_text(20, 500, text=f"{len(recs)} events recorded in the last 7 days", fill=DIM, font=(F, 8),
                              anchor="w", tags="hist")

    # ================================================================== SETTINGS (scrollable, two columns)
    sp = pages["Settings"]
    sc_canvas = tk.Canvas(sp, bg=BG, highlightthickness=0, bd=0, width=CW, height=520)
    sc_inner = tk.Frame(sc_canvas, bg=BG)
    sc_canvas.create_window(0, 0, window=sc_inner, anchor="nw", width=CW)
    sc_canvas.pack(fill="both")
    sc_inner.bind("<Configure>", lambda e: sc_canvas.configure(scrollregion=sc_canvas.bbox("all")))
    root.bind_all("<MouseWheel>", lambda e: sc_canvas.yview_scroll(int(-e.delta / 120) * 2, "units")
                  if nav["cur"] == "Settings" else None)
    colL = tk.Frame(sc_inner, bg=BG)
    colL.pack(side="left", anchor="n")
    colR = tk.Frame(sc_inner, bg=BG)
    colR.pack(side="left", anchor="n", padx=(14, 0))
    COLW = 431
    syncers = []

    def sec(parent, title):
        f = tk.Frame(parent, bg=CARD, highlightbackground=EDGE, highlightthickness=1)
        f.pack(pady=(0, 14), ipadx=4, fill="x")               # fill: every card in a column is as wide as the widest
        tk.Frame(f, bg=CARD, width=COLW - 10, height=1).pack()
        label(f, title, fg=FAINT, size=7, bold=True).pack(anchor="w", padx=14, pady=(10, 4))
        return f

    def switch_row(parent, text, name, note=None):
        r = tk.Frame(parent, bg=CARD)
        r.pack(fill="x", padx=14, pady=5)
        col = tk.Frame(r, bg=CARD)
        col.pack(side="left")
        label(col, text, fg=TEXT).pack(anchor="w")
        if note:
            label(col, note, fg=FAINT, size=8).pack(anchor="w")
        sw = Switch(r, lambda v, n=name: S._set_setting(n, v), theme)
        sw.pack(side="right")
        syncers.append(lambda: sw.set(bool(get(name))))

    def seg_row(parent, text, note, choices, name, w=64):
        r = tk.Frame(parent, bg=CARD)
        r.pack(fill="x", padx=14, pady=(6, 2))
        label(r, text, fg=TEXT).pack(anchor="w")
        if note:
            label(r, note, fg=FAINT, size=8).pack(anchor="w")
        sg = Seg(parent, choices, lambda: get(name), lambda v: S._set_setting(name, v), theme, seg_w=w)
        sg.pack(anchor="w", padx=12, pady=(2, 8))
        syncers.append(sg.sync)

    # ---- performance mode
    pm = sec(colL, "PERFORMANCE MODE")
    label(pm, "Changes how the console looks, how often it draws, and how hard the sentinel works.", fg=FAINT, size=8,
          justify="left").pack(anchor="w", padx=14)
    mode_btns = []
    for key_, m_ in MODES.items():
        row_ = tk.Frame(pm, bg=CARD)
        row_.pack(fill="x", padx=12, pady=(5, 0))
        sw_ = tk.Canvas(row_, width=34, height=34, bg=CARD, highlightthickness=0, bd=0)
        sw_.create_oval(3, 3, 31, 31, fill=m_["pal"][1], outline=m_["pal"][7], width=2)
        sw_.create_text(17, 17, text=m_["glyph"], fill=m_["pal"][7], font=(FS, 11))
        sw_.pack(side="left", padx=(2, 8))
        b_ = Btn(row_, f"{m_['label']}   -   {m_['blurb']}", lambda k=key_: choose_mode(k), theme, w=COLW - 80, h=34, bg=CARD, size=8)
        b_.pack(side="left")
        mode_btns.append((b_, key_))
    tk.Frame(pm, bg=CARD, height=8).pack()
    syncers.append(lambda: [b_.set(kind="primary" if k == UI_MODE else "ghost") for b_, k in mode_btns])
    label(pm, "Stealth hides every window, the tray icon and all alerts. Leaving it brings them back as they were.", fg=FAINT,
          size=8, justify="left", wraplength=COLW - 40).pack(anchor="w", padx=14, pady=(0, 10))

    # ---- health check
    hc = sec(colL, "HEALTH CHECK")
    hc_top = tk.Frame(hc, bg=CARD)
    hc_top.pack(fill="x", padx=14, pady=(0, 6))
    label(hc_top, "Checks that everything is wired up and reading properly.", fg=FAINT, size=8).pack(side="left")
    hc_rows = tk.Frame(hc, bg=CARD)
    hc_rows.pack(fill="x", padx=14, pady=(0, 10))

    def fix_capture():
        for h_, *_r in list(S._snapshot):
            S._caps.pop(h_, None)
            S._ensure_capture(h_)

    def trim_log():
        try:
            lines = open(S.LOG_PATH, encoding="utf-8", errors="replace").read().splitlines()[-1500:]
            open(S.LOG_PATH, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        except OSError as e:
            print("couldn't trim the log:", e)

    def run_checks():
        for ch in hc_rows.winfo_children():
            ch.destroy()
        snap = list(S._snapshot)
        live = [e for e in snap if not e[4]]
        now_ = time.time()
        res = []                                          # (state True/False/None, title, detail, (fix label, fn))

        res.append((bool(snap), "Roblox is running", f"{len(snap)} window(s)" if snap else "no Roblox window found",
                    None if snap else ("New instance", S._launch_instance)))
        got = [e for e in live if (S._caps.get(e[0]) or {}).get("frame") is not None]
        res.append((None if not live else len(got) == len(live), "Screen capture",
                    "nothing to capture" if not live else f"{len(got)} of {len(live)} window(s) delivering pictures",
                    None if not live or len(got) == len(live) else ("Restart capture", fix_capture)))
        lr = S.STATE["last_read"]
        res.append((bool(lr) and now_ - lr < 20, "Reading the game", f"last read {ago(now_ - lr)}" if lr else "no reading yet", None))
        try:
            S.ocr_win(Image.new("RGB", (240, 70), "white"))
            res.append((True, "Text recognition (OCR)", "Windows OCR is ready", None))
        except Exception as e:
            res.append((False, "Text recognition (OCR)", str(e)[:60], None))
        bs = S.STATE["board_seen"]
        res.append((True if bs and now_ - bs < 600 else None, "Weather sign in view",
                    f"seen {ago(now_ - bs)}" if bs else "not seen yet - put the sign on screen", None))
        wk = S.STATE["worker"]
        res.append((wk["ok"], "Bot link", "connected" if wk["ok"] else (wk["msg"] or "unreachable") if wk["ok"] is False
                    else "no report sent yet", None))
        try:
            size = os.path.getsize(S.LOG_PATH) / 1e6
        except OSError:
            size = 0
        res.append((size < 15, "Log file size", f"{size:.1f} MB", None if size < 15 else ("Trim log", trim_log)))
        for state, ttl, detail, fix in res:
            r_ = tk.Frame(hc_rows, bg=CARD)
            r_.pack(fill="x", pady=3)
            tk.Label(r_, text="✔" if state else "✖" if state is False else "!", bg=CARD, width=2,
                     fg=OK if state else BAD if state is False else WARN, font=(FS, 12, "bold")).pack(side="left")
            col_ = tk.Frame(r_, bg=CARD)
            col_.pack(side="left")
            label(col_, ttl, fg=TEXT).pack(anchor="w")
            label(col_, detail, fg=FAINT, size=8).pack(anchor="w")
            if fix:
                Btn(r_, fix[0], lambda f=fix[1]: (f(), root.after(600, run_checks)), theme, w=110, h=28, kind="primary",
                    bg=CARD, size=8).pack(side="right")

    Btn(hc_top, "Run checks", run_checks, theme, w=110, h=30, kind="primary", bg=CARD).pack(side="right")

    perf = sec(colL, "PERFORMANCE")
    pr = tk.Frame(perf, bg=CARD)
    pr.pack(fill="x", padx=12, pady=(0, 6))
    label(pr, "Preset", fg=TEXT).pack(anchor="w", padx=2, pady=(0, 4))
    preset_btns = {}
    for nm in PRESETS:
        b = Btn(pr, nm, lambda n=nm: [S._set_setting(k, v) for k, v in PRESETS[n].items()], theme, w=126, h=32, bg=CARD)
        b.pack(side="left", padx=2)
        preset_btns[nm] = b
    syncers.append(lambda: [preset_btns[n].set(kind="primary" if all(same(get(k), v) for k, v in PRESETS[n].items()) else "ghost")
                            for n in PRESETS])
    seg_row(perf, "Read speed", "How often it looks at the game", [("1 s", 1.0), ("2 s", 2.0), ("5 s", 5.0)], "POLL_SECONDS", 92)
    seg_row(perf, "Hidden Roblox may use", "CPU cores while parked (fewer = cooler)",
            [("All", 0), ("1", 1), ("2", 2), ("3", 3), ("4", 4)], "PARK_CORES", 56)
    switch_row(perf, "Low CPU for hidden Roblox", "THROTTLE_PARKED", "Lower priority + efficiency mode")
    switch_row(perf, "Slow down when board is away", "IDLE_BACKOFF", "Looks less often if it can't see the sign")

    # ---- alerts
    al = sec(colL, "ALERTS")
    switch_row(al, "Alerts", "NOTIFY", "Master switch for this PC")
    switch_row(al, "Self-check alerts", "SELFCHECK", "Warn if Roblox closes or the bot link drops")
    switch_row(al, "Pop-up for problems", "SELFCHECK_TOAST", "Shown even when event alerts are off")
    switch_row(al, "Sound", "NOTIFY_SOUND")
    switch_row(al, "Pop-up notification", "NOTIFY_TOAST")
    switch_row(al, "\"Next event\" notice", "NOTIFY_NEXT", "Also alert when a new next event appears")
    label(al, "Alert me for these events", fg=TEXT).pack(anchor="w", padx=14, pady=(8, 2))
    evbox = tk.Frame(al, bg=CARD)
    evbox.pack(fill="x", pady=(0, 8))
    ev_state = {"sig": None, "sync": []}

    def rebuild_events():
        names = tuple(sorted(S.KNOWN))
        if names == ev_state["sig"]:
            return
        ev_state["sig"] = names
        ev_state["sync"].clear()
        for ch in evbox.winfo_children():
            ch.destroy()
        for n in names:
            glyph, col, _e = S._event_style(n)
            rw = tk.Frame(evbox, bg=CARD)
            rw.pack(fill="x", padx=14, pady=3)
            tk.Label(rw, text=glyph, bg=CARD, fg=col, font=(FS, 12)).pack(side="left", padx=(6, 6))
            label(rw, n.title(), fg=TEXT).pack(side="left")
            sw = Switch(rw, lambda v, k=n: S._set_alert(k, v), theme)
            sw.pack(side="right")
            sw.set(n not in S.ALERT_OFF)
            ev_state["sync"].append(lambda s_=sw, k=n: s_.set(k not in S.ALERT_OFF))

    # ---- windows
    # ---- auto-place: record once per mutation, replay when it starts
    mc = sec(colL, "AUTO-PLACE ON EVENT")
    label(mc, "Record steps once per mutation: one set for when it STARTS, one for when it ENDS.\nThey replay in that account's window, wherever it is (parked, minimised or moved).",
          fg=FAINT, size=8, justify="left").pack(anchor="w", padx=14)
    mbox = tk.Frame(mc, bg=CARD)
    mbox.pack(fill="x", padx=14, pady=(6, 4))
    switch_row(mc, "Check it worked", "MACRO_VERIFY", "Looks at the screen afterwards; retries once if nothing changed")
    switch_row(mc, "Skip if the event is nearly over", "MACRO_SKIP_LATE", "Under about 20 seconds left, or already ended")
    switch_row(mc, "Alert me if it fails", "MACRO_ALERT", "Only when it has given up, not on each retry")
    seg_row(mc, "Retries", "When the window is covered or won't take focus", [("1", 1), ("2", 2), ("3", 3), ("5", 5)], "MACRO_RETRIES", 56)
    class StatusDict(dict):
        """Short-lived messages (recording, cleared...) remember when they were set so they fade back to the last result."""
        def __init__(self):
            super().__init__()
            self.t = {}

        def __setitem__(self, k, v):
            super().__setitem__(k, v)
            self.t[k] = time.time()

    mac = {"sig": None, "status": StatusDict(), "labels": {}, "switches": {}}

    def mrec(ev, ph="start"):
        m_ = S._macros_load().get(ev, {})
        a_ = m_.get("account")
        if not a_:
            mac["status"][ev] = "pick an account first"
            return
        hw_ = S._macro_window(a_)
        if hw_ is None:
            mac["status"][ev] = "assign a window to that account first"
            return
        if not messagebox.askokcancel("Record steps", f"After you click OK, switch to {a_}'s Roblox window and do the steps " +
                                      ("(equip the gnome, click to place it)." if ph == "start" else "(what should happen once the event is over, e.g. pick your normal gnome back up).") + "\n\n" + "Press Ctrl+Shift+F8 when you're done.", parent=root):
            return
        k_ = (ev, ph)
        mac["status"][k_] = "● recording…  Ctrl+Shift+F8 to finish"
        S._macro_record(ev, hw_, phase=ph, done=lambda n, k=k_: mac["status"].__setitem__(k, "busy - a replay or recording is already running" if n < 0 else f"recorded {n} steps" if n else "nothing was recorded"))

    def mtest(ev, ph="start"):
        k_ = (ev, ph)
        mac["status"][k_] = "running…"
        threading.Thread(target=lambda: mac["status"].__setitem__(k_, "done" if S._macro_play(ev, True, ph) else "couldn't run (see the log)"),
                         daemon=True).start()

    def rebuild_macros():
        macros = S._macros_load()
        accts_ = [a["label"] for a in load_accounts()]
        sig = (tuple(sorted(S.KNOWN)), tuple(accts_), json.dumps({k: (v.get("account"), v.get("enabled")) for k, v in macros.items()}))
        if sig != mac["sig"]:
            mac["sig"] = sig
            for ch in mbox.winfo_children():
                ch.destroy()
            mac["labels"].clear()
            mac["switches"].clear()
            for ev in sorted(S.KNOWN):
                glyph, col, _e = S._event_style(ev)
                card_ = tk.Frame(mbox, bg=CARD2)
                card_.pack(fill="x", pady=3)
                top_ = tk.Frame(card_, bg=CARD2)
                top_.pack(fill="x", padx=10, pady=(8, 2))
                sw_ = Switch(top_, lambda v, e=ev: S._macro_set(e, enabled=v), theme, bg=CARD2)
                sw_.pack(side="left")
                tk.Label(top_, text=glyph, bg=CARD2, fg=col, font=(FS, 12)).pack(side="left", padx=(8, 4))
                label(top_, ev.title(), bg=CARD2, fg=TEXT, bold=True).pack(side="left")
                var_ = tk.StringVar(value=macros.get(ev, {}).get("account") or "account…")
                om_ = tk.OptionMenu(top_, var_, *(accts_ or ["(no accounts)"]),
                                    command=lambda v, e=ev: S._macro_set(e, account=v) if v in accts_ else None)
                style_menu(om_)
                om_.config(width=15, bg=CARD)
                om_.pack(side="right")
                for ph_, cap_ in (("start", "START"), ("end", "END")):
                    bot_ = tk.Frame(card_, bg=CARD2)
                    bot_.pack(fill="x", padx=10, pady=(2, 6 if ph_ == "end" else 2))
                    label(bot_, cap_, bg=CARD2, fg=FAINT, size=7, bold=True).pack(side="left", padx=(0, 6))
                    Btn(bot_, "● Record", lambda e=ev, p=ph_: mrec(e, p), theme, w=84, h=26, kind="danger", bg=CARD2, size=8).pack(side="left")
                    Btn(bot_, "▶ Test", lambda e=ev, p=ph_: mtest(e, p), theme, w=70, h=26, bg=CARD2, size=8).pack(side="left", padx=6)
                    Btn(bot_, "Clear", lambda e=ev, p=ph_: (S._macro_set(e, **{S._phase_key(p): []}), mac["status"].__setitem__((e, p), "cleared")),
                        theme, w=60, h=26, bg=CARD2, size=8).pack(side="left")
                    st_ = label(bot_, "", bg=CARD2, fg=DIM, size=8)
                    st_.pack(side="left", padx=10)
                    mac["labels"][(ev, ph_)] = st_
                mac["switches"][ev] = sw_
        for (ev, ph_), st_ in mac["labels"].items():
            m_ = macros.get(ev, {})
            n_ = len(m_.get(S._phase_key(ph_)) or [])
            k_ = (ev, ph_)
            msg_ = mac["status"].get(k_)
            live_ = msg_ and (msg_.startswith("●") or msg_.startswith("running") or time.time() - mac["status"].t.get(k_, 0) < 6)
            run_ = S.MACRO_STATUS.get(k_)
            if live_:
                st_.config(text=msg_, fg=WARN if msg_.startswith(("●", "running")) else DIM)
            elif run_:
                st_.config(text=("✔ " if run_["ok"] else "✖ ") + time.strftime("%H:%M", time.localtime(run_["t"])) + "  " + run_["msg"],
                           fg=OK if run_["ok"] else BAD)
            else:
                st_.config(text=f"{n_} steps recorded" if n_ else "nothing recorded yet", fg=DIM)
        for ev, sw_ in mac["switches"].items():
            sw_.set(bool(macros.get(ev, {}).get("enabled")))

    syncers.append(rebuild_macros)

    ap_ = sec(colR, "APPEARANCE")
    label(ap_, "Accent colour", fg=TEXT).pack(anchor="w", padx=14)
    label(ap_, "Follow the event, or lock one colour", fg=FAINT, size=8).pack(anchor="w", padx=14)
    swrow = tk.Frame(ap_, bg=CARD)
    swrow.pack(anchor="w", padx=10, pady=(6, 8))
    swatches = []
    for val in ("event", "#3dd6ff", "#5cffc4", "#ff5fa8", "#ffc83d", "#b06bff", "#ff7a45"):
        cv = tk.Canvas(swrow, width=36, height=36, bg=CARD, highlightthickness=0, bd=0, cursor="hand2")
        ringi = cv.create_oval(2, 2, 34, 34, outline=CARD, width=2)
        if val == "event":
            for i, col_ in enumerate(("#5cffc4", "#8fb8ff", "#b06bff", "#ff5fa8", "#ff8a2b", "#8dff4d")):
                cv.create_arc(7, 7, 29, 29, start=i * 60, extent=60, fill=col_, outline="")
        else:
            cv.create_oval(7, 7, 29, 29, fill=val, outline="")
        cv.bind("<ButtonRelease-1>", lambda e, v=val: S._set_setting("ACCENT", v))
        cv.pack(side="left", padx=2)
        swatches.append((cv, ringi, val))
    syncers.append(lambda: [cv.itemconfig(r_, outline=TEXT if get("ACCENT") == v else CARD) for cv, r_, v in swatches])
    seg_row(ap_, "Animations", "Motion in the sidebar and cards. Off uses no extra CPU.",
            [("Off", "off"), ("Low", "low"), ("Normal", "normal")], "ANIMATIONS", 92)

    wn = sec(colR, "WINDOWS")
    switch_row(wn, "Hide the board window", "HIDE_WINDOW")
    switch_row(wn, "Hide all instances", "HIDE_ALL", "Every Roblox window, not just the board's")
    switch_row(wn, "Clean up Roblox helpers", "CLEAN_HELPERS", "Closes window-less tray launchers")
    switch_row(wn, "Allow multiple Roblox", "MULTI_INSTANCE", "Bloxstrap-style: the sentinel owns Roblox's lock")

    def entry_row(parent, text, name):
        rw = tk.Frame(parent, bg=CARD)
        rw.pack(fill="x", padx=14, pady=5)
        label(rw, text, fg=TEXT, width=14, anchor="w").pack(side="left")
        var = tk.StringVar(value=str(get(name)))
        e = tk.Entry(rw, textvariable=var, bg=CARD2, fg=TEXT, insertbackground=TEXT, relief="flat", font=(F, 9), width=22)
        e.pack(side="left", ipady=4)

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

    entry_row(wn, "Board window", "WINDOW_TITLE")
    entry_row(wn, "Other windows", "OTHER_TITLE")
    ir = tk.Frame(wn, bg=CARD)
    ir.pack(fill="x", padx=14, pady=(5, 10))
    label(ir, "Window icon", fg=TEXT, width=14, anchor="w").pack(side="left")
    icon_var = tk.StringVar()
    IG, IN, IC = "Gnome (built-in)", "Roblox's own", "Choose a file…"

    def icon_pick(choice):
        if choice == IG:
            S._set_setting("ROBLOX_ICON", "gnome")
        elif choice == IN:
            S._set_setting("ROBLOX_ICON", "")
        else:
            p = filedialog.askopenfilename(parent=root, title="Choose an .ico file", filetypes=[("Icon", "*.ico")])
            if p:
                S._set_setting("ROBLOX_ICON", p)

    im_ = tk.OptionMenu(ir, icon_var, IG, IN, IC, command=icon_pick)
    style_menu(im_)
    im_.config(width=18)
    im_.pack(side="left", ipady=1)
    syncers.append(lambda: icon_var.set(IG if S.ROBLOX_ICON.lower() == "gnome" else IN if not S.ROBLOX_ICON
                                        else os.path.basename(S.ROBLOX_ICON)))

    # ---- stay connected
    kp = sec(colR, "STAY CONNECTED")
    switch_row(kp, "Click to stay connected", "KEEPALIVE", "Clicks the middle of each Roblox window")
    seg_row(kp, "How often", None, [("5 min", 5.0), ("10 min", 10.0), ("15 min", 15.0)], "KEEPALIVE_MINUTES", 92)
    switch_row(kp, "Auto-reconnect", "AUTO_RECONNECT", "Presses Reconnect if Roblox disconnects")

    # ---- hotkeys
    hk = sec(colR, "HOTKEYS")

    def capture_hotkey(name, what):
        w = tk.Toplevel(root)
        w.title("Set hotkey")
        w.configure(bg=BG)
        w.attributes("-topmost", True)
        w.geometry(f"+{root.winfo_rootx() + 300}+{root.winfo_rooty() + 240}")
        label(w, f"Press the keys for “{what}”", bg=BG, fg=TEXT, size=11, bold=True).pack(padx=28, pady=(20, 4))
        msg = label(w, "Hold Ctrl / Shift / Alt, then press a key.   Esc cancels.", bg=BG, fg=DIM, size=8)
        msg.pack(padx=28, pady=(0, 20))
        chrome(w, glass=False)
        w.focus_force()
        w.grab_set()

        def on_key(e):
            ks = e.keysym.lower()
            if ks in ("shift_l", "shift_r", "control_l", "control_r", "alt_l", "alt_r", "win_l", "win_r", "caps_lock"):
                return
            if ks == "escape":
                w.destroy()
                return
            mods = (["ctrl"] if e.state & 0x4 else []) + (["alt"] if e.state & 0x20000 else []) + (["shift"] if e.state & 0x1 else [])
            if not mods:
                msg.config(text="Add Ctrl, Shift or Alt too – a bare key would fire while you type", fg=WARN)
                return
            key = ks if (len(ks) == 1 or (ks.startswith("f") and ks[1:].isdigit())) else None
            if key is None:
                msg.config(text="Use a letter, a number or an F-key", fg=WARN)
                return
            w.destroy()
            S._set_setting(name, "+".join(mods + [key]))

        w.bind("<KeyPress>", on_key)

    for text, name in (("Show / hide Roblox", "HOTKEY"), ("Show / hide this window", "PANEL_HOTKEY"),
                       ("Command palette (optional, global)", "PALETTE_HOTKEY"),
                       ("Panic - hide everything", "PANIC_HOTKEY")):
        rw = tk.Frame(hk, bg=CARD)
        rw.pack(fill="x", padx=14, pady=5)
        label(rw, text, fg=TEXT).pack(side="left")
        if name == "PALETTE_HOTKEY":
            Btn(rw, "×", lambda: S._set_setting("PALETTE_HOTKEY", ""), theme, w=30, h=28, bg=CARD, size=11).pack(side="right", padx=(6, 0))
        b = Btn(rw, str(get(name)) or "off", lambda n=name, t=text: capture_hotkey(n, t), theme, w=150, h=28, bg=CARD, size=8)
        b.pack(side="right")
        syncers.append(lambda b_=b, n=name: b_.set(str(get(n)) or "off"))
    tk.Frame(hk, bg=CARD, height=6).pack()

    # ---- launch target
    ln = sec(colR, "NEW INSTANCE OPENS")
    ls = Seg(ln, [("Game page", "game"), ("Private", "private"), ("Custom", "custom")], lambda: get("LAUNCH_TARGET"),
             lambda v: S._set_setting("LAUNCH_TARGET", v), theme, seg_w=126)
    ls.pack(padx=12, pady=(2, 4), anchor="w")
    syncers.append(ls.sync)
    entry_row(ln, "Custom link", "LAUNCH_URL_CUSTOM")

    # ---- scenes management
    sm = sec(colR, "MY SCENES")
    label(sm, "Save the current settings as a one-click scene.", fg=FAINT, size=8).pack(anchor="w", padx=14)
    sr = tk.Frame(sm, bg=CARD)
    sr.pack(fill="x", padx=14, pady=(6, 4))
    scene_name = tk.StringVar()
    tk.Entry(sr, textvariable=scene_name, bg=CARD2, fg=TEXT, insertbackground=TEXT, relief="flat", font=(F, 10),
             width=20).pack(side="left", ipady=5)

    def save_scene():
        nm = scene_name.get().strip()
        if not nm or nm in DEFAULT_SCENES:
            messagebox.showinfo("Scenes", "Pick a name that isn't one of the built-in scenes.", parent=root)
            return
        mine = jload("scenes.json", {})
        mine[nm] = {k: get(k) for k in SCENE_KEYS}
        jsave("scenes.json", mine)
        scene_name.set("")
        print(f"scene saved: {nm}")

    Btn(sr, "Save scene", save_scene, theme, w=110, h=32, kind="primary", bg=CARD).pack(side="left", padx=8)
    mine_box = tk.Frame(sm, bg=CARD)
    mine_box.pack(fill="x", padx=14, pady=(2, 10))
    mine_state = {"sig": None}

    def rebuild_mine():
        mine = jload("scenes.json", {})
        sig = tuple(sorted(mine))
        if sig == mine_state["sig"]:
            return
        mine_state["sig"] = sig
        for ch in mine_box.winfo_children():
            ch.destroy()
        for nm in mine:
            r_ = tk.Frame(mine_box, bg=CARD)
            r_.pack(fill="x", pady=2)
            label(r_, nm, fg=TEXT).pack(side="left")

            def rm(n=nm):
                d = jload("scenes.json", {})
                d.pop(n, None)
                jsave("scenes.json", d)

            Btn(r_, "×", rm, theme, w=32, h=26, bg=CARD, size=11).pack(side="right")
            Btn(r_, "Apply", lambda n=nm: apply_scene(n), theme, w=70, h=26, bg=CARD, size=8).pack(side="right", padx=6)

    Btn(colL, "Open settings file (.env)", lambda: os.startfile(S._env_path), theme, w=220, h=30, bg=BG, size=8).pack(
        anchor="w", pady=(0, 14))
    tk.Frame(colR, bg=BG, height=10).pack()

    # ================================================================== mini strip
    mini = tk.Frame(root, bg=BG)
    mrow = tk.Frame(mini, bg=BG)
    mrow.pack(fill="x", padx=14, pady=(8, 10))
    mglyph = tk.Label(mrow, text="✦", bg=BG, fg=IDLE, font=(FS, 22))
    mglyph.pack(side="left", padx=(0, 12))
    mcol = tk.Frame(mrow, bg=BG)
    mcol.pack(side="left")
    mname = label(mcol, "Watching", bg=BG, fg=TEXT, size=12, bold=True)
    mname.pack(anchor="w")
    msub = label(mcol, "", bg=BG, fg=DIM, size=8)
    msub.pack(anchor="w")
    Btn(mrow, "✕", lambda: root.withdraw(), theme, w=34, h=32, bg=BG, size=10).pack(side="right")
    Btn(mrow, "▢", lambda: set_mini(False), theme, w=34, h=32, bg=BG, size=11).pack(side="right")
    mtime = tk.Label(mrow, text="--:--", bg=BG, fg=TEXT, font=(F, 22, "bold"))
    mtime.pack(side="right", padx=(0, 12))
    mini_on = [False]

    def set_mini(on, save=True):
        mini_on[0] = on
        if on:
            shell.pack_forget()
            mini.pack(fill="x")
            root.geometry("420x92")
        else:
            mini.pack_forget()
            shell.pack(fill="both", expand=True)
            root.geometry(f"{W}x{H}")
        if save:
            S._set_setting("PANEL_MINI", on)

    def choose_mode(name):
        if name != UI_MODE:
            threading.Thread(target=S._set_mode, args=(name,), daemon=True).start()

    def cycle_mode():
        order = list(MODES)
        choose_mode(order[(order.index(UI_MODE) + 1) % len(order)])

    # ================================================================== command palette
    pal = {"win": None}
    panic_prev = {"vis": True}

    def actions():
        A = []

        def add(label_, fn, kw=""):
            A.append((label_, kw.lower(), fn))

        def confirmed(msg, fn):
            return lambda: fn() if messagebox.askyesno("Sentinel", msg, parent=root) else None

        add("Show all Roblox windows", lambda: S._set_show(True), "reveal bring back")
        add("Hide all Roblox windows", lambda: S._set_show(False), "conceal park")
        add("Close all Roblox windows…", confirmed("Close every Roblox window?", S._close_all), "kill quit end")
        add("New Roblox instance", S._launch_instance, "launch open start")
        for a in load_accounts():
            add(f"Launch as {a['label']}", lambda x=a: launch_account(x), "account switch")
            add(f"Rejoin as {a['label']}", lambda x=a: rejoin(x), "relaunch again")
        add("Restart Roblox for multi-instance…", restart_multi, "lock mutex multiple")
        add("Panic: hide everything / bring it back", lambda: threading.Thread(target=S._panic_toggle, daemon=True).start(),
            "hide all quick boss escape")
        for ev_, m_ in S._macros_load().items():
            if m_.get("steps"):
                add(f"Place gnome now: {ev_.title()}", lambda e=ev_: threading.Thread(target=S._macro_play, args=(e, True), daemon=True).start(),
                    "macro replay auto place")
        for key_, m_ in MODES.items():
            add(f"Mode: {m_['label']}", lambda k=key_: choose_mode(k), "performance look smooth fps stealth eco " + m_["blurb"])
        add("Pause / resume reading", lambda: (S.PAUSED.clear() if S.PAUSED.is_set() else S.PAUSED.set()), "stop")
        add("Mute / unmute alerts", lambda: (S.MUTED.clear() if S.MUTED.is_set() else S.MUTED.set()), "silence")
        add("Toggle mini mode", lambda: set_mini(not mini_on[0]), "compact strip small")
        add("Open the web dashboard", S._open_dashboard, "browser")
        for mins, txt in ((30, "30 minutes"), (60, "1 hour"), (240, "4 hours")):
            add(f"Snooze alerts for {txt}", lambda m=mins: do_snooze(m), "mute quiet silence")
        add("Cancel snooze (alerts back on)", cancel_snooze, "unmute resume")
        add("Run health check", lambda: (root.deiconify(), set_mini(False) if mini_on[0] else None, show("Settings"),
                                         sc_canvas.yview_moveto(0), run_checks()), "diagnose test problems")
        for nm in load_scenes():
            add(f"Scene: {nm}", lambda n=nm: apply_scene(n), "mode profile")
        for nm in PRESETS:
            add(f"Performance preset: {nm}", lambda n=nm: [S._set_setting(k, v) for k, v in PRESETS[n].items()], "speed cpu")
        for text, name in (("alerts", "NOTIFY"), ("keep-alive click", "KEEPALIVE"), ("hiding the board window", "HIDE_WINDOW"),
                           ("hiding all instances", "HIDE_ALL"), ("low CPU for hidden Roblox", "THROTTLE_PARKED")):
            add(f"Toggle {text}", lambda n=name: S._set_setting(n, not get(n)), "switch")
        for n_, _g in NAV:
            add(f"Go to {n_}", lambda x=n_: (root.deiconify(), set_mini(False) if mini_on[0] else None, show(x)), "page open")
        add("Show the log", lambda: (root.deiconify(), set_mini(False) if mini_on[0] else None, show("Activity")), "logs")
        add("Quit the sentinel…", confirmed("Quit the sentinel?", lambda: (S.QUIT.set())), "exit stop")
        return A

    def open_palette(_=None):
        if pal["win"] is not None:
            try:
                pal["win"].focus_force()
                return
            except tk.TclError:
                pal["win"] = None
        w = tk.Toplevel(root)
        pal["win"] = w
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        pw, ph = 600, 380
        w.geometry(f"{pw}x{ph}+{(w.winfo_screenwidth() - pw) // 2}+{(w.winfo_screenheight() - ph) // 3}")
        w.configure(bg=EDGE)
        body = tk.Frame(w, bg=CARD)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        top = tk.Frame(body, bg=CARD)
        top.pack(fill="x", padx=16, pady=(14, 6))
        tk.Label(top, text="⌘", bg=CARD, fg=theme.cur, font=(FS, 16)).pack(side="left", padx=(0, 10))
        q = tk.StringVar()
        ent = tk.Entry(top, textvariable=q, bg=CARD, fg=TEXT, insertbackground=theme.cur, relief="flat", font=(F, 15))
        ent.pack(side="left", fill="x", expand=True, ipady=4)
        tk.Frame(body, bg=EDGE, height=1).pack(fill="x")
        lb = tk.Listbox(body, bg=CARD, fg=TEXT, selectbackground=lerp(CARD, theme.cur, .35), selectforeground=TEXT,
                        highlightthickness=0, bd=0, relief="flat", font=(F, 11), activestyle="none")
        lb.pack(fill="both", expand=True, padx=8, pady=8)
        tk.Label(body, text="↑ ↓ choose   ·   Enter run   ·   Esc close", bg=CARD, fg=FAINT, font=(F, 8)).pack(pady=(0, 8))
        items = actions()
        shown = []

        def refill(*_):
            lb.delete(0, "end")
            shown.clear()
            for label_, kw, fn in filter_actions(items, q.get()):
                shown.append(fn)
                lb.insert("end", "   " + label_)
            if shown:
                lb.selection_set(0)

        def close(_=None):
            pal["win"] = None
            try:
                w.destroy()
            except tk.TclError:
                pass

        def run_sel(_=None):
            sel = lb.curselection()
            fn = shown[sel[0]] if sel else None
            close()
            if fn:
                root.after(60, fn)

        def move(d):
            sel = lb.curselection()
            i = (sel[0] if sel else -1) + d
            if shown:
                i = max(0, min(len(shown) - 1, i))
                lb.selection_clear(0, "end")
                lb.selection_set(i)
                lb.see(i)
            return "break"

        q.trace_add("write", refill)
        ent.bind("<Return>", run_sel)
        ent.bind("<Escape>", close)
        ent.bind("<Down>", lambda e: move(1))
        ent.bind("<Up>", lambda e: move(-1))
        lb.bind("<Double-Button-1>", run_sel)
        w.bind("<FocusOut>", lambda e: root.after(120, lambda: close() if pal["win"] is not None and
                                                  w.focus_displayof() is None else None))
        refill()
        w.update_idletasks()
        chrome(w, glass=False)
        w.deiconify()
        w.lift()
        w.focus_force()
        ent.focus_set()

        def ensure_focus():
            try:
                u = ctypes.windll.user32
                hw = u.GetParent(w.winfo_id()) or w.winfo_id()
                if u.GetForegroundWindow() not in (hw, w.winfo_id()):
                    u.keybd_event(0x12, 0, 0, 0)             # a tap of Alt lets Windows hand us the foreground
                    u.keybd_event(0x12, 0, 2, 0)
                    u.SetForegroundWindow(hw)
                    ent.focus_force()
            except tk.TclError:
                pass

        w.after(90, ensure_focus)

    root.bind_all("<Control-k>", focus_bar)

    # ================================================================== custom title bar (rounded, blends with the sidebar)
    def hwnd_of():
        return ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()

    def borderless():
        if os.environ.get("NATIVE_TITLE", "0") == "1":
            return
        try:
            u = ctypes.windll.user32
            hwnd = hwnd_of()
            u.SetWindowLongW(hwnd, -16, (u.GetWindowLongW(hwnd, -16) & ~0x00C00000) | 0x00020000)   # no caption, keep minimise
            u.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x37)                                                  # apply the frame change
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4)    # rounded corners
        except Exception:
            pass

    # Plain-Tk dragging. (Handing the drag to Windows' own move loop from inside a Tk callback aborted the process.)
    drag = {"on": False, "dx": 0, "dy": 0}

    def start_drag(e=None):
        if e is None:
            return
        drag.update(on=True, dx=e.x_root - root.winfo_x(), dy=e.y_root - root.winfo_y())

    def do_drag(e):
        if drag["on"]:
            root.geometry(f"+{e.x_root - drag['dx']}+{e.y_root - drag['dy']}")

    def end_drag(_e=None):
        drag["on"] = False

    # ================================================================== hover glow on cards
    hover = {"card": None, "job": None, "last": 0.0}

    def card_of(w):
        while w is not None:
            if isinstance(w, Card):
                return w
            w = getattr(w, "master", None)
        return None

    def hover_step():
        moving = False
        for c_ in list(Card.all):
            try:
                if not c_.winfo_exists():
                    Card.all.remove(c_)
                    continue
                tgt = 1.0 if c_ is hover["card"] else 0.0
                d = tgt - c_.level
                if abs(d) > .03:
                    c_.glow(c_.level + d * .4, theme.cur)
                    moving = True
                elif c_.level != tgt:
                    c_.glow(tgt, theme.cur)
            except tk.TclError:
                Card.all.remove(c_)
        hover["job"] = root.after(M["hover_ms"] or 33, hover_step) if moving else None

    def on_motion(e):
        if get("ANIMATIONS") == "off" or not M["hover_ms"] or time.time() - hover["last"] < .05:
            return
        hover["last"] = time.time()
        try:
            c_ = card_of(root.winfo_containing(e.x_root, e.y_root))
        except (tk.TclError, KeyError):
            c_ = None
        if c_ is not hover["card"]:
            hover["card"] = c_
            if hover["job"] is None:
                hover_step()

    # ================================================================== ambient motion (light on CPU/GPU)
    side_amb = Ambient(side, (6, 0, SIDE - 6, H), BG if not GLASS else "#0b0f18")
    ring_amb = Ambient(ring_card, (8, 8, 322, 188), CARD, above=ring_card.body)
    amb = {"t": time.time(), "job": None}

    def amb_tick():
        lvl = get("ANIMATIONS")
        try:
            visible = root.state() == "normal" and not mini_on[0]
            if lvl == "off" or not visible or not M["amb_fps"]:
                if side_amb.parts or ring_amb.parts:
                    side_amb.clear()
                    ring_amb.clear()
                delay = 600
            else:
                kind, name, _left = S._event_summary()
                ev = name if kind in ("active", "next") else "idle"
                col = theme.cur
                for a_ in (side_amb, ring_amb):
                    if a_.key != (ev, lvl) or (a_.parts and dist(a_.built, col) > 40):    # rebuild as the accent settles
                        a_.build(ev, col, lvl)
                    a_.colour = col
                now_ = time.time()
                dt = min(.25, now_ - amb["t"])
                amb["t"] = now_
                front = (FORCE_FRONT if FORCE_FRONT is not None
                         else ctypes.windll.user32.GetForegroundWindow() in (hwnd_of(), root.winfo_id()))
                side_amb.tick(dt)
                if nav["cur"] == "Overview":
                    ring_amb.tick(dt)
                # full rate only when you're actually looking at it; a crawl otherwise
                fps = (M["amb_fps"] if lvl == "normal" else min(8, M["amb_fps"])) if front and not auto["throttle"] else 2
                delay = int(1000 / max(1, fps))
        except tk.TclError:
            delay = 600
        except Exception as e:
            print("ambient error:", e)
            delay = 1500
        root.after(delay, amb_tick)

    # ================================================================== event chip, ring, mini strip (fast tick)
    totals = {}

    # ---- snooze alerts for a while
    snooze = {"until": 0.0}

    def do_snooze(minutes):
        snooze["until"] = time.time() + minutes * 60
        S.MUTED.set()
        print(f"alerts snoozed for {minutes} min")

    def cancel_snooze():
        snooze["until"] = 0.0
        S.MUTED.clear()

    # ---- burst effect when an event starts
    burst = {"parts": [], "t0": 0.0, "colour": IDLE, "job": None, "prev": None}

    def start_burst(colour):
        burst.update(t0=time.time(), colour=colour)
        burst["parts"] = [(random.uniform(0, 2 * math.pi), random.uniform(70, 200), random.uniform(2.2, 5.5)) for _ in range(48)]
        if burst["job"] is None:
            anim_burst()

    def anim_burst():
        t = time.time() - burst["t0"]
        try:
            ring_card.delete("burst")
            if t < 1.6:
                k = t / 1.6
                for ang, sp, sz in burst["parts"]:
                    d = sp * (1 - (1 - k) ** 2)
                    x, y = cx + math.cos(ang) * d, cy + math.sin(ang) * d * 0.85 + 46 * k * k
                    r_ = sz * (1 - k * .55)
                    ring_card.create_oval(x - r_, y - r_, x + r_, y + r_, fill=lerp(burst["colour"], CARD, k ** 1.4),
                                          outline="", tags="burst")
                side.coords("logo", SIDE / 2 + math.sin(t * 24) * 5 * (1 - k), 44 - abs(math.sin(t * 12)) * 8 * (1 - k))
                burst["job"] = root.after(33, anim_burst)
            else:
                burst["job"] = None
                side.coords("logo", SIDE / 2, 44)
        except tk.TclError:
            burst["job"] = None

    def fast_body():
        kind, name, left = S._event_summary()
        paused = S.PAUSED.is_set()
        probs = list(S._problems.values())
        if probs:                                              # something is wrong: say so under the title
            more = f"   (+{len(probs) - 1} more)" if len(probs) > 1 else ""
            lc(sub_lbl, text="⚠  " + probs[0]["text"] + more, fg=BAD)
        # snooze countdown (alerts come back by themselves)
        elif snooze["until"]:
            if time.time() >= snooze["until"]:
                cancel_snooze()
            else:
                left_s = int(snooze["until"] - time.time())
                sub_lbl.config(text=f"✕  alerts snoozed · {left_s // 60}:{left_s % 60:02d} left", fg=WARN)
        elif sub_lbl.cget("fg") in (WARN, BAD):
            sub_lbl.config(text=SUBS[nav["cur"]], fg=FAINT)
        # a new event just started -> celebrate (skip the very first reading after startup)
        bkey = f"active:{name}" if kind == "active" else kind
        if burst["prev"] is None:
            burst["prev"] = bkey
        elif bkey != burst["prev"]:
            if kind == "active" and (left is None or left > 8) and M["burst"]:
                start_burst(S._event_style(name)[1])
            burst["prev"] = bkey
        lock = get("ACCENT")
        if paused:
            accent = WARN
        elif M["mono"]:
            accent = IDLE
        elif lock and lock != "event":
            accent = lock
        elif kind == "active":
            accent = S._event_style(name)[1]
        elif kind == "next":
            accent = lerp(IDLE, S._event_style(name)[1], .55)
        else:
            accent = IDLE
        theme.go(accent)
        glyph, colour, _e = S._event_style(name) if kind != "none" else ("✦", theme.cur, "")
        # header chip (redrawn only when its text, colour or flash changes)
        flash = max(0.0, 1 - (time.time() - burst['t0']) / 1.3)
        csig = (paused, kind, name, S._fmt_left(left), theme.cur, round(flash * 8))
        if cache.get("chip") != csig:
            cache["chip"] = csig
            chip.delete("all")
            rr(chip, 1, 1, 289, 39, 19, fill=lerp(CARD, theme.cur, .12 + .25 * flash), outline=lerp(CARD, theme.cur, .55 + .45 * flash))
            if paused:
                chip.create_text(20, 20, text="❚❚  Paused", fill=WARN, font=(F, 11, "bold"), anchor="w")
            elif kind == "none":
                chip.create_text(20, 20, text="●  Watching – no event yet", fill=DIM, font=(F, 10, "bold"), anchor="w")
            else:
                chip.create_text(18, 20, text=glyph, fill=theme.cur, font=(FS, 14), anchor="w")
                chip.create_text(46, 20, text=name.title(), fill=TEXT, font=(F, 11, "bold"), anchor="w")
                chip.create_text(274, 20, text=("ends " if kind == "active" else "in ") + S._fmt_left(left),
                                 fill=theme.cur, font=(F, 11, "bold"), anchor="e")
        # ring
        if nav["cur"] == "Overview" and not mini_on[0]:
            if kind == "none" or left is None:
                frac, cap = 0.0, ("PAUSED" if paused else "WAITING" if kind == "none" else "ACTIVE" if kind == "active" else "SOON")
                txt = "--:--"
            else:
                key = f"{kind}:{name}"
                totals[key] = max(totals.get(key, 0), left)
                frac = max(0.0, min(1.0, left / (totals[key] or 1)))
                cap, txt = ("LEFT" if kind == "active" else "TO START"), S._fmt_left(left)
            ext_ = round(-359.9 * frac, 1) if frac else 0
            ic(ring_card, ring_prog, extent=ext_, outline=theme.cur)
            pulse = (math.sin(time.time() * 2.4) + 1) / 2 if M["pulse"] else .5
            ic(ring_card, ring_glow, extent=ext_, outline=lerp(CARD, theme.cur, round(.10 + .16 * pulse, 2)))
            ic(ring_card, ring_time, text=txt)
            ic(ring_card, ring_cap, text=cap)
            ic(ring_card, ring_glyph, text=glyph, fill=theme.cur)
            ic(ring_card, ring_name, text=name.title() if kind != "none" else "Watching")
            ic(ring_card, ring_kind, fill=theme.cur if kind != "none" else DIM,
               text=("ACTIVE NOW" if kind == "active" else "NEXT EVENT" if kind == "next" else "no event on the sign"))
            seen = (S.STATE.get(kind) or (0, 0, 0))[2] if kind != "none" else None
            via = "  ·  via corner icon" if (kind == "active" and S.STATE.get("source") == "icon") else ""
            ic(ring_card, ring_seen, text=(f"seen {ago(time.time() - seen)}" + via) if seen else "")
        # mini strip
        if mini_on[0]:
            if paused:
                lc(mglyph, text="❚❚", fg=WARN)
                lc(mname, text="Paused")
                lc(msub, text="not reading the game")
                lc(mtime, text="--:--", fg=DIM)
            elif kind == "none":
                lc(mglyph, text="✦", fg=IDLE)
                lc(mname, text="Watching")
                lc(msub, text="no event on the sign")
                lc(mtime, text="--:--", fg=DIM)
            else:
                lc(mglyph, text=glyph, fg=theme.cur)
                lc(mname, text=name.title())
                lc(msub, text="ACTIVE  ·  time left" if kind == "active" else "NEXT  ·  starts in", fg=theme.cur)
                lc(mtime, text=S._fmt_left(left), fg=theme.cur)

    auto = {"throttle": False, "since": 0.0, "told": False}

    def fullscreen_other():
        """Is some other app (a game) covering a whole monitor right now?"""
        try:
            u = ctypes.windll.user32
            fg = u.GetForegroundWindow()
            if not fg or fg in (hwnd_of(), root.winfo_id()):
                return False
            from ctypes import wintypes
            rc = wintypes.RECT()
            u.GetWindowRect(fg, ctypes.byref(rc))
            mon = u.MonitorFromWindow(fg, 2)

            class MI(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("mon", wintypes.RECT), ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

            mi = MI()
            mi.cb = ctypes.sizeof(MI)
            u.GetMonitorInfoW(mon, ctypes.byref(mi))
            return rc.left <= mi.mon.left and rc.top <= mi.mon.top and rc.right >= mi.mon.right and rc.bottom >= mi.mon.bottom
        except Exception:
            return False

    def fast_tick():
        hidden = root.state() == "withdrawn"
        if not hidden:
            guarded(fast_body)
        if UI_MODE in ("smooth",):
            if fullscreen_other():                           # a game is in front: ease off by itself, and say so once
                auto["since"] = auto["since"] or time.time()
                if time.time() - auto["since"] > 20 and not auto["throttle"]:
                    auto["throttle"] = True
                    if not auto["told"]:
                        auto["told"] = True
                        print("a fullscreen app is in front - the console is easing off (try FPS friendly mode for a game)")
            else:
                auto["since"], auto["throttle"] = 0.0, False
        else:
            auto["throttle"] = False
        front = (FORCE_FRONT if FORCE_FRONT is not None
                 else ctypes.windll.user32.GetForegroundWindow() in (hwnd_of(), root.winfo_id()))
        delay = M["fast"] if (front and not auto["throttle"]) else max(M["fast"], 500)
        if hidden:
            delay = 1500
        root.after(delay, fast_tick)

    # ================================================================== slow tick: data, lists, thumbnails
    procs = {}
    last = {"graph": 0.0, "thumb": 0.0}

    def cpu_of(pids):
        tot = 0.0
        for pid in pids:
            p = procs.get(pid)
            try:
                if p is None:
                    p = procs[pid] = psutil.Process(pid)
                    p.cpu_percent(None)
                tot += p.cpu_percent(None)
            except (psutil.Error, ValueError):
                procs.pop(pid, None)
        return tot

    def sample_usage():
        if time.time() - last["graph"] < 2.0:
            return
        last["graph"] = time.time()
        hidden, other = set(), set()
        for hwnd, role, title, is_hidden, mn in list(S._snapshot):
            try:
                pid = win32process.GetWindowThreadProcessId(hwnd)[1]
            except Exception:
                continue
            (hidden if is_hidden else other).add(pid)
        vals = {"sentinel": cpu_of([os.getpid()]), "hidden": cpu_of(hidden), "other": cpu_of(other)}
        for key, v in vals.items():
            series[key][2].append(v)
            nm, lb_ = legend[key]
            lb_.config(text=f" {nm}  {v:.0f}%")
        if nav["cur"] == "Overview":
            spark.redraw()

    def update_stats():
        snap = list(S._snapshot)
        c, v, sb, dot = stats["inst"]
        vis = sum(1 for e in snap if not e[3] and not e[4])
        hid = sum(1 for e in snap if e[3])
        c.itemconfig(v, text=f"{len(snap)} running")
        c.itemconfig(sb, text=f"{vis} visible · {hid} hidden")
        c.itemconfig(dot, fill=OK if snap else BAD)
        wk = S.STATE["worker"]
        c, v, sb, dot = stats["worker"]
        c.itemconfig(v, text="Connected" if wk["ok"] else "Unreachable" if wk["ok"] is False else "No report yet")
        c.itemconfig(sb, text=(f"last report {ago(time.time() - wk['last'])}" if wk["last"] else "waiting for the first read"))
        c.itemconfig(dot, fill=OK if wk["ok"] else BAD if wk["ok"] is False else WARN)
        lr = S.STATE["last_read"]
        age = time.time() - lr if lr else None
        c, v, sb, dot = stats["read"]
        c.itemconfig(v, text=ago(age) if age is not None else "never")
        c.itemconfig(sb, text="paused" if S.PAUSED.is_set() else f"board seen {ago(time.time() - S.STATE['board_seen'])}"
                     if S.STATE["board_seen"] else "board not seen yet")
        c.itemconfig(dot, fill=WARN if S.PAUSED.is_set() else OK if age is not None and age < 20 else BAD)
        c, v, sb, dot = stats["up"]
        up = int(time.time() - S.STATE["started"])
        c.itemconfig(v, text=f"{up // 3600}h {up % 3600 // 60}m" if up >= 3600 else f"{up // 60}m {up % 60}s")
        c.itemconfig(sb, text={"held": "multi-instance: ON", "waiting": "multi-instance: close Roblox first",
                               "off": "multi-instance: off", "failed": "multi-instance: error"}.get(S.STATE["lock"], "multi-instance: off"))
        c.itemconfig(dot, fill=OK)

    def teardown():
        """Cancel every pending timer first, so the old interpreter doesn't fire callbacks into a dead window."""
        try:
            for aid in root.tk.splitlist(root.tk.call("after", "info")):
                try:
                    root.after_cancel(aid)
                except tk.TclError:
                    pass
        except tk.TclError:
            pass
        root.destroy()

    def fade_to(show_it):
        """Show/hide the console with a short fade (Smooth mode only)."""
        if not M["fade"]:
            (root.deiconify if show_it else root.withdraw)()
            return
        steps = 7

        def step(i=1):
            try:
                a = i / steps if show_it else 1 - i / steps
                root.attributes("-alpha", max(0.02, min(1.0, a)))
                if i < steps:
                    root.after(14, step, i + 1)
                elif not show_it:
                    root.withdraw()
                    root.attributes("-alpha", 1.0)
            except tk.TclError:
                pass

        try:
            if show_it:
                root.attributes("-alpha", 0.02)
                root.deiconify()
            step()
        except tk.TclError:
            pass

    def slow_tick():
        if S.QUIT.is_set():
            root.destroy()
            return
        guarded(slow_body)
        if restart["on"]:
            return
        root.after(M["slow"] if root.state() != "withdrawn" else 2500, slow_tick)

    def slow_body():
        try:
            while True:
                cmd = S.PANEL_Q.get_nowait()
                if cmd == "toggle":
                    fade_to(root.state() == "withdrawn")
                elif cmd == "restyle":
                    S._console_pos = (root.winfo_x(), root.winfo_y())
                    restart["on"] = True
                    root.after(30, teardown)
                    return
                elif cmd == "hide":
                    root.withdraw()
                elif cmd == "show":
                    root.deiconify()
                elif cmd == "mini":
                    set_mini(not mini_on[0])
                elif cmd == "palette":
                    root.deiconify()
                    open_palette()
                elif cmd == "panic:on":
                    panic_prev["vis"] = root.state() != "withdrawn"
                    root.withdraw()
                elif cmd == "panic:off":
                    if panic_prev["vis"]:
                        root.deiconify()
                elif cmd == "health":
                    root.deiconify()
                    show("Settings")
                    sc_canvas.yview_moveto(0)
                    run_checks()
                elif cmd.startswith("snooze:"):
                    do_snooze(float(cmd[7:]))
                elif cmd.startswith("cmd:"):
                    focus_bar()
                    cmd_focus_in()
                    cmd_var.set(cmd[4:])
                elif cmd.startswith("page:"):
                    root.deiconify()
                    if mini_on[0]:
                        set_mini(False)
                    show(cmd[5:])
        except queue.Empty:
            pass
        guarded(S._apply_visibility)
        if root.state() == "withdrawn" or mini_on[0]:        # nobody is looking at the pages: don't rebuild them
            return
        guarded(update_stats)
        guarded(sample_usage)
        guarded(rebuild_scenes)
        guarded(sync_scenes)
        guarded(rebuild_events)
        guarded(rebuild_mine)
        for fn in syncers + ev_state["sync"]:
            guarded(fn)
        page = nav["cur"]
        if page == "Overview":
            guarded(update_sign)
            lines = feed_lines(6)
            if lines != feed_sig["small"]:
                feed_sig["small"] = lines
                guarded(lambda: fill_feed(feed_small, lines))
        elif page == "Instances":
            guarded(rebuild_instances)
        elif page == "Accounts":
            guarded(rebuild_accounts)
            acct_note.config(text=acct["note"])
        elif page == "Activity":
            lines = feed_lines(200)
            if lines != feed_sig["big"]:
                feed_sig["big"] = lines
                guarded(lambda: fill_feed(feed_big, lines))
            guarded(rebuild_history)
        if time.time() - last["thumb"] > 1.0:
            last["thumb"] = time.time()
            guarded(update_thumbs)

    # ------------------------------------------------------------------ go
    root.protocol("WM_DELETE_WINDOW", root.withdraw)
    show("Overview")
    root.update_idletasks()
    chrome(root)
    borderless()
    for w_ in (hdr, title_lbl, sub_lbl, mini, mrow, mglyph, mcol, mname, msub):
        w_.bind("<ButtonPress-1>", start_drag)
        w_.bind("<B1-Motion>", do_drag)
        w_.bind("<ButtonRelease-1>", end_drag)
    side.bind("<ButtonPress-1>", lambda e: start_drag(e) if e.y < 90 else None, add="+")
    side.bind("<B1-Motion>", do_drag, add="+")
    side.bind("<ButtonRelease-1>", end_drag, add="+")
    root.bind_all("<Motion>", on_motion, add="+")
    if get("PANEL_MINI"):
        set_mini(True, save=False)
    if UI_MODE == "stealth":
        root.after(300, root.withdraw)
    amb_tick()
    fast_tick()
    slow_tick()
    root.mainloop()
    return "restyle" if restart["on"] else None
