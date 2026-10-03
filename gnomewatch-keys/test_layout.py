"""Looks for layout breakage: widgets sticking out of their cards / windows, on every page, with long event names and after
pressing buttons.   python test_layout.py [outdir]"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from feature_harness import Harness

OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.gettempdir()
fails = []


def check(name, cond, extra=""):
    print((("PASS " if cond else "FAIL ") + name + (f"   {str(extra)[:600]}" if extra and not cond else "")).encode("ascii", "replace").decode())
    if not cond:
        fails.append(name)


SCRIPT = r'''
import json, os, time, tkinter as tk
from PIL import ImageGrab
out = {"over": {}}
def snap(w, name):
    w.lift(); root.update(); w.update()
    x, y, wd, ht = w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + wd, y + ht)).save(os.path.join(os.environ["GW_TEST_SHOTS"], "feat_" + name + ".png"))
def save():
    json.dump(out, open(os.environ["GW_TEST_OUT"], "w"))
def overflow(widget, label):
    """Every child must sit inside its parent. Returns [(child class/text, how far out)]"""
    bad = []
    def walk(w):
        try:
            pw = w.winfo_width()
            ph = w.winfo_height()
        except tk.TclError:
            return
        for c in w.winfo_children():
            try:
                if not c.winfo_viewable() and not c.winfo_ismapped():
                    continue
                x, y, cw, ch = c.winfo_x(), c.winfo_y(), c.winfo_width(), c.winfo_height()
            except tk.TclError:
                continue
            if cw > 2 and (x + cw > pw + 2 or x < -2) and pw > 20 and w.winfo_class() in ("Frame", "Canvas"):
                t = ""
                try:
                    t = c.cget("text")
                except Exception:
                    pass
                bad.append((w.winfo_class(), c.winfo_class(), str(t)[:24], x + cw - pw))
            walk(c)
    walk(widget)
    out["over"][label] = bad[:8]
po = page_objs["Auto-place"]
for ev in ("lightning storm", "candy land", "solar flare", "toxic", "cursed", "aurora"):
    S.KNOWN.add(ev)
show("Auto-place")
def a():
    root.update()
    overflow(ctx.pages["Auto-place"], "autoplace")
    snap(root, "layout_autoplace")
    d = po.import_dialog()
    root.after(500, lambda: b(d))
def b(d):
    overflow(d["box"].winfo_toplevel(), "import_empty")
    snap(d["box"].winfo_toplevel(), "layout_import_empty")
    d["close"]()
    show("Settings")
    root.after(700, c)
def c():
    root.update()
    overflow(ctx.pages["Settings"], "settings")
    snap(root, "layout_settings")
    ctx.run_health()
    root.after(900, d)
def d():
    root.update()
    overflow(ctx.pages["Settings"], "settings_after_health")
    snap(root, "layout_settings_health")
    show("Accounts")
    root.after(800, e)
def e():
    root.update()
    overflow(ctx.pages["Accounts"], "accounts")
    snap(root, "layout_accounts")
    for n in ("Instances", "Performance", "Activity", "Help", "Overview"):
        show(n); root.update(); time.sleep(0.2); root.update()
        overflow(ctx.pages[n], n.lower())
    out["done"] = True
    save()
root.after(2500, a)
'''

MAC = {"account": None, "enabled": False, "steps": [{"t": 0.4, "k": 0x31, "d": 1}, {"t": 0.5, "k": 0x31, "d": 0}], "end_steps": []}
h = Harness(8807, profile_files={"known_events.json": ["aurora", "lightning storm", "candy land"],
                                 "accounts.json": [{"label": "Exallento (MAIN)", "browser": "edge", "profile": "Default"}],
                                 "macros.json": {"lightning storm": MAC, "candy land": MAC}}, outdir=OUT)
try:
    r = h.run(SCRIPT, wait=120, script_delay=2500)
    res = r["result"] or {}
    check("the script ran to the end", res.get("done"), r["log"][-600:])
    for k, v in (res.get("over") or {}).items():
        check(f"nothing sticks out on: {k}", not v, v)
finally:
    h.close()
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
