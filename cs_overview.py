"""Overview page: event banner, four stat tiles, live activity, CPU graph, scenes."""
import collections
import os
import time
import tkinter as tk

import ui_kit as U
from ui_kit import K, font, lerp
import cs_common as C
from cs_common import CW, ago


def build(ctx):
    import psutil
    import win32process
    S, root, theme, M = ctx.S, ctx.root, ctx.theme, ctx.M
    page = ctx.pages["Overview"]
    ic = ctx.ic

    # ================================================================== event banner
    BH = 168
    banner = U.Card(page, CW, BH, pad=0)
    banner.pack(anchor="w")
    wash_item = banner.create_image(1, 1, anchor="nw")
    halo = banner.create_image(22, 18, anchor="nw")
    badge = banner.create_image(28, 24, anchor="nw")
    badge_icon = banner.create_image(78, 74)
    pill = U.Pill(banner, "WATCHING", K.DIM, bg=K.CARD)
    banner.create_window(152, 24, anchor="nw", window=pill)
    b_name = banner.create_text(152, 54, anchor="nw", text="Watching the board", fill=K.TEXT, font=font(34, "bold"))
    b_sub = banner.create_text(153, 102, anchor="nw", text="", fill=K.DIM, font=font(13))
    b_cap = banner.create_text(CW - 30, 26, anchor="ne", text="", fill=K.FAINT, font=font(11, "semibold"))
    bar = U.Bar(banner, CW - 60, 12, bg=K.CARD)
    banner.create_window(30, 138, anchor="nw", window=bar)
    totals = {}

    def retheme(acc):
        if theme.cur == theme.target:                        # only when the colour has settled (it's a heavy image)
            banner.itemconfig(wash_item, image=U.wash(CW - 2, BH - 2, 13, acc, .17, .62))

    theme.sub(retheme)

    def fast(kind, name, left, paused):
        acc = theme.cur
        if paused:
            glyph, gcol = "pause", K.WARN
            title, sub, cap, ptxt, pcol = "Paused", "Not reading the game right now", "", "PAUSED", K.WARN
            txt, tcol, frac = "--:--", K.DIM, 0.0
        elif kind == "none":
            glyph, gcol = "radio", K.DIM
            title, sub, cap, ptxt, pcol = "Watching the board", "No event on the sign right now", "", "WATCHING", K.DIM
            txt, tcol, frac = "--:--", K.DIM, 0.0
        else:
            glyph, gcol = U.event_icon(name), acc
            title = name.title()
            seen = (S.STATE.get(kind) or (0, 0, 0))[2]
            via = "  ·  via corner icon" if (kind == "active" and S.STATE.get("source") == "icon") else ""
            sub = f"seen {ago(time.time() - seen)}{via}" if seen else ""
            cap = "ENDS IN" if kind == "active" else "STARTS IN"
            ptxt, pcol = ("ACTIVE NOW" if kind == "active" else "NEXT EVENT"), acc
            tcol = acc
            if left is None:
                txt, frac = "--:--", 0.0
            else:
                key = f"{kind}:{name}"
                totals[key] = max(totals.get(key, 0), left)
                frac = max(0.0, min(1.0, left / (totals[key] or 1)))
                txt = S._fmt_left(left)
        pulse = (0.5 + 0.5 * __import__("math").sin(time.time() * 2.2)) if (M["pulse"] and kind == "active") else 0.4
        ic(banner, halo, image=U.rr_photo(112, 112, 28, None, lerp(K.CARD, gcol, round(.07 + .22 * pulse, 2)), 2))
        ic(banner, badge, image=U.rr_photo(100, 100, 24, lerp(K.CARD, gcol, .15), lerp(K.CARD, gcol, .5), 2))
        ic(banner, badge_icon, image=U.icon(glyph, 48, gcol))
        pill.set(ptxt, pcol)
        ic(banner, b_name, text=title)
        ic(banner, b_sub, text=sub)
        ic(banner, b_cap, text=cap)
        key = (txt, tcol)
        if ctx.cache.get("bt") != key:
            ctx.cache["bt"] = key
            U.draw_digits(banner, "tm", txt, CW - 30, 42, 54, tcol)
        bar.set(frac, acc if kind != "none" and not paused else K.CARD3)

    rip = {"job": None}

    def ripple(colour):
        t0 = time.time()

        def step():
            t = (time.time() - t0) / 1.2
            try:
                banner.delete("rip")
                if t < 1:
                    for k in range(3):
                        tt = max(0.0, t - k * .12)
                        if tt <= 0:
                            continue
                        s = 100 + tt * 120
                        a = lerp(colour, K.CARD, min(1.0, tt ** .7))
                        banner.create_image(78, 74, image=U.rr_photo(int(s), int(s), int(24 + tt * 30), None, a, 2), tags="rip")
                    rip["job"] = root.after(40, step)
                else:
                    rip["job"] = None
            except tk.TclError:
                rip["job"] = None

        if rip["job"] is None:
            step()

    # ================================================================== stat tiles
    row = tk.Frame(page, bg=K.BG)
    row.pack(fill="x", pady=(16, 0))
    stats = {}
    TW = (CW - 3 * 16) // 4
    for i, (key, ttl, icn) in enumerate((("inst", "INSTANCES", "monitor"), ("worker", "BOT LINK", "wifi"),
                                          ("read", "LAST READ", "scan-eye" if os.path.exists(os.path.join(U.ASSETS, "icons", "scan-eye.png")) else "eye"),
                                          ("up", "RUNNING FOR", "clock"))):
        c = U.Card(row, TW, 92, pad=0)
        c.pack(side="left", padx=(0, 16 if i < 3 else 0))
        box = c.create_image(18, 18, anchor="nw")
        icn_i = c.create_image(18 + 21, 18 + 21)
        c.create_text(76, 25, anchor="w", text=ttl, fill=K.FAINT, font=font(11, "semibold"))
        v = c.create_text(76, 49, anchor="w", text="–", fill=K.TEXT, font=font(21, "bold"))
        sb = c.create_text(76, 73, anchor="w", text="", fill=K.DIM, font=font(12))
        dot = c.create_image(TW - 20, 25)
        stats[key] = (c, v, sb, dot, box, icn_i, icn)

    def tile(key, value, sub, colour):
        c, v, sb, dot, box, icn_i, icn = stats[key]
        ic(c, v, text=U.ellipsize(value, font(21, "bold"), TW - 76 - 14))
        ic(c, sb, text=U.ellipsize(sub, font(12), TW - 76 - 14))
        ic(c, dot, image=U.circle_photo(10, colour))
        ic(c, box, image=U.rr_photo(42, 42, 11, lerp(K.CARD, colour, .15), lerp(K.CARD, colour, .35)))
        ic(c, icn_i, image=U.icon(icn, 21, colour))

    def update_stats():
        snap = list(S._snapshot)
        vis = sum(1 for e in snap if not e[3] and not e[4])
        hid = sum(1 for e in snap if e[3])
        tile("inst", f"{len(snap)} running", f"{vis} visible · {hid} hidden", K.OK if snap else K.BAD)
        wk = S.STATE["worker"]
        if not S._reporting():
            tile("worker", "Off", "not signed in, no bot set up", K.FAINT)
        else:
          tile("worker", "Connected" if wk["ok"] else "Unreachable" if wk["ok"] is False else "No report",
             f"last report {ago(time.time() - wk['last'])}" if wk["last"] else "waiting for first read",
             K.OK if wk["ok"] else K.BAD if wk["ok"] is False else K.WARN)
        lr = S.STATE["last_read"]
        age = time.time() - lr if lr else None
        tile("read", ago(age) if age is not None else "never",
             "paused" if S.PAUSED.is_set() else f"board seen {ago(time.time() - S.STATE['board_seen'])}"
             if S.STATE["board_seen"] else "board not seen yet",
             K.WARN if S.PAUSED.is_set() else K.OK if age is not None and age < 20 else K.BAD)
        up = int(time.time() - S.STATE["started"])
        tile("up", f"{up // 3600}h {up % 3600 // 60}m" if up >= 3600 else f"{up // 60}m {up % 60}s",
             {"held": "multi-instance on", "waiting": "close Roblox first",
              "off": "multi-instance off", "failed": "multi-instance error"}.get(S.STATE["lock"], "multi-instance off"), K.OK)

    # ================================================================== bottom row: activity feed | CPU + scenes
    r3 = tk.Frame(page, bg=K.BG)
    r3.pack(fill="x", pady=(16, 0))
    BOT = 618 - BH - 16 - 92 - 16
    acc_card = U.Card(r3, 556, BOT, pad=20, title="Accounts", icon="users")
    acc_card.pack(side="left", anchor="n")
    acc_box = tk.Frame(acc_card.inner, bg=K.CARD)
    acc_box.pack(fill="both", expand=True)
    acct_ui = {"sig": None, "cards": {}, "thumbs": {}, "last_thumb": 0.0}
    CARD_W, CARD_H = 252, 112

    def account_facts(a):
        """(windows of this account, last auto-place result line)."""
        alive = {e[0]: e for e in list(S._snapshot)}
        wins = [alive[h] for h, l in ctx.assign.items() if l == a["label"] and h in alive]
        macros = S._macros_load()
        best = None
        for ev, m in macros.items():
            if m.get("account") != a["label"]:
                continue
            for ph in ("start", "end"):
                r = S.MACRO_STATUS.get((ev, ph))
                if r and (best is None or r["t"] > best[0]["t"]):
                    best = (r, ev, ph)
        return wins, best

    def mk_account_card(a):
        c = U.Card(acc_box, CARD_W, CARD_H, pad=0, fill=K.CARD2, radius=12, bg=K.CARD)
        thumb = c.create_image(12, 12, anchor="nw")
        name = c.create_text(114, 21, anchor="w", text=a["label"], fill=K.TEXT, font=font(14, "semibold"))
        pill = U.Pill(c, "OFFLINE", K.FAINT, bg=K.CARD2)
        c.create_window(114, 36, anchor="nw", window=pill)
        up = c.create_text(114, 78, anchor="w", text="", fill=K.FAINT, font=font(11, "medium"))
        res = c.create_text(14, 96, anchor="w", text="", fill=K.DIM, font=font(11, "medium"))
        bar = tk.Frame(c, bg=K.CARD2)
        c.create_window(CARD_W - 10, 72, anchor="e", window=bar)
        return dict(card=c, thumb=thumb, name=name, pill=pill, up=up, res=res, bar=bar, btns=None, mode=None, label=a["label"])

    def fill_buttons(ui, a, wins):
        mode = (bool(wins), bool(wins and wins[0][3]))
        if ui["mode"] == mode:
            return
        ui["mode"] = mode
        for ch in ui["bar"].winfo_children():
            ch.destroy()
        if wins:
            hwnd, hidden = wins[0][0], wins[0][3]
            U.IconBtn(ui["bar"], "eye" if hidden else "eye-off", lambda h=hwnd, hd=hidden: S._set_window_show(h, hd), theme, size=28,
                      kind="ghost", bg=K.CARD2, tip="Show this window" if hidden else "Hide this window", px=15).pack(side="left")
            U.IconBtn(ui["bar"], "rotate-cw", lambda x=a: ctx.rejoin(x), theme, size=28, kind="ghost", bg=K.CARD2,
                      tip="Close and join again", px=15).pack(side="left", padx=(2, 0))
        else:
            U.IconBtn(ui["bar"], "play", lambda x=a: ctx.launch_account(x), theme, size=28, kind="primary", bg=K.CARD2,
                      tip="Launch this account", px=15).pack(side="left")

    def refresh_accounts():
        accts = ctx.load_accounts()
        sig = (tuple(a["label"] for a in accts), tuple(sorted(ctx.assign.items())), tuple(e[0] for e in S._snapshot))
        if sig != acct_ui["sig"]:
            acct_ui["sig"] = sig
            for ch in acc_box.winfo_children():
                ch.destroy()
            acct_ui["cards"].clear()
            if not accts:
                e = tk.Frame(acc_box, bg=K.CARD)
                e.pack(expand=True)
                tk.Label(e, image=U.icon("user-plus", 34, K.FAINT), bg=K.CARD, bd=0).pack(pady=(34, 8))
                tk.Label(e, text="No accounts yet", bg=K.CARD, fg=K.TEXT, font=font(15, "semibold")).pack()
                tk.Label(e, text="Add a browser profile on the Accounts page and it shows up here.", bg=K.CARD, fg=K.DIM,
                         font=font(12)).pack(pady=(2, 10))
                U.Btn(e, "Open Accounts", lambda: ctx.show("Accounts"), theme, icon="users", kind="primary", h=34, size=12, bg=K.CARD).pack()
            for i, a in enumerate(accts[:4]):
                ui = mk_account_card(a)
                ui["card"].grid(row=i // 2, column=i % 2, padx=(0 if i % 2 == 0 else 12, 0), pady=(0, 10))
                acct_ui["cards"][a["label"]] = ui
            if len(accts) > 4:
                tk.Label(acc_box, text=f"+{len(accts) - 4} more on the Accounts page", bg=K.CARD, fg=K.FAINT, font=font(12)).grid(
                    row=2, column=0, columnspan=2, sticky="w")
        now = time.time()
        for a in accts[:4]:
            ui = acct_ui["cards"].get(a["label"])
            if not ui:
                continue
            wins, best = account_facts(a)
            c = ui["card"]
            live = bool(wins)
            col = K.OK if live else K.FAINT
            ui["pill"].set("IN GAME" if live else "OFFLINE", col)
            since = ctx.win_since.get(wins[0][0]) if wins else None
            ic(c, ui["up"], text=(f"up {int((now - since) // 3600)}h {int((now - since) % 3600 // 60)}m" if since and now - since >= 3600
                                  else f"up {int((now - since) // 60)}m" if since else "not running")
               + (f"  ·  {len(wins)} windows" if len(wins) > 1 else ""))
            if best:
                r, ev, ph = best
                txt = ("✔ " if r["ok"] else "✖ ") + time.strftime("%H:%M", time.localtime(r["t"])) + f"  {ev.title()} ({ph})"
                ic(c, ui["res"], text=U.ellipsize(txt, font(11, "medium"), CARD_W - 28), fill=K.OK if r["ok"] else K.BAD)
            else:
                ic(c, ui["res"], text="no auto-place yet", fill=K.FAINT)
            fill_buttons(ui, a, wins)

    def update_thumbs():
        if time.time() - acct_ui["last_thumb"] < 1.0:
            return
        acct_ui["last_thumb"] = time.time()
        from PIL import Image, ImageOps, ImageTk
        for a in ctx.load_accounts()[:4]:
            ui = acct_ui["cards"].get(a["label"])
            if not ui:
                continue
            wins, _best = account_facts(a)
            panel = Image.new("RGBA", (92, 58), (20, 26, 40, 255))
            if wins:
                S._ensure_capture(wins[0][0])
                st = S._caps.get(wins[0][0])
                arr = st.get("frame") if st else None
                if arr is not None:
                    im = ImageOps.contain(Image.fromarray(arr[:, :, [2, 1, 0]]), (92, 58), Image.Resampling.BILINEAR).convert("RGBA")
                    panel.paste(im, ((92 - im.width) // 2, (58 - im.height) // 2))
            panel.putalpha(U.rr_pil(92, 58, 9, "#ffffff", None).getchannel("A"))
            ph = ImageTk.PhotoImage(panel)
            acct_ui["thumbs"][a["label"]] = ph
            ui["card"].itemconfig(ui["thumb"], image=ph)

    rcol = tk.Frame(r3, bg=K.BG)
    rcol.pack(side="left", anchor="n", padx=(16, 0))
    cpu_h = 166
    cpu = U.Card(rcol, 336, cpu_h, pad=20, title="CPU usage", icon="cpu")
    cpu.pack()
    legend_row = tk.Frame(cpu.inner, bg=K.CARD)
    legend_row.pack(fill="x")
    series = {"sentinel": ("Sentinel", "#8b7cff", collections.deque(maxlen=90)),
              "hidden": ("Hidden", "#ff9f43", collections.deque(maxlen=90)),
              "other": ("Other", "#3dd6ff", collections.deque(maxlen=90))}
    legend = {}
    for key, (nm, col, _d) in series.items():
        f_ = tk.Frame(legend_row, bg=K.CARD)
        f_.pack(side="left", padx=(0, 10))
        tk.Label(f_, text="●", bg=K.CARD, fg=col, font=font(10)).pack(side="left")
        lb = tk.Label(f_, text=f" {nm}  –", bg=K.CARD, fg=K.DIM, font=font(12, "medium"))
        lb.pack(side="left")
        legend[key] = (nm, lb)
    spark = U.Spark(cpu.inner, 296, 62, [(nm, col, d) for nm, col, d in series.values()], bg=K.CARD)
    spark.pack(pady=(8, 0))

    sc = U.Card(rcol, 336, BOT - cpu_h - 16, pad=20, title="Scenes", icon="layers")
    sc.pack(pady=(16, 0))
    scene_box = tk.Frame(sc.inner, bg=K.CARD)
    scene_box.pack(fill="x")
    scene_state = {"sig": None, "btns": {}}

    def rebuild_scenes():
        scn = ctx.load_scenes()
        sig = tuple(sorted(scn))
        if sig == scene_state["sig"]:
            return
        scene_state["sig"] = sig
        for ch in scene_box.winfo_children():
            ch.destroy()
        scene_state["btns"].clear()
        for i, nm in enumerate(scn):
            b = U.Btn(scene_box, nm, lambda n=nm: ctx.apply_scene(n), theme, icon=C.SCENE_ICONS.get(nm, "star"), w=141, h=34,
                      bg=K.CARD, size=12, weight="medium")
            b.grid(row=i // 2, column=i % 2, padx=(0, 10 if i % 2 == 0 else 0), pady=(0, 8))
            scene_state["btns"][nm] = b

    def sync_scenes():
        for nm, b in scene_state["btns"].items():
            b.set(kind="primary" if ctx.scene_matches(nm) else "secondary")

    # ---- cpu sampling
    procs = {}
    last = {"graph": 0.0, "feed": 0.0}

    def cpu_of(pids):
        tot = 0.0
        for pid in pids:
            p = procs.get(pid)
            try:
                if p is None:
                    p = procs[pid] = psutil.Process(pid)
                    p.cpu_percent(None)
                tot += p.cpu_percent(None) / max(1, psutil.cpu_count() or 1)      # share of the whole PC, not of one core
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
        spark.redraw()

    _ripple = ripple

    class Page:
        always = True                                         # the CPU graph keeps sampling even on other pages
        ripple = staticmethod(_ripple)

        def fast(self, kind, name, left, paused):
            fast(kind, name, left, paused)

        def slow(self):
            update_stats() if ctx.nav["cur"] == "Overview" else None
            sample_usage()
            if ctx.nav["cur"] != "Overview":
                return
            rebuild_scenes()
            sync_scenes()
            refresh_accounts()
            update_thumbs()

        def on_show(self):
            ctx.cache.pop("bt", None)
            acct_ui["sig"] = None

    return Page()
