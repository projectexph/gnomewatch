"""The overlay widget inside a sandbox console.   python test_overlay.py [outdir]"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from feature_harness import Harness

OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.gettempdir()
fails = []


def check(name, cond, extra=""):
    print((("PASS " if cond else "FAIL ") + name + (f"   {str(extra)[:300]}" if extra and not cond else "")).encode("ascii", "replace").decode())
    if not cond:
        fails.append(name)


SCRIPT = r'''
import ctypes, json, os, time
from PIL import ImageGrab
out = {}
def snap(w, name):
    root.update_idletasks(); w.update_idletasks()
    x, y, wd, ht = w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + wd, y + ht)).save(os.path.join(os.environ["GW_TEST_SHOTS"], "feat_" + name + ".png"))
def save():
    json.dump(out, open(os.environ["GW_TEST_OUT"], "w"))
S.PAUSED.set()                                  # stop reading the real game windows on this PC, so only what the test sets is shown
ov = ctx.overlay
now = time.time()
S.STATE["active"] = ("aurora", now + 125, now)
S.STATE["next"] = None
S._set_setting("OVERLAY_X", root.winfo_rootx() + 330)       # parked over the console so a picture of it shows only this program
S._set_setting("OVERLAY_Y", root.winfo_rooty() + 250)
S._set_setting("OVERLAY", True)
def exstyle():
    u = ctypes.windll.user32
    hwnd = u.GetParent(ov.win.winfo_id()) or ov.win.winfo_id()
    return bool(u.GetWindowLongW(hwnd, -20) & 0x20)
def a():
    out["open"] = ov.win is not None
    out["head"] = ov.cv.itemcget(ov.t_head, "text")
    out["count"] = ov.cv.itemcget(ov.t_count, "text")
    out["sub"] = ov.cv.itemcget(ov.t_sub, "text")
    snap(ov.win, "overlay_active")
    S.STATE["active"] = None
    S.STATE["next"] = ("blizzard", time.time() + 300, time.time())
    root.after(1700, b)
def b():
    out["next_head"] = ov.cv.itemcget(ov.t_head, "text")
    out["next_count"] = ov.cv.itemcget(ov.t_count, "text")
    out["next_sub"] = ov.cv.itemcget(ov.t_sub, "text")
    snap(ov.win, "overlay_next")
    S._set_setting("OVERLAY_NEXT", False)
    root.after(1700, c)
def c():
    out["hidden_next"] = ov.cv.itemcget(ov.t_head, "text")
    out["ex_unlocked"] = exstyle()
    S._set_setting("OVERLAY_LOCK", True)
    root.after(1700, d)
def d():
    out["ex_locked"] = exstyle()
    S._set_setting("OVERLAY_LOCK", False)
    root.after(1700, e)
def e():
    class Ev: pass
    ev = Ev(); ev.x_root, ev.y_root = ov.win.winfo_x() + 20, ov.win.winfo_y() + 20
    ov._press(ev)
    ev2 = Ev(); ev2.x_root, ev2.y_root = ev.x_root + 40, ev.y_root + 25
    ov._move(ev2); ov._release(ev2)
    out["moved"] = [S.OVERLAY_X, S.OVERLAY_Y, ov.win.winfo_x(), ov.win.winfo_y()]
    S._set_setting("OVERLAY", False)
    root.after(1700, f)
def f():
    out["closed"] = ov.win is None
    out["done"] = True
    save()
root.after(2200, a)
'''

h = Harness(8802, profile_files={"known_events.json": ["aurora"]}, outdir=OUT)
try:
    r = h.run(SCRIPT, wait=80)
    res = r["result"] or {}
    check("the script ran to the end", res.get("done"), r["log"][-500:])
    check("the pill opens and names the event with a countdown", res.get("open") and res.get("head") == "Aurora"
          and res.get("count", "").startswith("2:0") and res.get("sub") == "ENDS IN", res)
    check("with nothing on it shows the next event's countdown", res.get("next_head") == "Next: Blizzard"
          and res.get("next_count", "").startswith(("4:5", "5:0")) and res.get("next_sub") == "STARTS IN", res)
    check("'show the next event' off says it is just watching", res.get("hidden_next") == "Watching the board", res)
    check("unlocked: it takes clicks", res.get("ex_unlocked") is False, res)
    check("locked: clicks pass through", res.get("ex_locked") is True, res)
    mv = res.get("moved") or [0, 0, 0, 0]
    check("dragging moves it and remembers where", mv[0] == mv[2] and mv[1] == mv[3] and mv[0] > 0, res.get("moved"))
    check("switching it off closes it", res.get("closed") is True, res)
finally:
    h.close()
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
