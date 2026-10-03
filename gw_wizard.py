"""First-run setup wizard: a 60-second guided start for someone who has just installed Gnomewatch.

  1. welcome  ->  2. pick the browser profile that is signed in to Roblox  ->  3. where to join (the game, or a private server)
  ->  4. test a launch (and, if it fails, say why and offer the checks).

It only ever uses what the Accounts and Settings pages use: it adds one account to accounts.json and sets the launch link.
Opened automatically on a brand-new profile; reopen it any time from the command palette ("Open the setup guide") or Settings.
"""
import json
import os
import threading
import time
import tkinter as tk

import cs_common as C
import ui_kit as U
from ui_kit import K, font, lerp

W = 560
STEPS = ("Welcome", "Account", "Where to join", "Test it", "All set")


class Wizard:
    def __init__(self, ctx, on_finish=None, show_settings=None):
        self.ctx, self.S, self.root, self.theme = ctx, ctx.S, ctx.root, ctx.theme
        self.on_finish, self.show_settings = on_finish, show_settings
        self.step = 0
        kinds = [k for k in ("edge", "chrome") if C.browser_exe(k)]
        self.kinds = kinds
        self.fv = {"browser": kinds[0] if kinds else "edge", "profile": "", "alias": "Main", "where": "game", "link": ""}
        self.profiles = {}
        self.result = None                                     # (ok, message) once the test launch has finished
        self.testing = False
        self.dlg = tk.Toplevel(self.root)
        d = self.dlg
        d.overrideredirect(True)
        d.attributes("-topmost", True)
        d.configure(bg=K.EDGE_HI)
        body = tk.Frame(d, bg=K.CARD)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        self.inner = tk.Frame(body, bg=K.CARD)
        self.inner.pack(fill="both", expand=True, padx=30, pady=(24, 24))
        self.dots = tk.Canvas(self.inner, width=W - 60, height=26, bg=K.CARD, highlightthickness=0, bd=0)
        self.dots.pack(fill="x")
        self.page = tk.Frame(self.inner, bg=K.CARD, height=300, width=W - 60)
        self.page.pack(fill="x", pady=(10, 0))
        self.page.pack_propagate(False)
        self.bar = tk.Frame(self.inner, bg=K.CARD)
        self.bar.pack(fill="x", pady=(14, 0))
        d.bind("<Escape>", lambda e: self.close())
        d.bind("<Return>", lambda e: self.enter())
        self.enter_cmd = None
        self._fill_profiles()
        self.render()
        d.update_idletasks()
        w_, h_ = d.winfo_reqwidth(), d.winfo_reqheight()
        d.geometry(f"{w_}x{h_}+{self.root.winfo_rootx() + (C.W - w_) // 2}+{max(10, self.root.winfo_rooty() + (C.H - h_) // 2 - 20)}")
        d.focus_force()

    # ------------------------------------------------------------------ data
    def _fill_profiles(self):
        self.profiles = dict(C.browser_profiles(self.fv["browser"]))
        if self.fv["profile"] not in self.profiles:
            self.fv["profile"] = "Default" if "Default" in self.profiles else next(iter(self.profiles), "")

    def alias_ok(self):
        a = self.fv["alias"].strip()
        return bool(a) and not any(x.get("label") == a for x in self.ctx.load_accounts())

    def save_account(self):
        """Add the account and set the launch link. Safe to call again (it replaces its own earlier record)."""
        S, f = self.S, self.fv
        lst = [x for x in self.ctx.load_accounts() if x.get("label") != getattr(self, "_saved", None)]
        rec = {"label": f["alias"].strip(), "browser": f["browser"], "profile": f["profile"], "group": "", "notes": ""}
        if f["where"] == "private" and f["link"].strip():
            rec["target"] = "private"
            S._set_setting("LAUNCH_URL_PRIVATE", f["link"].strip())
            S._set_setting("LAUNCH_TARGET", "private")
        else:
            S._set_setting("LAUNCH_TARGET", "game")
            if not S.LAUNCH_URL_GAME:
                try:
                    pid = json.load(open(os.path.join(S.HERE, "assets", "game.json"), encoding="utf-8"))["placeId"]
                    S._set_setting("LAUNCH_URL_GAME", f"https://www.roblox.com/games/{pid}")
                except (OSError, ValueError, KeyError):
                    pass
        lst.append(rec)
        self.ctx.jsave("accounts.json", lst)
        self._saved = rec["label"]
        return rec

    # ------------------------------------------------------------------ drawing
    def render(self):
        for w in self.page.winfo_children():
            w.destroy()
        for w in self.bar.winfo_children():
            w.destroy()
        self.draw_dots()
        self.enter_cmd = None
        [self.p_welcome, self.p_account, self.p_where, self.p_test, self.p_done][self.step]()

    def draw_dots(self):
        c = self.dots
        c.delete("all")
        n = len(STEPS)
        x = 0
        for i, name in enumerate(STEPS):
            on, done = i == self.step, i < self.step
            col = self.theme.cur if (on or done) else K.FAINT
            c.create_image(x + 10, 13, image=U.circle_photo(18, lerp(K.CARD, col, .25 if not done else .9), col, 2))
            if done:
                c.create_image(x + 10, 13, image=U.icon("check", 12, K.CARD))
            else:
                c.create_text(x + 10, 13, text=str(i + 1), fill=col, font=font(10, "bold"))
            c.create_text(x + 26, 13, anchor="w", text=name, fill=K.TEXT if on else K.DIM, font=font(11, "semibold" if on else "medium"))
            x += 26 + U.text_w(name, font(11, "semibold")) + 26

    def head(self, title, note):
        tk.Label(self.page, text=title, bg=K.CARD, fg=K.TEXT, font=font(22, "bold")).pack(anchor="w", pady=(6, 0))
        tk.Label(self.page, text=note, bg=K.CARD, fg=K.DIM, font=font(13), wraplength=W - 70, justify="left").pack(anchor="w", pady=(4, 14))

    def enter(self):
        """Enter = the main button of the page, when it can be pressed."""
        if self.enter_cmd and not self.testing:
            self.enter_cmd()

    def buttons(self, next_text="Next", next_cmd=None, next_disabled=False, back=True, skip=None):
        self.enter_cmd = next_cmd or self.next
        self.next_btn = U.Btn(self.bar, next_text, next_cmd or self.next, self.theme, kind="primary", h=40, size=13, icon="arrow-right" if self.step < 3 else None)
        self.next_btn.pack(side="right")
        self.next_btn.set(disabled=next_disabled)
        if next_disabled:
            self.enter_cmd = None
        if back and self.step > 0:
            U.Btn(self.bar, "Back", self.back, self.theme, h=40, size=13).pack(side="right", padx=(0, 8))
        if skip:
            U.Btn(self.bar, skip[0], skip[1], self.theme, kind="ghost", h=40, size=12).pack(side="left")

    # ------------------------------------------------------------------ the four pages
    def p_welcome(self):
        self.head("Welcome to Gnomewatch", "Let's get your first account launching. It takes about a minute, and you can change everything later.")
        for icn, txt in (("users", "Pick the browser profile that is signed in to Roblox"), ("link", "Choose where to join: the game, or a private server"),
                         ("rocket", "Launch it once to check it all works")):
            r = tk.Frame(self.page, bg=K.CARD)
            r.pack(fill="x", pady=5)
            tk.Label(r, image=U.icon(icn, 20, self.theme.cur), bg=K.CARD, bd=0).pack(side="left", padx=(0, 12))
            tk.Label(r, text=txt, bg=K.CARD, fg=K.TEXT, font=font(14)).pack(side="left")
        tk.Label(self.page, text="Gnomewatch never asks for your Roblox password. It just opens the browser profile you already use.",
                 bg=K.CARD, fg=K.FAINT, font=font(12), wraplength=W - 70, justify="left").pack(anchor="w", pady=(16, 0))
        self.buttons("Let's go", skip=("Skip for now", self.close))

    def p_account(self):
        self.head("Your account", "Choose the browser profile where you're signed in to Roblox. Add more accounts later on the Accounts page.")
        if not self.kinds:
            tk.Label(self.page, text="Neither Microsoft Edge nor Google Chrome was found on this PC. Install one, sign in to Roblox in it, then open this guide again.",
                     bg=K.CARD, fg=K.WARN, font=font(13), wraplength=W - 70, justify="left").pack(anchor="w")
            self.buttons("Next", next_disabled=True)
            return
        row = tk.Frame(self.page, bg=K.CARD)
        row.pack(fill="x")
        c1 = tk.Frame(row, bg=K.CARD)
        c1.pack(side="left")
        tk.Label(c1, text="BROWSER", bg=K.CARD, fg=K.FAINT, font=font(10, "semibold")).pack(anchor="w")

        def pick_browser(v):
            self.fv["browser"] = v
            self._fill_profiles()
            self.prof_dd.refresh(force=True)
            self.kind_dd.refresh(force=True)
            self.check_next()

        self.kind_dd = U.Dropdown(c1, lambda: [({"edge": "Microsoft Edge", "chrome": "Google Chrome"}[k], k) for k in self.kinds], lambda: self.fv["browser"],
                                  pick_browser, self.theme, w=200, h=38, bg=K.CARD, size=13)
        self.kind_dd.pack(pady=(3, 0))
        c2 = tk.Frame(row, bg=K.CARD)
        c2.pack(side="left", padx=(16, 0))
        tk.Label(c2, text="PROFILE", bg=K.CARD, fg=K.FAINT, font=font(10, "semibold")).pack(anchor="w")
        self.prof_dd = U.Dropdown(c2, lambda: [(n, d) for d, n in self.profiles.items()] or [("No profiles found", "")], lambda: self.fv["profile"],
                                  lambda v: (self.fv.update(profile=v), self.check_next()), self.theme, w=250, h=38, bg=K.CARD, size=13)
        self.prof_dd.pack(pady=(3, 0))
        tk.Label(self.page, text="NAME FOR THIS ACCOUNT", bg=K.CARD, fg=K.FAINT, font=font(10, "semibold")).pack(anchor="w", pady=(16, 0))
        self.alias_var = tk.StringVar(value=self.fv["alias"])
        self.alias_var.trace_add("write", lambda *a: (self.fv.update(alias=self.alias_var.get()), self.check_next()))
        U.EntryBox(self.page, self.alias_var, w=300, h=38, theme=self.theme, size=13).pack(anchor="w", pady=(3, 0))
        self.note = tk.Label(self.page, text="", bg=K.CARD, fg=K.WARN, font=font(12), wraplength=W - 70, justify="left")
        self.note.pack(anchor="w", pady=(10, 0))
        self.buttons("Next")
        self.check_next()

    def check_next(self):
        if self.step != 1:
            return
        problem = ("That browser has no profiles yet. Open it once, sign in to Roblox, then come back." if not self.fv["profile"]
                   else "Give the account a name." if not self.fv["alias"].strip()
                   else "You already have an account with that name." if not self.alias_ok() else "")
        try:
            self.note.config(text=problem)
            self.next_btn.set(disabled=bool(problem))
        except tk.TclError:
            pass

    def p_where(self):
        self.head("Where to join", "Gnomewatch opens Roblox straight into this when it launches your account.")
        seg = U.Seg(self.page, [("Roll A Gnome", "game"), ("A private server link", "private")], lambda: self.fv["where"],
                    lambda v: (self.fv.update(where=v), self.render()), self.theme, seg_w=210, h=38, size=13)
        seg.pack(anchor="w")
        if self.fv["where"] == "private":
            tk.Label(self.page, text="PRIVATE SERVER LINK", bg=K.CARD, fg=K.FAINT, font=font(10, "semibold")).pack(anchor="w", pady=(16, 0))
            self.link_var = tk.StringVar(value=self.fv["link"])
            self.link_var.trace_add("write", lambda *a: (self.fv.update(link=self.link_var.get()), self.check_link()))
            U.EntryBox(self.page, self.link_var, w=W - 70, h=38, theme=self.theme, size=12, placeholder="https://www.roblox.com/share?code=…").pack(anchor="w", pady=(3, 0))
            self.note = tk.Label(self.page, text="", bg=K.CARD, fg=K.WARN, font=font(12), wraplength=W - 70, justify="left")
            self.note.pack(anchor="w", pady=(8, 0))
        else:
            tk.Label(self.page, text="Opens the Roll A Gnome page. Change it later under Settings > New instance opens.",
                     bg=K.CARD, fg=K.FAINT, font=font(12), wraplength=W - 70, justify="left").pack(anchor="w", pady=(14, 0))
        self.buttons("Next")
        self.check_link()

    def check_link(self):
        if self.step != 2 or self.fv["where"] != "private":
            return
        link = self.fv["link"].strip()
        bad = bool(link) and not link.lower().startswith("https://")
        empty = not link
        try:
            self.note.config(text="That should start with https://" if bad else "")
            self.next_btn.set(disabled=bad or empty)
        except tk.TclError:
            pass

    def p_test(self):
        self.head("Test it", "Launch your account once to see that everything works. Roblox opens in your browser profile.")
        self.status = tk.Label(self.page, text="", bg=K.CARD, fg=K.DIM, font=font(14, "medium"), wraplength=W - 70, justify="left")
        self.status.pack(anchor="w", pady=(6, 0))
        self.hint = tk.Label(self.page, text="", bg=K.CARD, fg=K.FAINT, font=font(12), wraplength=W - 70, justify="left")
        self.hint.pack(anchor="w", pady=(6, 0))
        if self.result:
            self.show_result()
        self.next_btn = None
        self.launch_btn = U.Btn(self.bar, "Launch it now", self.test_launch, self.theme, kind="primary", icon="rocket", h=40, size=13)
        self.launch_btn.pack(side="right")
        U.Btn(self.bar, "Back", self.back, self.theme, h=40, size=13).pack(side="right", padx=(0, 8))
        U.Btn(self.bar, "Skip the test", self.skip_test, self.theme, kind="ghost", h=40, size=12).pack(side="left")
        self.enter_cmd = self.test_launch
        if self.result:
            self.launch_btn.set("Try again")

    # ------------------------------------------------------------------ testing the launch
    def test_launch(self):
        if self.testing:
            return
        rec = self.save_account()
        self.testing, self.result = True, None
        self.launch_btn.set("Launching…", disabled=True)
        self.status.config(text="Opening Roblox…", fg=K.WARN)
        self.hint.config(text="")

        def work():
            try:
                res = self.S._launch_queue([rec])
                ok, msg = (res[0][1], res[0][2]) if res else (False, "nothing happened")
            except Exception as e:
                ok, msg = False, f"error: {str(e)[:80]}"
            self.result = (ok, msg)
            self.testing = False

        threading.Thread(target=work, daemon=True).start()
        self.poll()

    def poll(self):
        try:
            if self.testing:
                msg = self.S._lq.get("msg") or "Opening Roblox…"
                self.status.config(text=msg, fg=K.WARN)
                self.root.after(500, self.poll)
                return
            self.launch_btn.set("Try again", disabled=False)
            self.show_result()
        except tk.TclError:
            pass

    def show_result(self):
        ok, msg = self.result
        if ok:
            self.status.config(text="It works. Roblox opened and Gnomewatch can see it.", fg=K.OK)
            self.hint.config(text="You're set. Everything else is on the other pages whenever you want it.")
            self.launch_btn.pack_forget()
            U.Btn(self.bar, "Continue", self.next_after_test, self.theme, kind="primary", icon="arrow-right", h=40, size=13).pack(side="right")
            self.enter_cmd = self.next_after_test
        else:
            self.status.config(text=f"That didn't work: {msg}.", fg=K.BAD)
            self.hint.config(text="The checks can usually say why and fix it with one click.")
            U.Btn(self.bar, "Why?", lambda: self.finish(diagnose=True), self.theme, icon="heart-pulse", h=40, size=13).pack(side="right", padx=(0, 8))

    def skip_test(self):
        if not self.testing:
            self.save_account()
            self.step = 4
            self.render()

    def next_after_test(self):
        self.step = 4
        self.render()

    # ------------------------------------------------------------------ the last page
    def p_done(self):
        S = self.S
        self.head("You're all set", f"{self.fv['alias'].strip() or 'Your account'} is ready. A few things worth knowing:")
        for icn, title, txt in (("wand-sparkles", "Auto-place", "Record what you do once for an event, and it repeats it. The eye on each card is a safe dry run."),
                                ("heart-pulse", "If something stops working", "Settings > Health check finds the cause and offers a fix."),
                                ("info", "Help", "The Help page in the sidebar explains everything in short steps.")):
            r = tk.Frame(self.page, bg=K.CARD)
            r.pack(fill="x", pady=4)
            tk.Label(r, image=U.icon(icn, 20, self.theme.cur), bg=K.CARD, bd=0).pack(side="left", anchor="n", padx=(0, 12), pady=2)
            c = tk.Frame(r, bg=K.CARD)
            c.pack(side="left")
            tk.Label(c, text=title, bg=K.CARD, fg=K.TEXT, font=font(14, "semibold"), anchor="w").pack(anchor="w")
            tk.Label(c, text=txt, bg=K.CARD, fg=K.DIM, font=font(12), wraplength=W - 130, justify="left", anchor="w").pack(anchor="w")
        sr = tk.Frame(self.page, bg=K.CARD)
        sr.pack(fill="x", pady=(12, 0))
        sw = U.Switch(sr, lambda v: S._set_setting("OVERLAY", v), self.theme)
        sw.pack(side="left")
        sw.set(bool(S.OVERLAY))
        tk.Label(sr, text="Show the small event countdown over the game (the overlay)", bg=K.CARD, fg=K.TEXT, font=font(13)).pack(side="left", padx=(12, 0))
        self.enter_cmd = self.finish
        U.Btn(self.bar, "Finish", self.finish, self.theme, kind="primary", icon="check", h=40, size=13).pack(side="right")
        U.Btn(self.bar, "Open Help", lambda: self.finish(page="Help"), self.theme, icon="info", h=40, size=13).pack(side="right", padx=(0, 8))
        U.Btn(self.bar, "Add another account", self.add_another, self.theme, kind="ghost", h=40, size=12).pack(side="left")

    def add_another(self):
        names = {x.get("label") for x in self.ctx.load_accounts()}
        n = 2
        while f"Alt {n - 1}" in names:
            n += 1
        self.fv.update(alias=f"Alt {n - 1}", profile="")
        self._saved = None                                       # the next account is a new one, not an edit of the last
        self._fill_profiles()
        self.result = None
        self.step = 1
        self.render()

    # ------------------------------------------------------------------ moving around
    def next(self):
        if self.step == 1 and (not self.alias_ok() or not self.fv["profile"]):
            return
        if self.step == 2 and self.fv["where"] == "private" and not self.fv["link"].strip().lower().startswith("https://"):
            return
        if self.step < len(STEPS) - 1:
            self.step += 1
            self.render()

    def back(self):
        if self.step > 0 and not self.testing:
            self.step -= 1
            self.render()

    def finish(self, diagnose=False, page=None):
        if not getattr(self, "_saved", None) and self.step >= 1 and self.fv["profile"] and self.alias_ok():
            self.save_account()
        self.close(done=True)
        if diagnose and self.show_settings:
            self.show_settings()
        elif page and getattr(self.ctx, "show", None):
            self.ctx.show(page)

    def close(self, done=False):
        try:
            self.dlg.destroy()
        except tk.TclError:
            pass
        if self.on_finish:
            self.on_finish(done)
