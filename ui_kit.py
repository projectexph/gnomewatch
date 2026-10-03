"""Gnomewatch UI kit: design tokens, Inter font, Lucide icons, anti-aliased shapes and the widgets the console is built from.

Everything is drawn on Tk canvases using PIL-made, supersampled rounded rectangles (Tk's own rounded polygons are jagged),
Lucide icons that are tinted on the fly, and the Inter typeface loaded privately for this process only.

Design language: "gamer dashboard" - deep blue-black surfaces, 1px borders, one bold accent colour that follows the event,
chunky filled buttons with an icon, square-ish toggles.
"""
import ctypes
import math
import os
import time
import tkinter as tk
import tkinter.font as tkfont

from PIL import Image, ImageDraw, ImageTk

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets", "ui")

# ====================================================================================== tokens
KEYCOLOUR = "#010203"                                    # popups paint this colour transparent, so they can have rounded corners
BASE = dict(BG="#0a0c11", SIDE="#0d1016", CARD="#131723", CARD2="#1a2030", CARD3="#212840", EDGE="#252c3f", EDGE_HI="#36425f",
            TEXT="#f1f4fa", DIM="#9ba7bf", FAINT="#5e6a85", IDLE="#7c6cff", OK="#2ee59d", WARN="#ffb547", BAD="#ff5470")

# Each mode = same clean look, its own accent when idle and its own motion budget.
MODES = {
    "smooth":  dict(label="Smooth", icon="sparkles", blurb="Full motion: fades, slides and hover effects",
                    idle="#7c6cff", fast=120, slow=700, tween=28, rate=.2, slide=8, slide_ms=12, hover=True, fade=True,
                    burst=True, pulse=True, mono=False, btn_fade=4),
    "fps":     dict(label="FPS friendly", icon="zap", blurb="Static and flat. Redraws only on change",
                    idle="#4cc2ff", fast=1000, slow=2000, tween=60, rate=.5, slide=0, slide_ms=0, hover=False, fade=False,
                    burst=False, pulse=False, mono=False, btn_fade=0),
    "stealth": dict(label="Stealth", icon="eye-off", blurb="Hidden and silent. Nothing pops up",
                    idle="#8b93a7", fast=2000, slow=3000, tween=80, rate=1.0, slide=0, slide_ms=0, hover=False, fade=False,
                    burst=False, pulse=False, mono=True, btn_fade=0),
    "eco":     dict(label="Eco", icon="leaf", blurb="Slow reads and redraws, for long AFK sessions",
                    idle="#4ade80", fast=2000, slow=4000, tween=80, rate=1.0, slide=0, slide_ms=0, hover=False, fade=False,
                    burst=False, pulse=False, mono=False, btn_fade=0),
}


class _Tokens:
    pass


K = _Tokens()                                            # K.CARD, K.TEXT, K.M["fast"] ... read at draw time
K.mode = "smooth"


def apply_mode(name):
    name = name if name in MODES else "smooth"
    for k, v in BASE.items():
        setattr(K, k, v)
    K.mode, K.M = name, MODES[name]
    K.IDLE = K.M["idle"]
    if name == "stealth":
        K.TEXT, K.DIM, K.FAINT = "#aab1c0", "#6d7588", "#474e60"
    return K


apply_mode("smooth")

# ====================================================================================== colour helpers
def _rgb(c):
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def lerp(c1, c2, t):
    a, b = _rgb(c1), _rgb(c2)
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(x + (y - x) * t))) for x, y in zip(a, b))


def approach(cur, target, rate=.2):
    """Move each channel toward the target by a fraction (at least 1 step) so it always arrives."""
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
    return sum(abs(a - b) for a, b in zip(_rgb(c1), _rgb(c2)))


def on_colour(bg):
    """Readable text colour (near-black or white) for a filled button of this colour."""
    r, g, b = _rgb(bg)
    return "#0a0c11" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff"


# ====================================================================================== fonts
_fonts_loaded = []
FAMILY = {"regular": "Inter", "medium": "Inter Medium", "semibold": "Inter SemiBold", "bold": "Inter"}
_have = {"ok": False}


def load_fonts():
    """Register the bundled Inter files for this process only (FR_PRIVATE): nothing is installed on the system."""
    if _fonts_loaded:
        return
    _fonts_loaded.append(True)
    folder = os.path.join(ASSETS, "fonts")
    try:
        for f in ("Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf", "Inter-Bold.ttf"):
            ctypes.windll.gdi32.AddFontResourceExW(os.path.join(folder, f), 0x10, 0)
    except Exception as e:
        print("couldn't load the Inter font:", e)


def check_fonts():
    """Call once a Tk root exists: is Inter really available? (falls back to Segoe UI if not)"""
    try:
        fams = set(tkfont.families())
        _have["ok"] = "Inter" in fams and "Inter SemiBold" in fams
    except tk.TclError:
        _have["ok"] = False
    if not _have["ok"]:
        FAMILY.update(regular="Segoe UI", medium="Segoe UI", semibold="Segoe UI Semibold", bold="Segoe UI")
    return _have["ok"]


def font(px, weight="regular"):
    """Tk font tuple: size in pixels (negative = pixels), weight regular | medium | semibold | bold."""
    fam = FAMILY.get(weight, "Inter")
    return (fam, -int(px), "bold" if weight == "bold" else "normal")


_measure = {}


