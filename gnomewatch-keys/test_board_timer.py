"""Board reading: the countdown Windows OCR skips on the first pass is found by the second look.   python test_board_timer.py"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.argv = ["x"]
os.environ.pop("GW_BOOT", None)
import sentinel as S
from PIL import Image

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


if not os.path.isdir(os.path.join(HERE, "fixtures")):
    print("skipped: the board screenshots used by this test are not part of the public source")
    sys.stdout.flush()
    sys.exit(0)
for fname, want in (("board_solar_flare_2m59.png", 179), ("board_solar_flare_b.png", 338)):
    img = Image.open(os.path.join(HERE, "fixtures", fname)).convert("RGB")
    boxes = S.ocr(img)
    hit = S.find_weather(boxes)
    check(f"{fname}: the event name is read on the first pass", hit and hit[0] == "solar flare", hit)
    if hit and hit[1] is not None:
        check(f"{fname}: first pass already read the timer (fine)", hit[1] == want or abs(hit[1] - want) <= 1, hit)
        continue
    check(f"{fname}: the first pass misses the countdown (what the log showed)", hit and hit[1] is None, hit)
    got = S._reread_timer(img, S._sign_boxes[0])
    check(f"{fname}: the second look reads it: {want} s", got == want, got)

# nothing to read: a plain picture gives None, not a made-up time
blank = Image.new("RGB", (800, 600), (120, 60, 30))
check("a picture with no countdown gives None, not a guess", S._reread_timer(blank, (0.5, 0.4, 0.15, 0.04)) is None)
check("a tiny / off-edge box is handled", S._reread_timer(blank, (0.999, 0.999, 0.001, 0.001)) is None)
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.stdout.flush()
sys.exit(1 if fails else 0)
