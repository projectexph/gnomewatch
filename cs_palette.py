"""Command palette (Ctrl+K): type to find any action. Drawn on a canvas with an icon per action."""
import ctypes
import threading
import tkinter as tk

import ui_kit as U
from ui_kit import K, font, lerp
import cs_common as C

KEY = "#010203"                                              # painted transparent, so the card can have rounded corners


def build(ctx):
    S, root, theme, get = ctx.S, ctx.root, ctx.theme, ctx.get
    pal = {"win": None}

    def go(name):
        root.deiconify()
        if ctx.mini_on[0]:
            ctx.set_mini(False)
        ctx.show(name)

    def actions():
        A = []

        def add(label_, fn, kw="", icon="command"):
            A.append((label_, kw.lower(), fn, icon))

        def confirmed(msg, fn, yes="Yes"):
            return lambda: fn() if ctx.ask(C.APP_NAME, msg, yes=yes, danger=True) else None

        add("Show all Roblox windows", lambda: S._set_show(True), "reveal bring back", "eye")
        add("Hide all Roblox windows", lambda: S._set_show(False), "conceal park", "eye-off")
        add("Close all Roblox windows…", confirmed("Close every Roblox window?", S._close_all, "Close all"), "kill quit end", "power")
        add("New Roblox instance", S._launch_instance, "launch open start", "plus")
        for a in ctx.load_accounts():
            add(f"Launch as {a['label']}", lambda x=a: ctx.launch_account(x), "account switch", "play")
            add(f"Rejoin as {a['label']}", lambda x=a: ctx.rejoin(x), "relaunch again", "rotate-cw")
        add("Restart Roblox for multi-instance…", ctx.restart_multi, "lock mutex multiple", "refresh-cw")
        add("Panic: hide everything / bring it back", lambda: threading.Thread(target=S._panic_toggle, daemon=True).start(),
            "hide all quick boss escape", "shield")
        for ev_, m_ in S._macros_load().items():
            if m_.get("steps"):
                add(f"Place gnome now: {ev_.title()}",
                    lambda e=ev_: threading.Thread(target=S._macro_play, args=(e, True), daemon=True).start(),
                    "macro replay auto place", "wand-sparkles")
        for key_, m_ in U.MODES.items():
            add(f"Mode: {m_['label']}", lambda k=key_: ctx.choose_mode(k), "performance look smooth fps stealth eco " + m_["blurb"], m_["icon"])
        add("Arrange all Roblox windows in a grid", lambda: threading.Thread(target=S._grid_toggle, daemon=True).start(),
            "tile layout restore", "layout-grid")
        add("Free memory of background Roblox windows", lambda: threading.Thread(target=lambda: S._trim_background(force=True), daemon=True).start(),
            "ram trim clean", "memory-stick")
        add("Send a test message to Discord", lambda: threading.Thread(target=S._hook_test, daemon=True).start(), "webhook", "send")
        add("Pause / resume reading", lambda: (S.PAUSED.clear() if S.PAUSED.is_set() else S.PAUSED.set()), "stop", "pause")
        add("Mute / unmute alerts", lambda: (S.MUTED.clear() if S.MUTED.is_set() else S.MUTED.set()), "silence", "bell-off")
        add("Toggle mini mode", lambda: ctx.set_mini(not ctx.mini_on[0]), "compact strip small", "minimize-2")
        add("Open the web dashboard", S._open_dashboard, "browser", "external-link")
        for mins, txt in ((30, "30 minutes"), (60, "1 hour"), (240, "4 hours")):
            add(f"Snooze alerts for {txt}", lambda m=mins: ctx.do_snooze(m), "mute quiet silence", "timer")
        add("Cancel snooze (alerts back on)", ctx.cancel_snooze, "unmute resume", "bell")
        add("Run health check", lambda: (go("Settings"), ctx.run_health()), "diagnose test problems", "heart-pulse")
        for nm in ctx.load_scenes():
            add(f"Scene: {nm}", lambda n=nm: ctx.apply_scene(n), "mode profile", C.SCENE_ICONS.get(nm, "star"))
        for nm in C.PRESETS:
            add(f"Performance preset: {nm}", lambda n=nm: [S._set_setting(k, v) for k, v in C.PRESETS[n].items()], "speed cpu", "gauge")
        for text, name in (("alerts", "NOTIFY"), ("keep-alive click", "KEEPALIVE"), ("auto-reconnect", "AUTO_RECONNECT"), ("auto-rejoin after a crash", "AUTO_REJOIN"),
                           ("hiding the board window", "HIDE_WINDOW"), ("hiding all instances", "HIDE_ALL"),
                           ("low CPU for hidden Roblox", "THROTTLE_PARKED")):
            add(f"Toggle {text}", lambda n=name: S._set_setting(n, not get(n)), "switch", "sliders-horizontal")
        for n_, ic_ in C.NAV:
            add(f"Go to {n_}", lambda x=n_: go(x), "page open", ic_)
        add("Show the log", lambda: go("Activity"), "logs", "scroll-text")
        add("Open the setup guide", lambda: ctx.open_wizard(), "wizard first run start help new account", "rocket")
        add("Show / hide Gnomewatch on my Discord profile", lambda: S._set_setting("PRESENCE", not S.PRESENCE), "discord status rich presence profile", "radio")
        add("Show / hide the overlay", lambda: S._set_setting("OVERLAY", not S.OVERLAY), "overlay widget countdown pill", "layout-dashboard")
        add("Unlock the overlay to move it", lambda: (S._set_setting("OVERLAY", True), S._set_setting("OVERLAY_LOCK", False)), "overlay widget drag move", "lock-open")
        add("Back up my settings now", lambda: threading.Thread(target=lambda: S._backup_make(
            __import__("os").path.join(S.BACKUP_DIR, "manual-" + __import__("time").strftime("%Y%m%d-%H%M%S") + ".zip"), False), daemon=True).start(),
            "save export copy", "save")
        add("Quit Gnomewatch…", confirmed("Quit Gnomewatch?", lambda: S.QUIT.set(), "Quit"), "exit stop", "power")
        return A

    ctx.actions = actions

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
        w.configure(bg=KEY)
        try:
            w.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass
        pw, rows, rh = 660, 7, 48
        ph = 76 + rows * rh + 46
        w.geometry(f"{pw}x{ph}+{(w.winfo_screenwidth() - pw) // 2}+{(w.winfo_screenheight() - ph) // 3}")
        cv = tk.Canvas(w, width=pw, height=ph, bg=KEY, highlightthickness=0, bd=0)
        cv.pack()
        cv.create_image(0, 0, anchor="nw", image=U.rr_photo(pw, ph, 16, K.CARD, K.EDGE_HI))
        cv.create_image(34, 38, image=U.icon("command", 20, theme.cur))
        q = tk.StringVar()
        ent = tk.Entry(cv, textvariable=q, bg=K.CARD, fg=K.TEXT, insertbackground=theme.cur, relief="flat", bd=0,
                       highlightthickness=0, font=font(18))
        cv.create_window(60, 38, anchor="w", window=ent, width=pw - 100, height=34)
        hint = tk.Label(cv, text="Type a command or search…", bg=K.CARD, fg=K.FAINT, font=font(18), anchor="w", bd=0)
        hint.bind("<Button-1>", lambda e: ent.focus_set())
        cv.create_window(60, 38, anchor="w", window=hint, width=pw - 100, height=34)
        cv.create_line(20, 76, pw - 20, 76, fill=K.EDGE)
        cv.create_text(pw / 2, ph - 22, text="↑ ↓  choose      Enter  run      Esc  close", fill=K.FAINT, font=font(12, "medium"))
        items = actions()
        st = {"shown": [], "sel": 0, "off": 0}

        def draw():
            cv.delete("row")
            shown = st["shown"]
            if not shown:
                cv.create_text(pw / 2, 76 + rows * rh / 2, text="No matching action", fill=K.FAINT, font=font(14), tags="row")
                return
            for i in range(rows):
                idx = st["off"] + i
                if idx >= len(shown):
                    break
                label_, kw, fn, icn = shown[idx]
                y0 = 84 + i * rh
                sel = idx == st["sel"]
                if sel:
                    cv.create_image(10, y0, anchor="nw", image=U.rr_photo(pw - 20, rh - 4, 11, lerp(K.CARD, theme.cur, .16),
                                                                          lerp(K.CARD, theme.cur, .35)), tags="row")
                cv.create_image(22, y0 + (rh - 4) / 2 - 12, anchor="nw",
                                image=U.rr_photo(30, 30, 8, lerp(K.CARD, theme.cur, .18) if sel else K.CARD2, None), tags="row")
                cv.create_image(37, y0 + (rh - 4) / 2 + 3, image=U.icon(icn, 17, theme.cur if sel else K.DIM), tags="row")
                cv.create_text(68, y0 + (rh - 4) / 2 + 1, anchor="w", text=label_, fill=K.TEXT, font=font(14, "semibold" if sel else "medium"),
                               tags="row")
                if sel:
                    cv.create_image(pw - 30, y0 + (rh - 4) / 2 + 1, image=U.icon("arrow-right", 16, theme.cur), tags="row")

        def refill(*_):
            hint.lower() if q.get() else hint.lift()
            st["shown"] = C.filter_actions(items, q.get())
            st["sel"], st["off"] = 0, 0
            draw()

        def close(_=None):
            pal["win"] = None
            try:
                w.destroy()
            except tk.TclError:
                pass

        def run_sel(_=None):
            shown = st["shown"]
            fn = shown[st["sel"]][2] if shown else None
            close()
            if fn:
                root.after(60, fn)

        def move(d):
            shown = st["shown"]
            if shown:
                st["sel"] = max(0, min(len(shown) - 1, st["sel"] + d))
                if st["sel"] < st["off"]:
                    st["off"] = st["sel"]
                elif st["sel"] >= st["off"] + rows:
                    st["off"] = st["sel"] - rows + 1
                draw()
            return "break"

        def click(e):
            i = (e.y - 84) // rh
            idx = st["off"] + int(i)
            if 0 <= i < rows and idx < len(st["shown"]):
                st["sel"] = idx
                run_sel()

        cv.bind("<ButtonRelease-1>", click)
        cv.bind("<MouseWheel>", lambda e: move(-1 if e.delta > 0 else 1))
        q.trace_add("write", refill)
        ent.bind("<Return>", run_sel)
        ent.bind("<Escape>", close)
        ent.bind("<Down>", lambda e: move(1))
        ent.bind("<Up>", lambda e: move(-1))
        w.bind("<FocusOut>", lambda e: root.after(120, lambda: close() if pal["win"] is not None and w.focus_displayof() is None else None))
        refill()
        w.update_idletasks()
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

    return open_palette
