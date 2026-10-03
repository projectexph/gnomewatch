"""The Gnomewatch login page and welcome screen (and the same page as a lock screen when a key runs out).

A borderless card with rounded corners, the logo gently floating over a pulsing ring, a key field, "stay signed in",
and a Get-a-key button. A wrong key shakes the card; a right key plays a welcome (avatar, name typing in, time left,
a loading bar) and fades out. Used before the app starts (gw_boot) and inside the console when a key expires.
"""
import math
import os
import queue
import threading
import time
import tkinter as tk
import webbrowser

from PIL import Image, ImageDraw, ImageTk

import gw_license as L
import gw_update as UP
import ui_kit as U
from ui_kit import K, font, lerp

W, H = 540, 700
CARD_W, CARD_H = 520, 680
ACCENT = "#ff7a45"
GOLD = "#ffc83d"


def _ease_out(t):
    return 1 - (1 - t) ** 3


class LoginWindow:
    def __init__(self, app_dir, parent=None, mode="boot", auto_key=None, expected_id=None, message=None, on_success=None, on_close=None, update=None):
        self.app_dir, self.parent, self.mode = app_dir, parent, mode
        self.cfg = L.config(app_dir)
        self.expected_id, self.on_success, self.on_close = expected_id, on_success, on_close
        self.result, self.q, self.busy, self.closed = None, queue.Queue(), False, False
        self.upd = None                                     # set when this copy has to update before it can sign in
        self.t0 = time.time()
        if parent is None:
            U.apply_mode("smooth")
            U.load_fonts()
            U.reset()
        self.root = tk.Tk() if parent is None else tk.Toplevel(parent)
        if parent is None:
            U.check_fonts()
            self.root.withdraw()
        r = self.root
        r.title(self.cfg.get("app_name", "Gnomewatch"))
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.configure(bg=U.KEYCOLOUR)
        try:
            r.attributes("-transparentcolor", U.KEYCOLOUR)
        except tk.TclError:
            pass
        try:
            ico = os.path.join(app_dir, "gnome.ico")
            if os.path.exists(ico) and parent is None:
                r.iconbitmap(ico)
        except Exception:
            pass
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        if parent is not None:
            parent.update_idletasks()
            x, y = parent.winfo_rootx() + (parent.winfo_width() - W) // 2, parent.winfo_rooty() + (parent.winfo_height() - H) // 2
        else:
            x, y = (sw - W) // 2, max(10, (sh - H) // 2 - 20)
        r.geometry(f"{W}x{H}+{x}+{y}")
        self.theme = U.Theme(r)
        self.theme.cur = self.theme.target = ACCENT
        self.cv = tk.Canvas(r, width=W, height=H, bg=U.KEYCOLOUR, highlightthickness=0, bd=0)
        self.cv.pack()
        self._build()
        r.bind("<Escape>", lambda e: self._close())
        r.bind("<Return>", lambda e: self.submit())
        self.cv.bind("<ButtonPress-1>", self._drag_start)
        self.cv.bind("<B1-Motion>", self._drag)
        r.after(60, self._poll)
        r.after(40, self._tick)
        self._appear()
        if auto_key:
            self._busy(True, "Signing you in…")
            self._start_login(auto_key, auto=True)
            self._show("splash")
        else:
            self._show("form")
            if update and UP.usable(update):
                self.root.after(350, lambda: self._update_mode(update))
            elif message:
                self._message(message, bad=True)

    # ------------------------------------------------------------------ building
    def _build(self):
        cv, theme = self.cv, self.theme
        cx = W // 2
        cv.create_image(10, 10, anchor="nw", image=U.rr_photo(CARD_W, CARD_H, 26, K.CARD, K.EDGE_HI))
        # logo + ring
        self.ring = cv.create_image(cx, 150)
        self.logo = cv.create_image(cx, 150)
        self.logo_img = None
        try:
            im = Image.open(os.path.join(self.app_dir, "assets", "brand", "logo_256.png")).convert("RGBA").resize((148, 148), Image.Resampling.LANCZOS)
            self.logo_img = ImageTk.PhotoImage(im)
            cv.itemconfig(self.logo, image=self.logo_img)
        except Exception:
            pass
        self.title = cv.create_text(cx, 262, text=self.cfg.get("app_name", "Gnomewatch"), fill=K.TEXT, font=font(32, "bold"))
        self.sub = cv.create_text(cx, 298, text="Sign in with your key to continue", fill=K.DIM, font=font(14))
        # close button
        self.close_btn = U.IconBtn(self.root, "x", self._close, theme, size=36, kind="ghost", bg=K.CARD, tip="Close", px=17)
        cv.create_window(W - 36, 38, window=self.close_btn)
        # --- form items
        self.var = tk.StringVar()
        self.entry_box = U.EntryBox(self.root, self.var, w=336, h=54, bg=K.CARD, theme=theme, icon="shield-check", size=17,
                                    placeholder="GW-XXXX-XXXX-XXXX")
        self.entry_box.entry.config(font=("Consolas", -19))
        self.entry_box.phl.config(font=("Consolas", -19))
        self.var.trace_add("write", self._upper)
        self.paste_btn = U.IconBtn(self.root, "copy", self._paste, theme, size=54, kind="secondary", bg=K.CARD, tip="Paste from clipboard", px=20)
        self.f_entry = cv.create_window(cx - 32, 352, window=self.entry_box)
        self.f_paste = cv.create_window(cx + 168 - 27 + 2, 352, window=self.paste_btn)
        self.stay = U.Switch(self.root, lambda v: self.stay.set(v), theme)
        self.stay.set(True)
        self.f_switch = cv.create_window(cx - 154, 420, window=self.stay)
        self.f_switch_txt = cv.create_text(cx - 118, 420, anchor="w", text="Stay signed in on this PC", fill=K.DIM, font=font(13, "medium"))
        self.msg_icon = cv.create_image(cx - 200, 468, anchor="w")
        self.msg = cv.create_text(cx - 178, 468, anchor="w", text="", fill=K.BAD, font=font(12, "medium"), width=380)
        self.go = U.Btn(self.root, "Sign in", self.submit, theme, icon="arrow-right", kind="primary", w=400, h=54, bg=K.CARD, size=15, radius=12)
        self.f_go = cv.create_window(cx, 520, window=self.go)
        self.f_div = cv.create_line(cx - 200, 575, cx - 20, 575, fill=K.EDGE)
        self.f_div2 = cv.create_line(cx + 20, 575, cx + 200, 575, fill=K.EDGE)
        self.f_or = cv.create_text(cx, 575, text="or", fill=K.FAINT, font=font(12, "medium"))
        self.getkey = U.Btn(self.root, "Get a key on Discord", self._get_key, theme, icon="external-link", kind="secondary", w=400, h=46, bg=K.CARD,
                            size=13, radius=12)
        self.f_get = cv.create_window(cx, 618, window=self.getkey)
        self.f_steps = cv.create_text(cx, 662, text="Join the server  ·  run /getkey  ·  paste your key here", fill=K.FAINT, font=font(11, "medium"))
        self.form_items = [self.f_entry, self.f_paste, self.f_switch, self.f_switch_txt, self.msg_icon, self.msg, self.f_go, self.f_div, self.f_div2,
                           self.f_or, self.f_get, self.f_steps]
        # --- splash / busy items
        self.spin = cv.create_arc(cx - 16, 452, cx + 16, 484, start=0, extent=100, style="arc", outline=ACCENT, width=4, state="hidden")
        self.busy_txt = cv.create_text(cx, 510, text="", fill=K.DIM, font=font(14, "medium"), state="hidden")
        # --- welcome items
        self.av = cv.create_image(cx, 380, state="hidden")
        self.av_img = None
        self.welcome_small = cv.create_text(cx, 462, text="", fill=K.DIM, font=font(15, "medium"), state="hidden")
        self.welcome_name = cv.create_text(cx, 504, text="", fill=K.TEXT, font=font(30, "bold"), state="hidden")
        self.badge = tk.Frame(self.root, bg=K.CARD)
        self.badge_win = cv.create_window(cx, 556, window=self.badge, state="hidden")
        self.bar = U.Bar(self.root, 300, 8, bg=K.CARD)
        self.bar_win = cv.create_window(cx, 618, window=self.bar, state="hidden")
        self.bar_txt = cv.create_text(cx, 644, text="Loading Gnomewatch…", fill=K.FAINT, font=font(11, "medium"), state="hidden")
        self.welcome_items = [self.av, self.welcome_small, self.welcome_name, self.badge_win, self.bar_win, self.bar_txt]
        self.stage = None

    # ------------------------------------------------------------------ stages
    def _show(self, stage):
        cv = self.cv
        self.stage = stage
        for it in self.form_items:
            cv.itemconfig(it, state="normal" if stage == "form" else "hidden")
        for it in self.welcome_items:
            cv.itemconfig(it, state="normal" if stage == "welcome" else "hidden")
        cv.itemconfig(self.spin, state="normal" if stage in ("splash",) else "hidden")
        cv.itemconfig(self.busy_txt, state="normal" if stage == "splash" else "hidden")
        cv.itemconfig(self.sub, state="normal" if stage in ("form", "splash") else "hidden")
        cv.itemconfig(self.title, state="normal")
        if stage == "form":
            cv.itemconfig(self.sub, text="Sign in with your key to continue")
            self._slide_in(self.form_items)
            self.root.after(250, lambda: self.entry_box.entry.focus_force())
        elif stage == "splash":
            cv.itemconfig(self.sub, text="Welcome back")

    def _slide_in(self, items, dist=26, steps=9):
        cv = self.cv
        start = {it: cv.coords(it) for it in items}
        for it in items:
            cv.move(it, 0, dist)
        done = [0]

        def step(i=1):
            if self.closed:
                return
            k = _ease_out(i / steps)
            for it in items:
                c0 = start[it]
                cur = cv.coords(it)
                cv.move(it, 0, (c0[1] + dist * (1 - k)) - cur[1])
            if i < steps:
                self.root.after(14, step, i + 1)

        step()

    def _appear(self):
        r = self.root
        r.attributes("-alpha", 0.0)
        r.deiconify()
        steps = 10

        def step(i=1):
            if self.closed:
                return
            r.attributes("-alpha", min(1.0, _ease_out(i / steps)))
            if i < steps:
                r.after(16, step, i + 1)

        step()
        r.lift()
        r.focus_force()

    def _fade_out(self, then):
        r = self.root
        steps = 9

        def step(i=1):
            try:
                r.attributes("-alpha", max(0.0, 1 - _ease_out(i / steps)))
            except tk.TclError:
                return
            if i < steps:
                r.after(16, step, i + 1)
            else:
                then()

        step()

    # ------------------------------------------------------------------ small animations (logo float, ring pulse, spinner)
    def _tick(self):
        if self.closed:
            return
        t = time.time() - self.t0
        cx = W // 2
        bob = math.sin(t * 1.7) * 3.5
        self.cv.coords(self.logo, cx, 150 + bob)
        pulse = (math.sin(t * 2.2) + 1) / 2
        lvl = round(pulse * 6) / 6
        col = lerp(K.CARD, ACCENT, .12 + .38 * lvl)
        self.cv.itemconfig(self.ring, image=U.rr_photo(184, 184, 92, None, col, 3))
        self.cv.coords(self.ring, cx, 150 + bob)
        if self.cv.itemcget(self.spin, "state") == "normal":
            self.cv.itemconfig(self.spin, start=(t * 360) % 360)
        self.root.after(45, self._tick)

    # ------------------------------------------------------------------ interaction
    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _upper(self, *_):
        v = self.var.get()
        u = v.upper().replace(" ", "")
        if u != v:
            self.var.set(u)

    def _paste(self):
        try:
            txt = self.root.clipboard_get().strip()
        except tk.TclError:
            txt = ""
        if txt:
            self.var.set(txt.splitlines()[0][:60])
            self.entry_box.entry.focus_set()

    def _get_key(self):
        url = self.cfg.get("invite") or ""
        if url:
            webbrowser.open(url)
        else:
            self._message("The Discord invite link isn't set in gw_config.json yet.", bad=True)

    def _message(self, text, bad=True):
        self.cv.itemconfig(self.msg, text=text, fill=K.BAD if bad else K.DIM)
        self.cv.itemconfig(self.msg_icon, image=U.icon("triangle-alert", 16, K.BAD) if (bad and text) else "")

    def _shake(self):
        offs = [-12, 12, -9, 9, -5, 5, 0]
        base = {it: self.cv.coords(it) for it in self.form_items}

        def step(i=0):
            if self.closed or i >= len(offs):
                return
            for it in self.form_items:
                cur = self.cv.coords(it)
                self.cv.move(it, base[it][0] + offs[i] - cur[0], 0)
            self.root.after(34, step, i + 1)

        step()

    def _busy(self, on, text=""):
        self.busy = on
        self.go.set(disabled=on)
        if on:
            self.cv.itemconfig(self.busy_txt, text=text)

    def submit(self):
        if self.busy or self.stage != "form":
            return
        if self.upd:
            return self._do_update()
        key = self.var.get().strip()
        if not key:
            self._message("Paste your key first.")
            self._shake()
            return
        self._busy(True)
        self.cv.coords(self.spin, W // 2 - 200, 458, W // 2 - 180, 478)
        self.cv.itemconfig(self.spin, state="normal", width=3)
        self.cv.itemconfig(self.msg_icon, image="")
        self.cv.itemconfig(self.msg, text="Checking your key…", fill=K.DIM)
        self._start_login(key)

    def _start_login(self, key, auto=False):
        def work():
            res = L.login(self.app_dir, key)
            sess = L.make_session(self.app_dir, key, res) if res.get("ok") else None
            self.q.put((auto, key, res, sess))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        if self.closed:
            return
        try:
            while True:
                auto, key, res, sess = self.q.get_nowait()
                self._on_result(auto, key, res, sess)
        except queue.Empty:
            pass
        self.root.after(60, self._poll)

    def _on_result(self, auto, key, res, sess):
        self._busy(False)
        if sess and self.expected_id and sess["id"] != self.expected_id and not sess.get("admin"):
            res = {"ok": False, "error": "That key belongs to someone else. Restart Gnomewatch to switch user."}
            sess = None
        if sess:
            if auto or self.stay.on:
                L.save_key(self.app_dir, key)
            else:
                L.forget(self.app_dir)
            self.result = sess
            self._welcome(sess)
            return
        if auto:
            L.forget(self.app_dir)                       # the remembered key no longer works: ask again
        if res.get("update") and UP.usable(res["update"]):
            if self.stage != "form":
                self._show("form")
            self.root.after(120, lambda: self._update_mode(res["update"]))
            return
        self._show_form_after_fail(res.get("error") or "That key didn't work.", network=res.get("network"))

    # ------------------------------------------------------------------ updating
    def _update_mode(self, upd):
        """This copy is too old to sign in: explain, and turn the button into 'Update now'."""
        self.upd = upd
        for it in (self.msg, self.msg_icon):
            self.cv.itemconfig(it, state="normal")
        self.cv.itemconfig(self.sub, text="A new version is ready")
        self._message(f"Gnomewatch {upd['latest']} is out, and this version can't sign in any more. Update to carry on."
                      if upd.get("forced") else f"Gnomewatch {upd['latest']} is available.", bad=bool(upd.get("forced")))
        self.go.set("Update now", icon="download", disabled=False)
        self._shake()

    def _do_update(self):
        if self.busy:
            return
        self._busy(True)
        self.go.set("Updating…")
        self._message("Starting…", bad=False)
        UP.STATE.update(phase="idle", pct=0, msg="")

        def ready():                                       # the installer is running: close this window (and Gnomewatch with it)
            self.root.after(300, self._close)

        UP.start(self.upd, ready)
        self._watch_update()

    def _watch_update(self):
        if self.closed:
            return
        st = UP.STATE
        if st["phase"] == "error":
            self._busy(False)
            self.go.set("Try again", disabled=False)
            self._message(st["msg"], bad=True)
            return
        if st["phase"] == "done" and not UP.packaged():
            self._busy(False)
            self.go.set("Update now", disabled=False)
            self._message(st["msg"], bad=False)
            return
        txt = st["msg"] + (f" {st['pct']}%" if st["phase"] == "download" and st["pct"] else "")
        self._message(txt, bad=False)
        self.root.after(200, self._watch_update)

    def _show_form_after_fail(self, text, network=False):
        self.cv.itemconfig(self.spin, state="hidden", width=4)
        if self.stage != "form":
            self._show("form")
        for it in (self.msg, self.msg_icon):
            self.cv.itemconfig(it, state="normal")
        self._message(text, bad=True)
        self._shake()
        self.entry_box.entry.focus_force()

    # ------------------------------------------------------------------ welcome
    def _welcome(self, sess):
        cv, cx = self.cv, W // 2
        cv.itemconfig(self.spin, state="hidden")
        cv.itemconfig(self.busy_txt, state="hidden")
        self._show("welcome")
        cv.itemconfig(self.sub, state="hidden")
        cv.itemconfig(self.title, state="hidden")
        # avatar: their Discord picture, or a coloured circle with their initial
        size = 104
        av = Image.new("RGBA", (size + 8, size + 8), (0, 0, 0, 0))
        base = None
        if sess.get("avatar_path") and os.path.exists(sess["avatar_path"]):
            try:
                base = Image.open(sess["avatar_path"]).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
            except Exception:
                base = None
        if base is None:
            base = Image.new("RGBA", (size, size), (255, 255, 255, 0))
            d = ImageDraw.Draw(base)
            d.ellipse([0, 0, size - 1, size - 1], fill=lerp(K.CARD, ACCENT, .25))
        mask = Image.new("L", (size * 4, size * 4), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, size * 4 - 1, size * 4 - 1], fill=255)
        base.putalpha(mask.resize((size, size), Image.Resampling.LANCZOS))
        av.alpha_composite(base, (4, 4))
        ring = U.rr_pil(size + 8, size + 8, (size + 8) // 2, None, GOLD if sess.get("admin") else ACCENT, 3)
        av.alpha_composite(ring)
        self.av_img = ImageTk.PhotoImage(av)
        cv.itemconfig(self.av, image=self.av_img)
        if not sess.get("avatar_path"):
            self._initial = cv.create_text(cx, 380, text=(sess["name"][:1] or "?").upper(), fill=ACCENT, font=font(40, "bold"))
        cv.itemconfig(self.welcome_small, text="Welcome back," if time.time() - self.t0 > 0 and self.mode == "boot" else "Welcome,")
        cv.itemconfig(self.welcome_name, text="")
        # badges
        for ch in self.badge.winfo_children():
            ch.destroy()
        if sess.get("admin"):
            U.Pill(self.badge, "ADMIN", GOLD, bg=K.CARD, icon="shield-check").pack(side="left", padx=4)
            U.Pill(self.badge, "NEVER EXPIRES", K.OK, bg=K.CARD, icon="infinity" if os.path.exists(os.path.join(U.ASSETS, "icons", "infinity.png")) else "check").pack(side="left", padx=4)
        else:
            U.Pill(self.badge, f"{L.fmt_left(L.seconds_left(sess))} LEFT", K.OK, bg=K.CARD, icon="timer").pack(side="left", padx=4)
        self._slide_in([self.av, self.welcome_small, self.welcome_name, self.badge_win] + ([self._initial] if not sess.get("avatar_path") else []), 22, 10)
        name = sess["name"]
        t_start = time.time()

        def type_in(i=0):
            if self.closed:
                return
            cv.itemconfig(self.welcome_name, text=name[:i])
            if i <= len(name):
                self.root.after(max(22, min(60, 700 // max(1, len(name)))), type_in, i + 1)

        self.root.after(260, type_in)
        total = 1.9

        def load(_=None):
            if self.closed:
                return
            frac = min(1.0, (time.time() - t_start) / total)
            self.bar.set(_ease_out(frac), ACCENT)
            if frac < 1.0:
                self.root.after(30, load)
            else:
                self.root.after(240, lambda: self._fade_out(lambda: self._finish(sess)))

        self.root.after(200, load)

    def _finish(self, sess):
        self.closed = True
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        if self.on_success:
            self.on_success(sess)

    def _close(self):
        if self.busy and self.stage == "welcome":
            return
        self.closed = True
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        if self.on_close:
            self.on_close()

    def run(self):
        """Boot mode: show the page and wait. Returns the session, or None if the window was closed."""
        self.root.mainloop()
        return self.result
