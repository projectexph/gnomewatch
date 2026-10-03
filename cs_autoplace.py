"""Auto-place page: one card per mutation. Record what you do once for when it STARTS and once for when it ENDS;
the steps replay in that account's window. A timeline shows what was recorded, a floating HUD shows a recording live."""
import json
import threading
import time
import tkinter as tk

import gw_ghost
import gw_share
import ui_kit as U
from ui_kit import K, font, lerp
import cs_common as C
from cs_common import CW, BODY_H, SCROLL_W

CARD_W = (SCROLL_W - 16) // 2                        # 440
KEY = "#010203"                                      # painted transparent so the HUD can have rounded corners
KEYCAP = {0x20: "␣", 0x0D: "↵", 0x09: "⇥", 0x1B: "esc", 0x25: "←", 0x26: "↑", 0x27: "→", 0x28: "↓"}


def key_label(vk):
    if vk in KEYCAP:
        return KEYCAP[vk]
    return chr(vk) if 0x30 <= vk <= 0x5A else "?"


ellipsize = U.ellipsize


class Timeline(tk.Canvas):
    """The recorded steps laid out along time: keycaps for key presses, dots for clicks."""
    def __init__(self, parent, w, h, bg):
        super().__init__(parent, width=w, height=h, bg=bg, highlightthickness=0, bd=0)
        self.w, self.h, self.bgc, self.sig = w, h, bg, None

    def show(self, steps, colour):
        sig = (json.dumps(steps, sort_keys=True), colour)
        if sig == self.sig:
            return
        self.sig = sig
        self.delete("all")
        w, h, mid = self.w, self.h, self.h // 2
        if not steps:
            self.create_text(2, mid, anchor="w", text="Nothing recorded yet", fill=K.FAINT, font=font(12))
            return
        self.create_line(8, mid, w - 8, mid, fill=K.EDGE_HI, width=2)
        marks = [(s["t"], s) for s in steps if s.get("d")]                  # presses only: releases are not drawn
        dur = max(s["t"] for s in steps) or 1.0
        span = w - 44
        xs, prev = [], -99
        for t, s in marks:
            x = max(18 + span * t / dur, prev + 24)
            xs.append(x)
            prev = x
        if xs and xs[-1] > span + 18:                                       # too crowded: squeeze evenly
            f = (span) / (xs[-1] - 18)
            xs = [18 + (x - 18) * f for x in xs]
        for (t, s), x in zip(marks, xs):
            if "k" in s:
                lab = key_label(s["k"])
                self.create_image(x, mid, image=U.rr_photo(22, 22, 6, lerp(self.bgc, K.TEXT, .12), lerp(self.bgc, K.TEXT, .35)))
                self.create_text(x, mid, text=lab, fill=K.TEXT, font=font(10 if len(lab) > 1 else 11, "semibold"))
            else:
                self.create_image(x, mid, image=U.circle_photo(20, lerp(self.bgc, colour, .25), colour, 2))
                self.create_text(x, mid, text=s["m"], fill=colour, font=font(9, "bold"))


