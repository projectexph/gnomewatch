"""Ghost replay (dry run): shows where a recording WOULD click and press, in time, without sending a single input.

GhostCanvas  - a canvas that plays a list of steps as ghost markers (numbered rings for clicks, key chips along the bottom).
open_overlay - draws that over a real Roblox window (a click-through layer), with a small Stop bar. Nothing is ever clicked.
Used by Auto-place ("Dry run") and by the macro import preview.
"""
import time
import tkinter as tk

import ui_kit as U
from ui_kit import K, font, lerp

KEYCAP = {0x20: "space", 0x0D: "enter", 0x09: "tab", 0x1B: "esc", 0x25: "←", 0x26: "↑", 0x27: "→", 0x28: "↓"}
TAIL = 1.4                                             # seconds shown after the last step


def key_label(vk):
    return KEYCAP.get(vk) or (chr(vk) if 0x30 <= vk <= 0x5A else "?")


class GhostCanvas(tk.Canvas):
    def __init__(self, parent, w, h, bg, colour, mock=False):
        super().__init__(parent, width=w, height=h, bg=bg, highlightthickness=0, bd=0)
        self.w, self.h, self.bgc, self.colour, self.mock = w, h, bg, colour, mock
        self.steps, self.job, self.t0, self.i = [], None, 0, 0
        self.keep = []
        self.on_done, self.on_tick = None, None
        self.rings, self.chips, self.downs, self.n = [], [], {}, 0
        self.last_pt = None
        self.draw_backdrop()

    def draw_backdrop(self):
        self.delete("all")
        if self.mock:                                  # a stand-in for the Roblox window when there isn't one to draw over
            self.create_rectangle(0, 0, self.w, self.h, fill=lerp(K.BG, "#ffffff", .03), outline=K.EDGE)
            for gx in range(1, 8):
                self.create_line(self.w * gx / 8, 0, self.w * gx / 8, self.h, fill=lerp(K.BG, "#ffffff", .06))
            for gy in range(1, 5):
                self.create_line(0, self.h * gy / 5, self.w, self.h * gy / 5, fill=lerp(K.BG, "#ffffff", .06))
            self.create_text(self.w / 2, 14, text="PREVIEW  ·  positions are shown as a share of the window", fill=K.FAINT, font=font(10, "semibold"))

    def play(self, steps, on_done=None, on_tick=None):
        self.stop()
        self.draw_backdrop()
        self.steps = list(steps)
        self.i, self.n, self.last_pt = 0, 0, None
        self.rings, self.chips, self.downs, self.keep = [], [], {}, []
        self.on_done, self.on_tick = on_done, on_tick
        self.end_t = (max((s["t"] for s in self.steps), default=0.0)) + TAIL
        self.t0 = time.time()
        self._tick()

    def stop(self, finished=False):
        if self.job:
            try:
                self.after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None
        if finished and self.on_done:
            cb, self.on_done = self.on_done, None
            cb()

    # ------------------------------------------------------------------ frames
    def _tick(self):
        t = time.time() - self.t0
        try:
            while self.i < len(self.steps) and self.steps[self.i]["t"] <= t:
                self._fire(self.steps[self.i], t)
                self.i += 1
            self._animate(t)
            if self.on_tick:
                self.on_tick(min(1.0, t / self.end_t), self.n)
        except tk.TclError:
            self.job = None
            return
        if t >= self.end_t:
            self.job = None
            self.stop(finished=True)
            return
        self.job = self.after(33, self._tick)

    def _fire(self, s, t):
        c = self.colour
        if "k" in s:
            if s["d"]:
                self._chip(key_label(s["k"]), t)
                self.n += 1
            return
        px, py = s["x"] * self.w, s["y"] * self.h
        if s["d"]:
            self.n += 1
            self.downs[s["m"]] = (px, py)
            if self.last_pt:
                self.create_line(*self.last_pt, px, py, fill=lerp(self.bgc, c, .55), dash=(4, 4), width=2)
            self.last_pt = (px, py)
            ring = self.create_oval(px - 10, py - 10, px + 10, py + 10, outline=c, width=3)
            dot = self.create_oval(px - 13, py - 13, px + 13, py + 13, fill=lerp(self.bgc, c, .35), outline=c, width=2)
            num = self.create_text(px, py, text=str(self.n), fill="#ffffff", font=font(11, "bold"))
            tag = self.create_text(px + 18, py - 18, anchor="w", text=("L" if s["m"] == "L" else "R") + "-click", fill=c, font=font(10, "semibold"))
            self.rings.append([ring, px, py, t])
            self.tag_raise(num)
        else:
            d = self.downs.pop(s["m"], None)
            if d and (abs(d[0] - px) > 6 or abs(d[1] - py) > 6):               # a drag: show where the button was let go
                self.create_line(d[0], d[1], px, py, fill=self.colour, width=3, arrow="last")

    def _animate(self, t):
        for r in self.rings:
            age = t - r[3]
            if age < 0.6:
                rad = 13 + 30 * age / 0.6
                self.coords(r[0], r[1] - rad, r[2] - rad, r[1] + rad, r[2] + rad)
                self.itemconfig(r[0], outline=lerp(self.colour, self.bgc, age / 0.6))
            elif r[0]:
                self.delete(r[0])
                r[0] = None
        keep = []
        for ch in self.chips:
            age = t - ch[2]
            if age > 1.1:
                for it in ch[0]:
                    self.delete(it)
            else:
                keep.append(ch)
        self.chips = keep

    def _chip(self, label, t):
        cx = self.w / 2 + (len(self.chips) - 1.5) * 0 + 0
        x = self.w / 2 - 60 * (len(self.chips) % 4) + 90
        y = self.h - 34
        wd = max(34, U.text_w(label, font(14, "bold")) + 22)
        img = U.rr_photo(wd, 32, 8, lerp(self.bgc, self.colour, .22), self.colour, 2)
        self.keep.append(img)                                  # the shared image cache can be emptied while this is still on screen
        bg = self.create_image(x, y, image=img)
        tx = self.create_text(x, y, text=label, fill="#ffffff", font=font(14, "bold"))
        self.chips.append(([bg, tx], x, t))


