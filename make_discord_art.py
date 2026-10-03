"""Makes the Rich Presence pictures for Gnomewatch into the discord_art folder (1024 x 1024 PNGs):
    gnome.png            your logo                     (the main picture)
    aurora.png ...       one per weather event         (shown while that event is on)

Upload them once: Discord Developer Portal > Gnomewatch > Rich Presence > Art Assets > Add Image(s). Keep the file names (without .png): they are the
names Gnomewatch looks for, and it only uses the ones Discord actually has.   python make_discord_art.py
"""
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "discord_art")
ICONS = os.path.join(HERE, "assets", "ui", "icons")
LOGO = os.path.join(HERE, "assets", "brand", "logo_512.png")
SIZE = 1024
# name, colour, Lucide icon file
EVENTS = [("aurora", "#5cffc4", "sparkles"), ("solar_flare", "#ffb03c", "sun"), ("candy_land", "#ff7ac8", "candy"),
          ("lightning_storm", "#8fb8ff", "cloud-lightning"), ("toxic", "#7dff4d", "biohazard"), ("cursed", "#b06bff", "skull"),
          ("blizzard", "#9fd8ff", "snowflake")]


def rgb(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def background(colour):
    """Dark night with a soft glow of the event's colour behind the middle."""
    base = rgb("#0a0c11")
    c = rgb(colour)
    img = Image.new("RGB", (SIZE, SIZE), base)
    glow = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(glow).ellipse([SIZE * 0.1, SIZE * 0.1, SIZE * 0.9, SIZE * 0.9], fill=255)
    glow = glow.filter(ImageFilter.GaussianBlur(SIZE * 0.17))
    wash = Image.new("RGB", (SIZE, SIZE), mix(base, c, 0.55))
    img = Image.composite(wash, img, glow.point(lambda p: int(p * 0.62)))
    vignette = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(vignette).ellipse([-SIZE * 0.2, -SIZE * 0.2, SIZE * 1.2, SIZE * 1.2], fill=255)
    vignette = vignette.filter(ImageFilter.GaussianBlur(SIZE * 0.16))
    return Image.composite(img, Image.new("RGB", (SIZE, SIZE), rgb("#06070b")), vignette)


def icon_mask(name, px):
    src = Image.open(os.path.join(ICONS, name + ".png")).convert("RGBA")
    a = src.getchannel("A") if src.getextrema()[3][1] > 0 else src.convert("L")
    return a.resize((px, px), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(1.2))


def event_art(name, colour, icon):
    c = rgb(colour)
    img = background(colour).convert("RGBA")
    # a ring, drawn large and soft, then crisp
    ring = Image.new("L", (SIZE, SIZE), 0)
    d = ImageDraw.Draw(ring)
    d.ellipse([SIZE * 0.14, SIZE * 0.14, SIZE * 0.86, SIZE * 0.86], outline=255, width=14)
    glow = ring.filter(ImageFilter.GaussianBlur(26))
    img = Image.composite(Image.new("RGBA", (SIZE, SIZE), c + (255,)), img, glow.point(lambda p: int(min(255, p * 1.3))))
    img = Image.composite(Image.new("RGBA", (SIZE, SIZE), mix(c, (255, 255, 255), .35) + (255,)), img, ring)
    # the icon, with a bloom behind it
    px = int(SIZE * 0.5)
    m = icon_mask(icon, px)
    big = Image.new("L", (SIZE, SIZE), 0)
    big.paste(m, ((SIZE - px) // 2, (SIZE - px) // 2))
    bloom = big.filter(ImageFilter.GaussianBlur(34))
    img = Image.composite(Image.new("RGBA", (SIZE, SIZE), c + (255,)), img, bloom.point(lambda p: int(min(255, p * 1.1))))
    img = Image.composite(Image.new("RGBA", (SIZE, SIZE), mix(c, (255, 255, 255), .78) + (255,)), img, big)
    return img.convert("RGB")


def gnome_art():
    img = background("#ff7a45").convert("RGBA")
    logo = Image.open(LOGO).convert("RGBA")
    side = int(SIZE * 0.78)
    logo = logo.resize((side, side), Image.Resampling.LANCZOS)
    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 170), ((SIZE - side) // 2, (SIZE - side) // 2 + 18), logo.getchannel("A"))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(22)))
    img.alpha_composite(logo, ((SIZE - side) // 2, (SIZE - side) // 2))
    return img.convert("RGB")


def main():
    os.makedirs(OUT, exist_ok=True)
    gnome_art().save(os.path.join(OUT, "gnome.png"))
    for name, colour, icon in EVENTS:
        event_art(name, colour, icon).save(os.path.join(OUT, name + ".png"))
    # one contact sheet so they can be looked at together
    names = ["gnome"] + [e[0] for e in EVENTS]
    sheet = Image.new("RGB", (256 * 4, 256 * 2), (10, 12, 17))
    for i, n in enumerate(names):
        sheet.paste(Image.open(os.path.join(OUT, n + ".png")).resize((256, 256), Image.Resampling.LANCZOS), ((i % 4) * 256, (i // 4) * 256))
    sheet.save(os.path.join(OUT, "_preview.png"))
    print("made", len(names), "pictures in", OUT)


if __name__ == "__main__":
    main()