def ellipsize(text, spec, maxw):
    """Shorten text with an ellipsis so it fits maxw pixels."""
    if text_w(text, spec) <= maxw:
        return text
    while len(text) > 1 and text_w(text + "…", spec) > maxw:
        text = text[:-1]
    return text + "…"


def text_w(text, spec):
    key = (spec, text)
    w = _measure.get(key)
    if w is None:
        w = _measure[key] = tkfont.Font(font=spec).measure(text)
    return w


# ====================================================================================== images
_pil_cache = {}
_photos = {}                                              # per Tk root: cleared by reset()
_icon_src = {}


def reset():
    """A new Tk root is coming: PhotoImages belong to the old interpreter, so forget them (PIL images stay)."""
    _photos.clear()
    _measure.clear()


def _cap(d, limit=3000):
    if len(d) > limit:
        d.clear()                                    # safe for pictures on screen: every widget below keeps hold of the images it shows


def _install_image_holders():
    """tkinter shows a picture by NAME and keeps no Python reference to it, so a PhotoImage that only the cache holds is deleted
    (and its widget goes blank) the moment the cache is emptied. This makes every canvas item and label keep a reference to the image it
    displays for as long as it displays it, so emptying the cache can never blank anything that is on screen."""
    C = tk.Canvas
    if getattr(C, "_gw_holders", False):
        return
    C._gw_holders = True
    o_create, o_conf, o_delete = C.create_image, C.itemconfigure, C.delete

    def held(self):
        d = self.__dict__.get("_gw_imgs")
        if d is None:
            d = self.__dict__["_gw_imgs"] = {}
        return d

    def create_image(self, *args, **kw):
        item = o_create(self, *args, **kw)
        img = kw.get("image")
        if img is not None and not isinstance(img, str):
            held(self)[item] = img
        return item

    def itemconfigure(self, tagOrId, cnf=None, **kw):
        res = o_conf(self, tagOrId, cnf, **kw)
        if "image" in kw or (isinstance(cnf, dict) and "image" in cnf):
            img = kw["image"] if "image" in kw else cnf["image"]
            try:
                items = self.find_withtag(tagOrId)
            except tk.TclError:
                items = ()
            d = held(self)
            for it in items:
                if img is None or isinstance(img, str):          # "" = show no picture
                    d.pop(it, None)
                else:
                    d[it] = img
        return res

    def delete(self, *tags):
        d = self.__dict__.get("_gw_imgs")
        if d:
            if "all" in tags:
                d.clear()
            else:
                for t in tags:
                    try:
                        for it in self.find_withtag(t):
                            d.pop(it, None)
                    except tk.TclError:
                        pass
        return o_delete(self, *tags)

    C.create_image = create_image
    C.itemconfigure = C.itemconfig = itemconfigure
    C.delete = delete
    for W_ in (tk.Label, tk.Button):
        def patch_widget(W):
            o_init, o_cfg = W.__init__, W.configure

            def init(self, master=None, cnf={}, **kw):
                o_init(self, master, cnf, **kw)
                img = kw.get("image") or (cnf.get("image") if isinstance(cnf, dict) else None)
                if img is not None and not isinstance(img, str):
                    self.__dict__["_gw_img"] = img

            def configure(self, cnf=None, **kw):
                res = o_cfg(self, cnf, **kw)
                if "image" in kw:
                    img = kw["image"]
                    if img is None or isinstance(img, str):
                        self.__dict__.pop("_gw_img", None)
                    else:
                        self.__dict__["_gw_img"] = img
                return res

            W.__init__ = init
            W.configure = W.config = configure
        patch_widget(W_)


_install_image_holders()


def _rgba(c, a=255):
    r, g, b = _rgb(c)
    return r, g, b, a


