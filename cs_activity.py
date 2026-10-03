"""Activity page: what Gnomewatch is reading right now (live sign view) and the live log underneath."""
import time
import tkinter as tk

from PIL import Image, ImageDraw, ImageOps, ImageTk

import ui_kit as U
from ui_kit import K, font, lerp
import cs_common as C
from cs_common import CW, BODY_H, ago


def build(ctx):
    S, root, theme = ctx.S, ctx.root, ctx.theme
    page = ctx.pages["Activity"]

    # ---- live sign view (top): the zoomed crop of the weather sign with what was read, and a few facts beside it
    SW, SH = 300, 100
    TOPH = 152
    sign_card = U.Card(page, CW, TOPH, pad=0)
    sign_card.pack(anchor="w")
    sign_card.create_image(20, 25, anchor="w", image=U.icon("eye", 16, K.FAINT))
    sign_card.create_text(44, 25, anchor="w", text="LIVE SIGN VIEW", fill=K.FAINT, font=font(11, "semibold"))
    sign_img = sign_card.create_image(20, 40, anchor="nw")
    sign_msg = sign_card.create_text(20 + SW / 2, 40 + SH / 2, text="Sign not in view", fill=K.FAINT, font=font(13, "medium"))
    sign_ph = [None]
    sign_last = [0.0]
    # facts to the right of the picture
    fx = 20 + SW + 36
    facts = []
    for i, ttl in enumerate(("READING", "TIME LEFT", "SEEN", "SOURCE")):
        x = fx + (i % 2) * 260
        y = 46 + (i // 2) * 52
        sign_card.create_text(x, y, anchor="w", text=ttl, fill=K.FAINT, font=font(10, "semibold"))
        facts.append(sign_card.create_text(x, y + 22, anchor="w", text="–", fill=K.TEXT, font=font(16, "bold")))

    def update_sign():
        """Zoomed crop of the weather sign from the board window, with the text boxes it read outlined."""
        if time.time() - sign_last[0] < 1.0:
            return
        sign_last[0] = time.time()
        kind_, name_, left_ = S._event_summary()
        seen = (S.STATE.get(kind_) or (0, 0, 0))[2] if kind_ != "none" else None
        src = "Corner icon" if (kind_ == "active" and S.STATE.get("source") == "icon") else ("Weather board" if kind_ != "none" else "–")
        vals = (name_.title() if kind_ != "none" else "–", S._fmt_left(left_) if kind_ != "none" else "–",
                ago(time.time() - seen) if seen else "–", src)
        for item, v in zip(facts, vals):
            ctx.ic(sign_card, item, text=v, fill=K.TEXT if v != "–" else K.FAINT)
        fr, sg = S._last_frame[0], S.STATE.get("sign")
        if fr is None or not sg or not sg["boxes"] or time.time() - sg["t"] > 25:
            sign_card.itemconfig(sign_img, image="")
            sign_card.itemconfig(sign_msg, state="normal")
            return
        bx = sg["boxes"]
        x0, y0 = min(b[0] for b in bx), min(b[1] for b in bx)
        x1, y1 = max(b[0] + b[2] for b in bx), max(b[1] + b[3] for b in bx)
        W_, H_ = fr.size
        l, t = max(0.0, x0 - .07), max(0.0, y0 - .06)
        r, b_ = min(1.0, x1 + .07), min(1.0, y1 + .05)
        crop = fr.crop((int(l * W_), int(t * H_), int(r * W_), int(b_ * H_)))
        d = ImageDraw.Draw(crop)
        for (bx0, by0, bw_, bh_) in bx:
            d.rectangle([(bx0 - l) * W_, (by0 - t) * H_, (bx0 + bw_ - l) * W_, (by0 + bh_ - t) * H_], outline=theme.cur, width=2)
        crop = ImageOps.contain(crop, (SW, SH), Image.Resampling.BILINEAR)
        panel = Image.new("RGBA", (SW, SH), _hex(K.CARD2))
        panel.paste(crop.convert("RGBA"), ((SW - crop.width) // 2, (SH - crop.height) // 2))
        panel.putalpha(U.rr_pil(SW, SH, 10, "#ffffff", None).getchannel("A"))
        ph = ImageTk.PhotoImage(panel)
        sign_ph[0] = ph
        sign_card.itemconfig(sign_img, image=ph)
        sign_card.itemconfig(sign_msg, state="hidden")

    def _hex(c):
        c = c.lstrip("#")
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), 255

    # ---- live log (below, full width)
    log_card = U.Card(page, CW, BODY_H - TOPH - 16, pad=20, title="Log (live)", icon="scroll-text")
    log_card.pack(anchor="w", pady=(16, 0))
    feed_big = tk.Text(log_card.inner, bg=K.CARD, fg=K.DIM, font=("Consolas", -13), bd=0, relief="flat", highlightthickness=0,
                       state="disabled", wrap="none", cursor="arrow", spacing1=2, spacing3=2)
    feed_big.pack(fill="both", expand=True)

    class Page:
        def slow(self):
            lines = ctx.feed_lines(200)
            if lines != ctx.feed_sig["big"]:
                ctx.feed_sig["big"] = lines
                ctx.fill_feed(feed_big, lines)
            update_sign()

        def on_show(self):
            ctx.feed_sig["big"] = None
            sign_last[0] = 0.0

    return Page()
