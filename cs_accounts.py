"""Accounts page: a header with the launch controls and live progress, a grid of account cards, and a settings panel that slides out.
Only a label + browser profile name is stored - never a password or cookie.

Card: coloured initials, live status, RAM / CPU / uptime, and one-tap actions (Play, Rejoin, Close, show/hide) - no confirmations.
Header: Launch all with a progress bar while it opens accounts one by one; Launch options (where to join, pace, multi-instance).
Panel: the account's settings and its history (joins, disconnects with the reason, rejoins, auto-place results).
"""
import json
import time
import tkinter as tk

import ui_kit as U
from ui_kit import K, font, lerp
import cs_common as C
from cs_common import CW, BODY_H, SCROLL_W, ago, browser_exe, browser_profiles

PALETTE = ["#7c6cff", "#3dd6ff", "#5cffc4", "#ff5fa8", "#ffc83d", "#ff7a45", "#8dff4d", "#b06bff"]
KIND_ICON = {"launch": "rocket", "join": "log-in", "left": "log-out", "disconnect": "triangle-alert", "rejoin": "rotate-cw",
             "auto-place": "wand-sparkles", "memory": "memory-stick"}
KIND_COL = {"disconnect": "BAD", "rejoin": "WARN", "join": "OK", "auto-place": "IDLE", "memory": "WARN"}
STATE_LABEL = {"in game": ("IN GAME", "OK"), "reconnecting": ("RECONNECTING", "WARN"), "disconnected": ("DISCONNECTED", "BAD"),
               "starting": ("LOADING", "DIM"), "left": ("LEFT THE GAME", "WARN")}
QUEUE_LABEL = {"queued": ("QUEUED", "DIM"), "launching": ("OPENING", "WARN"), "retrying": ("RETRYING", "WARN"), "waiting for window": ("STARTING", "WARN"),
               "loading": ("LOADING", "WARN"), "no window appeared": ("DIDN'T START", "BAD"), "failed": ("FAILED", "BAD"), "cancelled": ("CANCELLED", "FAINT")}

GAP = 14
COLS = 3
CARD_W = (SCROLL_W - GAP * (COLS - 1)) // COLS
CARD_H = 186
HDR_H = 132
DRAWER_W = 478