def _click_through(win):
    """Let every click fall through this window to whatever is underneath, and never take the focus."""
    import ctypes
    try:
        u = ctypes.windll.user32
        hwnd = u.GetParent(win.winfo_id()) or win.winfo_id()
        style = u.GetWindowLongW(hwnd, -20)
        u.SetWindowLongW(hwnd, -20, style | 0x00080000 | 0x00000020 | 0x08000000 | 0x00000080)   # LAYERED | TRANSPARENT | NOACTIVATE | TOOLWINDOW
    except Exception:
        pass


def open_overlay(root, theme, rect, steps, colour, title, on_close=None):
    """Play the steps as ghosts over the screen rectangle rect = (x, y, w, h). Returns a function that closes it."""
    x0, y0, w, h = rect
    ov = tk.Toplevel(root)
    ov.overrideredirect(True)
    ov.attributes("-topmost", True)
    ov.configure(bg=U.KEYCOLOUR)
    try:
        ov.attributes("-transparentcolor", U.KEYCOLOUR)
    except tk.TclError:
        pass
    ov.geometry(f"{w}x{h}+{x0}+{y0}")
    gc = GhostCanvas(ov, w, h, U.KEYCOLOUR, colour)
    gc.pack()
    ov.update_idletasks()
    _click_through(ov)

    bar = tk.Toplevel(root)
    bar.overrideredirect(True)
    bar.attributes("-topmost", True)
    bar.configure(bg=U.KEYCOLOUR)
    try:
        bar.attributes("-transparentcolor", U.KEYCOLOUR)
    except tk.TclError:
        pass
    bw, bh = 520, 54
    bar.geometry(f"{bw}x{bh}+{x0 + (w - bw) // 2}+{max(0, y0 - bh - 8) if y0 > bh + 12 else y0 + 10}")
    cv = tk.Canvas(bar, width=bw, height=bh, bg=U.KEYCOLOUR, highlightthickness=0, bd=0)
    cv.pack()
    cv.create_image(0, 0, anchor="nw", image=U.rr_photo(bw, bh, 27, lerp(K.BG, colour, .14), lerp(K.BG, colour, .6), 2))
    cv.create_text(24, 27, anchor="w", text="DRY RUN", fill=colour, font=font(13, "bold"))
    cv.create_text(104, 18, anchor="w", text=title, fill=K.TEXT, font=font(13, "semibold"))
    sub = cv.create_text(104, 37, anchor="w", text="Nothing is clicked or pressed - this only shows where it would.", fill=K.DIM, font=font(10, "medium"))
    prog = cv.create_rectangle(104, bh - 4, 104, bh - 2, fill=colour, outline="")
    state = {"closed": False}

    def close(_=None):
        if state["closed"]:
            return
        state["closed"] = True
        gc.stop()
        for wnd in (ov, bar):
            try:
                wnd.destroy()
            except tk.TclError:
                pass
        if on_close:
            on_close()

    stop = U.Btn(cv, "Stop", close, theme, icon="square", kind="danger", h=34, size=12, bg=lerp(K.BG, colour, .14), pad=12)
    cv.create_window(bw - 14, 27, anchor="e", window=stop)

    def tick(frac, n):
        try:
            cv.coords(prog, 104, bh - 4, 104 + (bw - 250) * frac, bh - 2)
            cv.itemconfig(sub, text=f"{n} press{'es' if n != 1 else ''} shown")
        except tk.TclError:
            pass

    gc.play(steps, on_done=lambda: root.after(900, close), on_tick=tick)
    return close
