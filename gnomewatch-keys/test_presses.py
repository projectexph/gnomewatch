"""Presses the new buttons one after another and takes a picture of the console after each, to see what breaks.
    python test_presses.py [outdir]"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from feature_harness import Harness

OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.gettempdir()

SCRIPT = r'''
import json, os, time, tkinter as tk
from PIL import ImageGrab
out = {}
def snap(w, name):
    w.lift(); root.update(); w.update()
    x, y, wd, ht = w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + wd, y + ht)).save(os.path.join(os.environ["GW_TEST_SHOTS"], "press_" + name + ".png"))
po = page_objs["Auto-place"]
for ev in ("lightning storm", "candy land"):
    S.KNOWN.add(ev)
show("Auto-place")
def sfm():
    root.update()
    return [s for s in ctx.scrollers if s.winfo_ismapped()][0]
steps = []
def step(fn, wait=900):
    steps.append((fn, wait))
def run(i=0):
    if i >= len(steps):
        out["done"] = True
        json.dump(out, open(os.environ["GW_TEST_OUT"], "w")); return
    fn, wait = steps[i]
    try:
        fn()
    except Exception as e:
        out["err_%d" % i] = repr(e)
    root.after(wait, lambda: run(i + 1))
step(lambda: snap(root, "0_start"))
step(lambda: (sfm().cv.yview_moveto(0.55), root.update()), 500)
step(lambda: snap(root, "1_scrolled"))
step(lambda: po.share("candy land"), 400)
step(lambda: snap(root, "2_after_share"))
step(lambda: po.dry("candy land", "start"), 1800)
step(lambda: [t for t in root.winfo_children() if t.winfo_class() == "Toplevel" and t.winfo_ismapped()] and snap([t for t in root.winfo_children() if t.winfo_class() == "Toplevel" and t.winfo_ismapped()][-1], "3_dry_preview"))
step(lambda: [t.destroy() for t in root.winfo_children() if t.winfo_class() == "Toplevel" and t.winfo_ismapped() and t.winfo_width() > 400], 600)
step(lambda: snap(root, "4_after_dry_closed"))
step(lambda: show("Settings"), 700)
step(lambda: ctx.run_health(), 1200)
step(lambda: snap(root, "5_settings_health"))
step(lambda: ctx.open_wizard(), 900)
step(lambda: ctx.wizard_win["w"] and snap(ctx.wizard_win["w"].dlg, "6_wizard"))
step(lambda: ctx.wizard_win["w"] and ctx.wizard_win["w"].close(), 600)
step(lambda: snap(root, "7_after_wizard"))
step(lambda: show("Help"), 800)
step(lambda: snap(root, "8_help"))
step(lambda: (sfm().cv.yview_moveto(0.5), root.update()), 500)
step(lambda: snap(root, "9_help_scrolled"))
step(lambda: show("Overview"), 700)
step(lambda: snap(root, "10_overview"))
root.after(2000, run)
'''
MAC = {"account": None, "enabled": False, "steps": [{"t": 0.4, "k": 0x31, "d": 1}, {"t": 0.5, "k": 0x31, "d": 0}, {"t": 1.0, "m": "L", "d": 1, "x": 0.4, "y": 0.5}, {"t": 1.1, "m": "L", "d": 0, "x": 0.4, "y": 0.5}], "end_steps": []}
h = Harness(8808, profile_files={"known_events.json": ["aurora", "lightning storm", "candy land", "solar flare"],
                                 "accounts.json": [{"label": "Exallento (MAIN)", "browser": "edge", "profile": "Default"}],
                                 "macros.json": {"lightning storm": MAC, "candy land": MAC}, "state.json": {"wizard_done": True, "last_version": __import__("gw_version").VERSION}}, outdir=OUT)
try:
    r = h.run(SCRIPT, wait=120, script_delay=2000)
    print({k: v for k, v in (r["result"] or {}).items()})
finally:
    h.close()
