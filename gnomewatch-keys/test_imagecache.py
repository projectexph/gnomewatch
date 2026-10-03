"""Regression: the shared image cache being emptied (it happens after enough buttons/animations) must NOT blank the screen.
    python test_imagecache.py [outdir]"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from feature_harness import Harness

OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.gettempdir()
fails = []


def check(name, cond, extra=""):
    print((("PASS " if cond else "FAIL ") + name + (f"   {str(extra)[:400]}" if extra and not cond else "")).encode("ascii", "replace").decode())
    if not cond:
        fails.append(name)


SCRIPT = r'''
import gc, json, os, time
from PIL import ImageGrab, ImageChops, ImageStat
out = {}
def grab(name):
    root.lift(); root.update()
    x, y, wd, ht = root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height()
    im = ImageGrab.grab(bbox=(x, y, x + wd, y + ht)).convert("RGB")
    im.save(os.path.join(os.environ["GW_TEST_SHOTS"], "cache_" + name + ".png"))
    return im
def diff(a, b):
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3.0
pages = ["Settings", "Auto-place", "Help", "Overview"]
imgs = {}
state = {"i": 0}
def one():
    i = state["i"]
    if i >= len(pages):
        out["done"] = True
        json.dump(out, open(os.environ["GW_TEST_OUT"], "w")); return
    p = pages[i]
    show(p); root.update()
    root.after(900, lambda: two(p))
def two(p):
    a = grab(p + "_before")
    n0 = len(U._photos)
    U._photos.clear()                                  # exactly what the cache does when it fills up
    gc.collect()
    root.update()
    time.sleep(0.2); root.update()
    b = grab(p + "_after")
    out[p] = {"diff": round(diff(a, b), 3), "cached_before": n0}
    state["i"] += 1
    root.after(300, one)
root.after(1500, one)
'''
h = Harness(8810, profile_files={"known_events.json": ["aurora", "lightning storm"], "accounts.json": [{"label": "Main", "browser": "edge", "profile": "Default"}],
                                 "state.json": {"wizard_done": True, "last_version": __import__("gw_version").VERSION}}, outdir=OUT)
try:
    r = h.run(SCRIPT, wait=120, script_delay=2500)
    res = r["result"] or {}
    check("the script ran to the end", res.get("done"), r["log"][-400:])
    for p in ("Settings", "Auto-place", "Help", "Overview"):
        d = (res.get(p) or {}).get("diff")
        check(f"{p}: emptying the image cache changes nothing on screen", d is not None and d < 1.5, res.get(p))
finally:
    h.close()
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
