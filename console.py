"""Sentinel console (v2). Runs in its own thread; `S` is the sentinel module itself.

Sidebar pages: Overview, Instances, Accounts, Activity, Settings.  Ctrl+K opens the command palette.
The accent colour follows the event that is on.  Look: "gamer dashboard" - see ui_kit.py.

Accounts are browser profiles: only a label + profile folder name is stored (accounts.json) - never a password
or cookie.  Which window belongs to which account is assigned by hand (assign.json).

The previous console is kept as console_v1.py (PANEL_STYLE=v1 runs it).
"""
import ctypes
import gc
import json
import math
import os
import sys
import queue
import random
import threading
import time
import tkinter as tk

from PIL import Image, ImageTk

import ui_kit as U
from ui_kit import K, font, lerp
import cs_common as C
from cs_common import W, H, SIDE, PADX, CW, NAV, SUBS, MINI_W, MINI_H

FORCE_FRONT = None          # tests only: True / False pretends the console is / isn't the front window


def run(S):
    """Run the console; a mode change rebuilds it (new window, new look) in the same thread."""
    while True:
        res = _run_once(S)
        gc.collect()                              # free the old window's Tk objects here, in the thread that owns them
        if res != "restyle" or S.QUIT.is_set():
            return


def _run_once(S):
    import psutil
    import win32process
    from tkinter import ttk

    U.apply_mode(getattr(S, "UI_MODE", "smooth"))
    U.load_fonts()
    U.reset()
    U.Card.all.clear()
    M = K.M
    sess = getattr(S, "GW_SESSION", None) or {}
    nav_items = list(NAV) + ([("Admin", "shield-check")] if sess.get("admin") else [])
    root = tk.Tk()
    U.check_fonts()
    root.title(C.APP_NAME)
    root.configure(bg=K.BG)
    root.attributes("-topmost", True)
    root.resizable(False, False)
    px_, py_ = getattr(S, "_console_pos", (60, 40))
    root.geometry(f"{W}x{H}+{px_}+{py_}")
    try:
        root.iconbitmap(S._gnome_ico())
    except Exception:
        pass
    theme = U.Theme(root)
    keep = []                                            # keep image references alive
    errors = set()
    restart = {"on": False}
    cache = {}

    ctx = C.Ctx()
    ctx.S, ctx.root, ctx.theme, ctx.keep, ctx.cache, ctx.M = S, root, theme, keep, cache, M

    # ------------------------------------------------------------------ small helpers
    def guarded(fn):
        """Run a UI update; report a problem once instead of killing the loop."""
        try:
            fn()
        except tk.TclError:
            pass
        except Exception as e:
            key = f"{getattr(fn, '__name__', 'fn')}:{type(e).__name__}"
            if key not in errors:
                errors.add(key)
                import traceback
                print(f"console: {getattr(fn, '__name__', 'fn')} failed: {e!r}")
                print(traceback.format_exc())

    def get(name):
        return getattr(S, S._GLOBAL_OF.get(name, name))

    def jload(name, default):
        try:
            return json.load(open(os.path.join(S.DATA, name), encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def jsave(name, data):
        try:
            json.dump(data, open(os.path.join(S.DATA, name), "w", encoding="utf-8"), indent=1)
        except OSError as e:
            print(f"couldn't save {name}: {e}")

    def load_scenes():
        sc = dict(C.DEFAULT_SCENES)
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

    def hwnd_of():
        return ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()

    ctx.guarded, ctx.get, ctx.jload, ctx.jsave = guarded, get, jload, jsave
    ctx.load_scenes, ctx.load_accounts, ctx.assign, ctx.set_assign, ctx.hwnd_of = load_scenes, load_accounts, assign, set_assign, hwnd_of
    ctx.alive = lambda: not restart["on"]

    # ------------------------------------------------------------------ window chrome (dark frame, rounded corners)
    def chrome(win):
        try:
            win.update_idletasks()
            hw = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hw, 20, ctypes.byref(ctypes.c_int(1)), 4)          # dark title bar
            c = K.EDGE.lstrip("#")
            colorref = int(c[4:6] + c[2:4] + c[0:2], 16)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hw, 34, ctypes.byref(ctypes.c_int(colorref)), 4)   # 1px border colour
        except Exception:
            pass

    ctx.chrome = chrome

    # ------------------------------------------------------------------ modal dialog (replaces the grey Windows boxes)
    def ask(title, message, yes="OK", no="Cancel", danger=False, info=False):
        """Styled modal dialog. Returns True if the main button was pressed."""
        res = {"v": False}
        dlg = tk.Toplevel(root)
        ctx.dialog = dlg
        dlg.overrideredirect(True)
        dlg.attributes("-topmost", True)
        dlg.configure(bg=K.EDGE_HI)
        body = tk.Frame(dlg, bg=K.CARD)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        inner = tk.Frame(body, bg=K.CARD)
        inner.pack(fill="both", expand=True, padx=26, pady=(24, 22))
        top = tk.Frame(inner, bg=K.CARD)
        top.pack(fill="x")
        ic = "triangle-alert" if danger else "info"
        tk.Label(top, image=U.icon(ic, 22, K.BAD if danger else theme.cur), bg=K.CARD, bd=0).pack(side="left", padx=(0, 12))
        tk.Label(top, text=title, bg=K.CARD, fg=K.TEXT, font=font(17, "bold")).pack(side="left")
        tk.Label(inner, text=message, bg=K.CARD, fg=K.DIM, font=font(13), justify="left", wraplength=400, anchor="w").pack(
            fill="x", pady=(14, 22))
        row = tk.Frame(inner, bg=K.CARD)
        row.pack(fill="x")

        def done(v):
            res["v"] = v
            try:
                dlg.grab_release()
            except tk.TclError:
                pass
            dlg.destroy()

        U.Btn(row, yes, lambda: done(True), theme, kind="danger" if danger else "primary", h=38).pack(side="right")
        if not info:
            U.Btn(row, no, lambda: done(False), theme, kind="secondary", h=38).pack(side="right", padx=(0, 10))
        dlg.update_idletasks()
        w_, h_ = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        rx, ry = root.winfo_rootx(), root.winfo_rooty()
        dlg.geometry(f"{max(w_, 480)}x{h_}+{rx + (W - max(w_, 480)) // 2}+{ry + (H - h_) // 2 - 30}")
        dlg.bind("<Escape>", lambda e: done(False))
        dlg.bind("<Return>", lambda e: done(True))
        dlg.focus_force()
        try:
            dlg.grab_set()
        except tk.TclError:
            pass
        root.wait_window(dlg)
        return res["v"]

    ctx.ask = ask

    # ------------------------------------------------------------------ scenes
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
        return bool(vals) and all(C.same(get(k), v) for k, v in vals)

    ctx.apply_scene, ctx.scene_matches = apply_scene, scene_matches

    # ================================================================== shell: sidebar + content
    shell = tk.Frame(root, bg=K.BG)
    shell.pack(fill="both", expand=True)
    side = tk.Canvas(shell, width=SIDE, height=H, bg=K.SIDE, highlightthickness=0, bd=0)
    side.pack(side="left")
    tk.Frame(shell, bg=K.EDGE, width=1).pack(side="left", fill="y")
    main = tk.Frame(shell, bg=K.BG)
    main.pack(side="left", fill="both", expand=True)

    nav = {"cur": "Overview", "hover": None, "mode_hover": False}
    badges = {}
    try:
        logo = ImageTk.PhotoImage(Image.open(S._gnome_ico()).convert("RGBA").resize((40, 40), Image.Resampling.LANCZOS))
        keep.append(logo)
    except Exception:
        logo = None
    status_pill = U.Pill(side, "STARTING", K.DIM, bg=K.SIDE)
    side.create_window(24, 88, anchor="nw", window=status_pill)

    ITEM_Y0, ITEM_STEP, ITEM_H = (140, 54, 46) if len(nav_items) <= 8 else (140, 47, 41)        # nine entries (with Admin) get a little tighter
    MODE_Y = H - 94
    USER_Y = MODE_Y - 74
    user_av = [None]
    if sess:
        try:
            from PIL import ImageDraw
            size = 40
            if sess.get("avatar_path") and os.path.exists(sess["avatar_path"]):
                base = Image.open(sess["avatar_path"]).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
            else:
                base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
                ImageDraw.Draw(base).ellipse([0, 0, size - 1, size - 1], fill=lerp(K.CARD, K.IDLE, .3))
            mask = Image.new("L", (size * 4, size * 4), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, size * 4 - 1, size * 4 - 1], fill=255)
            base.putalpha(mask.resize((size, size), Image.Resampling.LANCZOS))
            user_av[0] = ImageTk.PhotoImage(base)
            keep.append(user_av[0])
        except Exception:
            user_av[0] = None

    def lic_text():
        if not sess:
            return "", K.FAINT
        if sess.get("admin"):
            return "Admin · no expiry", K.WARN
        left = S._license_left()
        if S.LOCKED.is_set():
            return "key ended", K.BAD
        return (f"{__import__('gw_license').fmt_left(left)} left", K.WARN if left is not None and left <= 1800 else K.DIM)

    def draw_side(accent=None):
        accent = accent or theme.cur
        side.delete("nav")
        if logo:
            side.create_image(22, 24, anchor="nw", image=logo, tags="nav")
        side.create_text(70, 25, anchor="nw", text=C.APP_NAME, fill=K.TEXT, font=font(18, "bold"), tags="nav")
        side.create_text(71, 49, anchor="nw", text=C.TAGLINE, fill=K.FAINT, font=font(11, "medium"), tags="nav")
        side.create_line(22, 126, SIDE - 22, 126, fill=K.EDGE, tags="nav")
        for i, (name, ic) in enumerate(nav_items):
            y = ITEM_Y0 + i * ITEM_STEP
            on, hov = name == nav["cur"], name == nav["hover"]
            if on:
                side.create_image(12, y, anchor="nw", image=U.rr_photo(SIDE - 24, ITEM_H, 11, lerp(K.SIDE, accent, .15),
                                                                     lerp(K.SIDE, accent, .32)), tags="nav")
                side.create_image(0, y + 11, anchor="nw", image=U.rr_photo(4, 24, 2, accent, None), tags="nav")
            elif hov:
                side.create_image(12, y, anchor="nw", image=U.rr_photo(SIDE - 24, ITEM_H, 11, K.CARD2, None), tags="nav")
            col = accent if on else (K.TEXT if hov else K.DIM)
            side.create_image(32, y + ITEM_H / 2, anchor="w", image=U.icon(ic, 20, col), tags="nav")
            side.create_text(66, y + ITEM_H / 2 - 1, anchor="w", text=name, fill=K.TEXT if (on or hov) else K.DIM,
                             font=font(14, "semibold" if on else "medium"), tags="nav")
            b = badges.get(name)
            if b:
                txt, colr = b
                bw = max(22, U.text_w(txt, font(11, "semibold")) + 14)
                bx = SIDE - 24 - bw
                side.create_image(bx, y + ITEM_H / 2 - 10, anchor="nw",
                                  image=U.rr_photo(bw, 20, 10, lerp(K.SIDE, colr, .20), lerp(K.SIDE, colr, .40)), tags="nav")
                side.create_text(bx + bw / 2, y + ITEM_H / 2 - 1, text=txt, fill=colr, font=font(11, "semibold"), tags="nav")
        if sess:                                              # who is signed in, how long the key lasts, sign out
            side.create_image(12, USER_Y, anchor="nw", image=U.rr_photo(SIDE - 24, 64, 12, K.CARD, K.EDGE), tags="nav")
            ring = lerp(K.CARD, K.WARN if sess.get("admin") else accent, .8)
            if user_av[0]:
                side.create_image(26, USER_Y + 12, anchor="nw", image=user_av[0], tags="nav")
            else:
                side.create_text(46, USER_Y + 32, text=(sess.get("name", "?")[:1] or "?").upper(), fill=accent, font=font(16, "bold"), tags="nav")
            side.create_image(24, USER_Y + 10, anchor="nw", image=U.circle_photo(44, None, ring, 2), tags="nav")
            side.create_text(78, USER_Y + 22, anchor="w", text=U.ellipsize(sess.get("name", ""), font(13, "semibold"), 84), fill=K.TEXT,
                             font=font(13, "semibold"), tags="nav")
            lt, lc_ = lic_text()
            side.create_text(78, USER_Y + 42, anchor="w", text=U.ellipsize(lt, font(11, "medium"), 84), fill=lc_, font=font(11, "medium"), tags="nav")
            side.create_image(SIDE - 32, USER_Y + 32, image=U.icon("log-out", 16, K.FAINT), tags="nav")
        # mode card
        mh = nav["mode_hover"]
        side.create_image(12, MODE_Y, anchor="nw", image=U.rr_photo(SIDE - 24, 70, 12, K.CARD2 if mh else K.CARD,
                                                                   K.EDGE_HI if mh else K.EDGE), tags="nav")
        side.create_image(26, MODE_Y + 17, anchor="nw", image=U.rr_photo(36, 36, 9, lerp(K.CARD, accent, .18),
                                                                        lerp(K.CARD, accent, .4)), tags="nav")
        side.create_image(44, MODE_Y + 35, image=U.icon(M["icon"], 20, accent), tags="nav")
        side.create_text(74, MODE_Y + 20, anchor="nw", text="MODE", fill=K.FAINT, font=font(10, "semibold"), tags="nav")
        side.create_text(74, MODE_Y + 36, anchor="nw", text=M["label"], fill=K.TEXT, font=font(14, "semibold"), tags="nav")
        side.create_image(SIDE - 30, MODE_Y + 35, image=U.icon("chevrons-up-down", 16, K.DIM), tags="nav")

    def side_at(y):
        i = (y - ITEM_Y0) // ITEM_STEP
        return nav_items[i][0] if 0 <= i < len(nav_items) and (y - ITEM_Y0) % ITEM_STEP < ITEM_H else None

    def side_motion(e):
        n = side_at(e.y)
        m = MODE_Y <= e.y <= MODE_Y + 70
        if n != nav["hover"] or m != nav["mode_hover"]:
            nav.update(hover=n, mode_hover=m)
            draw_side()

    def side_leave(_e):
        nav.update(hover=None, mode_hover=False)
        draw_side()

    def sign_out():
        if ask("Sign out?", "Gnomewatch closes and shows the login page next time. Your settings are kept.", yes="Sign out", danger=True):
            S._sign_out()

    def side_click(e):
        if sess and USER_Y <= e.y <= USER_Y + 64 and e.x >= SIDE - 56:
            sign_out()
        elif MODE_Y <= e.y <= MODE_Y + 70:
            open_mode_menu()
        elif side_at(e.y):
            show(side_at(e.y))

    side.bind("<Motion>", side_motion)
    side.bind("<Leave>", side_leave)
    side.bind("<Button-1>", side_click)
    theme.sub(draw_side)

    # ---- mode menu (pops up above the mode card)
    mode_menu = {"win": None}

    def close_mode_menu(_e=None):
        w = mode_menu["win"]
        mode_menu["win"] = None
        if w is not None:
            try:
                w.destroy()
            except tk.TclError:
                pass

    def choose_mode(name):
        if name != S.UI_MODE:
            threading.Thread(target=S._set_mode, args=(name,), daemon=True).start()

    ctx.choose_mode = choose_mode

    def open_mode_menu():
        if mode_menu["win"] is not None:
            close_mode_menu()
            return
        win = mode_menu["win"] = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=U.KEYCOLOUR)
        try:
            win.attributes("-transparentcolor", U.KEYCOLOUR)
        except tk.TclError:
            pass
        rows = list(U.MODES.items())
        rh, pw = 62, 340
        ph = rh * len(rows) + 20
        x, y = root.winfo_rootx() + 12, root.winfo_rooty() + MODE_Y - ph - 8
        win.geometry(f"{pw}x{ph}+{x}+{y}")
        cv = tk.Canvas(win, width=pw, height=ph, bg=U.KEYCOLOUR, highlightthickness=0, bd=0)
        cv.pack()
        hov = {"i": -1}

        def draw():
            cv.delete("all")
            cv.create_image(0, 0, anchor="nw", image=U.rr_photo(pw, ph, 14, K.CARD, K.EDGE_HI))
            for i, (key, m) in enumerate(rows):
                y0 = 10 + i * rh
                cur = key == S.UI_MODE
                if i == hov["i"] or cur:
                    cv.create_image(8, y0, anchor="nw", image=U.rr_photo(pw - 16, rh - 4, 10,
                                    lerp(K.CARD, theme.cur, .13) if cur else K.CARD2, lerp(K.CARD, theme.cur, .3) if cur else None))
                cv.create_image(24, y0 + 9, anchor="nw", image=U.rr_photo(36, 36, 9, lerp(K.CARD, theme.cur, .16),
                                                                          lerp(K.CARD, theme.cur, .35)))
                cv.create_image(42, y0 + 27, image=U.icon(m["icon"], 20, theme.cur if cur else K.DIM))
                cv.create_text(74, y0 + 17, anchor="w", text=m["label"], fill=K.TEXT, font=font(14, "semibold"))
                cv.create_text(74, y0 + 38, anchor="w", text=m["blurb"], fill=K.FAINT, font=font(11))
                if cur:
                    cv.create_image(pw - 26, y0 + 27, image=U.icon("check", 18, theme.cur))

        def at(e):
            i = (e.y - 10) // rh
            return int(i) if 0 <= i < len(rows) else -1

        cv.bind("<Motion>", lambda e: (hov.update(i=at(e)), draw()))
        cv.bind("<Leave>", lambda e: (hov.update(i=-1), draw()))

        def click(e):
            i = at(e)
            close_mode_menu()
            if i >= 0:
                choose_mode(rows[i][0])

        cv.bind("<ButtonRelease-1>", click)
        win.bind("<Escape>", close_mode_menu)
        root.bind("<ButtonPress-1>", lambda e: close_mode_menu() if mode_menu["win"] is not None and
                  not str(e.widget).startswith(str(mode_menu["win"])) and e.widget is not side else None, add="+")
        draw()

    ctx.open_mode_menu = open_mode_menu

    # ---- header
    hdr = tk.Frame(main, bg=K.BG, height=72)
    hdr.pack(fill="x", padx=PADX, pady=(22, 0))
    hdr.pack_propagate(False)
    title_lbl = tk.Label(hdr, text="Overview", bg=K.BG, fg=K.TEXT, font=font(26, "bold"))
    title_lbl.place(x=-2, y=-4)
    sub_lbl = tk.Label(hdr, text="", bg=K.BG, fg=K.FAINT, font=font(13))
    sub_lbl.place(x=0, y=40)
    hb = tk.Frame(hdr, bg=K.BG)
    hb.place(relx=1.0, x=0, y=0, anchor="ne")
    chip = tk.Canvas(hb, width=232, height=42, bg=K.BG, highlightthickness=0, bd=0)
    chip.pack(side="left", padx=(0, 12))
    cmd_box = U.EntryBox(hb, None, w=224, h=42, bg=K.BG, theme=theme, icon="search", size=13)
    cmd_box.pack(side="left", padx=(0, 10))
    cmd_var, cmd_entry = cmd_box.var, cmd_box.entry
    U.IconBtn(hb, "minimize-2", lambda: set_mini(True), theme, size=42, kind="secondary", bg=K.BG, tip="Mini mode").pack(side="left")
    U.IconBtn(hb, "x", lambda: root.withdraw(), theme, size=42, kind="secondary", bg=K.BG,
              tip="Hide (it keeps running)").pack(side="left", padx=(8, 0))

    # inline command bar: matches drop down right under it
    PH = "Type a command…"
    cmd_state = {"ph": True}
    cmd_shown = []
    cmd_list = tk.Listbox(root, bg=K.CARD, fg=K.TEXT, selectbackground=lerp(K.CARD, K.IDLE, .45), selectforeground=K.TEXT,
                          highlightthickness=1, highlightbackground=K.EDGE_HI, highlightcolor=K.EDGE_HI, bd=0, relief="flat",
                          font=font(13), activestyle="none", takefocus=0)

    def cmd_hide():
        cmd_list.place_forget()

    def cmd_reset():
        cmd_state["ph"] = True
        cmd_var.set(PH)
        cmd_entry.config(fg=K.FAINT)
        cmd_hide()

    def cmd_focus_in(_=None):
        if cmd_state["ph"]:
            cmd_state["ph"] = False
            cmd_var.set("")
            cmd_entry.config(fg=K.TEXT)

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
        for it in C.filter_actions(ctx.actions(), text)[:8]:
            cmd_shown.append(it[2])
            cmd_list.insert("end", "   " + it[0])
        if not cmd_shown:
            cmd_hide()
            return
        cmd_list.selection_set(0)
        root.update_idletasks()
        x = cmd_box.winfo_rootx() - root.winfo_rootx()
        y = cmd_box.winfo_rooty() - root.winfo_rooty() + 48
        cmd_list.place(x=x, y=y, width=330, height=min(8, len(cmd_shown)) * 28 + 8)
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

    # accent rule under the header: a short bright segment fading into a hairline
    rule = tk.Canvas(main, height=3, bg=K.BG, highlightthickness=0, bd=0)
    rule.pack(fill="x", padx=PADX, pady=(6, 14))
    rule_items = []

    def draw_rule(accent=None):
        accent = accent or theme.cur
        if not rule_items:
            rule_items.append(rule.create_rectangle(0, 1, CW, 2, outline="", fill=K.EDGE))
            for i in range(18):
                rule_items.append(rule.create_rectangle(i * 7, 0, i * 7 + 7, 3, outline="", fill=accent))
        for i, it in enumerate(rule_items[1:]):
            rule.itemconfig(it, fill=lerp(accent, K.EDGE, (i / 18) ** 1.2))

    theme.sub(draw_rule)

    content = tk.Frame(main, bg=K.BG)
    content.pack(fill="both", expand=True, padx=PADX)
    pages = {n: tk.Frame(content, bg=K.BG) for n, _ in nav_items}
    ctx.pages, ctx.nav = pages, nav

    def slide_in(f, dist=40):
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

    page_objs = {}

    def show(name):
        nav["cur"] = name
        for n, f in pages.items():
            f.place_forget()
        slide_in(pages[name])
        title_lbl.config(text=name)
        sub_lbl.config(text=SUBS[name])
        draw_side()
        po = page_objs.get(name)
        if po is not None and hasattr(po, "on_show"):
            guarded(po.on_show)

    ctx.show = show

    # ---- little helpers shared by the ticks
    def lc(w, **kw):
        key = (str(w), "w")
        if cache.get(key) != kw:
            cache[key] = dict(kw)
            w.config(**kw)

    def ic(cv, item, **kw):
        key = (str(cv), item)
        if cache.get(key) != kw:
            cache[key] = dict(kw)
            cv.itemconfig(item, **kw)

    ctx.lc, ctx.ic = lc, ic

    # ---- windows opened from the Accounts page assign themselves to that account (handles change whenever Roblox restarts)
    seen_windows = set()
    ctx.win_since = {}
    first_scan = [True]

    def auto_assign():
        while S._assign_now:                                      # windows the launch queue just opened: assign at once
            h_, l_ = S._assign_now.pop(0)
            set_assign(h_, l_)
        snap = list(S._snapshot)
        alive = {e[0] for e in snap}
        u = ctypes.windll.user32
        for h in [h for h in assign if not u.IsWindow(h)]:        # forget windows that are gone
            set_assign(h, None)
        new = sorted(h for h in alive if h not in seen_windows and h not in assign)
        seen_windows.update(alive)
        for h_ in alive:
            ctx.win_since.setdefault(h_, time.time())
        for h_ in [h_ for h_ in ctx.win_since if h_ not in alive]:
            ctx.win_since.pop(h_, None)
        if first_scan[0]:                                         # windows that were already open at start stay as they are
            first_scan[0] = False
            return
        now = time.time()
        S._pending_launch[:] = [p for p in S._pending_launch if now - p[1] < 200]     # launches by the Accounts page or by auto-rejoin
        for h in new:
            if S._pending_launch:
                label, _t = S._pending_launch.pop(0)
                set_assign(h, label)
                print(f"account: new Roblox window assigned to '{label}'")

    # ---- scroll areas (Instances, Settings) share one wheel handler
    ctx.scrollers = []

    def on_wheel(e):
        try:
            w = root.winfo_containing(e.x_root, e.y_root)
        except (tk.TclError, KeyError):
            return
        for sf in ctx.scrollers:
            if sf.winfo_ismapped() and sf.contains(w):
                sf.scroll(e.delta)
                return

    ctx.on_wheel = on_wheel

    # ---- a button that asks "sure?" on the first click and acts on the second
    def confirm_btn(parent, text, sure, action, icon=None, **kw):
        size, weight, pad = kw.get("size", 13), kw.get("weight", "semibold"), kw.get("pad", 16)
        if "w" not in kw:
            spec = font(size, weight)
            kw["w"] = max(U.text_w(text, spec), U.text_w(sure, spec)) + ((int(size * 1.25) + 8) if icon else 0) + pad * 2
        b = U.Btn(parent, text, None, theme, icon=icon, kind="danger", **kw)
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

    ctx.confirm_btn = confirm_btn

    # ================================================================== pages
    import cs_overview, cs_instances, cs_accounts, cs_autoplace, cs_performance, cs_activity, cs_settings, cs_palette, cs_admin, cs_help

    # shared "activity" helpers (the log feed is used by Overview and Activity)
    def feed_lines(n):
        txt = S._tail(S.LOG_PATH, 60000)
        lines = [l.rstrip() for l in txt.splitlines() if l.strip() and not l.startswith(("  File", "Traceback"))]
        return lines[-n:]

    def fill_feed(widget, lines):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        for tag, col in C.LOG_TAGS:
            widget.tag_configure(tag, foreground=getattr(K, col, col))
        for ln in lines:
            widget.insert("end", ln + "\n", C.classify(ln))
        widget.see("end")
        widget.configure(state="disabled")

    ctx.feed_lines, ctx.fill_feed = feed_lines, fill_feed
    ctx.feed_sig = {"small": None, "big": None}

    mini_on = [False]
    ctx.mini_on = mini_on
    snooze = {"until": 0.0}
    ctx.snooze = snooze

    def do_snooze(minutes):
        snooze["until"] = time.time() + minutes * 60
        S.MUTED.set()
        print(f"alerts snoozed for {minutes} min")

    def cancel_snooze():
        snooze["until"] = 0.0
        S.MUTED.clear()

    ctx.do_snooze, ctx.cancel_snooze = do_snooze, cancel_snooze

    def set_mini(on, save=True):
        mini_on[0] = on
        if on:
            shell.pack_forget()
            mini.pack(fill="both", expand=True)
            root.geometry(f"{MINI_W}x{MINI_H}")
        else:
            mini.pack_forget()
            shell.pack(fill="both", expand=True)
            root.geometry(f"{W}x{H}")
        if save:
            S._set_setting("PANEL_MINI", on)

    ctx.set_mini = set_mini

    # ---- the lock screen: when the key runs out (or is revoked) the same login page comes back over the console
    lock_win = {"w": None}

    launch_seen = {"t": time.time(), "asked": 0.0}

    def launch_problem(msg):
        if ask("A launch didn't work", f"{msg[:1].upper() + msg[1:]}.\n\nRun the checks to find out why and fix it?", yes="Fix it", no="Not now"):
            show("Settings")
            root.after(250, lambda: ctx.run_health())

    ctx.launch_problem = launch_problem

    def whats_new():
        """Once after an update: a short card with what changed. A brand-new profile gets the setup wizard instead."""
        import gw_version
        stt = jload("state.json", {})
        last = stt.get("last_version")
        if last is None and not jload("accounts.json", []):
            stt["last_version"] = gw_version.VERSION                      # nothing set up yet: nothing to be "new" to
            jsave("state.json", stt)
            return
        if last == gw_version.VERSION:
            return
        stt["last_version"] = gw_version.VERSION
        jsave("state.json", stt)
        if gw_version.NOTES.strip():
            ask(f"What's new in {gw_version.VERSION}", gw_version.NOTES.strip(), yes="Got it", info=True)

    ctx.whats_new = whats_new
    root.after(4000, lambda: guarded(whats_new))

    wizard_win = {"w": None}

    def open_wizard():
        """The setup guide (first start on a new profile; any time from the palette or Settings)."""
        import gw_wizard
        if wizard_win["w"] is not None:
            return

        def done(_finished):
            wizard_win["w"] = None
            stt = jload("state.json", {})
            stt["wizard_done"] = True
            jsave("state.json", stt)

        def to_settings():
            show("Settings")
            root.after(250, lambda: ctx.run_health())

        wizard_win["w"] = gw_wizard.Wizard(ctx, on_finish=done, show_settings=to_settings)

    ctx.open_wizard = open_wizard
    ctx.wizard_win = wizard_win

    def first_run():
        stt = jload("state.json", {})
        if stt.get("wizard_done"):
            return
        if jload("accounts.json", []):                                 # someone who already set things up doesn't need the guide
            stt["wizard_done"] = True
            jsave("state.json", stt)
            return
        open_wizard()

    ctx.first_run = first_run
    root.after(5200, lambda: guarded(first_run))

    def hide_console():
        """Tuck the console away (recording, dry runs). Returns what show_console needs to put it back exactly as it was."""
        tok = {"was": root.state() != "withdrawn", "geo": root.geometry()}
        if tok["was"]:
            root.withdraw()
        return tok

    def show_console(tok):
        if tok and tok.get("was"):
            root.deiconify()
            try:
                root.geometry(tok["geo"])                       # same place and size as before, never a stale one
            except tk.TclError:
                pass
            root.update_idletasks()
            root.lift()

    ctx.hide_console, ctx.show_console = hide_console, show_console

    import gw_overlay
    overlay = gw_overlay.Overlay(root, S, theme)                          # the small event + countdown pill (Settings > Overlay)
    ctx.overlay = overlay

    def overlay_loop():
        try:
            overlay.sync()
        except Exception as e:
            print("overlay error:", e)
        try:
            root.after(1000 if S.OVERLAY else 2500, overlay_loop)
        except tk.TclError:
            pass

    root.after(1500, overlay_loop)

    def _update_usable():
        import gw_update
        return gw_update.usable((S.GW_SESSION or {}).get("update"))

    def start_update():
        import gw_update
        upd = (S.GW_SESSION or {}).get("update")
        if gw_update.usable(upd):
            gw_update.STATE.update(phase="idle", pct=0, msg="")
            gw_update.start(upd, S._quit_for_update)

    ctx.start_update = start_update
    update_seen = {"v": None}

    def show_lock():
        if lock_win["w"] is not None or not sess:
            return
        root.deiconify()
        import gw_login_ui

        def ok(new_sess):
            lock_win["w"] = None
            S._license_unlock(new_sess)
            cache.pop("lic_text", None)
            draw_side()

        def closed():
            lock_win["w"] = None
            S.QUIT.set()

        reason = (getattr(S, "_lic_state", {}).get("reason") or "your key ended").strip().rstrip(".")
        reason = reason[:1].lower() + reason[1:]
        lock_win["w"] = gw_login_ui.LoginWindow(S.HERE, parent=root, mode="lock", expected_id=sess.get("id"),
                                                message=f"Gnomewatch is paused: {reason}. Run /getkey for a new key.",
                                                on_success=ok, on_close=closed, update=getattr(S, "_lic_state", {}).get("update"))

    ctx.show_lock = show_lock

    page_objs["Overview"] = cs_overview.build(ctx)
    page_objs["Instances"] = cs_instances.build(ctx)
    page_objs["Accounts"] = cs_accounts.build(ctx)
    page_objs["Auto-place"] = cs_autoplace.build(ctx)
    page_objs["Performance"] = cs_performance.build(ctx)
    if sess.get("admin"):
        page_objs["Admin"] = cs_admin.build(ctx)
    page_objs["Activity"] = cs_activity.build(ctx)
    page_objs["Settings"] = cs_settings.build(ctx)
    page_objs["Help"] = cs_help.build(ctx)
    open_palette = cs_palette.build(ctx)
    ctx.open_palette = open_palette

    # ================================================================== mini strip
    mini = tk.Canvas(root, width=MINI_W, height=MINI_H, bg=K.BG, highlightthickness=0, bd=0)
    mini.create_image(14, 14, anchor="nw", image=U.rr_photo(MINI_W - 28, MINI_H - 28, 14, K.CARD, K.EDGE), tags="static")
    m_badge = mini.create_image(28, 26, anchor="nw")
    m_icon = mini.create_image(28 + 22, 26 + 22)
    m_name = mini.create_text(98, 31, anchor="nw", text="Watching", fill=K.TEXT, font=font(17, "bold"))
    m_sub = mini.create_text(99, 56, anchor="nw", text="", fill=K.DIM, font=font(12, "medium"))
    mini_btns = tk.Frame(mini, bg=K.CARD)
    U.IconBtn(mini_btns, "maximize-2", lambda: set_mini(False), theme, size=34, kind="ghost", bg=K.CARD, tip="Full window", px=16).pack(side="left")
    U.IconBtn(mini_btns, "x", lambda: root.withdraw(), theme, size=34, kind="ghost", bg=K.CARD, tip="Hide", px=16).pack(side="left", padx=(2, 0))
    mini.create_window(MINI_W - 28, MINI_H / 2, anchor="e", window=mini_btns)
    for item in (m_badge, m_icon, m_name, m_sub):
        pass


    # ================================================================== plain-Tk dragging (SendMessage drag aborted the process)
    drag = {"on": False, "dx": 0, "dy": 0}

    def start_drag(e=None):
        if e is not None:
            drag.update(on=True, dx=e.x_root - root.winfo_x(), dy=e.y_root - root.winfo_y())

    def do_drag(e):
        if drag["on"]:
            root.geometry(f"+{e.x_root - drag['dx']}+{e.y_root - drag['dy']}")

    def end_drag(_e=None):
        drag["on"] = False

    def borderless():
        """No title bar: Tk's own overrideredirect gives exact sizing; the app-window style keeps a taskbar button."""
        if os.environ.get("NATIVE_TITLE", "0") == "1":
            return
        try:
            root.overrideredirect(True)
            root.update_idletasks()
            u = ctypes.windll.user32
            hw = hwnd_of()
            ex = u.GetWindowLongW(hw, -20)
            u.SetWindowLongW(hw, -20, (ex | 0x00040000) & ~0x00000080)           # WS_EX_APPWINDOW on, TOOLWINDOW off
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hw, 33, ctypes.byref(ctypes.c_int(2)), 4)    # rounded corners
            c = K.EDGE.lstrip("#")
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hw, 34, ctypes.byref(ctypes.c_int(int(c[4:6] + c[2:4] + c[0:2], 16))), 4)  # border
            root.withdraw()                                                          # re-map so the taskbar picks the style up
            root.after(20, root.deiconify)
        except Exception as e:
            print("borderless:", e)

    # ================================================================== hover glow on cards
    hover = {"card": None, "job": None, "last": 0.0}

    def card_of(w):
        while w is not None:
            if isinstance(w, U.Card):
                return w
            w = getattr(w, "master", None)
        return None

    def hover_step():
        moving = False
        for c_ in list(U.Card.all):
            try:
                if not c_.winfo_exists():
                    U.Card.all.remove(c_)
                    continue
                tgt = 1.0 if c_ is hover["card"] else 0.0
                d = tgt - c_.level
                if abs(d) > .12:
                    c_.glow(c_.level + d * .5, theme.cur)
                    moving = True
                elif c_.level != tgt:
                    c_.glow(tgt, theme.cur)
            except tk.TclError:
                U.Card.all.remove(c_)
        hover["job"] = root.after(33, hover_step) if moving else None

    def on_motion(e):
        if get("ANIMATIONS") == "off" or not M["hover"] or time.time() - hover["last"] < .06:
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

    # ================================================================== fast tick: event chip, banner, mini strip
    def draw_chip(sig, kind, name, left, paused):
        if cache.get("chip") == sig:
            return
        cache["chip"] = sig
        chip.delete("all")
        col = theme.cur
        chip.create_image(0, 0, anchor="nw", image=U.rr_photo(232, 42, 21, lerp(K.BG, col, .12), lerp(K.BG, col, .45)))
        if paused:
            chip.create_image(20, 21, image=U.icon("pause", 16, K.WARN))
            chip.create_text(40, 20, anchor="w", text="Paused", fill=K.WARN, font=font(13, "semibold"))
        elif kind == "none":
            chip.create_image(20, 21, image=U.icon("radio", 16, K.DIM))
            chip.create_text(40, 20, anchor="w", text="Watching the board", fill=K.DIM, font=font(13, "semibold"))
        else:
            chip.create_image(22, 21, image=U.icon(U.event_icon(name), 18, col))
            chip.create_text(42, 20, anchor="w", text=name.title(), fill=K.TEXT, font=font(13, "semibold"))
            chip.create_text(216, 20, anchor="e", text=S._fmt_left(left), fill=col, font=font(13, "bold"))

    def draw_mini(sig, kind, name, left, paused, glyph_col):
        if cache.get("mini") == sig:
            return
        cache["mini"] = sig
        mini.delete("dyn")
        col = glyph_col
        mini.itemconfig(m_badge, image=U.rr_photo(52, 52, 13, lerp(K.CARD, col, .16), lerp(K.CARD, col, .45)))
        if paused:
            ic_name, nm, sb, tcol = "pause", "Paused", "not reading the game", K.DIM
        elif kind == "none":
            ic_name, nm, sb, tcol = "radio", "Watching", "no event on the sign", K.DIM
        else:
            ic_name, nm = U.event_icon(name), name.title()
            sb, tcol = ("ACTIVE  ·  time left" if kind == "active" else "NEXT  ·  starts in"), col
        mini.itemconfig(m_icon, image=U.icon(ic_name, 26, col))
        mini.itemconfig(m_name, text=nm)
        mini.itemconfig(m_sub, text=sb, fill=col if kind != "none" and not paused else K.DIM)
        U.draw_digits(mini, "dyn", S._fmt_left(left) if (kind != "none" and not paused) else "--:--", MINI_W - 138, 28, 30,
                      tcol if kind != "none" else K.DIM)

    burst = {"t0": 0.0, "colour": K.IDLE, "prev": None}

    def fast_body():
        kind, name, left = S._event_summary()
        paused = S.PAUSED.is_set()
        probs = list(S._problems.values())
        if probs:                                              # something is wrong: say so under the title
            more = f"   (+{len(probs) - 1} more)" if len(probs) > 1 else ""
            lc(sub_lbl, text="⚠  " + probs[0]["text"] + more, fg=K.BAD)
        elif snooze["until"]:
            if time.time() >= snooze["until"]:
                cancel_snooze()
            else:
                left_s = int(snooze["until"] - time.time())
                lc(sub_lbl, text=f"Alerts snoozed · {left_s // 60}:{left_s % 60:02d} left", fg=K.WARN)
        elif sess and not sess.get("admin") and S._license_left() is not None and 0 < S._license_left() <= 1800:
            lc(sub_lbl, text=f"⏳  Your key ends in {__import__('gw_license').fmt_left(S._license_left())}. Run /getkey for a new one.", fg=K.WARN)
        else:
            lc(sub_lbl, text=SUBS[nav["cur"]], fg=K.FAINT)
        bkey = f"active:{name}" if kind == "active" else kind
        if burst["prev"] is None:
            burst["prev"] = bkey
        elif bkey != burst["prev"]:
            if kind == "active" and (left is None or left > 8) and M["burst"] and get("ANIMATIONS") != "off":
                burst.update(t0=time.time(), colour=S._event_style(name)[1])
                if hasattr(page_objs["Overview"], "ripple"):
                    page_objs["Overview"].ripple(burst["colour"])
            burst["prev"] = bkey
        lock = get("ACCENT")
        if paused:
            accent = K.WARN
        elif M["mono"]:
            accent = K.IDLE
        elif lock and lock != "event":
            accent = lock
        elif kind == "active":
            accent = S._event_style(name)[1]
        elif kind == "next":
            accent = lerp(K.IDLE, S._event_style(name)[1], .55)
        else:
            accent = K.IDLE
        theme.go(accent)
        # sidebar status pill
        if paused:
            status_pill.set("PAUSED", K.WARN)
        elif probs:
            status_pill.set("PROBLEM", K.BAD)
        elif S.STATE["worker"]["ok"]:
            status_pill.set("ONLINE", K.OK)
        elif S.STATE["worker"]["ok"] is False:
            status_pill.set("BOT OFFLINE", K.BAD)
        elif S.STATE["last_read"] and time.time() - S.STATE["last_read"] < 20:
            status_pill.set("WATCHING", K.OK)                      # reading the game fine; nothing has needed reporting yet
        else:
            status_pill.set("STARTING", K.DIM)
        flash = max(0.0, 1 - (time.time() - burst["t0"]) / 1.3)
        csig = (paused, kind, name, S._fmt_left(left), theme.cur, round(flash * 6))
        if not mini_on[0]:
            draw_chip(csig, kind, name, left, paused)
            ov = page_objs["Overview"]
            if nav["cur"] == "Overview":
                ov.fast(kind, name, left, paused)
        else:
            draw_mini(csig, kind, name, left, paused, theme.cur)

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
        if S.UI_MODE == "smooth":
            if fullscreen_other():                           # a game is in front: ease off by itself
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
    def fade_to(show_it):
        """Show/hide the console with a short fade (Smooth mode only)."""
        if not M["fade"] or get("ANIMATIONS") == "off":
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

    panic_prev = {"vis": True}

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
                    page_objs["Settings"].run_health()
                elif cmd.startswith("test:"):                      # UI tests drive popups through this hook
                    fn = getattr(S, "_ui_test_hooks", {}).get(cmd[5:])
                    if fn:
                        fn(ctx)
                elif cmd == "license:locked":
                    show_lock()
                elif cmd == "license:warn":
                    cache.pop("lic_text", None)
                elif cmd.startswith("scene:"):
                    apply_scene(cmd[6:])
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
        guarded(auto_assign)
        if root.state() == "withdrawn" or mini_on[0]:        # nobody is looking at the pages: don't rebuild them
            return
        for name, po in page_objs.items():
            if name == nav["cur"] or getattr(po, "always", False):
                guarded(po.slow)
        lq = S._lq                                                          # a launch just finished with a problem: offer the diagnosis
        if lq.get("done_t", 0) > launch_seen["t"]:
            launch_seen["t"] = lq["done_t"]
            if lq.get("fail_msg") and time.time() - launch_seen["asked"] > 600 and not lock_win["w"]:
                launch_seen["asked"] = time.time()
                root.after(400, lambda m=lq["fail_msg"]: guarded(lambda: launch_problem(m)))
        upd = (S.GW_SESSION or {}).get("update")
        if _update_usable() and update_seen["v"] != upd.get("latest"):         # tell them once per version
            update_seen["v"] = upd.get("latest")
            S.notify("Gnomewatch update", f"Version {upd['latest']} is ready. Open Settings > Updates to install it.")
        lt = lic_text()
        if cache.get("lic_text") != lt:                      # the key's time-left changes by the minute
            cache["lic_text"] = lt
            draw_side()
        # sidebar badges
        snap = list(S._snapshot)
        in_game = len({v for h, v in assign.items() if h in {e[0] for e in snap}})
        nb = {}
        if _update_usable():
            nb["Settings"] = ("Update", theme.cur)
        if snap:
            nb["Instances"] = (str(len(snap)), K.OK)
        if in_game:
            nb["Accounts"] = (str(in_game), theme.cur)
        armed = sum(1 for m in S._macros_load().values() if m.get("enabled") and (m.get("steps") or m.get("end_steps")))
        if armed:
            nb["Auto-place"] = (str(armed), K.OK)
        if S._problems:
            nb["Activity"] = ("!", K.BAD)
        if nb != badges:
            badges.clear()
            badges.update(nb)
            draw_side()

    # ------------------------------------------------------------------ go
    root.protocol("WM_DELETE_WINDOW", root.withdraw)
    root.bind_all("<Control-k>", focus_bar)
    root.bind_all("<MouseWheel>", lambda e: ctx.on_wheel(e) if hasattr(ctx, "on_wheel") else None)
    show(os.environ.get("GW_START_PAGE") if os.environ.get("GW_START_PAGE") in dict(nav_items) else "Overview")      # (GW_START_PAGE: tests only)
    _ts = os.environ.get("GW_TEST_SCRIPT")                         # source-run tests only: run a script inside the console after a delay
    if _ts and not getattr(sys, "frozen", False) and os.path.isfile(_ts):
        root.after(int(os.environ.get("GW_TEST_DELAY", "5000")), lambda: exec(compile(open(_ts, encoding="utf-8").read(), _ts, "exec"),
                                                                              {"ctx": ctx, "root": root, "S": S, "U": U, "theme": theme, "show": show,
                                                                               "page_objs": page_objs}))
    root.update_idletasks()
    chrome(root)
    borderless()
    for w_ in (hdr, title_lbl, sub_lbl, mini):
        w_.bind("<ButtonPress-1>", start_drag)
        w_.bind("<B1-Motion>", do_drag)
        w_.bind("<ButtonRelease-1>", end_drag)
    side.bind("<ButtonPress-1>", lambda e: start_drag(e) if e.y < 80 else None, add="+")
    side.bind("<B1-Motion>", do_drag, add="+")
    side.bind("<ButtonRelease-1>", end_drag, add="+")
    root.bind_all("<Motion>", on_motion, add="+")
    if get("PANEL_MINI"):
        set_mini(True, save=False)
    if S.UI_MODE == "stealth":
        root.after(300, root.withdraw)
    fast_tick()
    slow_tick()
    if S.LOCKED.is_set():
        root.after(600, show_lock)
    root.mainloop()
    return "restyle" if restart["on"] else None
