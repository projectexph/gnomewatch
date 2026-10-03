"""Settings page: two columns of section cards. Every row = title + note on the left, its control on the right."""
import json
import os
import threading
import time
import tkinter as tk
from tkinter import filedialog

from PIL import Image, ImageDraw

import ui_kit as U
from ui_kit import K, font, lerp
import cs_common as C
from cs_common import CW, BODY_H, SCROLL_W, ago, same

COLW = (SCROLL_W - 16) // 2                                 # 440
INNER = COLW - 40                                           # 400


def build(ctx):
    S, root, theme, get = ctx.S, ctx.root, ctx.theme, ctx.get
    page = ctx.pages["Settings"]
    ask, jload, jsave = ctx.ask, ctx.jload, ctx.jsave
    syncers = []

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

    # ================================================================== building blocks
    def section(parent, title, icon):
        c = U.AutoCard(parent, COLW, title=title, icon=icon)
        c.pack(pady=(0, 16))
        c.inner._rows = 0
        return c.inner

    def rowframe(parent, pady=10):
        """A row with a hairline above it (except the first one)."""
        if getattr(parent, "_rows", 0):
            tk.Frame(parent, bg=K.EDGE, height=1).pack(fill="x")
        parent._rows = getattr(parent, "_rows", 0) + 1
        r = tk.Frame(parent, bg=K.CARD)
        r.pack(fill="x", pady=pady)
        return r

    def text_col(r, title, note=None, wrap=270, side="left"):
        col = tk.Frame(r, bg=K.CARD)
        col.pack(side=side, anchor="w")
        tk.Label(col, text=title, bg=K.CARD, fg=K.TEXT, font=font(14, "medium"), anchor="w").pack(anchor="w")
        if note:
            tk.Label(col, text=note, bg=K.CARD, fg=K.FAINT, font=font(12), anchor="w", justify="left", wraplength=wrap).pack(anchor="w")
        return col

    def switch_row(parent, title, name, note=None):
        r = rowframe(parent)
        text_col(r, title, note)
        sw = U.Switch(r, lambda v, n=name: S._set_setting(n, v), theme)
        sw.pack(side="right")
        syncers.append(lambda: sw.set(bool(get(name))))

    def seg_row(parent, title, note, choices, name, w=64):
        r = rowframe(parent, pady=12)
        text_col(r, title, note, wrap=INNER, side="top")
        sg = U.Seg(r, choices, lambda: get(name), lambda v: S._set_setting(name, v), theme, seg_w=w, h=32)
        sg.pack(anchor="w", pady=(8, 0))
        syncers.append(sg.sync)

    def entry_row(parent, title, name, w=200):
        r = rowframe(parent)
        tk.Label(r, text=title, bg=K.CARD, fg=K.TEXT, font=font(14, "medium")).pack(side="left")
        var = tk.StringVar(value=str(get(name)))
        e = U.EntryBox(r, var, w=w, h=34, theme=theme, size=12)
        e.pack(side="right")

        def commit(_=None):
            if var.get().strip() != str(get(name)):
                S._set_setting(name, var.get().strip())

        e.entry.bind("<Return>", commit)
        e.entry.bind("<FocusOut>", commit, add="+")

        def sync_entry():
            try:
                typing = root.focus_get() is e.entry
            except KeyError:
                typing = False
            if not typing and var.get() != str(get(name)):
                var.set(str(get(name)))

        syncers.append(sync_entry)

    def para(parent, text):
        tk.Label(parent, text=text, bg=K.CARD, fg=K.FAINT, font=font(12), justify="left", anchor="w", wraplength=INNER).pack(anchor="w", pady=(0, 6))

    # ================================================================== HEALTH CHECK
    # ================================================================== UPDATES
    import gw_update as UP
    from gw_version import VERSION
    ud = section(colL, "Updates", "download")
    ur = rowframe(ud)
    ucol = text_col(ur, f"Gnomewatch {VERSION}", "You're up to date.", wrap=250)
    ulbl = ucol.winfo_children()[1]
    ubtn = U.Btn(ur, "Update now", lambda: ctx.start_update(), theme, icon="download", kind="primary", h=36, size=12, bg=K.CARD)

    def sync_update():
        upd = (S.GW_SESSION or {}).get("update")
        st = UP.STATE
        busy = st["phase"] in ("download", "verify", "launch", "done") and UP.packaged()
        if busy:
            txt, col = st["msg"] + (f" {st['pct']}%" if st["phase"] == "download" and st["pct"] else ""), K.WARN
        elif st["phase"] == "error":
            txt, col = st["msg"], K.BAD
        elif UP.usable(upd):
            txt, col = f"Version {upd['latest']} is available." + (f" {upd['notes']}" if upd.get("notes") else ""), K.OK
        else:
            txt, col = "You're up to date.", K.FAINT
        if ulbl.cget("text") != txt or ulbl.cget("fg") != col:
            ulbl.config(text=txt, fg=col)
        show = UP.usable(upd) or st["phase"] == "error"
        if show and not ubtn.winfo_ismapped():
            ubtn.pack(side="right")
        elif not show and ubtn.winfo_ismapped():
            ubtn.pack_forget()
        if show:
            ubtn.set("Try again" if st["phase"] == "error" else "Update now", disabled=busy)

    syncers.append(sync_update)

    hc = section(colL, "Health check", "heart-pulse")
    hc_top = tk.Frame(hc, bg=K.CARD)
    hc_top.pack(fill="x")
    tk.Label(hc_top, text="Finds what's wrong with launching, links, your key and reading, and offers a fix.", bg=K.CARD, fg=K.FAINT, font=font(12),
             wraplength=250, justify="left").pack(side="left")
    hc_rows = tk.Frame(hc, bg=K.CARD)
    hc_rows.pack(fill="x", pady=(6, 0))

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

    net = {"t": 0.0, "ok": None, "msg": ""}                 # is the key server reachable (looked at in the background, never blocks)

    def probe_server():
        import urllib.request
        import gw_license as L
        api = L.config(S.HERE).get("api")
        if not api:
            net.update(ok=False, msg="no key server address is set (gw_config.json)")
            return
        try:
            urllib.request.urlopen(urllib.request.Request(api + "/", headers={"User-Agent": "Gnomewatch"}), timeout=4, context=L._ssl()).read(200)
            net.update(ok=True, msg="answering")
        except Exception as e:
            net.update(ok=False, msg=f"can't reach it ({str(e)[:40]})")
        root.after(0, run_checks)

    def use_game_link():
        S._set_setting("LAUNCH_TARGET", "game")
        S._set_setting("LAUNCH_URL_GAME", "https://www.roblox.com/games/%s" % json.load(open(os.path.join(S.HERE, "assets", "game.json"), encoding="utf-8"))["placeId"])

    def launch_checks(res):
        """Why launching might not work: sign-in, key server, launch link, browsers, each account's browser profile, Roblox itself."""
        sess = S.GW_SESSION or {}
        left = S._license_left()
        if S.LOCKED.is_set():
            res.append((False, "Your key", "it ended, so Gnomewatch is paused", ("Sign in", lambda: ctx.show_lock())))
        elif sess:
            res.append((True, "Your key", ("no expiry (admin)" if left is None else f"{int(left // 3600)}h {int(left % 3600 // 60)}m left")
                        + f" · signed in as {sess.get('name', '?')}", None))
        if time.time() - net["t"] > 30:
            net["t"] = time.time()
            threading.Thread(target=probe_server, daemon=True).start()
        res.append((net["ok"], "Key server", net["msg"] or "checking…", None))
        url = S._launch_url()
        if url:
            shown = url.replace("https://", "").replace("www.", "")
            res.append((True, "Launch link", shown[:48] + ("…" if len(shown) > 48 else ""), None))
        else:
            res.append((False, "Launch link", "none set, so launching can't start anything", ("Use Roll A Gnome", use_game_link)))
        accts = ctx.load_accounts()
        kinds = sorted({a.get("browser", "edge") for a in accts}) or ["edge"]
        for k_ in kinds:
            have = C.browser_exe(k_) is not None
            res.append((have, "Edge" if k_ == "edge" else "Chrome", "found" if have else "not installed - accounts that use it can't launch", None))
        missing = 0
        for a in accts:
            k_, p_ = a.get("browser", "edge"), a.get("profile", "")
            profs = dict(C.browser_profiles(k_))
            if profs and p_ not in profs:
                missing += 1
                res.append((False, f"Profile for {a['label']}", f"'{p_}' isn't in {k_.title()} any more - pick another on the Accounts page", None))
        if accts and not missing:
            res.append((True, "Account profiles", f"all {len(accts)} found in their browsers", None))
        rb = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Roblox", "Versions")
        res.append((os.path.isdir(rb), "Roblox installed", "found" if os.path.isdir(rb) else "no Roblox install found for this Windows user", None))
        if S._rbx_locked():
            res.append((None, "Roblox settings file", "read-only, so low-resource launches are skipped (Gnomewatch never overrides that)", None))
        if S.MULTI_INSTANCE:
            lk = S.STATE.get("lock")
            res.append((True if lk == "held" else None if lk == "waiting" else False, "Several Roblox windows",
                        {"held": "holding the lock", "waiting": "Roblox already owns it: close ALL Roblox windows once", "failed": "couldn't take the lock"}.get(lk, "starting…"), None))
        over = [f"{l_} ({r_['mb'] / 1000:.1f} GB)" for l_, r_ in S.RAMCAP.items() if r_.get("over")]
        if S.RAMCAP:
            res.append((None if over else True, "Memory caps", ("over: " + ", ".join(over)) if over else "everyone is under their cap", None))

    def run_checks():
        for ch in hc_rows.winfo_children():
            ch.destroy()
        snap = list(S._snapshot)
        live = [e for e in snap if not e[4]]
        now_ = time.time()
        res = []                                          # (state True/False/None, title, detail, (fix label, fn))
        launch_checks(res)
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
                    f"seen {ago(now_ - bs)}" if bs else "not seen yet: put the sign on screen", None))
        wk = S.STATE["worker"]
        res.append((wk["ok"], "Bot link", "connected" if wk["ok"] else (wk["msg"] or "unreachable") if wk["ok"] is False
                    else "no report sent yet", None))
        try:
            size = os.path.getsize(S.LOG_PATH) / 1e6
        except OSError:
            size = 0
        res.append((size < 15, "Log file size", f"{size:.1f} MB", None if size < 15 else ("Trim log", trim_log)))
        for i, (state, ttl, detail, fix) in enumerate(res):
            if i:
                tk.Frame(hc_rows, bg=K.EDGE, height=1).pack(fill="x")
            r_ = tk.Frame(hc_rows, bg=K.CARD)
            r_.pack(fill="x", pady=8)
            icn, col = ("circle-check", K.OK) if state else ("circle-x", K.BAD) if state is False else ("triangle-alert", K.WARN)
            tk.Label(r_, image=U.icon(icn, 20, col), bg=K.CARD, bd=0).pack(side="left", padx=(0, 12))
            col_ = tk.Frame(r_, bg=K.CARD)
            col_.pack(side="left")
            tk.Label(col_, text=ttl, bg=K.CARD, fg=K.TEXT, font=font(13, "medium")).pack(anchor="w")
            tk.Label(col_, text=detail, bg=K.CARD, fg=K.FAINT, font=font(12), wraplength=170 if fix else 330, justify="left", anchor="w").pack(anchor="w")
            if fix:
                U.Btn(r_, fix[0], lambda f=fix[1]: (f(), root.after(600, run_checks)), theme, kind="primary", h=30, size=12,
                      bg=K.CARD).pack(side="right")

    ctx.run_health = run_checks
    U.Btn(hc_top, "Run checks", run_checks, theme, icon="play", kind="primary", h=34, size=12, bg=K.CARD).pack(side="right")
    gd = tk.Frame(hc, bg=K.CARD)
    gd.pack(fill="x", pady=(8, 2))
    U.Btn(gd, "Setup guide", lambda: ctx.open_wizard(), theme, icon="rocket", h=34, size=12, bg=K.CARD).pack(side="left")
    U.Btn(gd, "Help", lambda: ctx.show("Help"), theme, icon="info", h=34, size=12, bg=K.CARD).pack(side="left", padx=(8, 0))

    # ================================================================== PERFORMANCE
    perf = section(colL, "Performance", "cpu")
    pr = rowframe(perf, pady=8)
    text_col(pr, "Preset", "One tap to a sensible set of speeds", wrap=INNER, side="top")
    pbar = tk.Frame(pr, bg=K.CARD)
    pbar.pack(anchor="w", pady=(8, 0))
    preset_btns = {}
    for nm in C.PRESETS:
        b = U.Btn(pbar, nm, lambda n=nm: [S._set_setting(k, v) for k, v in C.PRESETS[n].items()], theme, w=120, h=34, bg=K.CARD, size=12,
                  weight="medium", radius=8)
        b.pack(side="left", padx=(0, 6))
        preset_btns[nm] = b
    syncers.append(lambda: [preset_btns[n].set(kind="primary" if all(same(get(k), v) for k, v in C.PRESETS[n].items()) else "secondary")
                            for n in C.PRESETS])
    seg_row(perf, "Read speed", "How often it looks at the game", [("1 s", 1.0), ("2 s", 2.0), ("5 s", 5.0)], "POLL_SECONDS", 92)
    seg_row(perf, "Hidden Roblox may use", "CPU cores while parked (fewer = cooler)",
            [("All", 0), ("1", 1), ("2", 2), ("3", 3), ("4", 4)], "PARK_CORES", 60)
    switch_row(perf, "Low CPU for hidden Roblox", "THROTTLE_PARKED", "Lower priority and efficiency mode")
    switch_row(perf, "Slow down when board is away", "IDLE_BACKOFF", "Looks less often if it can't see the sign")

    # ================================================================== ALERTS
    al = section(colL, "Alerts", "bell")
    switch_row(al, "Alerts", "NOTIFY", "Master switch for this PC")
    switch_row(al, "Self-check alerts", "SELFCHECK", "Warn if Roblox closes or the bot link drops")
    switch_row(al, "Pop-up for problems", "SELFCHECK_TOAST", "Shown even when event alerts are off")
    switch_row(al, "Sound", "NOTIFY_SOUND")
    switch_row(al, "Pop-up notification", "NOTIFY_TOAST")
    switch_row(al, "“Next event” notice", "NOTIFY_NEXT", "Also alert when a new next event appears")
    rowframe(al, pady=2)
    tk.Label(al, text="Alert me for these events", bg=K.CARD, fg=K.TEXT, font=font(14, "medium")).pack(anchor="w", pady=(6, 4))
    evbox = tk.Frame(al, bg=K.CARD)
    evbox.pack(fill="x")
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
            _g, col, _e = S._event_style(n)
            rw = tk.Frame(evbox, bg=K.CARD)
            rw.pack(fill="x", pady=5)
            tk.Label(rw, image=U.rr_photo(34, 34, 9, lerp(K.CARD, col, .14), lerp(K.CARD, col, .32)), bg=K.CARD, bd=0).pack(side="left")
            tk.Label(rw, image=U.icon(U.event_icon(n), 18, col), bg=lerp(K.CARD, col, .14), bd=0).place(x=8, y=8)
            tk.Label(rw, text=n.title(), bg=K.CARD, fg=K.TEXT, font=font(14, "medium")).pack(side="left", padx=12)
            sw = U.Switch(rw, lambda v, k=n: S._set_alert(k, v), theme)
            sw.pack(side="right")
            sw.set(n not in S.ALERT_OFF)
            ev_state["sync"].append(lambda s_=sw, k=n: s_.set(k not in S.ALERT_OFF))

    # ================================================================== APPEARANCE
    ap = section(colR, "Appearance", "star")
    r_ = rowframe(ap, pady=8)
    text_col(r_, "Accent colour", "Follow the event, or lock one colour", wrap=INNER, side="top")
    swrow = tk.Frame(r_, bg=K.CARD)
    swrow.pack(anchor="w", pady=(10, 2))
    swatches = []

    def event_swatch(d):
        def make():
            ss = 4
            im = Image.new("RGBA", (d * ss, d * ss), (0, 0, 0, 0))
            dr = ImageDraw.Draw(im)
            for i, col_ in enumerate(("#5cffc4", "#8fb8ff", "#b06bff", "#ff5fa8", "#ff8a2b", "#8dff4d")):
                dr.pieslice([0, 0, d * ss - 1, d * ss - 1], start=i * 60, end=i * 60 + 60, fill=col_)
            mask = Image.new("L", (d * ss, d * ss), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, d * ss - 1, d * ss - 1], fill=255)
            im.putalpha(mask)
            return im.resize((d, d), Image.Resampling.LANCZOS)
        return U.photo(("evsw", d), make)

    for val in ("event", "#7c6cff", "#3dd6ff", "#5cffc4", "#ff5fa8", "#ffc83d", "#ff7a45"):
        cv = tk.Canvas(swrow, width=40, height=40, bg=K.CARD, highlightthickness=0, bd=0, cursor="hand2")
        ring = cv.create_image(20, 20)
        cv.create_image(20, 20, image=event_swatch(26) if val == "event" else U.circle_photo(26, val))
        cv.bind("<ButtonRelease-1>", lambda e, v=val: S._set_setting("ACCENT", v))
        cv.pack(side="left", padx=(0, 4))
        swatches.append((cv, ring, val))

    def sync_swatches():
        for cv, ring, v in swatches:
            cv.itemconfig(ring, image=U.circle_photo(38, None, K.TEXT, 2) if get("ACCENT") == v else "")

    syncers.append(sync_swatches)
    seg_row(ap, "Animations", "Motion in the console. Off uses no extra CPU.", [("Off", "off"), ("Low", "low"), ("Normal", "normal")], "ANIMATIONS", 92)

    # ================================================================== WINDOWS
    wn = section(colR, "Windows", "app-window")
    switch_row(wn, "Hide the board window", "HIDE_WINDOW")
    switch_row(wn, "Hide all instances", "HIDE_ALL", "Every Roblox window, not just the board's")
    switch_row(wn, "Clean up Roblox helpers", "CLEAN_HELPERS", "Closes window-less tray launchers")
    switch_row(wn, "Allow multiple Roblox", "MULTI_INSTANCE", "Bloxstrap-style: Gnomewatch owns Roblox's lock")
    gr = rowframe(wn)
    text_col(gr, "Arrange windows", "Tiles every Roblox window across your main screen; press again to put them back.", wrap=250)
    arr_btn = U.Btn(gr, "Arrange", lambda: threading.Thread(target=S._grid_toggle, daemon=True).start(), theme, icon="layout-grid", h=34, size=12, bg=K.CARD)
    arr_btn.pack(side="right")
    syncers.append(lambda: arr_btn.set("Restore" if S._grid["on"] else "Arrange", icon="rotate-cw" if S._grid["on"] else "layout-grid"))
    rs = rowframe(wn)
    text_col(rs, "Roblox's own settings", "Undo what low-resource launches changed (FPS cap, graphics level).", wrap=250)
    rs_msg = {"v": None}

    def restore_rbx():
        ok = S._rbx_restore()
        rs_btn.set("Restored" if ok else "Nothing to undo")
        root.after(3000, lambda: rs_btn.set("Restore"))

    rs_btn = U.Btn(rs, "Restore", restore_rbx, theme, icon="rotate-cw", h=34, size=12, bg=K.CARD)
    rs_btn.pack(side="right")
    entry_row(wn, "Board window", "WINDOW_TITLE")
    entry_row(wn, "Other windows", "OTHER_TITLE")
    ir = rowframe(wn)
    tk.Label(ir, text="Window icon", bg=K.CARD, fg=K.TEXT, font=font(14, "medium")).pack(side="left")
    IG, IN, IC = "gnome", "", "__file__"

    def icon_get():
        v = (S.ROBLOX_ICON or "")
        return IG if v.lower() == "gnome" else IN if not v else "__custom__"

    def icon_opts():
        o = [("Gnome (built-in)", IG), ("Roblox's own", IN)]
        if icon_get() == "__custom__":
            o.append((os.path.basename(S.ROBLOX_ICON), "__custom__"))
        return o + [("Choose a file…", IC)]

    def icon_pick(v):
        if v == IG:
            S._set_setting("ROBLOX_ICON", "gnome")
        elif v == IN:
            S._set_setting("ROBLOX_ICON", "")
        elif v == IC:
            p = filedialog.askopenfilename(parent=root, title="Choose an .ico file", filetypes=[("Icon", "*.ico")])
            if p:
                S._set_setting("ROBLOX_ICON", p)

    idd = U.Dropdown(ir, icon_opts, icon_get, icon_pick, theme, w=200, h=34, bg=K.CARD, size=12)
    idd.pack(side="right")
    syncers.append(idd.refresh)

    # ================================================================== STAY CONNECTED
    kp = section(colR, "Stay connected", "wifi")
    switch_row(kp, "Click to stay connected", "KEEPALIVE", "Clicks the middle of each Roblox window")
    seg_row(kp, "How often", None, [("5 min", 5.0), ("10 min", 10.0), ("15 min", 15.0)], "KEEPALIVE_MINUTES", 92)
    switch_row(kp, "Auto-reconnect", "AUTO_RECONNECT", "Presses Reconnect if Roblox disconnects")
    switch_row(kp, "Auto-rejoin after a crash", "AUTO_REJOIN",
               "Opens the account again if its Roblox window vanishes. Closing a window yourself with the X also counts; "
               "use Close in this console to avoid that.")

    # ================================================================== DISCORD WEBHOOK
    wh = section(colL, "Discord webhook", "send")
    para(wh, "Post failures, rejoins and disconnects to a Discord channel you choose. Make a webhook in the channel's settings "
             "(Integrations) and paste its link here.")
    wh_var = tk.StringVar(value=str(get("WEBHOOK_URL")))
    wh_box = U.EntryBox(wh, wh_var, w=INNER, h=36, theme=theme, size=12, show="•", placeholder="https://discord.com/api/webhooks/…")
    wh_box.pack(anchor="w")
    wh_msg = tk.Label(wh, text="", bg=K.CARD, fg=K.DIM, font=font(12), anchor="w", justify="left", wraplength=INNER)

    def wh_say(text, colour=None):
        wh_msg.config(text=text, fg=colour or K.DIM)

    def wh_commit(_=None):
        v = wh_var.get().strip()
        if v != str(get("WEBHOOK_URL")):
            S._set_setting("WEBHOOK_URL", v)
        if v and not S._hook_url_ok(v):
            wh_say("That doesn't look like a Discord webhook link.", K.WARN)
        elif v:
            wh_say("Link saved.", K.OK)
        else:
            wh_say("Webhook is off.")

    wh_box.entry.bind("<Return>", wh_commit)
    wh_box.entry.bind("<FocusOut>", wh_commit, add="+")

    def wh_test():
        wh_commit()
        wh_say("Sending…", K.WARN)

        def run():
            ok, msg = S._hook_test()
            root.after(0, lambda: wh_say("Test message sent. Check your channel." if ok else f"Couldn't send: {msg}", K.OK if ok else K.BAD))

        threading.Thread(target=run, daemon=True).start()

    wbar = tk.Frame(wh, bg=K.CARD)
    wbar.pack(fill="x", pady=(10, 4))
    U.Btn(wbar, "Send a test", wh_test, theme, icon="send", kind="primary", h=34, size=12, bg=K.CARD).pack(side="left")
    wh_msg.pack(fill="x", pady=(0, 6))
    switch_row(wh, "Failures", "WEBHOOK_FAILS", "Auto-place failed, gave up on an account")
    switch_row(wh, "Rejoins", "WEBHOOK_REJOINS", "An account's window vanished and was reopened")
    switch_row(wh, "Disconnects", "WEBHOOK_DISCONNECTS", "With the reason Roblox gives (lost connection, kicked…)")
    switch_row(wh, "Event starts", "WEBHOOK_EVENTS", "Every time a new event begins")
    seg_row(wh, "Board snapshot", "A picture of the weather board every so often", [("Off", 0.0), ("5 min", 5.0), ("15 min", 15.0), ("30 min", 30.0)],
            "WEBHOOK_SHOT_MIN", 72)

    # ================================================================== WEATHER BOT (optional)
    bt = section(colL, "Weather bot (optional)", "radio")
    para(bt, "Signed in, Gnomewatch already reports event starts through the Gnomewatch server, which tells the weather bot once. "
             "Only fill these in if you run a bot of your own and want to report to it directly.")
    bt_state = tk.Label(bt, text="", bg=K.CARD, fg=K.DIM, font=font(12), anchor="w")

    def bot_field(label_, name, secret=False):
        tk.Label(bt, text=label_, bg=K.CARD, fg=K.FAINT, font=font(10, "semibold")).pack(anchor="w", pady=(6, 0))
        var = tk.StringVar(value=str(get(name)))
        box = U.EntryBox(bt, var, w=INNER, h=36, theme=theme, size=12, show="•" if secret else None)
        box.pack(anchor="w", pady=(3, 0))

        def commit(_=None):
            if var.get().strip() != str(get(name)):
                S._set_setting(name, var.get().strip())
                bt_state.config(text="Reporting is on." if S._reporting() else "Reporting is off (needs both).", fg=K.OK if S._reporting() else K.DIM)

        box.entry.bind("<Return>", commit)
        box.entry.bind("<FocusOut>", commit, add="+")

    bot_field("BOT ADDRESS", "WORKER_URL")
    bot_field("BOT PASSWORD", "INGEST_SECRET", secret=True)
    bt_state.pack(fill="x", pady=(8, 4))
    bt_state.config(text="Reporting is on." if S._reporting() else "Reporting is off.", fg=K.OK if S._reporting() else K.DIM)

    # ================================================================== MEMORY
    mm = section(colL, "Memory", "memory-stick")
    para(mm, "Roblox uses 1 GB or more per window. Trimming hands the idle part of hidden windows back to Windows; nothing is closed.")
    switch_row(mm, "Trim background windows", "MEM_TRIM", "Hidden, parked or minimised windows only, never the one in front")
    seg_row(mm, "How often", None, [("5 min", 5.0), ("10 min", 10.0), ("30 min", 30.0)], "MEM_TRIM_MIN", 80)
    mrow = tk.Frame(mm, bg=K.CARD)
    mrow.pack(fill="x", pady=(8, 4))
    mem_msg = tk.Label(mm, text="", bg=K.CARD, fg=K.DIM, font=font(12), anchor="w", justify="left", wraplength=INNER)

    def free_now():
        mem_msg.config(text="Trimming…", fg=K.WARN)

        def run():
            n, mb = S._trim_background(force=True)
            root.after(0, lambda: mem_msg.config(text=f"Trimmed {n} window(s), freed about {mb:.0f} MB." if n else "Nothing to trim right now.",
                                                 fg=K.OK if n else K.DIM))

        threading.Thread(target=run, daemon=True).start()

    U.Btn(mrow, "Free memory now", free_now, theme, icon="trending-down" if False else "gauge", h=34, size=12, bg=K.CARD).pack(side="left")
    mem_msg.pack(fill="x", pady=(0, 6))

    # ================================================================== AUTO SCENES
    asn = section(colL, "Auto scenes", "moon")
    switch_row(asn, "Switch scenes by itself", "AUTO_SCENES", "Away scene when you leave, back scene when you return. "
                                                                 "The sentinel's own clicks don't count as you.")
    seg_row(asn, "After no activity for", None, [("5 min", 5.0), ("10 min", 10.0), ("20 min", 20.0), ("30 min", 30.0)], "AUTO_SCENES_MIN", 80)

    def scene_options():
        return [("(do nothing)", "")] + [(n, n) for n in ctx.load_scenes()]

    for title, name in (("While you're away", "AUTO_SCENE_AWAY"), ("When you're back", "AUTO_SCENE_BACK")):
        rw = rowframe(asn)
        tk.Label(rw, text=title, bg=K.CARD, fg=K.TEXT, font=font(14, "medium")).pack(side="left")
        dd = U.Dropdown(rw, scene_options, lambda n=name: get(n), lambda v, n=name: S._set_setting(n, v), theme, w=190, h=34,
                        bg=K.CARD, icon="layers", size=12)
        dd.pack(side="right")
        syncers.append(dd.refresh)

    # ================================================================== HOTKEYS
    hk = section(colR, "Hotkeys", "keyboard")

    def capture_hotkey(name, what):
        w = tk.Toplevel(root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=K.EDGE_HI)
        body = tk.Frame(w, bg=K.CARD)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(body, image=U.icon("keyboard", 28, theme.cur), bg=K.CARD, bd=0).pack(padx=40, pady=(26, 8))
        tk.Label(body, text=f"Press the keys for “{what}”", bg=K.CARD, fg=K.TEXT, font=font(16, "bold")).pack(padx=40)
        msg = tk.Label(body, text="Hold Ctrl / Shift / Alt, then press a key.   Esc cancels.", bg=K.CARD, fg=K.DIM, font=font(12))
        msg.pack(padx=40, pady=(6, 26))
        w.update_idletasks()
        ww, wh = w.winfo_reqwidth(), w.winfo_reqheight()
        w.geometry(f"{ww}x{wh}+{root.winfo_rootx() + (1180 - ww) // 2}+{root.winfo_rooty() + (760 - wh) // 2}")
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
                msg.config(text="Add Ctrl, Shift or Alt too – a bare key would fire while you type", fg=K.WARN)
                return
            key = ks if (len(ks) == 1 or (ks.startswith("f") and ks[1:].isdigit())) else None
            if key is None:
                msg.config(text="Use a letter, a number or an F-key", fg=K.WARN)
                return
            w.destroy()
            S._set_setting(name, "+".join(mods + [key]))

        w.bind("<KeyPress>", on_key)

    for text, name in (("Show / hide Roblox", "HOTKEY"), ("Show / hide this window", "PANEL_HOTKEY"),
                       ("Command palette", "PALETTE_HOTKEY"), ("Arrange windows in a grid", "GRID_HOTKEY"), ("Panic: hide everything", "PANIC_HOTKEY")):
        rw = rowframe(hk, pady=9)
        tk.Label(rw, text=text, bg=K.CARD, fg=K.TEXT, font=font(14, "medium")).pack(side="left")
        if name == "PALETTE_HOTKEY":
            U.IconBtn(rw, "x", lambda: S._set_setting("PALETTE_HOTKEY", ""), theme, size=34, kind="ghost", bg=K.CARD,
                      tip="Turn the global shortcut off", px=16).pack(side="right", padx=(6, 0))
        b = U.Btn(rw, (str(get(name)) or "off").upper().replace("+", " + "), lambda n=name, t=text: capture_hotkey(n, t), theme,
                  icon="keyboard", w=186, h=34, bg=K.CARD, size=12, weight="medium")
        b.pack(side="right")
        syncers.append(lambda b_=b, n=name: b_.set((str(get(n)) or "off").upper().replace("+", " + ")))

    # ================================================================== OVERLAY
    ovc = section(colR, "Overlay", "layout-dashboard")
    para(ovc, "A small always-on-top pill with the current event and its countdown, readable over the game.")
    switch_row(ovc, "Show the overlay", "OVERLAY", "Drag it where you like")
    switch_row(ovc, "Lock in place", "OVERLAY_LOCK", "Clicks pass straight through it to the game. Unlock here to move it.")
    switch_row(ovc, "Show the next event", "OVERLAY_NEXT", "A countdown to what's coming when nothing is on")
    ovb = tk.Frame(ovc, bg=K.CARD)
    ovb.pack(fill="x", pady=(2, 6))
    U.Btn(ovb, "Reset position", lambda: ctx.overlay.reset_position(), theme, icon="rotate-cw", h=34, size=12, bg=K.CARD).pack(side="left")

    # ================================================================== DISCORD PROFILE
    dp = section(colR, "Discord profile", "radio")
    para(dp, "Show what Gnomewatch is watching on your Discord profile: the event that's on with a live countdown, and how many accounts are in game. "
             "It only works while Discord is open, and it's off unless you turn it on.")
    switch_row(dp, "Show Gnomewatch on my profile", "PRESENCE", "Needs Discord's 'Share my detected activity' setting on")

    # ================================================================== NEW INSTANCE OPENS
    ln = section(colR, "New instance opens", "rocket")
    ls = U.Seg(ln, [("Game page", "game"), ("Private", "private"), ("Custom", "custom")], lambda: get("LAUNCH_TARGET"),
               lambda v: S._set_setting("LAUNCH_TARGET", v), theme, seg_w=126, h=34)
    ls.pack(anchor="w", pady=(0, 4))
    syncers.append(ls.sync)
    entry_row(ln, "Private server link", "LAUNCH_URL_PRIVATE", w=250)
    entry_row(ln, "Custom link", "LAUNCH_URL_CUSTOM", w=250)

    # ================================================================== MY SCENES
    sm = section(colR, "My scenes", "layers")
    para(sm, "Save the current settings as a one-click scene.")
    sr = tk.Frame(sm, bg=K.CARD)
    sr.pack(fill="x")
    scene_name = tk.StringVar()
    U.EntryBox(sr, scene_name, w=250, h=36, theme=theme, size=12).pack(side="left")

    def save_scene():
        nm = scene_name.get().strip()
        if not nm or nm in C.DEFAULT_SCENES:
            ask("Scenes", "Pick a name that isn't one of the built-in scenes.", info=True)
            return
        mine = jload("scenes.json", {})
        mine[nm] = {k: get(k) for k in C.SCENE_KEYS}
        jsave("scenes.json", mine)
        scene_name.set("")
        print(f"scene saved: {nm}")

    U.Btn(sr, "Save scene", save_scene, theme, icon="save", kind="primary", h=36, size=12, bg=K.CARD).pack(side="right")
    mine_box = tk.Frame(sm, bg=K.CARD)
    mine_box.pack(fill="x", pady=(8, 0))
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
            r2 = tk.Frame(mine_box, bg=K.CARD)
            r2.pack(fill="x", pady=4)
            tk.Label(r2, image=U.icon("star", 16, K.DIM), bg=K.CARD, bd=0).pack(side="left", padx=(0, 8))
            tk.Label(r2, text=nm, bg=K.CARD, fg=K.TEXT, font=font(14, "medium")).pack(side="left")

            def rm(n=nm):
                d = jload("scenes.json", {})
                d.pop(n, None)
                jsave("scenes.json", d)

            U.IconBtn(r2, "trash-2", rm, theme, size=32, kind="ghost", bg=K.CARD, tip="Delete this scene", px=16).pack(side="right")
            U.Btn(r2, "Apply", lambda n=nm: ctx.apply_scene(n), theme, icon="play", h=32, size=12, bg=K.CARD).pack(side="right", padx=6)

    # ================================================================== BACKUP & RESTORE
    bk = section(colR, "Backup & restore", "save")
    para(bk, "Saves your settings, accounts, scenes, recordings and learned icons to one file. A copy is also kept automatically "
             "every day (the last 7) in the backups folder.")
    bk_msg = tk.Label(bk, text="", bg=K.CARD, fg=K.DIM, font=font(12), anchor="w", justify="left", wraplength=INNER)

    def say(text, colour=None):
        bk_msg.config(text=text, fg=colour or K.DIM)

    def do_export():
        keep_secrets = ask("Include your secrets?", "The bot password and your private-server link are saved in a separate file. "
                           "Include them in this backup? Leave them out if you will share or sync the file.", yes="Include", no="Leave out")
        p = filedialog.asksaveasfilename(parent=root, title="Save a Gnomewatch backup", defaultextension=".zip",
                                         initialfile="gnomewatch-backup-" + time.strftime("%Y%m%d") + ".zip", filetypes=[("Backup", "*.zip")])
        if not p:
            return
        try:
            n = S._backup_make(p, keep_secrets)
            say(f"Saved {n} files to {os.path.basename(p)}", K.OK)
        except Exception as e:
            say(f"Couldn't save the backup: {e}", K.BAD)

    def do_restore():
        p = filedialog.askopenfilename(parent=root, title="Choose a Gnomewatch backup", filetypes=[("Backup", "*.zip")])
        if not p:
            return
        if not ask("Restore this backup?", f"{os.path.basename(p)} replaces your current settings, accounts, scenes and recordings. "
                   "Your current files are saved first in the backups folder, and Gnomewatch restarts when it is done.",
                   yes="Restore", danger=True):
            return
        ok, msg = S._backup_restore(p)
        say(msg.capitalize() + (". Restarting…" if ok else ""), K.OK if ok else K.BAD)
        if ok:
            root.after(1500, S._restart_self)

    def open_folder():
        os.makedirs(S.BACKUP_DIR, exist_ok=True)
        os.startfile(S.BACKUP_DIR)

    bbar = tk.Frame(bk, bg=K.CARD)
    bbar.pack(fill="x", pady=(2, 8))
    U.Btn(bbar, "Export…", do_export, theme, icon="download", kind="primary", h=36, size=12, bg=K.CARD).pack(side="left")
    U.Btn(bbar, "Restore…", do_restore, theme, icon="upload", h=36, size=12, bg=K.CARD).pack(side="left", padx=8)
    U.Btn(bbar, "Backups folder", open_folder, theme, icon="folder", h=36, size=12, bg=K.CARD).pack(side="left")
    bk_msg.pack(fill="x")

    U.Btn(colL, "Open settings file (.env)", lambda: os.startfile(S._env_path), theme, icon="folder", h=36, size=12, bg=K.BG).pack(
        anchor="w", pady=(0, 8))

    class Page:
        def slow(self):
            ctx.guarded(rebuild_events)
            ctx.guarded(rebuild_mine)
            for fn in syncers + ev_state["sync"]:
                ctx.guarded(fn)

        def on_show(self):
            sf.to_top()

        run_health = staticmethod(run_checks)

    return Page()