def rr_pil(w, h, r, fill=None, outline=None, bw=1, ss=4):
    """Anti-aliased rounded rectangle. fill/outline are hex colours or None (transparent)."""
    key = ("rr", w, h, r, fill, outline, bw)
    im = _pil_cache.get(key)
    if im is None:
        _cap(_pil_cache)
        W, H = max(1, w) * ss, max(1, h) * ss
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        rad = min(r * ss, W // 2, H // 2)
        if outline:
            d.rounded_rectangle([0, 0, W - 1, H - 1], radius=rad, fill=_rgba(outline))
            inset = bw * ss
            if fill:
                d.rounded_rectangle([inset, inset, W - 1 - inset, H - 1 - inset], radius=max(0, rad - inset), fill=_rgba(fill))
            else:                                              # outline only: outer rounded shape minus the inner one
                outer = Image.new("L", (W, H), 0)
                ImageDraw.Draw(outer).rounded_rectangle([0, 0, W - 1, H - 1], radius=rad, fill=255)
                inner = Image.new("L", (W, H), 0)
                ImageDraw.Draw(inner).rounded_rectangle([inset, inset, W - 1 - inset, H - 1 - inset], radius=max(0, rad - inset), fill=255)
                from PIL import ImageChops
                im.putalpha(ImageChops.subtract(outer, inner))
        elif fill:
            d.rounded_rectangle([0, 0, W - 1, H - 1], radius=rad, fill=_rgba(fill))
        im = im.resize((max(1, w), max(1, h)), Image.Resampling.LANCZOS)
        _pil_cache[key] = im
    return im


def photo(key, make):
    ph = _photos.get(key)
    if ph is None:
        _cap(_photos)
        ph = _photos[key] = ImageTk.PhotoImage(make())
    return ph


def rr_photo(w, h, r, fill=None, outline=None, bw=1):
    return photo(("rr", w, h, r, fill, outline, bw), lambda: rr_pil(w, h, r, fill, outline, bw))


def circle_photo(d, fill, outline=None, bw=1):
    return photo(("circ", d, fill, outline, bw), lambda: rr_pil(d, d, d // 2, fill, outline, bw))


def icon_pil(name, px, colour):
    key = ("ic", name, px, colour)
    im = _pil_cache.get(key)
    if im is None:
        _cap(_pil_cache)
        src = _icon_src.get(name)
        if src is None:
            path = os.path.join(ASSETS, "icons", name + ".png")
            try:
                src = Image.open(path).convert("RGBA")
            except OSError:
                src = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
            _icon_src[name] = src
        big = src.resize((px * 2, px * 2), Image.Resampling.LANCZOS) if px * 2 < 96 else src
        alpha = big.getchannel("A").resize((px, px), Image.Resampling.LANCZOS)
        im = Image.new("RGBA", (px, px), _rgba(colour, 255))
        im.putalpha(alpha)
        _pil_cache[key] = im
    return im


def icon(name, px, colour):
    return photo(("ic", name, px, colour), lambda: icon_pil(name, px, colour))


EVENT_ICONS = {"aurora": "sparkles", "solar flare": "sun", "candy land": "candy", "lightning storm": "cloud-lightning",
               "toxic": "biohazard", "cursed": "skull", "blizzard": "snowflake"}


def event_icon(name):
    return EVENT_ICONS.get((name or "").lower(), "zap")


# ====================================================================================== theme (accent follows the event)
class Theme:
    def __init__(self, root):
        self.root, self.cur, self.target, self.subs, self.job = root, K.IDLE, K.IDLE, [], None

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
        self.cur = approach(self.cur, self.target, K.M["rate"])
        if self.cur != self.target:
            self.job = self.root.after(K.M["tween"], self.step)
        for fn in list(self.subs):
            try:
                fn(self.cur)
            except tk.TclError:
                self.subs.remove(fn)


# ====================================================================================== tooltips
class Tip:
    """Small hover tooltip for icon-only buttons."""
    def __init__(self, widget, text):
        self.w, self.text, self.job, self.win = widget, text, None, None
        widget.bind("<Enter>", self.enter, add="+")
        widget.bind("<Leave>", self.leave, add="+")
        widget.bind("<ButtonPress-1>", self.leave, add="+")

    def enter(self, _e=None):
        self.leave()
        self.job = self.w.after(450, self.show)

    def show(self):
        try:
            x = self.w.winfo_rootx() + self.w.winfo_width() // 2
            y = self.w.winfo_rooty() + self.w.winfo_height() + 6
            win = self.win = tk.Toplevel(self.w)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            tk.Label(win, text=self.text, bg=K.CARD3, fg=K.TEXT, font=font(11, "medium"), padx=9, pady=4, bd=0,
                     highlightthickness=1, highlightbackground=K.EDGE_HI).pack()
            win.update_idletasks()
            win.geometry(f"+{x - win.winfo_width() // 2}+{y}")
        except tk.TclError:
            self.win = None

    def leave(self, _e=None):
        try:
            if self.job:
                self.w.after_cancel(self.job)
        except tk.TclError:
            pass
        self.job = None
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
            self.win = None


# ====================================================================================== buttons
class Btn(tk.Canvas):
    """Chunky rounded button with an optional Lucide icon.
    kind: primary (accent fill) | secondary | ghost | ok | danger.   h: 30 small / 36 normal / 44 large."""
    GAP = 8

    def __init__(self, parent, text="", cmd=None, theme=None, icon=None, kind="secondary", w=None, h=36, bg=None,
                 size=13, weight="semibold", pad=16, radius=9, tip=None):
        self.bgc = bg or K.CARD
        self.spec = font(size, weight)
        self.iname, self.isz = icon, max(14, int(size * 1.25))
        tw = text_w(text, self.spec) if text else 0
        iw = (self.isz + (self.GAP if text else 0)) if icon else 0
        width = w or (tw + iw + pad * 2)
        super().__init__(parent, width=width, height=h, bg=self.bgc, highlightthickness=0, bd=0, cursor="hand2")
        self.cmd, self.kind, self.theme, self.w, self.h, self.r = cmd, kind, theme, width, h, radius
        self.text, self.state, self.disabled, self.job = text, "normal", False, None
        self.accent = theme.cur if theme else K.IDLE
        self.cur = None
        self.bgimg = self.create_image(0, 0, anchor="nw")
        self.iconimg = self.create_image(0, 0, anchor="w") if icon else None
        self.txt = self.create_text(0, h / 2 - 1, text=text, anchor="w", font=self.spec)
        self.bind("<Enter>", lambda e: self._go("hover"))
        self.bind("<Leave>", lambda e: self._go("normal"))
        self.bind("<ButtonPress-1>", lambda e: self._go("press"))
        self.bind("<ButtonRelease-1>", self._release)
        if tip:
            Tip(self, tip)
        if theme and kind in ("primary",):
            theme.sub(self.retheme)
        self.paint()

    # ---- colours
    def colours(self, state):
        a = self.accent
        if self.disabled:
            return lerp(self.bgc, K.CARD2, .6), None, K.FAINT
        if self.kind == "primary":
            fill = {"normal": a, "hover": lerp(a, "#ffffff", .14), "press": lerp(a, "#000000", .16)}[state]
            return fill, None, on_colour(a)
        if self.kind == "ok":
            f = {"normal": .16, "hover": .26, "press": .34}[state]
            return lerp(K.CARD2, K.OK, f), lerp(K.CARD2, K.OK, .45), K.OK
        if self.kind == "danger":
            f = {"normal": .16, "hover": .26, "press": .34}[state]
            return lerp(K.CARD2, K.BAD, f), lerp(K.CARD2, K.BAD, .45), K.BAD
        if self.kind == "ghost":
            return {"normal": None, "hover": K.CARD2, "press": K.CARD3}[state], None, K.TEXT if state != "normal" else K.DIM
        fill = {"normal": K.CARD2, "hover": K.CARD3, "press": lerp(K.CARD2, "#000000", .2)}[state]
        edge = {"normal": K.EDGE, "hover": lerp(K.EDGE_HI, a, .35), "press": K.EDGE_HI}[state]
        return fill, edge, K.TEXT

    def paint(self, state=None):
        state = state or self.state
        fill, edge, fg = self.colours(state)
        key = (fill, edge, fg, self.iname)
        if key == self.cur:
            return
        self.cur = key
        self.itemconfig(self.bgimg, image=rr_photo(self.w, self.h, self.r, fill, edge))
        self.itemconfig(self.txt, fill=fg)
        dy = 1 if state == "press" and not self.disabled else 0
        tw = text_w(self.text, self.spec) if self.text else 0
        iw = self.isz if self.iname else 0
        total = iw + (self.GAP if (self.iname and self.text) else 0) + tw
        x = (self.w - total) / 2
        if self.iname:
            self.itemconfig(self.iconimg, image=icon(self.iname, self.isz, fg))
            self.coords(self.iconimg, x, self.h / 2 + dy)
            x += iw + (self.GAP if self.text else 0)
        self.coords(self.txt, x, self.h / 2 - 1 + dy)

    def _go(self, state):
        if self.disabled:
            return
        self.state = state
        self.paint(state)

    def _release(self, e):
        if self.disabled:
            return
        inside = 0 <= e.x <= self.w and 0 <= e.y <= self.h
        self._go("hover" if inside else "normal")
        if inside and self.cmd:
            self.cmd()

    def retheme(self, accent):
        self.accent = accent
        self.cur = None
        self.paint()

    def set(self, text=None, kind=None, icon=None, disabled=None):
        changed = False
        if text is not None and text != self.text:
            self.text = text
            self.itemconfig(self.txt, text=text)
            changed = True
        if kind is not None and kind != self.kind:
            if kind == "primary" and self.theme and self.retheme not in self.theme.subs:
                self.theme.sub(self.retheme)
            self.kind, changed = kind, True
        if icon is not None and icon != self.iname and self.iconimg is not None:
            self.iname, changed = icon, True
        if disabled is not None and disabled != self.disabled:
            self.disabled = disabled
            self.config(cursor="arrow" if disabled else "hand2")
            changed = True
        if changed:
            self.cur = None
            self.paint()


class IconBtn(Btn):
    """Square icon-only button with a tooltip."""
    def __init__(self, parent, icon, cmd=None, theme=None, size=36, kind="ghost", bg=None, tip=None, px=18):
        self._px = px
        super().__init__(parent, "", cmd, theme, icon=icon, kind=kind, w=size, h=size, bg=bg, pad=0, radius=9, tip=tip)
        self.isz = px


# ====================================================================================== switch / segmented
class Switch(tk.Canvas):
    """Square-ish toggle with a short slide."""
    W, H = 46, 26

    def __init__(self, parent, on_change, theme, bg=None):
        super().__init__(parent, width=self.W, height=self.H, bg=bg or K.CARD, highlightthickness=0, bd=0, cursor="hand2")
        self.on, self.cb, self.t, self.job, self.accent, self.theme = None, on_change, 0.0, None, theme.cur, theme
        self.track = self.create_image(0, 0, anchor="nw")
        self.knob = self.create_image(0, 0, anchor="nw")
        self.bind("<ButtonRelease-1>", lambda e: self.cb(not self.on))
        theme.sub(self.retheme)

    def retheme(self, accent):
        self.accent = accent
        if self.t > 0 or self.on is None:
            self.draw(self.t)

    def draw(self, t):
        self.t = t
        fill = lerp(K.CARD3, self.accent, t)
        edge = lerp(K.EDGE_HI, self.accent, t)
        self.itemconfig(self.track, image=rr_photo(self.W, self.H, 8, fill, edge))
        x = 4 + (self.W - 22 - 4 * 2 + 2) * t
        self.itemconfig(self.knob, image=rr_photo(18, 18, 6, lerp(K.DIM, "#ffffff", t), None))
        self.coords(self.knob, round(x), 4)

    def set(self, on):
        if on == self.on:
            return
        first = self.on is None
        self.on = on
        if first or not K.M["btn_fade"]:
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
                k = i / 5
                self.draw(start + (end - start) * (k * k * (3 - 2 * k)))
                self.job = self.after(16, step, i + 1) if i < 5 else None
            except tk.TclError:
                pass

        step()


class Seg(tk.Frame):
    """Segmented choice: a row of buttons, the current one filled with the accent."""
    def __init__(self, parent, choices, get, pick, theme, seg_w=72, bg=None, h=32, size=12):
        super().__init__(parent, bg=bg or K.CARD)
        self.get, self.btns = get, []
        for label, value in choices:
            b = Btn(self, label, lambda v=value: pick(v), theme, w=seg_w, h=h, bg=bg or K.CARD, size=size, radius=8, weight="medium")
            b.pack(side="left", padx=(0, 6))
            self.btns.append((b, value))
        self.sync()

    def sync(self):
        cur = self.get()
        for b, v in self.btns:
            b.set(kind="primary" if _same(cur, v) else "secondary")


def _same(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return a == b


# ====================================================================================== cards, pills, bars
class Card(tk.Canvas):
    """Flat rounded panel with a 1px border. Put widgets in .inner, or draw straight on the canvas (pad=0).
    title/icon draw a small caps header; .inner then starts below it."""
    all = []

    def __init__(self, parent, w, h, pad=20, title=None, icon=None, fill=None, radius=14, bg=None, edge=None):
        super().__init__(parent, width=w, height=h, bg=bg or K.BG, highlightthickness=0, bd=0)
        self.w, self.h, self.level = w, h, 0.0
        self.fill0, self.edge0, self.radius = fill or K.CARD, edge or K.EDGE, radius
        Card.all.append(self)
        self.body = self.create_image(0, 0, anchor="nw", image=rr_photo(w, h, radius, self.fill0, self.edge0))
        self.inner = None
        top = pad
        if title:
            x = pad
            if icon:
                self.create_image(x, 25, anchor="w", image=globals()["icon"](icon, 16, K.FAINT))
                x += 24
            self.create_text(x, 25, anchor="w", text=title.upper(), fill=K.FAINT, font=font(11, "semibold"))
            top = 48
        if pad > 0:
            self.inner = tk.Frame(self, bg=self.fill0)
            self.create_window(pad, top, window=self.inner, anchor="nw", width=w - 2 * pad, height=h - top - pad)

    def glow(self, level, accent):
        """Hover: the border warms toward the accent."""
        self.level = level
        self.itemconfig(self.body, image=rr_photo(self.w, self.h, self.radius, self.fill0, lerp(self.edge0, accent, .55 * level)))


class Pill(tk.Canvas):
    """Small status pill: coloured dot (or icon) and text on a tinted background."""
    def __init__(self, parent, text="", colour=None, bg=None, dot=True, icon=None, size=11):
        self.bgc, self.spec, self.dot, self.iname = bg or K.CARD, font(size, "semibold"), dot, icon
        super().__init__(parent, width=10, height=24, bg=self.bgc, highlightthickness=0, bd=0)
        self.img = self.create_image(0, 0, anchor="nw")
        self.ic = self.create_image(0, 12, anchor="w") if icon else None
        self.dt = self.create_oval(0, 0, 0, 0, outline="", state="hidden")
        self.tx = self.create_text(0, 11, anchor="w", font=self.spec)
        self.key = None
        self.set(text, colour or K.DIM)

    def set(self, text, colour):
        key = (text, colour)
        if key == self.key:
            return
        self.key = key
        left = 10
        extra = 0
        if self.iname:
            extra = 16 + 6
        elif self.dot:
            extra = 8 + 6
        w = left * 2 + extra + text_w(text, self.spec)
        h = 24
        self.config(width=w, height=h)
        self.itemconfig(self.img, image=rr_photo(w, h, 12, lerp(self.bgc, colour, .16), lerp(self.bgc, colour, .38)))
        x = left
        if self.iname:
            self.itemconfig(self.ic, image=icon(self.iname, 14, colour))
            self.coords(self.ic, x, h / 2)
            x += 20
        elif self.dot:
            self.coords(self.dt, x, h / 2 - 4, x + 8, h / 2 + 4)
            self.itemconfig(self.dt, fill=colour, state="normal")
            x += 14
        self.itemconfig(self.tx, text=text, fill=colour)
        self.coords(self.tx, x, h / 2 - 1)


class Bar(tk.Canvas):
    """Progress bar: dark track, accent fill with a bright end."""
    def __init__(self, parent, w, h=10, bg=None):
        super().__init__(parent, width=w, height=h, bg=bg or K.CARD, highlightthickness=0, bd=0)
        self.w, self.h, self.key = w, h, None
        self.track = self.create_image(0, 0, anchor="nw", image=rr_photo(w, h, h // 2, K.CARD3, None))
        self.fill = self.create_image(0, 0, anchor="nw")
        self.tip = self.create_image(0, 0, anchor="nw")

    def set(self, frac, colour):
        frac = max(0.0, min(1.0, frac))
        fw = int(self.w * frac)
        key = (fw, colour)
        if key == self.key:
            return
        self.key = key
        if fw < 2:
            self.itemconfig(self.fill, state="hidden")
            self.itemconfig(self.tip, state="hidden")
            return
        fw = max(fw, self.h)
        self.itemconfig(self.fill, image=rr_photo(fw, self.h, self.h // 2, colour, None), state="normal")
        tipw = min(fw, 26)
        self.itemconfig(self.tip, image=rr_photo(tipw, self.h, self.h // 2, lerp(colour, "#ffffff", .35), None), state="normal")
        self.coords(self.tip, fw - tipw, 0)


class EntryBox(tk.Canvas):
    """Rounded text input: a canvas draws the field, a bare Entry sits inside it. The border turns accent when focused."""
    def __init__(self, parent, var=None, w=220, h=36, bg=None, theme=None, icon=None, size=13, show=None, placeholder=None):
        self.bgc = bg or K.CARD
        super().__init__(parent, width=w, height=h, bg=self.bgc, highlightthickness=0, bd=0)
        self.w, self.h, self.theme, self.focus = w, h, theme, False
        self.var = var or tk.StringVar()
        self.img = self.create_image(0, 0, anchor="nw")
        x = 12
        if icon:
            self.create_image(12, h / 2, anchor="w", image=globals()["icon"](icon, 16, K.FAINT))
            x = 36
        self.entry = tk.Entry(self, textvariable=self.var, bg=K.CARD2, fg=K.TEXT, insertbackground=K.TEXT, relief="flat", bd=0,
                              highlightthickness=0, font=font(size, "regular"), show=show or "",
                              selectbackground=lerp(K.CARD2, K.IDLE, .5), selectforeground=K.TEXT)
        self.create_window(x, h / 2, anchor="w", window=self.entry, width=w - x - 12, height=h - 14)
        self.entry.bind("<FocusIn>", lambda e: self._focus(True), add="+")
        self.entry.bind("<FocusOut>", lambda e: self._focus(False), add="+")
        self.bind("<Button-1>", lambda e: self.entry.focus_set())
        self.phl = None
        if placeholder:
            self.phl = tk.Label(self, text=placeholder, bg=K.CARD2, fg=K.FAINT, font=font(size), anchor="w", bd=0, cursor="xterm")
            self.phl.bind("<Button-1>", lambda e: self.entry.focus_set())
            self.create_window(x, h / 2, anchor="w", window=self.phl, width=w - x - 12, height=h - 14)
            self.var.trace_add("write", lambda *a: self._ph())
        self._paint()
        self._ph()

    def _paint(self):
        accent = self.theme.cur if self.theme else K.IDLE
        edge = accent if self.focus else K.EDGE
        self.itemconfig(self.img, image=rr_photo(self.w, self.h, 9, K.CARD2, edge))

    def _ph(self):
        """Show the placeholder only while the field is empty and not being edited."""
        if self.phl is None:
            return
        if self.var.get() or self.focus:
            self.phl.lower()
        else:
            self.phl.lift()

    def _focus(self, on):
        self.focus = on
        self._paint()
        self._ph()


class TextBox(tk.Canvas):
    """Rounded multi-line input."""
    def __init__(self, parent, w=240, h=70, bg=None, theme=None, size=12):
        super().__init__(parent, width=w, height=h, bg=bg or K.CARD, highlightthickness=0, bd=0)
        self.w, self.h, self.theme, self.focus = w, h, theme, False
        self.img = self.create_image(0, 0, anchor="nw")
        self.text = tk.Text(self, bg=K.CARD2, fg=K.TEXT, insertbackground=K.TEXT, relief="flat", bd=0, highlightthickness=0,
                            font=font(size), wrap="word", selectbackground=lerp(K.CARD2, K.IDLE, .5))
        self.create_window(10, 8, anchor="nw", window=self.text, width=w - 20, height=h - 16)
        self.text.bind("<FocusIn>", lambda e: self._focus(True), add="+")
        self.text.bind("<FocusOut>", lambda e: self._focus(False), add="+")
        self._paint()

    def _paint(self):
        accent = self.theme.cur if self.theme else K.IDLE
        self.itemconfig(self.img, image=rr_photo(self.w, self.h, 9, K.CARD2, accent if self.focus else K.EDGE))

    def _focus(self, on):
        self.focus = on
        self._paint()


# ====================================================================================== dropdown
class Dropdown(tk.Canvas):
    """Styled dropdown. options() -> [(label, value)]; get() -> current value; pick(value) is called on selection."""
    ROW = 34

    def __init__(self, parent, options, get, pick, theme, w=200, h=36, bg=None, placeholder="Choose…", icon=None, size=13):
        self.bgc = bg or K.CARD
        super().__init__(parent, width=w, height=h, bg=self.bgc, highlightthickness=0, bd=0, cursor="hand2")
        self.options, self.get, self.pick, self.theme = options, get, pick, theme
        self.w, self.h, self.ph, self.spec, self.iname = w, h, placeholder, font(size, "medium"), icon
        self.win, self.hover, self.key = None, False, None
        self.img = self.create_image(0, 0, anchor="nw")
        self.ic = self.create_image(12, h / 2, anchor="w") if icon else None
        self.tx = self.create_text(12 + (26 if icon else 0), h / 2 - 1, anchor="w", font=self.spec)
        self.chev = self.create_image(w - 14, h / 2, anchor="e")
        self.bind("<Enter>", lambda e: self._hov(True))
        self.bind("<Leave>", lambda e: self._hov(False))
        self.bind("<ButtonRelease-1>", lambda e: self.toggle())
        self.refresh()

    def _hov(self, on):
        self.hover = on
        self.refresh(force=True)

    def label_now(self):
        cur = self.get()
        for lab, val in self.options():
            if val == cur or lab == cur:
                return lab, val not in (None, "")
        return (str(cur) if cur not in (None, "") else self.ph), bool(cur not in (None, ""))

    def refresh(self, force=False):
        text, chosen = self.label_now()
        key = (text, chosen, self.hover, self.win is not None)
        if key == self.key and not force:
            return
        self.key = key
        accent = self.theme.cur
        edge = lerp(K.EDGE_HI, accent, .5) if (self.win is not None) else (K.EDGE_HI if self.hover else K.EDGE)
        self.itemconfig(self.img, image=rr_photo(self.w, self.h, 9, K.CARD2 if not self.hover else K.CARD3, edge))
        maxw = self.w - 52 - (26 if self.iname else 0)
        if text_w(text, self.spec) > maxw:
            while len(text) > 1 and text_w(text + "…", self.spec) > maxw:
                text = text[:-1]
            text += "…"
        self.itemconfig(self.tx, text=text, fill=K.TEXT if chosen else K.FAINT)
        if self.ic:
            self.itemconfig(self.ic, image=icon(self.iname, 16, K.DIM))
        self.itemconfig(self.chev, image=icon("chevron-down", 16, K.DIM))

    def toggle(self):
        if self.win is not None:
            self.close()
        else:
            self.open()

    def open(self):
        opts = list(self.options())
        if not opts:
            return
        root = self.winfo_toplevel()
        win = self.win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=KEYCOLOUR)
        try:
            win.attributes("-transparentcolor", KEYCOLOUR)
        except tk.TclError:
            pass
        rows = min(len(opts), 8)
        ph = rows * self.ROW + 12
        pw = max(self.w, 190)
        x, y = self.winfo_rootx(), self.winfo_rooty() + self.h + 4
        if y + ph > self.winfo_screenheight() - 40:
            y = self.winfo_rooty() - ph - 4
        win.geometry(f"{pw}x{ph}+{x}+{y}")
        cv = tk.Canvas(win, width=pw, height=ph, bg=KEYCOLOUR, highlightthickness=0, bd=0)
        cv.pack(fill="both", expand=True)
        cv.create_image(0, 0, anchor="nw", image=rr_photo(pw, ph, 11, K.CARD, K.EDGE_HI))
        cur = self.get()
        state = {"off": 0, "hover": -1}

        def draw():
            cv.delete("row")
            for i in range(rows):
                idx = state["off"] + i
                if idx >= len(opts):
                    break
                lab, val = opts[idx]
                y0 = 6 + i * self.ROW
                sel = val == cur
                if i == state["hover"]:
                    cv.create_image(6, y0, anchor="nw", image=rr_photo(pw - 12, self.ROW - 2, 7, K.CARD3, None), tags="row")
                cv.create_text(18, y0 + self.ROW / 2 - 1, anchor="w", text=lab, fill=self.theme.cur if sel else K.TEXT,
                               font=font(13, "semibold" if sel else "regular"), tags="row")
                if sel:
                    cv.create_image(pw - 18, y0 + self.ROW / 2 - 1, anchor="e", image=icon("check", 16, self.theme.cur), tags="row")

        def at(e):
            i = (e.y - 6) // self.ROW
            return int(i) if 0 <= i < rows and state["off"] + i < len(opts) else -1

        def motion(e):
            i = at(e)
            if i != state["hover"]:
                state["hover"] = i
                draw()

        def click(e):
            i = at(e)
            if i >= 0:
                val = opts[state["off"] + i][1]
                self.close()
                self.pick(val)
                self.refresh(force=True)

        def wheel(e):
            if len(opts) > rows:
                state["off"] = max(0, min(len(opts) - rows, state["off"] - (1 if e.delta > 0 else -1)))
                draw()

        cv.bind("<Motion>", motion)
        cv.bind("<Leave>", lambda e: (state.update(hover=-1), draw()))
        cv.bind("<ButtonRelease-1>", click)
        cv.bind("<MouseWheel>", wheel)
        draw()
        self._outside = root.bind("<ButtonPress-1>", self._maybe_close, add="+")
        win.bind("<Escape>", lambda e: self.close())
        self.refresh(force=True)

    def _maybe_close(self, e):
        try:
            if self.win is not None and e.widget is not self and not str(e.widget).startswith(str(self.win)):
                self.close()
        except tk.TclError:
            pass

    def close(self):
        win, self.win = self.win, None
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass
        self.refresh(force=True)


# ====================================================================================== sparkline
class Spark(tk.Canvas):
    """Rolling line graph with a soft fill. series: [(name, colour, deque)]."""
    def __init__(self, parent, w, h, series, bg=None):
        super().__init__(parent, width=w, height=h, bg=bg or K.CARD, highlightthickness=0, bd=0)
        self.w, self.h, self.series, self.bgc = w, h, series, bg or K.CARD

    def redraw(self):
        self.delete("all")
        w, h = self.w, self.h
        peak = max([100.0] + [max(d) * 1.15 for _, _, d in self.series if d])
        for i in range(1, 4):
            y = h * i / 4
            self.create_line(0, y, w, y, fill=lerp(self.bgc, K.EDGE, .8), dash=(2, 5))
        self.create_text(w - 2, 2, anchor="ne", text=f"{peak:.0f}%", fill=K.FAINT, font=font(10, "medium"))
        for name, colour, data in self.series:
            if len(data) < 2:
                continue
            n = data.maxlen or len(data)
            pts = []
            for i, v in enumerate(data):
                x = w - (len(data) - 1 - i) * (w / (n - 1))
                y = h - 3 - (h - 8) * min(v, peak) / peak
                pts += [x, y]
            self.create_polygon([pts[0], h] + pts + [pts[-2], h], fill=lerp(self.bgc, colour, .13), outline="")
            self.create_line(pts, fill=colour, width=2, smooth=True)


# ====================================================================================== auto-height card and scroll area
class AutoCard(tk.Canvas):
    """A rounded card whose height follows what you put in .inner (for Settings sections)."""
    def __init__(self, parent, w, title=None, icon=None, pad=20, bg=None, fill=None, radius=14, edge=None):
        super().__init__(parent, width=w, height=80, bg=bg or K.BG, highlightthickness=0, bd=0)
        self.w, self.pad, self.radius = w, pad, radius
        self.fill0, self.edge0 = fill or K.CARD, edge or K.EDGE
        self.body = self.create_image(0, 0, anchor="nw")
        self.top = pad
        if title:
            x = pad
            if icon:
                self.create_image(x, 26, anchor="w", image=globals()["icon"](icon, 16, K.FAINT))
                x += 24
            self.create_text(x, 26, anchor="w", text=title.upper(), fill=K.FAINT, font=font(11, "semibold"))
            self.top = 50
        self.inner = tk.Frame(self, bg=self.fill0)
        self.create_window(pad, self.top, window=self.inner, anchor="nw", width=w - 2 * pad)
        self.h = 0
        self.inner.bind("<Configure>", lambda e: self._fit())
        self._fit()

    def _fit(self):
        try:
            h = self.inner.winfo_reqheight() + self.top + self.pad
        except tk.TclError:
            return
        if h != self.h:
            self.h = h
            self.config(height=h)
            self.itemconfig(self.body, image=rr_photo(self.w, h, self.radius, self.fill0, self.edge0))


class ScrollFrame(tk.Frame):
    """Vertically scrolling area with a slim thumb on the right. Put widgets in .inner."""
    def __init__(self, parent, w, h, bg=None, theme=None):
        super().__init__(parent, width=w, height=h, bg=bg or K.BG)
        self.pack_propagate(False)
        self.w, self.h, self.bgc, self.theme = w, h, bg or K.BG, theme
        self.cv = tk.Canvas(self, bg=self.bgc, highlightthickness=0, bd=0, width=w - 12, height=h)
        self.cv.pack(side="left", fill="both")
        self.bar = tk.Canvas(self, bg=self.bgc, highlightthickness=0, bd=0, width=12, height=h)
        self.bar.pack(side="right", fill="y")
        self.inner = tk.Frame(self.cv, bg=self.bgc)
        self.cv.create_window(0, 0, window=self.inner, anchor="nw", width=w - 12)
        self.inner.bind("<Configure>", lambda e: self._sync())
        self.thumb = self.bar.create_image(6, 0, anchor="n")
        self.bar.bind("<B1-Motion>", self._drag)
        self.bar.bind("<Button-1>", self._drag)
        self.frac = (0.0, 1.0)

    def _sync(self):
        try:
            bbox = (0, 0, self.w - 12, max(self.inner.winfo_reqheight(), self.h))
            self.cv.configure(scrollregion=bbox)
            self._thumb()
        except tk.TclError:
            pass

    def _thumb(self):
        lo, hi = self.cv.yview()
        self.frac = (lo, hi)
        if hi - lo >= 0.999:
            self.bar.itemconfig(self.thumb, state="hidden")
            return
        th = max(36, int(self.h * (hi - lo)))
        self.bar.itemconfig(self.thumb, image=rr_photo(6, th, 3, K.EDGE_HI, None), state="normal")
        self.bar.coords(self.thumb, 6, int((self.h - th) * (lo / max(1e-6, 1 - (hi - lo)))) if hi - lo < 1 else 0)

    def _drag(self, e):
        lo, hi = self.frac
        span = hi - lo
        self.cv.yview_moveto(max(0.0, min(1 - span, e.y / self.h - span / 2)))
        self._thumb()

    def scroll(self, delta):
        lo, hi = self.cv.yview()
        if hi - lo >= 0.999:
            return
        self.cv.yview_scroll(int(-delta / 120) * 3, "units")
        self._thumb()

    def to_top(self):
        self.cv.yview_moveto(0)
        self._thumb()

    def contains(self, widget):
        while widget is not None:
            if widget is self:
                return True
            widget = getattr(widget, "master", None)
        return False


# ====================================================================================== timer digits + banner wash
def draw_digits(cv, tag, text, x_right, y, px, colour, weight="bold", anchor_y="n"):
    """Draw a countdown right-aligned with every digit in a fixed-width slot, so it never jitters as it ticks."""
    spec = font(px, weight)
    dw = max(text_w(str(d), spec) for d in range(10)) + 1
    cw = text_w(":", spec) + 1
    cv.delete(tag)
    x = x_right
    for ch in reversed(text):
        slot = cw if ch == ":" else dw if ch.isdigit() else text_w(ch, spec) + 1
        cv.create_text(x - slot / 2, y, text=ch, fill=colour, font=spec, anchor="n", tags=tag)
        x -= slot
    return x_right - x                                    # total width drawn


def wash_pil(w, h, r, colour, strength=.16, reach=.6):
    """A soft accent glow fading from the left edge, clipped to a rounded rectangle."""
    key = ("wash", w, h, r, colour, strength, reach)
    im = _pil_cache.get(key)
    if im is None:
        _cap(_pil_cache)
        import numpy as np
        xs = np.linspace(0, 1, w, dtype="float32")
        ys = np.linspace(0, 1, h, dtype="float32")
        fall = np.clip(1 - xs / reach, 0, 1) ** 1.6
        vert = 1 - 0.35 * np.abs(ys - 0.5) * 2
        alpha = (fall[None, :] * vert[:, None] * strength * 255).astype("uint8")
        mask = rr_pil(w, h, r, "#ffffff", None).getchannel("A")
        a = Image.fromarray(alpha, "L")
        from PIL import ImageChops
        a = ImageChops.multiply(a, mask)
        im = Image.new("RGBA", (w, h), _rgba(colour))
        im.putalpha(a)
        _pil_cache[key] = im
    return im


def wash(w, h, r, colour, strength=.16, reach=.6):
    return photo(("wash", w, h, r, colour, strength, reach), lambda: wash_pil(w, h, r, colour, strength, reach))
