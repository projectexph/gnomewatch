"""Instances page: a live wall of every Roblox window.
Each tile: a live picture, which account it is, its state, RAM / CPU / uptime, and buttons (hide, rejoin, close).
The size switch shows 4, 3 or 2 tiles per row; "Arrange" tiles the real windows across your screen."""
import ctypes
import time
import tkinter as tk

from PIL import Image, ImageOps, ImageTk

import ui_kit as U
from ui_kit import K, font, lerp
import cs_common as C
from cs_common import CW, BODY_H, SCROLL_W
from cs_accounts import colour_of, initials, fmt_ram, fmt_up, STATE_LABEL

SIZES = {"s": 4, "m": 3, "l": 2}
GAP = 14


def build(ctx):
    S, root, theme = ctx.S, ctx.root, ctx.theme
    page = ctx.pages["Instances"]
    assign, set_assign = ctx.assign, ctx.set_assign
    ic = ctx.ic

    # ---- toolbar
    bar = tk.Frame(page, bg=K.BG)
    bar.pack(fill="x", pady=(0, 14))
    U.Btn(bar, "Show all", lambda: S._set_show(True), theme, icon="eye", bg=K.BG, h=40).pack(side="left", padx=(0, 8))
    U.Btn(bar, "Hide all", lambda: S._set_show(False), theme, icon="eye-off", bg=K.BG, h=40).pack(side="left", padx=(0, 8))
    arrange_btn = U.Btn(bar, "Arrange", lambda: threading_run(S._grid_toggle), theme, icon="layout-grid", bg=K.BG, h=40)
    arrange_btn.pack(side="left", padx=(0, 8))
    U.Btn(bar, "New instance", S._launch_instance, theme, icon="plus", kind="primary", bg=K.BG, h=40).pack(side="left")
    ctx.confirm_btn(bar, "Close all", "Sure? Click again", S._close_all, icon="power", bg=K.BG, h=40).pack(side="right")
    size_seg = U.Seg(bar, [("Small", "s"), ("Medium", "m"), ("Large", "l")], lambda: S.WALL_SIZE, lambda v: (S._set_setting("WALL_SIZE", v), rebuild(True)),
                     theme, seg_w=74, h=40, bg=K.BG, size=12)
    size_seg.pack(side="right", padx=(0, 12))

    def threading_run(fn):
        import threading
        threading.Thread(target=fn, daemon=True).start()

    sf = U.ScrollFrame(page, CW, BODY_H - 54, bg=K.BG, theme=theme)
    sf.pack(anchor="w")
    ctx.scrollers.append(sf)
    grid = sf.inner
    wall = {"sig": None, "tiles": {}, "thumbs": {}, "last_thumb": 0.0}

    def tile_dims():
        cols = SIZES.get(S.WALL_SIZE, 3)
        tw = (SCROLL_W - GAP * (cols - 1)) // cols
        th = int((tw - 24) * 9 / 16)
        return cols, tw, th, th + 24 + 78

    def account_options():
        return [("Not set", None)] + [(a["label"], a["label"]) for a in ctx.load_accounts()]

    def make_tile(i, e, cols, tw, th, tile_h):
        hwnd, role, title, hidden, mini = e
        S._ensure_capture(hwnd)
        c = U.Card(grid, tw, tile_h, pad=0)
        c.grid(row=i // cols, column=i % cols, padx=(0 if i % cols == 0 else GAP, 0), pady=(0, GAP))
        thumb = c.create_image(12, 12, anchor="nw")
        state_pill = U.Pill(c, "IN GAME", K.OK, bg=K.CARD)
        c.create_window(22, 22, anchor="nw", window=state_pill)
        ui = {"card": c, "thumb": thumb, "pill": state_pill, "hwnd": hwnd, "tw": tw, "th": th}
        if role == "board":
            bp = U.Pill(c, "BOARD", K.WARN, bg=K.CARD, icon="layout-dashboard")
            c.create_window(tw - 22, 22, anchor="ne", window=bp)
        y0 = 12 + th + 10
        ui["avatar"] = c.create_image(12 + 15, y0 + 15)
        ui["ini"] = c.create_text(12 + 15, y0 + 15, text="", fill=K.TEXT, font=font(11, "bold"))
        ui["name"] = c.create_text(12 + 36, y0 + 9, anchor="w", text="", fill=K.TEXT, font=font(13, "semibold"))
        ui["sub"] = c.create_text(12 + 36, y0 + 26, anchor="w", text="", fill=K.FAINT, font=font(11, "medium"))
        ui["dd"] = None
        ui["chips_y"] = y0 + 50
        ui["chips"] = []
        btns = tk.Frame(c, bg=K.CARD)
        c.create_window(tw - 10, y0 + 15, anchor="e", window=btns)
        ui["btns"] = btns
        ui["mode"] = None
        return ui

    def fill_buttons(ui, e, label):
        hwnd, role, title, hidden, mini = e
        mode = (hidden, mini, bool(label))
        if ui["mode"] == mode:
            return
        ui["mode"] = mode
        for ch in ui["btns"].winfo_children():
            ch.destroy()
        if mini:
            U.IconBtn(ui["btns"], "maximize-2", lambda h=hwnd: ctypes.windll.user32.ShowWindowAsync(h, 9), theme, size=28, kind="primary",
                      bg=K.CARD, tip="Restore", px=15).pack(side="left")
        else:
            U.IconBtn(ui["btns"], "eye" if hidden else "eye-off", lambda h=hwnd, hd=hidden: S._set_window_show(h, hd), theme, size=28, kind="ghost",
                      bg=K.CARD, tip="Show this window" if hidden else "Hide this window", px=15).pack(side="left")
        a = next((x for x in ctx.load_accounts() if x["label"] == label), None) if label else None
        if a:
            U.IconBtn(ui["btns"], "rotate-cw", lambda x=a: ctx.rejoin(x), theme, size=28, kind="ghost", bg=K.CARD, tip="Close and join again",
                      px=15).pack(side="left", padx=(2, 0))
        ctx.confirm_btn(ui["btns"], "", "Close?", lambda h=hwnd: S._close_window(h), icon="x", h=28, w=62, pad=8, size=11, bg=K.CARD).pack(side="left", padx=(2, 0))

    def refresh_tile(ui, e):
        hwnd, role, title, hidden, mini = e
        c, tw = ui["card"], ui["tw"]
        label = assign.get(hwnd)
        info = S._client_for_hwnd(hwnd) or {}
        a = next((x for x in ctx.load_accounts() if x["label"] == label), None) if label else None
        col = colour_of(a) if a else (K.OK if role == "board" else theme.cur)
        lab, ccol = STATE_LABEL.get(info.get("state"), ("IN GAME", "OK")) if info else ("WINDOW", "DIM")
        if mini:
            lab, ccol = "MINIMISED", "WARN"
        ui["pill"].set(lab, getattr(K, ccol))
        ic(c, ui["avatar"], image=U.circle_photo(30, lerp(K.CARD, col, .22), col, 2))
        ic(c, ui["ini"], text=initials(label) if label else ("B" if role == "board" else "R"), fill=col)
        if label:
            ic(c, ui["name"], text=U.ellipsize(label, font(13, "semibold"), tw - 12 - 36 - 110), state="normal")
            if ui["dd"] is not None:
                c.itemconfig(ui["dd"], state="hidden")
        else:
            ic(c, ui["name"], text="Weather board" if role == "board" else "Roblox window", state="normal")
        job = info.get("job")
        sub = (f"Server {job[:8]}" if job else (title or "")) + (" · hidden" if hidden and not mini else "")
        bad = info.get("state") == "disconnected" and info.get("reason")
        ic(c, ui["sub"], text=U.ellipsize(info["reason"] if bad else sub, font(11, "medium"), tw - 12 - 36 - 110), fill=K.BAD if bad else K.FAINT)
        # chips: RAM, CPU, uptime
        chips = []
        if info:
            chips = [("memory-stick", fmt_ram(info.get("rss", 0))), ("cpu", f"{info.get('cpu', 0):.0f}%")]
            if info.get("start"):
                chips.append(("timer", fmt_up(time.time() - info["start"])))
        sig = tuple(chips)
        if ui.get("chip_sig") != sig:
            ui["chip_sig"] = sig
            c.delete("chip")
            x = 12
            for icn, text in chips:
                c.create_image(x, ui["chips_y"], anchor="nw", image=U.icon(icn, 14, K.FAINT), tags="chip")
                c.create_text(x + 19, ui["chips_y"] + 7, anchor="w", text=text, fill=K.DIM, font=font(11, "medium"), tags="chip")
                x += 19 + U.text_w(text, font(11, "medium")) + 14
        fill_buttons(ui, e, label)

    def rebuild(force=False):
        snap = list(S._snapshot)
        sig = (tuple(snap), tuple(sorted(assign.items())), tuple(a["label"] for a in ctx.load_accounts()), S.WALL_SIZE)
        if sig != wall["sig"] or force:
            wall["sig"] = sig
            for ch in grid.winfo_children():
                ch.destroy()
            wall["tiles"].clear()
            wall["thumbs"].clear()
            if not snap:
                c = U.Card(grid, SCROLL_W, 230, pad=0)
                c.pack(anchor="w")
                c.create_image(SCROLL_W / 2, 74, image=U.icon("monitor-off", 46, K.FAINT))
                c.create_text(SCROLL_W / 2, 126, text="No Roblox window found", fill=K.TEXT, font=font(17, "semibold"))
                c.create_text(SCROLL_W / 2, 154, text="Open Roblox or launch an account and it will appear on the wall.", fill=K.DIM, font=font(13))
                return
            cols, tw, th, tile_h = tile_dims()
            for i, e in enumerate(snap):
                wall["tiles"][e[0]] = make_tile(i, e, cols, tw, th, tile_h)
        for e in snap:
            ui = wall["tiles"].get(e[0])
            if ui:
                refresh_tile(ui, e)
        arrange_btn.set("Restore" if S._grid["on"] else "Arrange", icon="rotate-cw" if S._grid["on"] else "layout-grid")
        size_seg.sync()

    def update_thumbs():
        if time.time() - wall["last_thumb"] < 1.0:
            return
        wall["last_thumb"] = time.time()
        for hwnd, ui in list(wall["tiles"].items()):
            tw, th = ui["tw"] - 24, ui["th"]
            st_ = S._caps.get(hwnd)
            arr = st_.get("frame") if st_ else None
            panel = Image.new("RGBA", (tw, th), (20, 26, 40, 255))
            if arr is not None:
                im = ImageOps.contain(Image.fromarray(arr[:, :, [2, 1, 0]]), (tw, th), Image.Resampling.BILINEAR).convert("RGBA")
                panel.paste(im, ((tw - im.width) // 2, (th - im.height) // 2))
            panel.putalpha(U.rr_pil(tw, th, 10, "#ffffff", None).getchannel("A"))
            ph = ImageTk.PhotoImage(panel)
            wall["thumbs"][hwnd] = ph
            ui["card"].itemconfig(ui["thumb"], image=ph)

    class Page:
        def slow(self):
            rebuild()
            update_thumbs()

        def on_show(self):
            wall["sig"] = None
            sf.to_top()
            rebuild()

    return Page()