def build(ctx):
    S, root, theme, get = ctx.S, ctx.root, ctx.theme, ctx.get
    page = ctx.pages["Auto-place"]
    ask = ctx.ask

    sf = U.ScrollFrame(page, CW, BODY_H, bg=K.BG, theme=theme)
    sf.pack(anchor="w")
    ctx.scrollers.append(sf)
    top = sf.inner

    # ================================================================== options
    opts = U.Card(top, SCROLL_W, 190, pad=20, title="Options", icon="sliders-horizontal")
    opts.pack(anchor="w", pady=(0, 16))
    oi = opts.inner
    row1 = tk.Frame(oi, bg=K.CARD)
    row1.pack(fill="x")
    syncers = []

    def opt_switch(parent, title, note, name):
        g = tk.Frame(parent, bg=K.CARD)
        g.pack(side="left", padx=(0, 26))
        sw = U.Switch(g, lambda v, n=name: S._set_setting(n, v), theme)
        sw.pack(side="left")
        col = tk.Frame(g, bg=K.CARD)
        col.pack(side="left", padx=(10, 0))
        tk.Label(col, text=title, bg=K.CARD, fg=K.TEXT, font=font(13, "medium")).pack(anchor="w")
        tk.Label(col, text=note, bg=K.CARD, fg=K.FAINT, font=font(11)).pack(anchor="w")
        syncers.append(lambda: sw.set(bool(get(name))))

    opt_switch(row1, "Check it worked", "Retries once if the screen didn't change", "MACRO_VERIFY")
    opt_switch(row1, "Skip if nearly over", "Under ~20 s left, or already ended", "MACRO_SKIP_LATE")
    opt_switch(row1, "Alert if it fails", "Only after it has given up", "MACRO_ALERT")
    row2 = tk.Frame(oi, bg=K.CARD)
    row2.pack(fill="x", pady=(14, 0))

    def opt_seg(parent, title, choices, name, w):
        g = tk.Frame(parent, bg=K.CARD)
        g.pack(side="left", padx=(0, 30))
        tk.Label(g, text=title, bg=K.CARD, fg=K.FAINT, font=font(10, "semibold")).pack(anchor="w", pady=(0, 4))
        sg = U.Seg(g, choices, lambda: get(name), lambda v, n=name: S._set_setting(n, v), theme, seg_w=w, h=30, size=12)
        sg.pack(anchor="w")
        syncers.append(sg.sync)

    opt_seg(row2, "RETRIES", [("1", 1), ("2", 2), ("3", 3), ("5", 5)], "MACRO_RETRIES", 48)
    opt_seg(row2, "WAIT AFTER AN EVENT STARTS", [("None", 0.0), ("3 s", 3.0), ("5 s", 5.0), ("10 s", 10.0)], "MACRO_DELAY", 62)

    # ================================================================== share / import
    sh = tk.Frame(top, bg=K.BG)
    sh.pack(anchor="w", pady=(0, 14))
    U.Btn(sh, "Import a macro", lambda: import_dialog(), theme, icon="download", kind="primary", h=38, size=12, bg=K.BG).pack(side="left")
    tk.Label(sh, text="Paste a friend's code here. On each card: the eye is a dry run, the arrow copies a share code.",
             bg=K.BG, fg=K.FAINT, font=font(12)).pack(side="left", padx=14)

    # ================================================================== event cards
    grid = tk.Frame(top, bg=K.BG)
    grid.pack(anchor="w")
    empty = tk.Frame(top, bg=K.BG)

    class StatusDict(dict):
        """Short-lived messages (recording, cleared...) remember when they were set so they fade back to the last result."""
        def __init__(self):
            super().__init__()
            self.t = {}

        def __setitem__(self, k, v):
            super().__setitem__(k, v)
            self.t[k] = time.time()

    st = {"sig": None, "cards": {}, "status": StatusDict()}
    rec_ui = {"hud": None, "hidden": False, "was": False, "ev": None}

    def mrec(ev, ph):
        m_ = S._macros_load().get(ev, {})
        a_ = m_.get("account")
        k_ = (ev, ph)
        if not a_:
            st["status"][k_] = "Pick an account first"
            return
        hw_ = S._macro_window(a_)
        if hw_ is None:
            st["status"][k_] = f"No open Roblox window is assigned to {a_}"
            return
        what = ("equip the gnome, then click to place it" if ph == "start"
                else "what should happen once the event is over, e.g. pick your normal gnome back up")
        if not ask("Record steps", f"After you click Record, this window hides and {a_}'s Roblox comes to the front. "
                   f"Do the steps ({what}).\n\nPress Ctrl+Shift+F8, or click Stop on the red bar, when you're done.", yes="Record"):
            return
        st["status"][k_] = "Recording…"
        rec_ui["ev"] = (ev, ph)
        S._macro_record(ev, hw_, phase=ph, done=lambda n, k=k_: st["status"].__setitem__(
            k, "Busy: a replay or recording is already running" if n < 0 else f"Recorded {n} steps" if n else "Nothing was recorded"))

    def mtest(ev, ph):
        k_ = (ev, ph)
        st["status"][k_] = "Running…"
        threading.Thread(target=lambda: st["status"].__setitem__(k_, "Done" if S._macro_play(ev, True, ph) else "Couldn't run (see the log)"),
                         daemon=True).start()

    def clear(ev, ph):
        if ask("Clear recording", f"Delete the {ph.upper()} steps recorded for {ev.title()}?", yes="Clear", danger=True):
            S._macro_set(ev, **{S._phase_key(ph): []})
            st["status"][(ev, ph)] = "Cleared"

    def account_options():
        return [(a["label"], a["label"]) for a in ctx.load_accounts()] or [("No accounts yet", "")]

    # ---- dialogs, dry run (ghost replay), share codes
    def new_dialog(width=580):
        dlg = tk.Toplevel(root)
        dlg.overrideredirect(True)
        dlg.attributes("-topmost", True)
        dlg.configure(bg=K.EDGE_HI)
        body = tk.Frame(dlg, bg=K.CARD)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        inner = tk.Frame(body, bg=K.CARD)
        inner.pack(fill="both", expand=True, padx=24, pady=(22, 20))
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        return dlg, inner

    def centre(dlg):
        dlg.update_idletasks()
        w_, h_ = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        dlg.geometry(f"{w_}x{h_}+{root.winfo_rootx() + (C.W - w_) // 2}+{max(10, root.winfo_rooty() + (C.H - h_) // 2 - 20)}")
        dlg.focus_force()

    def ev_colour(ev):
        try:
            return S._event_style(ev)[1]
        except Exception:
            return theme.cur

    def preview_dialog(title, note, steps, col):
        """The ghost replay on a stand-in window (when there is no live window to draw over)."""
        dlg, inner = new_dialog()
        tk.Label(inner, text=title, bg=K.CARD, fg=K.TEXT, font=font(17, "bold")).pack(anchor="w")
        tk.Label(inner, text=note, bg=K.CARD, fg=K.DIM, font=font(12), wraplength=540, justify="left").pack(anchor="w", pady=(2, 10))
        gc = gw_ghost.GhostCanvas(inner, 540, 304, K.BG, col, mock=True)
        gc.pack()
        info = tk.Label(inner, text="", bg=K.CARD, fg=K.FAINT, font=font(11, "medium"))
        info.pack(anchor="w", pady=(8, 12))
        row = tk.Frame(inner, bg=K.CARD)
        row.pack(fill="x")

        def tick(_f, n):
            info.config(text=f"{n} press{'es' if n != 1 else ''} shown  ·  nothing is clicked")

        def close():
            gc.stop()
            dlg.destroy()

        U.Btn(row, "Close", close, theme, kind="primary", h=36, size=12).pack(side="right")
        U.Btn(row, "Replay", lambda: gc.play(steps, on_tick=tick), theme, icon="rotate-cw", h=36, size=12).pack(side="right", padx=(0, 8))
        centre(dlg)
        gc.play(steps, on_tick=tick)

    def dry(ev, ph):
        m_ = S._macros_load().get(ev, {})
        steps = m_.get(S._phase_key(ph)) or []
        k_ = (ev, ph)
        if not steps:
            return
        col, title = ev_colour(ev), f"{ev.title()}  ·  {ph.upper()}"
        acct = m_.get("account")
        hw = S._macro_window(acct) if acct else None
        rect = None
        if hw is not None:
            try:
                x0, y0, w, h = S._client_rect(hw)
                if w >= 200 and h >= 150:
                    rect = (x0, y0, w, h)
            except Exception:
                rect = None
        if rect is None:
            why = "Pick an account for this event to see it over the real window." if not acct else (
                f"{acct} has no open Roblox window right now, so this is a stand-in preview.")
            return preview_dialog(title, why, steps, col)
        st["status"][k_] = "Dry run…"
        try:
            prev, parked = S._bring_forward(hw)
        except Exception:
            prev, parked = None, False
        tok = ctx.hide_console()

        def closed():
            try:
                S._mark_own()
                S._put_back(hw, prev, parked)
            except Exception:
                pass
            ctx.show_console(tok)
            st["status"][k_] = "Dry run finished"

        root.after(350, lambda: gw_ghost.open_overlay(root, theme, rect, steps, col, title, on_close=closed))

    def share(ev, ph="start"):
        m_ = S._macros_load().get(ev, {})
        if not (m_.get("steps") or m_.get("end_steps")):
            st["status"][(ev, ph)] = "Record something first, then share it"
            return
        who = (S.GW_SESSION or {}).get("name") or "Gnomewatch"
        code = gw_share.encode(ev, m_.get("steps"), m_.get("end_steps"), name=f"{who}'s {ev}")
        ok = gw_share.copy_text(code, root)
        st["status"][(ev, ph)] = "Share code copied. Paste it to a friend" if ok else "Couldn't reach the clipboard. Try again"

    def import_dialog(code=None):
        dlg, inner = new_dialog()
        tk.Label(inner, text="Import a macro", bg=K.CARD, fg=K.TEXT, font=font(18, "bold")).pack(anchor="w")
        tk.Label(inner, text="Paste the code a friend sent you. You'll see what it does before anything is saved, and it arrives switched off.",
                 bg=K.CARD, fg=K.DIM, font=font(12), wraplength=540, justify="left").pack(anchor="w", pady=(2, 10))
        box = tk.Text(inner, height=4, width=62, bg=K.CARD2, fg=K.TEXT, insertbackground=K.TEXT, relief="flat", bd=0, wrap="char",
                      font=("Consolas", -12), padx=10, pady=8, highlightthickness=1, highlightbackground=K.EDGE, highlightcolor=theme.cur)
        box.pack(fill="x")
        msg = tk.Label(inner, text="", bg=K.CARD, fg=K.DIM, font=font(12, "medium"), wraplength=540, justify="left", anchor="w")
        msg.pack(fill="x", pady=(8, 0))
        pv = tk.Frame(inner, bg=K.CARD)                                            # filled in once a code checks out
        pv_state = {"info": None, "gc": None, "ev": None}
        row = tk.Frame(inner, bg=K.CARD)
        row.pack(fill="x", pady=(14, 0))

        def paste():
            text = gw_share.paste_text(root).strip()
            if text:
                box.delete("1.0", "end")
                box.insert("1.0", text)
            check()

        def check():
            if pv_state["gc"]:
                pv_state["gc"].stop()
            for ch in pv.winfo_children():
                ch.destroy()
            pv.pack_forget()
            info, err = gw_share.decode(box.get("1.0", "end"), S._clean_steps)
            pv_state["info"] = info
            imp_b.set(disabled=info is None)
            if err:
                msg.config(text=err, fg=K.BAD)
                centre(dlg)
                return
            ev = info["event"]
            pv_state["ev"] = ev
            have = S._macros_load().get(ev, {})
            repl = [n for n, key in (("START", "steps"), ("END", "end_steps")) if info[key] and have.get(key)]
            msg.config(text=f"{ev.title()}{' - ' + info['name'] if info['name'] else ''}: {info['presses']} presses over {info['seconds']:.1f} s"
                            + (f"  ·  replaces your current {' and '.join(repl)} recording" if repl else ""),
                       fg=K.WARN if repl else K.OK)
            pv.pack(fill="x", pady=(10, 0))
            gc = gw_ghost.GhostCanvas(pv, 540, 304, K.BG, ev_colour(ev), mock=True)
            gc.pack()
            pv_state["gc"] = gc
            gc.play(info["steps"] + [dict(s_, t=s_["t"] + (max((x["t"] for x in info["steps"]), default=0) + 1.0)) for s_ in info["end_steps"]])
            centre(dlg)

        def do_import():
            info = pv_state["info"]
            if not info:
                return
            kw = {"enabled": False}
            if info["steps"]:
                kw["steps"] = info["steps"]
            if info["end_steps"]:
                kw["end_steps"] = info["end_steps"]
            S._macro_set(info["event"], **kw)
            if info["event"] not in S.KNOWN:                               # a card only exists for events seen on the board: add this one
                S.KNOWN.add(info["event"])
                S._save_known()
            st["status"][(info["event"], "start")] = "Imported. Pick an account, then switch it on"
            st["sig"] = None
            if pv_state["gc"]:
                pv_state["gc"].stop()
            dlg.destroy()

        def close():
            if pv_state["gc"]:
                pv_state["gc"].stop()
            dlg.destroy()

        imp_b = U.Btn(row, "Import it", do_import, theme, kind="primary", icon="download", h=38, size=12)
        imp_b.set(disabled=True)
        imp_b.pack(side="right")
        U.Btn(row, "Cancel", close, theme, h=38, size=12).pack(side="right", padx=(0, 8))
        U.Btn(row, "Check code", check, theme, icon="eye", h=38, size=12).pack(side="left")
        U.Btn(row, "Paste", paste, theme, icon="copy", h=38, size=12).pack(side="left", padx=(8, 0))
        centre(dlg)
        box.focus_set()
        if code:
            box.insert("1.0", code)
            check()
        return {"do": do_import, "close": close, "check": check, "state": pv_state, "box": box, "msg": msg}

    def make_card(ev):
        _g, col, _e = S._event_style(ev)
        card = U.Card(grid, CARD_W, 322, pad=18)
        inner = card.inner
        hdr = tk.Frame(inner, bg=K.CARD)
        hdr.pack(fill="x")
        badge = tk.Canvas(hdr, width=40, height=40, bg=K.CARD, highlightthickness=0, bd=0)
        badge.create_image(0, 0, anchor="nw", image=U.rr_photo(40, 40, 11, lerp(K.CARD, col, .15), lerp(K.CARD, col, .4)))
        badge.create_image(20, 20, image=U.icon(U.event_icon(ev), 22, col))
        badge.pack(side="left")
        tk.Label(hdr, text=ev.title(), bg=K.CARD, fg=K.TEXT, font=font(16, "bold")).pack(side="left", padx=(12, 0))
        sw = U.Switch(hdr, lambda v, e=ev: S._macro_set(e, enabled=v), theme)
        sw.pack(side="right")
        dd = U.Dropdown(hdr, account_options, lambda e=ev: (S._macros_load().get(e, {}) or {}).get("account"),
                        lambda v, e=ev: S._macro_set(e, account=v) if v else None, theme, w=160, h=34, bg=K.CARD,
                        placeholder="Account…", size=12)
        dd.pack(side="right", padx=(0, 12))
        blocks = {}
        for ph, cap, icn in (("start", "START", "circle-play"), ("end", "END", "circle-stop")):
            b = U.Card(inner, 404, 112, pad=0, fill=K.CARD2, radius=12, bg=K.CARD, edge=K.EDGE)
            b.pack(pady=(12 if ph == "start" else 8, 0))
            b.create_image(14, 12, anchor="nw", image=U.rr_photo(66, 26, 13, lerp(K.CARD2, col, .16), lerp(K.CARD2, col, .4)))
            b.create_image(27, 25, image=U.icon(icn, 14, col))
            b.create_text(40, 24, anchor="w", text=cap, fill=col, font=font(11, "bold"))
            tl = Timeline(b, 300, 30, K.CARD2)
            b.create_window(92, 10, anchor="nw", window=tl)
            meta = b.create_text(92, 49, anchor="w", text="", fill=K.FAINT, font=font(11, "medium"))
            stat = b.create_text(92, 63, anchor="w", text="", fill=K.DIM, font=font(12, "medium"))
            bar = tk.Frame(b, bg=K.CARD2)
            rec_b = U.Btn(bar, "Record", lambda e=ev, p=ph: mrec(e, p), theme, icon="circle-dot", kind="danger", h=30, size=12, bg=K.CARD2, pad=12)
            rec_b.pack(side="left")
            test_b = U.Btn(bar, "Test", lambda e=ev, p=ph: mtest(e, p), theme, icon="play", h=30, size=12, bg=K.CARD2, pad=12)
            test_b.pack(side="left", padx=6)
            dry_b = U.IconBtn(bar, "eye", lambda e=ev, p=ph: dry(e, p), theme, size=30, kind="ghost", bg=K.CARD2,
                              tip="Dry run: show where it would click, without clicking", px=16)
            dry_b.pack(side="left")
            shr_b = U.IconBtn(bar, "upload", lambda e=ev, p=ph: share(e, p), theme, size=30, kind="ghost", bg=K.CARD2,
                              tip="Share: copy a code for this event's macro to send to a friend", px=16)
            shr_b.pack(side="left")
            clr_b = U.IconBtn(bar, "trash-2", lambda e=ev, p=ph: clear(e, p), theme, size=30, kind="ghost", bg=K.CARD2, tip="Clear this recording", px=16)
            clr_b.pack(side="left")
            b.create_window(14, 76, anchor="nw", window=bar)
            blocks[ph] = dict(card=b, tl=tl, meta=meta, stat=stat, rec=rec_b, test=test_b, clr=clr_b, dry=dry_b, shr=shr_b, col=col)
        return dict(card=card, sw=sw, dd=dd, blocks=blocks, col=col)

    def refresh_card(ev, c, macros, busy):
        m_ = macros.get(ev, {})
        c["sw"].set(bool(m_.get("enabled")))
        c["dd"].refresh()
        for ph, blk in c["blocks"].items():
            steps = m_.get(S._phase_key(ph)) or []
            blk["tl"].show(steps, blk["col"])
            dur = max((s["t"] for s in steps), default=0)
            presses = sum(1 for s in steps if s.get("d"))
            k_ = (ev, ph)
            blk["card"].itemconfig(blk["meta"], text=(f"{presses} press{'es' if presses != 1 else ''}  ·  {dur:.1f} s" if steps else ""))
            msg_ = st["status"].get(k_)
            live_ = msg_ and (msg_.startswith(("Recording", "Running")) or time.time() - st["status"].t.get(k_, 0) < 6)
            run_ = S.MACRO_STATUS.get(k_)
            if live_:
                text, colour = msg_, K.WARN if msg_.startswith(("Recording", "Running")) else K.DIM
            elif run_:
                text = ("✔ " if run_["ok"] else "✖ ") + time.strftime("%H:%M", time.localtime(run_["t"])) + "  " + run_["msg"]
                colour = K.OK if run_["ok"] else K.BAD
            else:
                text, colour = "", K.DIM
            blk["card"].itemconfig(blk["stat"], text=ellipsize(text, font(12, "medium"), 296), fill=colour)
            blk["rec"].set(disabled=busy)
            blk["test"].set(disabled=busy or not steps)
            blk["clr"].set(disabled=busy or not steps)
            blk["dry"].set(disabled=busy or not steps)
            blk["shr"].set(disabled=not (m_.get("steps") or m_.get("end_steps")))

    def rebuild():
        macros = S._macros_load()
        names = sorted(S.KNOWN)
        sig = (tuple(names), tuple(a["label"] for a in ctx.load_accounts()))
        if sig != st["sig"]:
            st["sig"] = sig
            for ch in grid.winfo_children():
                ch.destroy()
            st["cards"].clear()
            empty.pack_forget()
            if not names:
                empty.pack(anchor="w")
                for ch in empty.winfo_children():
                    ch.destroy()
                c = U.Card(empty, SCROLL_W, 190, pad=0)
                c.pack()
                c.create_image(SCROLL_W / 2, 62, image=U.icon("wand-sparkles", 40, K.FAINT))
                c.create_text(SCROLL_W / 2, 110, text="No events seen yet", fill=K.TEXT, font=font(17, "semibold"))
                c.create_text(SCROLL_W / 2, 138, text="Mutations show up here after the weather board has been read once.", fill=K.DIM, font=font(13))
            for i, ev in enumerate(names):
                c = make_card(ev)
                c["card"].grid(row=i // 2, column=i % 2, padx=(0 if i % 2 == 0 else 16, 0), pady=(0, 16))
                st["cards"][ev] = c
        busy = bool(S._rec.get("on")) or S._play_lock.locked()
        for ev, c in st["cards"].items():
            refresh_card(ev, c, macros, busy)
        for fn in syncers:
            fn()

    # ================================================================== recording HUD (red bar) + hide the console meanwhile
    def hud_open():
        w = tk.Toplevel(root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=KEY)
        try:
            w.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass
        pw, ph = 470, 54
        w.geometry(f"{pw}x{ph}+{(w.winfo_screenwidth() - pw) // 2}+16")
        cv = tk.Canvas(w, width=pw, height=ph, bg=KEY, highlightthickness=0, bd=0)
        cv.pack()
        cv.create_image(0, 0, anchor="nw", image=U.rr_photo(pw, ph, 27, lerp(K.BG, K.BAD, .14), lerp(K.BG, K.BAD, .6), 2))
        dot = cv.create_image(28, 27)
        cv.create_text(48, 27, anchor="w", text="REC", fill=K.BAD, font=font(13, "bold"))
        what = cv.create_text(92, 27, anchor="w", text="", fill=K.TEXT, font=font(13, "semibold"))
        info = cv.create_text(pw - 128, 27, anchor="e", text="", fill=K.DIM, font=font(12, "medium"))
        stop = U.Btn(cv, "Stop", lambda: S._rec.update(on=False), theme, icon="square", kind="danger", h=34, size=12, bg=lerp(K.BG, K.BAD, .14), pad=12)
        cv.create_window(pw - 14, 27, anchor="e", window=stop)
        rec_ui["hud"] = (w, cv, dot, what, info)

    def hud_close():
        h = rec_ui["hud"]
        rec_ui["hud"] = None
        if h:
            try:
                h[0].destroy()
            except tk.TclError:
                pass

    def watch():
        try:
            on = bool(S._rec.get("on"))
            if on and not rec_ui["was"]:                              # a recording just started
                rec_ui["hidden"] = ctx.hide_console()
                if rec_ui["hud"] is None:
                    hud_open()
            if on and rec_ui["hud"]:
                w, cv, dot, what, info = rec_ui["hud"]
                t = time.time() - S._rec.get("t0", time.time())
                cv.itemconfig(what, text=f"{S._rec.get('event', '').title()}  ·  {S._rec.get('phase', '').upper()}")
                cv.itemconfig(info, text=f"{int(t // 60)}:{int(t % 60):02d}   ·   {S._rec.get('n', 0)} steps")
                on_dot = int(t * 2) % 2 == 0
                cv.itemconfig(dot, image=U.circle_photo(12, K.BAD if on_dot else lerp(K.BG, K.BAD, .35)))
            if not on and rec_ui["was"]:                              # it just finished
                hud_close()
                if rec_ui["hidden"]:
                    ctx.show_console(rec_ui["hidden"])
                    rec_ui["hidden"] = False
            rec_ui["was"] = on
        except tk.TclError:
            return
        root.after(300 if on else 800, watch)

    root.after(1000, watch)

    _imp, _dry, _share = import_dialog, dry, share

    class Page:
        always = False
        import_dialog = staticmethod(_imp)
        dry = staticmethod(_dry)
        share = staticmethod(_share)

        def slow(self):
            rebuild()

        def on_show(self):
            st["sig"] = None
            sf.to_top()
            rebuild()

    return Page()
