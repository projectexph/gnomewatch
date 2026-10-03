"""Admin page (only with the admin key): every key, who it belongs to, and what you can do with it.
List, revoke / restore, reset the device lock, add time, copy the key, and create a key for a name."""
import queue
import threading
import time
import tkinter as tk

import gw_license as L
import ui_kit as U
from ui_kit import K, font, lerp
import cs_common as C
from cs_common import CW, BODY_H, ago

ROW, HEAD = 46, 34
COLS = (("name", "PERSON", 190), ("key", "KEY", 150), ("made", "CREATED", 100), ("left", "TIME LEFT", 110), ("device", "PC", 96), ("seen", "LAST SEEN", 100))


def left_text(ms_left):
    if ms_left <= 0:
        return "expired"
    m = int(ms_left // 60000)
    return f"{m // 60}h {m % 60}m" if m >= 60 else f"{m}m"


def build(ctx):
    S, root, theme = ctx.S, ctx.root, ctx.theme
    page = ctx.pages["Admin"]
    st = {"keys": [], "sel": None, "off": 0, "note": "", "loaded": 0.0, "busy": False, "server": 0.0, "skew": 0.0}
    q = queue.Queue()

    def call(action, **kw):
        """Run an admin call in the background; the answer comes back through the queue."""
        if st["busy"]:
            return
        st["busy"] = True

        def work():
            res = L.admin_call(S.HERE, S.GW_SESSION["key"], action, **kw)
            q.put((action, res))

        threading.Thread(target=work, daemon=True).start()

    # ---- toolbar
    bar = tk.Frame(page, bg=K.BG)
    bar.pack(fill="x", pady=(0, 14))
    U.Btn(bar, "Refresh", lambda: call("list"), theme, icon="rotate-cw", bg=K.BG, h=40).pack(side="left", padx=(0, 8))
    U.Btn(bar, "Create a key", lambda: create_dialog(), theme, icon="plus", kind="primary", bg=K.BG, h=40).pack(side="left")
    count = tk.Label(bar, text="", bg=K.BG, fg=K.DIM, font=font(13, "medium"))
    count.pack(side="right")

    # ---- table
    TW = CW
    card = U.Card(page, TW, HEAD + ROW * 8 + 36, pad=0)
    card.pack(anchor="w")
    lv = tk.Canvas(card, width=TW - 2, height=HEAD + ROW * 8, bg=K.CARD, highlightthickness=0, bd=0)
    card.create_window(1, 18, anchor="nw", window=lv)
    lv.bind("<ButtonRelease-1>", lambda e: pick(e))
    lv.bind("<MouseWheel>", lambda e: scroll_rows(e))

    def key_status(rec):
        now = time.time() * 1000 + st["skew"] * 1000
        if rec.get("revoked"):
            return "REVOKED", K.BAD
        if rec["expires"] and rec["expires"] <= now:
            return "EXPIRED", K.FAINT
        return "ACTIVE", K.OK

    def draw():
        lv.delete("all")
        x = 24
        for key, title, w in COLS:
            lv.create_text(x, HEAD / 2, anchor="w", text=title, fill=K.FAINT, font=font(10, "semibold"))
            x += w
        lv.create_text(TW - 26, HEAD / 2, anchor="e", text="STATUS", fill=K.FAINT, font=font(10, "semibold"))
        lv.create_line(18, HEAD - 2, TW - 22, HEAD - 2, fill=K.EDGE)
        if not st["keys"]:
            lv.create_text(TW / 2, HEAD + 130, text="No keys yet" if st["loaded"] else "Loading keys…", fill=K.DIM, font=font(15, "medium"))
            return
        now = time.time() * 1000 + st["skew"] * 1000
        for i in range(8):
            idx = st["off"] + i
            if idx >= len(st["keys"]):
                break
            rec = st["keys"][idx]
            y = HEAD + i * ROW
            sel = rec["key"] == st["sel"]
            if sel:
                lv.create_image(10, y + 2, anchor="nw", image=U.rr_photo(TW - 22, ROW - 4, 10, lerp(K.CARD, theme.cur, .13), lerp(K.CARD, theme.cur, .4)))
            x = 24
            lbl, col = key_status(rec)
            vals = {"name": rec["name"], "key": rec["key"][:7] + "…" + rec["key"][-4:] if len(rec["key"]) > 12 else rec["key"],
                    "made": ago(time.time() - rec["created"] / 1000), "left": left_text(rec["expires"] - now) if rec["expires"] else "never",
                    "device": "locked" if rec.get("devices") else "free", "seen": ago(time.time() - rec["lastSeen"] / 1000) if rec.get("lastSeen") else "never"}
            for key, title, w in COLS:
                lv.create_text(x, y + ROW / 2, anchor="w", text=U.ellipsize(vals[key], font(13, "semibold" if key == "name" else "medium"), w - 12),
                               fill=K.TEXT if key == "name" else K.DIM, font=font(13, "semibold" if key == "name" else "medium"))
                x += w
            bw = U.text_w(lbl, font(10, "bold")) + 22
            lv.create_image(TW - 26 - bw, y + ROW / 2 - 11, anchor="nw", image=U.rr_photo(bw, 22, 11, lerp(K.CARD, col, .16), lerp(K.CARD, col, .4)))
            lv.create_text(TW - 26 - bw / 2, y + ROW / 2, text=lbl, fill=col, font=font(10, "bold"))
        if len(st["keys"]) > 8:
            lv.create_text(TW - 26, HEAD + 8 * ROW - 8, anchor="se", text=f"{st['off'] + 1}-{min(len(st['keys']), st['off'] + 8)} of {len(st['keys'])}  ·  scroll for more",
                           fill=K.FAINT, font=font(11, "medium"))

    def pick(e):
        i = (e.y - HEAD) // ROW
        idx = st["off"] + int(i)
        if 0 <= i < 8 and idx < len(st["keys"]):
            st["sel"] = st["keys"][idx]["key"]
            refresh_actions()
            draw()

    def scroll_rows(e):
        if len(st["keys"]) > 8:
            st["off"] = max(0, min(len(st["keys"]) - 8, st["off"] - (1 if e.delta > 0 else -1)))
            draw()

    # ---- actions on the selected key
    act = tk.Frame(page, bg=K.BG)
    act.pack(fill="x", pady=(14, 0))
    sel_lbl = tk.Label(act, text="Select a key to manage it", bg=K.BG, fg=K.DIM, font=font(13, "medium"))
    sel_lbl.pack(side="left", padx=(0, 18))
    btns = {}

    def need(fn):
        return lambda: fn() if st["sel"] else None

    def copy_key():
        root.clipboard_clear()
        root.clipboard_append(st["sel"])
        st["note"] = "Key copied"

    btns["revoke"] = U.Btn(act, "Revoke", need(lambda: call("revoke" if not cur_rec().get("revoked") else "unrevoke", target=st["sel"])), theme,
                           icon="ban", kind="danger", bg=K.BG, h=38, size=12)
    btns["reset"] = U.Btn(act, "Reset PC lock", need(lambda: call("reset", target=st["sel"])), theme, icon="rotate-cw", bg=K.BG, h=38, size=12)
    btns["extend"] = U.Btn(act, "Add 24 hours", need(lambda: call("extend", target=st["sel"], hours=24)), theme, icon="timer", bg=K.BG, h=38, size=12)
    btns["copy"] = U.Btn(act, "Copy key", need(copy_key), theme, icon="copy", bg=K.BG, h=38, size=12)
    for b in btns.values():
        b.pack(side="left", padx=(0, 8))
    note = tk.Label(act, text="", bg=K.BG, fg=K.WARN, font=font(12, "medium"))
    note.pack(side="right")

    def cur_rec():
        return next((r for r in st["keys"] if r["key"] == st["sel"]), {})

    def refresh_actions():
        r = cur_rec()
        sel_lbl.config(text=f"{r['name']}  ·  {r['key']}" if r else "Select a key to manage it", fg=K.TEXT if r else K.DIM)
        btns["revoke"].set("Restore" if r.get("revoked") else "Revoke", kind="ok" if r.get("revoked") else "danger", icon="rotate-cw" if r.get("revoked") else "ban")
        for b in btns.values():
            b.set(disabled=not r)

    # ---- create-a-key dialog
    def create_dialog():
        dlg = tk.Toplevel(root)
        dlg.overrideredirect(True)
        dlg.attributes("-topmost", True)
        dlg.configure(bg=K.EDGE_HI)
        body = tk.Frame(dlg, bg=K.CARD)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        inner = tk.Frame(body, bg=K.CARD)
        inner.pack(fill="both", expand=True, padx=26, pady=(24, 22))
        tk.Label(inner, text="Create a key", bg=K.CARD, fg=K.TEXT, font=font(18, "bold")).pack(anchor="w")
        tk.Label(inner, text="For someone who isn't in the server, or to give a longer key.", bg=K.CARD, fg=K.DIM, font=font(12)).pack(anchor="w", pady=(2, 14))
        tk.Label(inner, text="NAME", bg=K.CARD, fg=K.FAINT, font=font(10, "semibold")).pack(anchor="w")
        nm = tk.StringVar()
        U.EntryBox(inner, nm, w=360, h=40, theme=theme, size=13, placeholder="Their Discord name").pack(anchor="w", pady=(3, 12))
        tk.Label(inner, text="LASTS", bg=K.CARD, fg=K.FAINT, font=font(10, "semibold")).pack(anchor="w")
        hrs = {"v": 24}
        seg = U.Seg(inner, [("6 h", 6), ("24 h", 24), ("7 days", 168), ("30 days", 720)], lambda: hrs["v"], lambda v: (hrs.update(v=v), seg.sync()), theme, seg_w=84, h=34)
        seg.pack(anchor="w", pady=(3, 18))
        row = tk.Frame(inner, bg=K.CARD)
        row.pack(fill="x")
        U.Btn(row, "Create", lambda: (call("create", name=nm.get().strip(), hours=hrs["v"]), dlg.destroy()), theme, kind="primary", icon="plus", h=38).pack(side="right")
        U.Btn(row, "Cancel", dlg.destroy, theme, h=38).pack(side="right", padx=(0, 10))
        dlg.update_idletasks()
        w_, h_ = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        dlg.geometry(f"{w_}x{h_}+{root.winfo_rootx() + (C.W - w_) // 2}+{root.winfo_rooty() + (C.H - h_) // 2 - 30}")
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        dlg.focus_force()

    def poll():
        try:
            while True:
                action, res = q.get_nowait()
                st["busy"] = False
                if not res.get("ok"):
                    st["note"] = res.get("error") or "That didn't work."
                    continue
                if action == "list":
                    st["keys"] = res["keys"]
                    st["loaded"] = time.time()
                    st["skew"] = res.get("serverTime", time.time() * 1000) / 1000.0 - time.time()
                    if st["sel"] not in [r["key"] for r in st["keys"]]:
                        st["sel"] = None
                elif action == "create":
                    st["note"] = f"Created a key for {res['key']['name']}: {res['key']['key']}"
                    st["sel"] = res["key"]["key"]
                    call("list")
                else:
                    st["note"] = {"revoke": "Revoked", "unrevoke": "Restored", "reset": "PC lock reset", "extend": "Added 24 hours"}.get(action, "Done")
                    call("list")
                refresh_actions()
                draw()
        except queue.Empty:
            pass

    class Page:
        def slow(self):
            poll()
            count.config(text=f"{sum(1 for r in st['keys'] if key_status(r)[0] == 'ACTIVE')} active · {len(st['keys'])} total" if st["keys"] else "")
            if note.cget("text") != st["note"]:
                note.config(text=st["note"])
            if st["keys"] and time.time() - st["loaded"] > 60:
                call("list")
            draw() if int(time.time()) % 5 == 0 else None

        def on_show(self):
            call("list")
            refresh_actions()
            draw()

    refresh_actions()
    return Page()
