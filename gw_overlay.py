"""Overlay widget: a small always-on-top pill that shows the current weather event and a countdown, readable over the game.

Unlocked: drag it anywhere; right-click for a menu. Locked: every click falls through to the game (and it never takes focus).
It only redraws when something changes, so it costs next to nothing. Switched on/off and locked with the OVERLAY / OVERLAY_LOCK
settings (Settings > Overlay, or the command palette).
"""
import time
import tkinter as tk

import ui_kit as U
from ui_kit import K, font, lerp
from gw_ghost import _click_through

W, H = 268, 58
GWL_EXSTYLE, WS_EX_TRANSPARENT = -20, 0x00000020


def fmt_clock(sec):
    sec = max(0, int(sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def view(S, now=None, show_next=True):
    """What the pill should say right now: (event name or '', headline, countdown text, colour)."""
    now = now or time.time()
    a, n = S.STATE.get("active"), S.STATE.get("next")
    if a and now - a[2] < 90:                                               # on the board a moment ago
        left = (a[1] - now) if a[1] else None
        return a[0], a[0].title(), (fmt_clock(left) if left is not None and left > -5 else "now"), S._event_style(a[0])[1]
    if show_next and n and n[1] and n[1] > now and now - n[2] < 600:
        return n[0], "Next: " + n[0].title(), fmt_clock(n[1] - now), S._event_style(n[0])[1]
    return "", "Watching the board", "", K.DIM


class Overlay:
    def __init__(self, root, S, theme):
        self.root, self.S, self.theme = root, S, theme
        self.win = None
        self.sig = None
        self.drag = None
        self.menu = None

    # ------------------------------------------------------------------ life
    def sync(self):
        """Called about once a second by the console. Cheap when nothing changed."""
        S = self.S
        want = bool(S.OVERLAY) and not S.PANIC.is_set()
        if want and self.win is None:
            self._open()
        elif not want and self.win is not None:
            self._close()
        if self.win is None:
            return
        locked = bool(S.OVERLAY_LOCK)
        if locked != self.locked_now:
            self._lock(locked)
        ev, head, count, col = view(S, show_next=bool(S.OVERLAY_NEXT))
        sig = (ev, head, col, bool(count))
        if sig != self.sig:
            self._paint(ev, head, col, bool(count))
            self.sig = sig
        try:
            self.cv.itemconfig(self.t_count, text=count)
        except tk.TclError:
            pass

    def _open(self):
        S = self.S
        w = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=U.KEYCOLOUR)
        try:
            w.attributes("-transparentcolor", U.KEYCOLOUR)
            w.attributes("-alpha", 0.95)
        except tk.TclError:
            pass
        x, y = int(S.OVERLAY_X), int(S.OVERLAY_Y)
        if x < 0 or y < 0 or x > w.winfo_screenwidth() - 40 or y > w.winfo_screenheight() - 30:       # first time, or its screen went away
            x, y = (w.winfo_screenwidth() - W) // 2, 14
        w.geometry(f"{W}x{H}+{x}+{y}")
        self.cv = tk.Canvas(w, width=W, height=H, bg=U.KEYCOLOUR, highlightthickness=0, bd=0)
        self.cv.pack()
        self.bg = self.cv.create_image(0, 0, anchor="nw")
        self.badge = self.cv.create_image(31, H / 2)
        self.icon = self.cv.create_image(31, H / 2)
        self.t_head = self.cv.create_text(62, H / 2 - 8, anchor="w", text="", fill=K.TEXT, font=font(14, "bold"))
        self.t_sub = self.cv.create_text(62, H / 2 + 12, anchor="w", text="", fill=K.DIM, font=font(10, "semibold"))
        self.t_count = self.cv.create_text(W - 18, H / 2, anchor="e", text="", fill=K.TEXT, font=font(21, "bold"))
        self.win = w
        self.locked_now = None
        self.sig = None
        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<B1-Motion>", self._move)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.cv.bind("<Button-3>", self._menu)
        w.update_idletasks()

    def _close(self):
        self._unmenu()
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self.win = None

    def _lock(self, locked):
        """Locked = clicks pass straight through to the game. Unlocked = you can drag it."""
        import ctypes
        self.locked_now = locked
        try:
            u = ctypes.windll.user32
            hwnd = u.GetParent(self.win.winfo_id()) or self.win.winfo_id()
            style = u.GetWindowLongW(hwnd, GWL_EXSTYLE) | 0x00080000 | 0x08000000 | 0x00000080          # LAYERED | NOACTIVATE | TOOLWINDOW
            style = (style | WS_EX_TRANSPARENT) if locked else (style & ~WS_EX_TRANSPARENT)
            u.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception:
            pass
        self.cv.config(cursor="arrow" if locked else "fleur")

    # ------------------------------------------------------------------ look
    def _paint(self, ev, head, col, has_count):
        c = col if ev else K.IDLE
        # keep our own references: the shared image cache can be emptied while this long-lived widget still shows these
        self.imgs = (U.rr_photo(W, H, 29, lerp(K.BG, c, .13), lerp(K.BG, c, .65), 2), U.rr_photo(38, 38, 11, lerp(K.BG, c, .22), lerp(K.BG, c, .55)),
                     U.icon(U.event_icon(ev) if ev else "radar", 22, c))
        self.cv.itemconfig(self.bg, image=self.imgs[0])
        self.cv.itemconfig(self.badge, image=self.imgs[1])
        self.cv.itemconfig(self.icon, image=self.imgs[2])
        self.cv.itemconfig(self.t_head, text=U.ellipsize(head, font(14, "bold"), W - 62 - (84 if has_count else 18)))
        self.cv.itemconfig(self.t_sub, text="ENDS IN" if has_count and not head.startswith("Next") else "STARTS IN" if has_count else "NO EVENT")
        self.cv.itemconfig(self.t_count, fill=c)

    # ------------------------------------------------------------------ dragging and the little menu
    def _press(self, e):
        if self.locked_now:
            return
        self._unmenu()
        self.drag = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def _move(self, e):
        if self.drag and self.win:
            self.win.geometry(f"+{e.x_root - self.drag[0]}+{e.y_root - self.drag[1]}")

    def _release(self, _e):
        if self.drag and self.win:
            self.drag = None
            self.S._set_setting("OVERLAY_X", self.win.winfo_x())
            self.S._set_setting("OVERLAY_Y", self.win.winfo_y())

    def _unmenu(self):
        if self.menu:
            try:
                self.menu.destroy()
            except tk.TclError:
                pass
            self.menu = None

    def _menu(self, e):
        if self.locked_now:
            return
        self._unmenu()
        m = tk.Menu(self.win, tearoff=0, bg=K.CARD, fg=K.TEXT, activebackground=K.CARD3, activeforeground=K.TEXT, bd=0, font=font(12))
        m.add_command(label="Lock in place (clicks pass through)", command=lambda: self.S._set_setting("OVERLAY_LOCK", True))
        m.add_command(label="Reset position", command=self.reset_position)
        m.add_command(label="Hide the overlay", command=lambda: self.S._set_setting("OVERLAY", False))
        self.menu = m
        m.tk_popup(e.x_root, e.y_root)

    def reset_position(self):
        self.S._set_setting("OVERLAY_X", -1)
        self.S._set_setting("OVERLAY_Y", -1)
        if self.win:
            self.win.geometry(f"+{(self.win.winfo_screenwidth() - W) // 2}+14")