def initials(label):
    parts = [p for p in "".join(c if c.isalnum() else " " for c in label).split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return (parts[0][:2] if parts else "?").upper()


def colour_of(a):
    c = a.get("color")
    if c and isinstance(c, str) and c.startswith("#") and len(c) == 7:
        return c
    return PALETTE[sum(ord(ch) for ch in a.get("label", "")) % len(PALETTE)]


def fmt_ram(b):
    return f"{b / 1e9:.1f} GB" if b >= 1e9 else f"{b / 1e6:.0f} MB"


def fmt_up(sec):
    sec = int(sec)
    return f"{sec // 3600}h {sec % 3600 // 60}m" if sec >= 3600 else f"{sec // 60}m"


def build(ctx):
    S, root, theme, get = ctx.S, ctx.root, ctx.theme, ctx.get
    page = ctx.pages["Accounts"]
    assign, set_assign, jsave = ctx.assign, ctx.set_assign, ctx.jsave
    load_accounts, ask = ctx.load_accounts, ctx.ask
    st = {"edit": None, "new": False, "checked": set(), "q": "", "group": "All", "tab": "settings", "note": "", "sig": None,
          "form_for": None, "hist_sig": None, "cards_sig": None, "cards": {}, "drawer_open": False, "multi_job": None}
    note_seen = {"text": "", "t": 0.0}

    def account_windows(label_):
        return [e for e in list(S._snapshot) if assign.get(e[0]) == label_]

    def find(label_):
        for x in load_accounts():
            if x["label"] == label_:
                return x
        return None

    def status_of(a):
        """(label, colour, rss bytes or 0, client info or None, windows)"""
        wins = account_windows(a["label"])
        if not wins:
            return "OFFLINE", K.FAINT, 0, None, wins
        info = S._client_for_hwnd(wins[0][0]) or {}
        lab, col = STATE_LABEL.get(info.get("state"), ("IN GAME", "OK"))
        return lab, getattr(K, col), info.get("rss", 0), info, wins

    def queue_state(label_):
        """While the launch queue is working on this account (or just finished with a problem): (label, colour) for its pill, else None."""
        lq = S._lq
        recent = lq.get("running") or time.time() - lq.get("done_t", 0) < 12
        if not recent:
            return None
        for it in lq.get("items", []):
            if it.get("label") == label_ and it.get("state") in QUEUE_LABEL:
                lab, col = QUEUE_LABEL[it["state"]]
                if it["state"] in ("no window appeared", "failed", "cancelled") and lq.get("running"):
                    continue                                              # a failure shows once the queue has finished with it
                return lab, getattr(K, col)
        return None

    # ================================================================== actions (all launching goes through the queue; none of them asks "are you sure")
    def note(text):
        st["note"] = text

    def launch_account(a):
        S._launch_queue_async([a])
        note(f"Opening {a['label']}…")

    def with_multi(accts, go):
        """Several accounts at once need multi-instance. Switch it on by itself; only if another Roblox already owns the lock is a choice needed."""
        if len(accts) < 2 or S.STATE["lock"] == "held":
            return go()
        if S.STATE["lock"] == "waiting":
            if ask("Roblox is already open", "A Roblox window is open without multi-instance, so Roblox would close the earlier copies as the later ones open.\n\n"
                   "Restart Roblox with multi-instance on, then launch?", yes="Restart and launch", no="Launch anyway"):
                return restart_multi(accts)
            return go()
        S._set_setting("MULTI_INSTANCE", True)
        note("Turning multi-instance on…")
        t0 = time.time()

        def poll():
            if S.STATE["lock"] == "held" or time.time() - t0 > 14:
                go()
            else:
                root.after(500, poll)

        root.after(500, poll)

    def launch_many(accts):
        if not accts:
            note("Nothing to launch yet. Add an account first")
            return
        with_multi(accts, lambda: (S._launch_queue_async(accts), note("")))

    def rejoin(a):
        S._launch_queue_async([a], close_first=True)
        note(f"Rejoining {a['label']}…")

    def close_one(hwnd):
        S._close_window(hwnd)

    def restart_multi(accts=None):
        """Close every Roblox window, turn multi-instance on, wait for the lock, then open the accounts that were open. One click."""
        if accts is None:
            alive = {h for h, *_r in S._snapshot}
            labels = []
            for h_, lbl in assign.items():
                if h_ in alive and lbl not in labels:
                    labels.append(lbl)
            accts = [x for x in load_accounts() if x["label"] in labels]
        S._set_setting("MULTI_INSTANCE", True)
        S._close_all()
        note("Closing Roblox… getting the multi-instance lock")
        t0 = time.time()

        def poll():
            if S.STATE["lock"] == "held" and not S._snapshot:
                note("Multi-instance is on")
                if accts:
                    S._launch_queue_async(accts)
            elif time.time() - t0 > 45:
                note("Gave up waiting for Roblox to close")
            else:
                root.after(800, poll)

        root.after(1200, poll)

    ctx.launch_account, ctx.rejoin, ctx.restart_multi = launch_account, rejoin, restart_multi

    def targets():
        idx = {x["label"]: x for x in load_accounts()}
        return [idx[x["label"]] for x in load_accounts() if x["label"] in st["checked"]]

    def move_account(label_, step):
        lst = load_accounts()
        names = [x["label"] for x in lst]
        if label_ not in names:
            return
        i = names.index(label_)
        j = max(0, min(len(lst) - 1, i + step))
        if i != j:
            lst.insert(j, lst.pop(i))
            jsave("accounts.json", lst)
            st["sig"] = None
            st["cards_sig"] = None

    # ================================================================== header: counts, live launch progress, the main buttons
    hdr = U.Card(page, CW, HDR_H, pad=0)
    hdr.pack(anchor="w")
    h_count = hdr.create_text(26, 38, anchor="w", text="", fill=K.TEXT, font=font(24, "bold"))
    h_sub = hdr.create_text(26, 68, anchor="w", text="", fill=K.DIM, font=font(12, "medium"))
    multi_pill = U.Pill(hdr, "MULTI-INSTANCE OFF", K.FAINT, bg=K.CARD)
    hdr.create_window(26, 102, anchor="w", window=multi_pill)
    PX = 300                                                                       # the progress area: the second row, clear of the buttons above
    h_icon = hdr.create_image(PX, 96, anchor="w")
    h_msg = hdr.create_text(PX + 28, 90, anchor="w", text="", fill=K.TEXT, font=font(14, "semibold"))
    h_prog_txt = hdr.create_text(PX + 28, 111, anchor="w", text="", fill=K.FAINT, font=font(11, "medium"))
    prog = U.Bar(hdr, 190, 8, bg=K.CARD)
    h_prog = hdr.create_window(PX + 28 + 340, 98, anchor="w", window=prog)
    cancel_btn = U.IconBtn(hdr, "x", lambda: S._lq.update(cancel=True), theme, size=30, kind="danger", bg=K.CARD, tip="Cancel the launch", px=15)
    h_cancel = hdr.create_window(PX + 28 + 340 + 190 + 10, 98, anchor="w", window=cancel_btn)

    rb = tk.Frame(hdr, bg=K.CARD)
    hdr.create_window(CW - 22, 40, anchor="e", window=rb)
    launch_all_btn = U.Btn(rb, "Launch all", lambda: launch_many(load_accounts()), theme, icon="rocket", kind="ok", h=46, size=14, bg=K.CARD)
    launch_all_btn.pack(side="left", padx=(0, 8))
    opts_btn = U.Btn(rb, "Launch options", lambda: toggle_options(), theme, icon="sliders-horizontal", h=46, size=13, bg=K.CARD)
    opts_btn.pack(side="left", padx=(0, 8))
    U.Btn(rb, "Add account", lambda: open_drawer(None, new=True), theme, icon="user-plus", kind="primary", h=46, size=13, bg=K.CARD).pack(side="left")

    # ================================================================== toolbar: search, group, and what to do with the ticked accounts
    tb = tk.Frame(page, bg=K.BG)
    tb.pack(fill="x", pady=(12, 12))
    search = U.EntryBox(tb, None, w=230, h=38, bg=K.BG, theme=theme, icon="search", size=13, placeholder="Search accounts")
    search.pack(side="left")
    group_dd = U.Dropdown(tb, lambda: [("All groups", "All")] + [(g, g) for g in sorted({x.get("group", "") for x in load_accounts() if x.get("group")})],
                          lambda: st["group"], lambda v: (st.update(group=v), refresh_all()), theme, w=170, h=38, bg=K.BG, icon="layers", size=13)
    group_dd.pack(side="left", padx=(10, 0))
    search.var.trace_add("write", lambda *a: (st.update(q=search.var.get().strip().lower()), refresh_all()))
    selbar = tk.Frame(tb, bg=K.BG)                                                   # appears once something is ticked
    sel_lbl = tk.Label(selbar, text="", bg=K.BG, fg=K.DIM, font=font(13, "medium"))
    sel_lbl.pack(side="left", padx=(0, 10))
    U.Btn(selbar, "Launch", lambda: launch_many(targets()), theme, icon="play", kind="ok", h=38, size=12, bg=K.BG).pack(side="left", padx=(0, 6))
    U.Btn(selbar, "Rejoin", lambda: S._launch_queue_async(targets(), close_first=True), theme, icon="rotate-cw", h=38, size=12, bg=K.BG).pack(side="left", padx=(0, 6))
    U.Btn(selbar, "Close", lambda: [close_one(w[0]) for a in targets() for w in account_windows(a["label"])], theme, icon="x", h=38, size=12,
          bg=K.BG).pack(side="left", padx=(0, 6))
    U.Btn(selbar, "Clear", lambda: (st["checked"].clear(), refresh_all()), theme, kind="ghost", h=38, size=12, bg=K.BG).pack(side="left")

    # ================================================================== the cards
    sf = U.ScrollFrame(page, CW, BODY_H - HDR_H - 12 - 38 - 12, bg=K.BG, theme=theme)
    sf.pack(anchor="w")
    ctx.scrollers.append(sf)
    grid = sf.inner

    def build_card(i, a):
        c = U.Card(grid, CARD_W, CARD_H, pad=0)
        c.grid(row=i // COLS, column=i % COLS, padx=(0 if i % COLS == 0 else GAP, 0), pady=(0, GAP))
        ui = {"card": c, "label": a["label"], "mode": None, "chip_sig": None, "edge": None}
        ui["avatar"] = c.create_image(16 + 22, 16 + 22)
        ui["ini"] = c.create_text(16 + 22, 16 + 22, text="", fill=K.TEXT, font=font(15, "bold"))
        ui["name"] = c.create_text(76, 28, anchor="w", text="", fill=K.TEXT, font=font(15, "semibold"))
        ui["sub"] = c.create_text(76, 50, anchor="w", text="", fill=K.FAINT, font=font(11, "medium"))
        ui["tick"] = c.create_image(CARD_W - 28, 26)
        ui["tickic"] = c.create_image(CARD_W - 28, 26)
        ui["pill"] = U.Pill(c, "OFFLINE", K.FAINT, bg=K.CARD)
        c.create_window(16, 88, anchor="w", window=ui["pill"])
        ui["detail"] = c.create_text(16, 116, anchor="w", text="", fill=K.DIM, font=font(12, "medium"))
        ui["capbar"] = c.create_image(16, 136, anchor="nw")
        btns = tk.Frame(c, bg=K.CARD)
        c.create_window(CARD_W - 14, CARD_H - 14, anchor="se", window=btns)
        ui["btns"] = btns
        c.bind("<ButtonRelease-1>", lambda e, lbl=a["label"]: card_click(lbl, e))
        return ui

    def card_click(label_, e):
        if e.x >= CARD_W - 52 and e.y <= 52:                              # the tick box
            st["checked"] ^= {label_}
            refresh_all()
        else:
            open_drawer(label_)

    def fill_buttons(ui, a, wins, queued):
        """The card's action row: Play when it's offline; Rejoin / show-hide / Close when it's running. Rebuilt only when that changes."""
        hidden = bool(wins) and wins[0][3]
        mode = (bool(wins), hidden, bool(queued), a["label"])
        if ui["mode"] == mode:
            return
        ui["mode"] = mode
        for ch in ui["btns"].winfo_children():
            ch.destroy()
        b = ui["btns"]
        if queued:
            U.Btn(b, "Working on it…", None, theme, icon="loader-circle", h=36, size=12, bg=K.CARD, w=CARD_W - 28 - 44).pack(side="left")
        elif wins:
            hw = wins[0][0]
            U.Btn(b, "Rejoin", lambda x=a: rejoin(x), theme, icon="rotate-cw", kind="secondary", h=36, size=12, bg=K.CARD, pad=14).pack(side="left", padx=(0, 6))
            U.IconBtn(b, "eye" if hidden else "eye-off", lambda h=hw, hd=hidden: S._set_window_show(h, hd), theme, size=36, kind="secondary", bg=K.CARD,
                      tip="Show this window" if hidden else "Hide this window", px=17).pack(side="left", padx=(0, 6))
            U.IconBtn(b, "x", lambda h=hw: close_one(h), theme, size=36, kind="secondary", bg=K.CARD, tip="Close this Roblox window", px=17).pack(side="left", padx=(0, 6))
        else:
            U.Btn(b, "Play", lambda x=a: launch_account(x), theme, icon="play", kind="primary", h=36, size=13, bg=K.CARD,
                  w=CARD_W - 28 - 44 - 6).pack(side="left", padx=(0, 6))
        U.IconBtn(b, "pencil", lambda lbl=a["label"]: open_drawer(lbl), theme, size=36, kind="ghost", bg=K.CARD, tip="Edit this account", px=17).pack(side="left")

    def refresh_card(ui, a):
        c = ui["card"]
        ic = ctx.ic
        lab_, col_, rss, info, wins = status_of(a)
        q = queue_state(a["label"]) if not wins else None            # a window that exists is judged by what it is really doing, not by the queue's progress
        if q:
            lab_, col_ = q
        col = colour_of(a)
        ic(c, ui["avatar"], image=U.circle_photo(44, lerp(K.CARD, col, .22), col, 2))
        ic(c, ui["ini"], text=initials(a["label"]), fill=col)
        ic(c, ui["name"], text=U.ellipsize(a["label"], font(15, "semibold"), CARD_W - 76 - 54))
        ic(c, ui["sub"], text=U.ellipsize(" · ".join(p for p in (a.get("group", ""), f"{a['browser'].title()} {a['profile']}") if p), font(11, "medium"), CARD_W - 76 - 14))
        ui["pill"].set(lab_, col_)
        ticked = a["label"] in st["checked"]
        ic(c, ui["tick"], image=U.rr_photo(20, 20, 6, theme.cur if ticked else K.CARD2, theme.cur if ticked else K.EDGE_HI))
        ic(c, ui["tickic"], image=U.icon("check", 14, U.on_colour(theme.cur)) if ticked else "")
        # the middle line: live numbers while it runs, otherwise when it last ran
        cap_ = S.RAMCAP.get(a["label"])
        if wins:
            parts = [fmt_ram(rss) + (f" / {cap_['cap'] / 1000:.1f} GB" if cap_ else ""), f"{(info or {}).get('cpu', 0):.0f}% CPU"]
            if (info or {}).get("start"):
                parts.append(fmt_up(time.time() - info["start"]))
            detail = "  ·  ".join(parts) + ("  ⚠" if cap_ and cap_.get("over") else "")
            dcol = K.WARN if cap_ and cap_.get("over") else K.DIM
        else:
            last = a.get("last")
            detail = f"Last launched {ago(time.time() - last)}" if last else "Never launched"
            dcol = K.FAINT
        ic(c, ui["detail"], text=detail, fill=dcol)
        # a thin bar of memory against the cap
        if wins and cap_ and cap_.get("cap"):
            frac = max(0.02, min(1.0, rss / 1e6 / cap_["cap"]))
            bw = int((CARD_W - 32) * frac)
            ic(c, ui["capbar"], image=U.rr_photo(max(6, bw), 6, 3, K.WARN if cap_.get("over") else lerp(K.CARD, col, .8), None), state="normal")
        else:
            ic(c, ui["capbar"], state="hidden")
        edge = lerp(K.EDGE, theme.cur, .8) if (ticked or st["edit"] == a["label"]) else None
        if edge != ui["edge"]:
            ui["edge"] = edge
            c.edge0 = edge or K.EDGE
            c.itemconfig(c.body, image=U.rr_photo(c.w, c.h, c.radius, c.fill0, c.edge0))
        fill_buttons(ui, a, wins, q is not None and lab_ in ("QUEUED", "OPENING", "RETRYING", "STARTING", "LOADING"))

    def visible_accounts():
        out = []
        for x in load_accounts():
            hay = f"{x['label']} {x.get('group', '')} {x.get('notes', '')}".lower()
            if st["group"] != "All" and x.get("group", "") != st["group"]:
                continue
            if st["q"] and st["q"] not in hay:
                continue
            out.append(x)
        return out

    def ensure_cards():
        accts = visible_accounts()
        sig = (tuple(a["label"] for a in accts), st["group"], st["q"])
        if sig == st["cards_sig"]:
            return accts
        st["cards_sig"] = sig
        for ch in grid.winfo_children():
            ch.destroy()
        st["cards"].clear()
        if not accts:
            empty = tk.Frame(grid, bg=K.BG)
            empty.grid(row=0, column=0, sticky="w")
            c = U.Card(empty, SCROLL_W, 220, pad=0)
            c.pack()
            c.create_image(SCROLL_W / 2, 74, image=U.icon("users", 44, K.FAINT))
            has_any = bool(load_accounts())
            c.create_text(SCROLL_W / 2, 126, text="No matches" if has_any else "No accounts yet", fill=K.TEXT, font=font(18, "semibold"))
            c.create_text(SCROLL_W / 2, 156, text="Try a different search or group." if has_any else "Add the browser profile you play Roblox in, and launch it from here.",
                          fill=K.DIM, font=font(13))
            if not has_any:
                add = U.Btn(c, "Add your first account", lambda: open_drawer(None, new=True), theme, icon="user-plus", kind="primary", h=40, size=13)
                c.create_window(SCROLL_W / 2, 194, window=add)
            return accts
        for i, a in enumerate(accts):
            st["cards"][a["label"]] = build_card(i, a)
        return accts

    # ================================================================== the launch options popover
    pop = {"win": None}

    def close_options(_e=None):
        w = pop["win"]
        pop["win"] = None
        if w is not None:
            try:
                root.unbind_all("<Button-1>") if False else None
                w.destroy()
            except tk.TclError:
                pass

    def toggle_options():
        if pop["win"] is not None:
            close_options()
            return
        w = tk.Toplevel(root)
        pop["win"] = w
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=K.EDGE_HI)
        body = tk.Frame(w, bg=K.CARD)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        inner = tk.Frame(body, bg=K.CARD)
        inner.pack(fill="both", expand=True, padx=20, pady=(18, 18))
        tk.Label(inner, text="Launch options", bg=K.CARD, fg=K.TEXT, font=font(16, "bold")).pack(anchor="w")

        def lab(t):
            tk.Label(inner, text=t, bg=K.CARD, fg=K.FAINT, font=font(10, "semibold")).pack(anchor="w", pady=(14, 4))

        lab("JOIN")
        lt_seg = U.Seg(inner, [("Game page", "game"), ("Private server", "private"), ("Custom", "custom")], lambda: get("LAUNCH_TARGET"),
                       lambda v: S._set_setting("LAUNCH_TARGET", v), theme, seg_w=122, h=34)
        lt_seg.pack(anchor="w")
        for title, name, ph in (("Private server link", "LAUNCH_URL_PRIVATE", "https://www.roblox.com/share?code=…"),
                                ("Custom link or place ID", "LAUNCH_URL_CUSTOM", "a link, or just a place ID")):
            tk.Label(inner, text=title.upper(), bg=K.CARD, fg=K.FAINT, font=font(10, "semibold")).pack(anchor="w", pady=(10, 4))
            var = tk.StringVar(value=str(get(name)))
            eb = U.EntryBox(inner, var, w=366, h=36, theme=theme, size=12, placeholder=ph)
            eb.pack(anchor="w")

            def commit(_=None, n=name, v=var):
                if v.get().strip() != str(get(n)):
                    S._set_setting(n, v.get().strip())

            eb.entry.bind("<Return>", commit)
            eb.entry.bind("<FocusOut>", commit, add="+")
        lab("PACE BETWEEN ACCOUNTS")
        U.Seg(inner, [("Smart", 0.0), ("5 s", 5.0), ("10 s", 10.0), ("20 s", 20.0)], lambda: get("LAUNCH_DELAY"), lambda v: S._set_setting("LAUNCH_DELAY", v),
              theme, seg_w=88, h=34).pack(anchor="w")
        tk.Label(inner, text="Smart opens the next account as soon as the last one is in game.", bg=K.CARD, fg=K.FAINT, font=font(11)).pack(anchor="w", pady=(4, 0))
        lab("SEVERAL ROBLOX WINDOWS")
        r = tk.Frame(inner, bg=K.CARD)
        r.pack(fill="x")
        mp = U.Pill(r, "", K.FAINT, bg=K.CARD)
        mp.pack(side="left")
        U.Btn(r, "Restart Roblox for multi-instance", restart_multi, theme, icon="refresh-cw", h=34, size=12, bg=K.CARD).pack(side="right")
        pop["pill"] = mp
        tk.Label(inner, text="One click closes every Roblox window, turns multi-instance on, and reopens the accounts that were open.", bg=K.CARD,
                 fg=K.FAINT, font=font(11), wraplength=366, justify="left").pack(anchor="w", pady=(6, 0))
        w.update_idletasks()
        ww, hh = w.winfo_reqwidth(), w.winfo_reqheight()
        x = min(opts_btn.winfo_rootx() + opts_btn.winfo_width() - ww, root.winfo_rootx() + C.W - ww - 10)
        w.geometry(f"{ww}x{hh}+{x}+{opts_btn.winfo_rooty() + opts_btn.winfo_height() + 8}")
        w.bind("<Escape>", close_options)
        w.focus_force()

        def outside(e):                                          # a click anywhere else closes it
            try:
                if pop["win"] is None or str(e.widget).startswith(str(pop["win"])):
                    return
            except tk.TclError:
                pass
            close_options()

        root.bind("<Button-1>", outside, add="+")

    # ================================================================== the settings panel that slides out
    drawer = tk.Frame(page, bg=K.EDGE_HI)
    di = tk.Frame(drawer, bg=K.CARD)
    di.pack(fill="both", expand=True, padx=1, pady=1)
    IW = DRAWER_W - 2 - 36
    dhead = tk.Canvas(di, width=DRAWER_W - 2, height=96, bg=K.CARD, highlightthickness=0, bd=0)
    dhead.pack(anchor="w")
    close_x = U.IconBtn(dhead, "x", lambda: close_drawer(), theme, size=34, kind="ghost", bg=K.CARD, tip="Close", px=16)
    dhead.create_window(DRAWER_W - 2 - 16, 28, anchor="ne", window=close_x)
    tabs = U.Seg(di, [("Settings", "settings"), ("History", "history")], lambda: st["tab"], lambda v: (st.update(tab=v), show_tab()), theme,
                 seg_w=104, h=32, size=12)
    tabs.pack(anchor="w", padx=18, pady=(0, 8))
    pane = tk.Frame(di, bg=K.CARD)
    pane.pack(fill="both", expand=True, padx=18)
    foot = tk.Frame(di, bg=K.CARD)
    foot.pack(fill="x", padx=18, pady=(8, 16))
    PH = BODY_H - 96 - 48 - 70 - 8

    # ---- the settings form (scrollable)
    form_sf = U.ScrollFrame(pane, IW, PH, bg=K.CARD, theme=theme)
    ctx.scrollers.append(form_sf)
    f = form_sf.inner
    FW = IW - 12
    alias_var, group_var, url_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
    fv = {"browser": "edge" if browser_exe("edge") else "chrome", "profile": "", "target": "", "low": False, "fps": 20, "quality": 2, "color": "", "ram": 0}
    prof_choices = {}

    def lab(parent, text):
        tk.Label(parent, text=text, bg=K.CARD, fg=K.FAINT, font=font(10, "semibold")).pack(anchor="w")

    def field_row():
        r = tk.Frame(f, bg=K.CARD)
        r.pack(fill="x", pady=(0, 10))
        return r

    r1 = field_row()
    c = tk.Frame(r1, bg=K.CARD)
    c.pack(side="left")
    lab(c, "ALIAS")
    U.EntryBox(c, alias_var, w=FW - 170 - 10, h=36, theme=theme, size=12).pack(pady=(3, 0))
    c = tk.Frame(r1, bg=K.CARD)
    c.pack(side="left", padx=(10, 0))
    lab(c, "GROUP")
    U.EntryBox(c, group_var, w=170, h=36, theme=theme, size=12).pack(pady=(3, 0))
    r2 = field_row()
    c = tk.Frame(r2, bg=K.CARD)
    c.pack(side="left")
    lab(c, "BROWSER")
    kind_dd = U.Dropdown(c, lambda: [("Edge", "edge"), ("Chrome", "chrome")], lambda: fv["browser"],
                         lambda v: (fv.update(browser=v), fill_profiles()), theme, w=120, h=36, size=12)
    kind_dd.pack(pady=(3, 0))
    c = tk.Frame(r2, bg=K.CARD)
    c.pack(side="left", padx=(10, 0))
    lab(c, "BROWSER PROFILE")
    prof_dd = U.Dropdown(c, lambda: [(f"{n_}  ({d_})", d_) for d_, n_ in browser_profiles(fv["browser"])] or [("no profiles found", "")],
                         lambda: fv["profile"], lambda v: fv.update(profile=v), theme, w=FW - 130, h=36, size=12)
    prof_dd.pack(pady=(3, 0))
    r3 = field_row()
    c = tk.Frame(r3, bg=K.CARD)
    c.pack(side="left")
    lab(c, "JOINS")
    tgt_dd = U.Dropdown(c, lambda: [("Default link", ""), ("Game page", "game"), ("Private server", "private"), ("Custom link", "custom")],
                        lambda: fv["target"], lambda v: (fv.update(target=v), sync_target()), theme, w=170, h=36, size=12, icon="link")
    tgt_dd.pack(pady=(3, 0))
    c = tk.Frame(r3, bg=K.CARD)
    c.pack(side="left", padx=(10, 0))
    lab(c, "LINK OR PLACE ID")
    url_box = U.EntryBox(c, url_var, w=FW - 180, h=36, theme=theme, size=12, placeholder="only for a custom link")
    url_box.pack(pady=(3, 0))

    def sync_target():
        url_box.entry.config(state="normal" if fv["target"] == "custom" else "disabled")
        tgt_dd.refresh(force=True)

    r4 = tk.Frame(f, bg=K.CARD2, highlightbackground=K.EDGE, highlightthickness=1)
    r4.pack(fill="x", pady=(0, 10))
    r4a = tk.Frame(r4, bg=K.CARD2)
    r4a.pack(fill="x", padx=12, pady=(10, 4))
    low_sw = U.Switch(r4a, lambda v: (fv.update(low=v), sync_low()), theme, bg=K.CARD2)
    low_sw.pack(side="left")
    tk.Label(r4a, text="Low-resource launch", bg=K.CARD2, fg=K.TEXT, font=font(13, "semibold")).pack(side="left", padx=(10, 0))
    low_note = tk.Label(r4, text="", bg=K.CARD2, fg=K.FAINT, font=font(11), wraplength=FW - 40, justify="left", anchor="w")
    low_note.pack(fill="x", padx=12)
    r4b = tk.Frame(r4, bg=K.CARD2)
    r4b.pack(fill="x", padx=12, pady=(8, 10))
    tk.Label(r4b, text="FPS", bg=K.CARD2, fg=K.FAINT, font=font(10, "semibold")).pack(side="left", padx=(0, 8))
    fps_seg = U.Seg(r4b, [("15", 15), ("20", 20), ("30", 30), ("60", 60)], lambda: fv["fps"], lambda v: (fv.update(fps=v), fps_seg.sync()), theme,
                    seg_w=44, h=28, bg=K.CARD2, size=11)
    fps_seg.pack(side="left")
    tk.Label(r4b, text="GRAPHICS", bg=K.CARD2, fg=K.FAINT, font=font(10, "semibold")).pack(side="left", padx=(16, 8))
    q_seg = U.Seg(r4b, [("1", 1), ("2", 2), ("3", 3), ("5", 5)], lambda: fv["quality"], lambda v: (fv.update(quality=v), q_seg.sync()), theme,
                  seg_w=36, h=28, bg=K.CARD2, size=11)
    q_seg.pack(side="left")

    def sync_low():
        low_sw.set(bool(fv["low"]))
        locked = S._rbx_locked()
        bad = locked and fv["low"]
        low_note.config(text=("Roblox's settings file is read-only, so Gnomewatch leaves it alone: this won't take effect until it's unlocked."
                              if bad else "Opens this account with a lower FPS cap and graphics level, so extra accounts stay light."),
                        fg=K.WARN if bad else K.FAINT)
        fps_seg.sync()
        q_seg.sync()

    r4c = field_row()
    lab(r4c, "MEMORY CAP")
    ram_seg = U.Seg(r4c, [("Off", 0), ("1.5 GB", 1536), ("2 GB", 2048), ("3 GB", 3072), ("4 GB", 4096)], lambda: fv["ram"],
                    lambda v: (fv.update(ram=v), ram_seg.sync()), theme, seg_w=66, h=30, size=11)
    ram_seg.pack(anchor="w", pady=(4, 0))
    tk.Label(r4c, text="Over the cap, Gnomewatch hands this account's idle memory back to Windows, and warns you if it stays over. Nothing is closed.",
             bg=K.CARD, fg=K.FAINT, font=font(11), wraplength=FW - 8, justify="left", anchor="w").pack(fill="x", pady=(4, 0))

    r5 = field_row()
    lab(r5, "COLOUR")
    swrow = tk.Frame(r5, bg=K.CARD)
    swrow.pack(anchor="w", pady=(4, 0))
    swatches = []
    for col in PALETTE:
        cv = tk.Canvas(swrow, width=34, height=34, bg=K.CARD, highlightthickness=0, bd=0, cursor="hand2")
        ring = cv.create_image(17, 17)
        cv.create_image(17, 17, image=U.circle_photo(22, col))
        cv.bind("<ButtonRelease-1>", lambda e, c=col: (fv.update(color=c), sync_color()))
        cv.pack(side="left", padx=(0, 4))
        swatches.append((cv, ring, col))

    def sync_color():
        cur = fv["color"] or colour_of({"label": alias_var.get()})
        for cv, ring, col in swatches:
            cv.itemconfig(ring, image=U.circle_photo(32, None, K.TEXT, 2) if col == cur else "")

    r6 = field_row()
    lab(r6, "NOTES")
    notes = U.TextBox(r6, w=FW, h=58, theme=theme, size=12)
    notes.pack(pady=(3, 0))
    notes_txt = notes.text
    tk.Frame(f, bg=K.CARD, height=8).pack()

    def fill_profiles():
        profs = browser_profiles(fv["browser"])
        prof_choices.clear()
        prof_choices.update({d_: n_ for d_, n_ in profs})
        if fv["profile"] not in prof_choices:
            fv["profile"] = next(iter(prof_choices), "")
        prof_dd.refresh(force=True)
        kind_dd.refresh(force=True)

    def load_form(a):
        alias_var.set(a["label"] if a else "")
        group_var.set(a.get("group", "") if a else "")
        fv.update(browser=a["browser"] if a else fv["browser"], target=(a or {}).get("target", ""), color=(a or {}).get("color", ""))
        lr = (a or {}).get("lowres") or {}
        fv.update(low=bool(lr.get("on")), fps=lr.get("fps", 20), quality=lr.get("quality", 2), ram=int((a or {}).get("ram_cap") or 0))
        url_var.set((a or {}).get("url", ""))
        fv["profile"] = (a or {}).get("profile", "")
        fill_profiles()
        notes_txt.delete("1.0", "end")
        notes_txt.insert("1.0", (a or {}).get("notes", ""))
        sync_target()
        sync_low()
        sync_color()
        ram_seg.sync()

    def save_form():
        nm, d_ = alias_var.get().strip(), fv["profile"]
        if not nm or not d_:
            note("Give it an alias and pick a browser profile")
            return
        lst = load_accounts()
        old = st["edit"] if not st["new"] else None
        if nm != old and any(x["label"] == nm for x in lst):
            note(f"There is already an account called {nm}")
            return
        rec = {"label": nm, "browser": fv["browser"], "profile": d_, "group": group_var.get().strip(), "notes": notes_txt.get("1.0", "end").strip()}
        if fv["target"]:
            rec["target"] = fv["target"]
            if fv["target"] == "custom":
                rec["url"] = url_var.get().strip()
        if fv["low"]:
            rec["lowres"] = {"on": True, "fps": fv["fps"], "quality": fv["quality"]}
        if fv["color"]:
            rec["color"] = fv["color"]
        if fv["ram"]:
            rec["ram_cap"] = fv["ram"]
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
        st.update(edit=nm, new=False, form_for=nm, sig=None, cards_sig=None)
        note(f"Saved {nm}")
        draw_dhead()

    def remove_account():
        gone = st["edit"]
        if not gone:
            return
        jsave("accounts.json", [x for x in load_accounts() if x["label"] != gone])
        for h_ in [h for h, l_ in assign.items() if l_ == gone]:
            assign.pop(h_, None)
        jsave("assign.json", {str(k): v for k, v in assign.items()})
        st["checked"].discard(gone)
        st.update(sig=None, cards_sig=None)
        note(f"Removed {gone}")
        close_drawer()

    U.Btn(foot, "Save account", save_form, theme, icon="save", kind="primary", h=38).pack(side="left")
    rm = ctx.confirm_btn(foot, "Remove", "Sure? Click again", remove_account, icon="trash-2", h=38)
    rm.pack(side="left", padx=(8, 0))
    U.Btn(foot, "Later", lambda: move_account(st["edit"], 1), theme, kind="ghost", h=38, size=12).pack(side="right")
    U.Btn(foot, "Earlier", lambda: move_account(st["edit"], -1), theme, kind="ghost", h=38, size=12).pack(side="right")

    # ---- history tab
    hist = tk.Canvas(pane, width=IW, height=PH, bg=K.CARD, highlightthickness=0, bd=0)

    def draw_history():
        a = st["edit"]
        rows = S._sessions_load(a, 14) if a and not st["new"] else []
        sig = (a, tuple((r["t"], r["kind"]) for r in rows), theme.cur, int(time.time() // 30))
        if sig == st["hist_sig"]:
            return
        st["hist_sig"] = sig
        hist.delete("all")
        if not rows:
            hist.create_image(IW / 2, 70, image=U.icon("history", 34, K.FAINT))
            hist.create_text(IW / 2, 112, text="Nothing yet", fill=K.TEXT, font=font(14, "semibold"))
            hist.create_text(IW / 2, 144, text="Launches, joins, disconnects (with the reason), rejoins and\nauto-place results appear here.",
                             fill=K.DIM, font=font(12), justify="center")
            return
        for i, r in enumerate(rows):
            y = i * 34
            col = getattr(K, KIND_COL.get(r["kind"], "DIM"))
            hist.create_image(6, y + 17, anchor="w", image=U.icon(KIND_ICON.get(r["kind"], "activity"), 16, col))
            hist.create_text(34, y + 17, anchor="w", text=r["kind"].replace("-", " ").title(), fill=K.TEXT, font=font(12, "semibold"))
            hist.create_text(124, y + 17, anchor="w", text=U.ellipsize(r["detail"], font(12), IW - 124 - 90), fill=K.DIM, font=font(12))
            hist.create_text(IW - 4, y + 17, anchor="e", text=ago(time.time() - r["t"]), fill=K.FAINT, font=font(11, "medium"))
            hist.create_line(6, y + 33, IW - 4, y + 33, fill=lerp(K.CARD, K.EDGE, .6))

    def show_tab():
        tabs.sync()
        form_sf.pack_forget()
        hist.pack_forget()
        if st["tab"] == "history":
            st["hist_sig"] = None
            hist.pack(anchor="w")
            draw_history()
        else:
            form_sf.pack(anchor="w")

    # ---- the panel's header: avatar, name, state, and which window belongs to it
    win_dd = U.Dropdown(dhead, lambda: [(f"{(t or 'Roblox')[:14]} ({'board' if r == 'board' else 'other'}) #{h % 10000}", h)
                                        for h, r, t, hd, mn in list(S._snapshot) if h not in assign],
                        lambda: None, lambda h: (set_assign(h, st["edit"]) if st["edit"] else None, st.update(sig=None)), theme, w=170, h=30,
                        size=11, placeholder="Assign a window…", icon="link", bg=K.CARD)
    win_item = dhead.create_window(DRAWER_W - 2 - 16, 74, anchor="e", window=win_dd, state="hidden")

    def draw_dhead():
        dhead.delete("hd")
        a = None if st["new"] else find(st["edit"]) if st["edit"] else None
        dhead.itemconfig(win_item, state="hidden")
        if st["new"] or a is None:
            dhead.create_image(46, 48, image=U.circle_photo(54, lerp(K.CARD, theme.cur, .2), theme.cur, 2), tags="hd")
            dhead.create_image(46, 48, image=U.icon("user-plus", 24, theme.cur), tags="hd")
            dhead.create_text(88, 40, anchor="w", text="New account", fill=K.TEXT, font=font(19, "bold"), tags="hd")
            dhead.create_text(88, 64, anchor="w", text="Pick the browser profile that's signed in to Roblox.", fill=K.DIM, font=font(12), tags="hd")
            return
        col = colour_of(a)
        lab_, col_, rss, info, wins = status_of(a)
        dhead.create_image(46, 48, image=U.circle_photo(54, lerp(K.CARD, col, .22), col, 2), tags="hd")
        dhead.create_text(46, 48, text=initials(a["label"]), fill=col, font=font(18, "bold"), tags="hd")
        dhead.create_text(88, 36, anchor="w", text=U.ellipsize(a["label"], font(19, "bold"), DRAWER_W - 88 - 70), fill=K.TEXT, font=font(19, "bold"), tags="hd")
        bw = U.text_w(lab_, font(10, "bold")) + 24
        dhead.create_image(88, 56, anchor="nw", image=U.rr_photo(bw, 22, 11, lerp(K.CARD, col_, .16), lerp(K.CARD, col_, .4)), tags="hd")
        dhead.create_text(88 + bw / 2, 67, text=lab_, fill=col_, font=font(10, "bold"), tags="hd")
        if not wins:
            win_dd.refresh(force=True)
            dhead.itemconfig(win_item, state="normal")

    def slide(x0, x1, done=None):
        steps = 9 if K.M.get("btn_fade", True) else 1

        def step(i=1):
            t = i / steps
            e = 1 - (1 - t) ** 3
            try:
                drawer.place(x=int(x0 + (x1 - x0) * e), y=0)
            except tk.TclError:
                return
            if i < steps:
                root.after(14, step, i + 1)
            elif done:
                done()

        step()

    def open_drawer(label_, new=False):
        st.update(edit=None if new else label_, new=new, form_for=None, tab="settings")
        a = None if new else find(label_)
        load_form(a)
        draw_dhead()
        show_tab()
        refresh_all()
        drawer.config(width=DRAWER_W, height=BODY_H)
        drawer.lift()
        if not st["drawer_open"]:
            st["drawer_open"] = True
            drawer.place(x=CW, y=0, width=DRAWER_W, height=BODY_H)
            slide(CW, CW - DRAWER_W + 6)
        else:
            drawer.place(x=CW - DRAWER_W + 6, y=0, width=DRAWER_W, height=BODY_H)

    def close_drawer():
        if not st["drawer_open"]:
            return
        st["drawer_open"] = False
        st.update(edit=None, new=False)

        def gone():
            drawer.place_forget()

        slide(CW - DRAWER_W + 6, CW, gone)
        refresh_all()

    ctx.open_account = lambda label_: open_drawer(label_)

    # ================================================================== header and refresh
    def update_header():
        accts = load_accounts()
        n = len(accts)
        running = sum(1 for a in accts if account_windows(a["label"]))
        h_text = f"{n} account{'s' if n != 1 else ''}"
        ctx.ic(hdr, h_count, text=h_text)
        ctx.ic(hdr, h_sub, text=(f"{running} in Roblox now" if running else "none running") if n else "Add one to get started")
        lock_txt = {"held": ("MULTI-INSTANCE ON", K.OK), "waiting": ("CLOSE ROBLOX FIRST", K.WARN),
                    "off": ("MULTI-INSTANCE OFF", K.FAINT), "failed": ("MULTI-INSTANCE ERROR", K.BAD)}.get(S.STATE["lock"], ("MULTI-INSTANCE OFF", K.FAINT))
        multi_pill.set(*lock_txt)
        if pop["win"] is not None and pop.get("pill"):
            try:
                pop["pill"].set(*lock_txt)
            except tk.TclError:
                pass
        lq = S._lq
        now = time.time()
        if st["note"] != note_seen["text"]:
            note_seen.update(text=st["note"], t=now)
        show_prog, show_cancel = False, False
        if lq["running"]:
            items = lq["items"]
            done = sum(1 for it in items if it["state"] == "in game")
            part = sum(0.5 for it in items if it["state"] in ("loading", "waiting for window"))
            frac = (done + part) / max(1, len(items))
            msg, sub, col, icn = lq["msg"] or "Launching…", f"{done} of {len(items)} in game", theme.cur, "rocket"
            show_prog = show_cancel = True
            prog.set(frac, theme.cur)
        elif lq["done_msg"] and now - lq["done_t"] < 14:
            ok_ = " - " not in lq["done_msg"]
            msg, sub, col, icn = lq["done_msg"], "", K.OK if ok_ else K.WARN, "circle-check" if ok_ else "triangle-alert"
        elif st["note"] and now - note_seen["t"] < 8:
            msg, sub, col, icn = st["note"], "", K.WARN, "info"
        else:
            msg, sub, col, icn = "", "", K.DIM, ""
        idle = not msg and bool(accts)
        if idle:
            msg, icn = "Play opens one. Launch all opens them in turn.", "info"
        ctx.ic(hdr, h_icon, image=U.icon(icn, 22, K.FAINT if idle else col) if icn else "")
        ctx.ic(hdr, h_msg, text=U.ellipsize(msg, font(14, "semibold"), 330), fill=K.FAINT if idle else K.TEXT)
        ctx.ic(hdr, h_prog_txt, text=sub if show_prog else "")
        hdr.itemconfig(h_prog, state="normal" if show_prog else "hidden")
        hdr.itemconfig(h_cancel, state="normal" if show_cancel else "hidden")
        launch_all_btn.set(disabled=bool(lq["running"]))
        # the selection bar
        k = len(st["checked"])
        if k and not selbar.winfo_ismapped():
            selbar.pack(side="right")
        elif not k and selbar.winfo_ismapped():
            selbar.pack_forget()
        if k:
            sel_lbl.config(text=f"{k} selected")

    def refresh_all(force=False):
        names = [x["label"] for x in load_accounts()]
        st["checked"] &= set(names)
        if st["edit"] and st["edit"] not in names and not st["new"] and st["drawer_open"]:
            close_drawer()
        accts = ensure_cards()
        for a in accts:
            ui = st["cards"].get(a["label"])
            if ui:
                refresh_card(ui, a)
        group_dd.refresh()
        update_header()
        if st["drawer_open"]:
            draw_dhead()
            if st["tab"] == "history":
                draw_history()

    show_tab()

    class Page:
        def slow(self):
            alive = tuple(sorted(e[0] for e in S._snapshot))
            sig = (json.dumps(load_accounts(), sort_keys=True), tuple(sorted(assign.items())), alive, st["group"], st["q"], int(time.time() // 3),
                   tuple(sorted((p, int(i.get("rss", 0) // 50_000_000), i.get("state")) for p, i in S._clients.items())),
                   tuple((it.get("label"), it.get("state")) for it in S._lq.get("items", [])), S._lq.get("running"), tuple(sorted(st["checked"])))
            if sig != st["sig"]:
                st["sig"] = sig
                refresh_all()
            update_header()

        def on_show(self):
            st["sig"] = None
            sf.to_top()
            refresh_all()

    refresh_all()
    return Page()
