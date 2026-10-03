"""Performance page: the four modes as cards that say exactly what they change, live numbers, and auto-switch rules."""
import collections
import os
import time
import tkinter as tk

import ui_kit as U
from ui_kit import K, font, lerp
import cs_common as C
from cs_common import CW, BODY_H

PRIORITY_TEXT = {"normal": "Normal priority", "below": "Lower priority", "idle": "Lowest priority"}


def build(ctx):
    import psutil
    S, root, theme, get = ctx.S, ctx.root, ctx.theme, ctx.get
    page = ctx.pages["Performance"]
    ic = ctx.ic
    me = psutil.Process(os.getpid())
    me.cpu_percent(None)

    # ================================================================== mode cards
    CARD_H = 322
    CW4 = (CW - 3 * 14) // 4
    top = tk.Frame(page, bg=K.BG)
    top.pack(fill="x")
    cards = {}

    def facts(key):
        """The checklist of what a mode changes, from its real settings."""
        m, prof = U.MODES[key], S.MODE_PROFILES[key]
        s = prof["settings"]
        poll = s.get("POLL_SECONDS", 2.0)
        cap = s.get("CAPTURE_MS", 500)
        rows = [("sparkles" if s.get("ANIMATIONS") == "normal" else "square", "Animations on" if s.get("ANIMATIONS") == "normal" else "No animations"),
                ("monitor", f"Redraws every {m['fast']} ms"),
                ("eye", f"Reads every {poll:g} s"),
                ("camera", f"Looks {1000 / cap:g}× a second"),
                ("cpu", PRIORITY_TEXT[prof["priority"]])]
        if s.get("THROTTLE_PARKED"):
            rows.append(("leaf", "Throttles hidden Roblox"))
        if key == "stealth":
            rows[1] = ("eye-off", "Hides windows & alerts")
        return rows[:5]

    for i, key in enumerate(U.MODES):
        m = U.MODES[key]
        c = U.Card(top, CW4, CARD_H, pad=0)
        c.grid(row=0, column=i, padx=(0 if i == 0 else 14, 0))
        items = {"badge": c.create_image(18, 18, anchor="nw"), "icon": c.create_image(18 + 24, 18 + 24)}
        c.create_text(18, 86, anchor="w", text=m["label"], fill=K.TEXT, font=font(16, "bold"))
        c.create_text(18, 112, anchor="nw", text=m["blurb"], fill=K.FAINT, font=font(11, "medium"), width=CW4 - 36)
        y = 166
        for icn, text in facts(key):
            c.create_image(18, y, image=U.icon(icn, 14, K.DIM), anchor="w", tags="fact")
            c.create_text(40, y, anchor="w", text=U.ellipsize(text, font(11, "medium"), CW4 - 58), fill=K.DIM, font=font(11, "medium"), tags="fact")
            y += 22
        use = U.Btn(c, "Use this mode", lambda k=key: ctx.choose_mode(k), theme, icon="check", h=34, size=12, bg=K.CARD, w=CW4 - 36)
        items["use"] = c.create_window(18, CARD_H - 18, anchor="sw", window=use)
        items["active_pill"] = U.Pill(c, "ACTIVE NOW", K.OK, bg=K.CARD)
        items["active"] = c.create_window(18, CARD_H - 18, anchor="sw", window=items["active_pill"], state="hidden")
        items["btn"], items["state"] = use, None
        cards[key] = (c, items)

    def paint_cards():
        for key, (c, items) in cards.items():
            on = key == S.UI_MODE
            sig = (on, theme.cur)
            if items["state"] == sig:
                continue
            items["state"] = sig
            a = theme.cur if on else K.DIM
            c.fill0, c.edge0 = (lerp(K.CARD, theme.cur, .07) if on else K.CARD), (lerp(K.EDGE, theme.cur, .8) if on else K.EDGE)
            c.itemconfig(c.body, image=U.rr_photo(c.w, c.h, c.radius, c.fill0, c.edge0, 2 if on else 1))
            c.itemconfig(items["badge"], image=U.rr_photo(48, 48, 13, lerp(K.CARD, a, .18), lerp(K.CARD, a, .45)))
            c.itemconfig(items["icon"], image=U.icon(U.MODES[key]["icon"], 24, a))
            c.itemconfig(items["active"], state="normal" if on else "hidden")
            c.itemconfig(items["use"], state="hidden" if on else "normal")
            items["active_pill"].set("ACTIVE NOW", theme.cur)

    # ================================================================== live numbers (left) + auto-switch rules (right)
    LOW_H = BODY_H - CARD_H - 14
    row2 = tk.Frame(page, bg=K.BG)
    row2.pack(fill="x", pady=(14, 0))
    live = U.Card(row2, 556, LOW_H, pad=20, title="Live numbers", icon="activity")
    live.pack(side="left")
    li = live.inner
    nums = {}
    top_row = tk.Frame(li, bg=K.CARD)
    top_row.pack(fill="x")
    for key, ttl in (("cpu", "GNOMEWATCH CPU"), ("ram", "GNOMEWATCH RAM"), ("rbx", "ROBLOX RAM")):
        g = tk.Frame(top_row, bg=K.CARD)
        g.pack(side="left", padx=(0, 34))
        tk.Label(g, text=ttl, bg=K.CARD, fg=K.FAINT, font=font(10, "semibold")).pack(anchor="w")
        nums[key] = tk.Label(g, text="–", bg=K.CARD, fg=K.TEXT, font=font(24, "bold"))
        nums[key].pack(anchor="w")
    series_cpu, series_rbx = collections.deque(maxlen=90), collections.deque(maxlen=90)
    spark = U.Spark(li, 516, LOW_H - 48 - 20 - 56 - 64, [("Gnomewatch", "#8b7cff", series_cpu), ("Roblox", "#ff9f43", series_rbx)], bg=K.CARD)
    spark.pack(pady=(10, 0))
    legend = tk.Frame(li, bg=K.CARD)
    legend.pack(fill="x", pady=(6, 0))
    for nm, col in (("Gnomewatch CPU", "#8b7cff"), ("Roblox CPU (all clients)", "#ff9f43")):
        tk.Label(legend, text="●", bg=K.CARD, fg=col, font=font(10)).pack(side="left")
        tk.Label(legend, text=f" {nm}   ", bg=K.CARD, fg=K.DIM, font=font(11, "medium")).pack(side="left")
    foot = tk.Label(li, text="", bg=K.CARD, fg=K.FAINT, font=font(11, "medium"), anchor="w")
    foot.pack(fill="x", pady=(8, 0))

    rules = U.Card(row2, CW - 556 - 14, LOW_H, pad=20, title="Auto-switch rules", icon="repeat")
    rules.pack(side="left", padx=(14, 0))
    ri = rules.inner
    mode_opts = [(m["label"], k) for k, m in U.MODES.items()]
    syncers = []

    def rule_row(icn, title, note, name, none_label):
        r = tk.Frame(ri, bg=K.CARD)
        r.pack(fill="x", pady=(0, 10))
        tk.Label(r, image=U.icon(icn, 16, K.DIM), bg=K.CARD, bd=0).pack(side="left", padx=(0, 8), anchor="n", pady=2)
        col = tk.Frame(r, bg=K.CARD)
        col.pack(side="left")
        tk.Label(col, text=title, bg=K.CARD, fg=K.TEXT, font=font(13, "semibold"), anchor="w").pack(anchor="w")
        tk.Label(col, text=note, bg=K.CARD, fg=K.FAINT, font=font(11), anchor="w").pack(anchor="w")
        dd = U.Dropdown(r, lambda: [(none_label, "")] + mode_opts, lambda: get(name), lambda v: S._set_setting(name, v), theme, w=140, h=34,
                        bg=K.CARD, size=12)
        dd.pack(side="right")
        syncers.append(dd.refresh)
        return r

    rule_row("gamepad-2", "A game is fullscreen", "Roblox doesn't count", "RULE_FULLSCREEN", "No change")
    rule_row("moon", "You've been away", "no mouse or keyboard", "RULE_AWAY", "No change")
    rr_ = tk.Frame(ri, bg=K.CARD)
    rr_.pack(anchor="e", pady=(0, 10))
    tk.Label(rr_, text="after", bg=K.CARD, fg=K.FAINT, font=font(11, "medium")).pack(side="left", padx=(0, 8))
    away_seg = U.Seg(rr_, [("10 m", 10.0), ("20 m", 20.0), ("30 m", 30.0), ("60 m", 60.0)], lambda: get("RULE_AWAY_MIN"),
                     lambda v: S._set_setting("RULE_AWAY_MIN", v), theme, seg_w=46, h=28, size=11)
    away_seg.pack(side="left")
    syncers.append(away_seg.sync)
    rule_row("log-in", "You're back", "restores your mode", "RULE_BACK", "Previous mode")

    # ================================================================== live sampling
    last = {"t": 0.0}

    def sample():
        if time.time() - last["t"] < 2.0:
            return
        last["t"] = time.time()
        try:
            cpu = me.cpu_percent(None) / max(1, psutil.cpu_count() or 1)
            ram = me.memory_info().rss
        except psutil.Error:
            return
        rbx_ram = sum(i.get("rss", 0) for i in S._clients.values())
        rbx_cpu = sum(i.get("cpu", 0) for i in S._clients.values())
        series_cpu.append(cpu)
        series_rbx.append(rbx_cpu)
        nums["cpu"].config(text=f"{cpu:.1f}%")
        nums["ram"].config(text=f"{ram / 1e6:.0f} MB")
        nums["rbx"].config(text=f"{rbx_ram / 1e9:.1f} GB" if rbx_ram else "–")
        spark.redraw()
        foot.config(text=f"Reads every {S.POLL_S:g} s  ·  looks at windows {1000 / max(1, S.CAPTURE_MS):g}× a second  ·  "
                         f"console redraws every {K.M['fast']} ms")

    class Page:
        always = False

        def slow(self):
            paint_cards()
            sample()
            for fn in syncers:
                ctx.guarded(fn)

        def on_show(self):
            for k in cards.values():
                k[1]["state"] = None
            last["t"] = 0.0

    return Page()
