"""Self-diagnosis ('Why isn't it working?') and the 'a launch didn't work' prompt.   python test_diag.py [outdir]"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from feature_harness import Harness

OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.gettempdir()
fails = []


def check(name, cond, extra=""):
    print((("PASS " if cond else "FAIL ") + name + (f"   {str(extra)[:400]}" if extra and not cond else "")).encode("ascii", "replace").decode())
    if not cond:
        fails.append(name)


SCRIPT = r'''
import json, os, time, tkinter as tk
from PIL import ImageGrab
out = {}
def snap(w, name):
    root.update_idletasks(); w.update_idletasks()
    x, y, wd, ht = w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + wd, y + ht)).save(os.path.join(os.environ["GW_TEST_SHOTS"], "feat_" + name + ".png"))
def texts(w, acc):
    for c in w.winfo_children():
        if isinstance(c, tk.Label):
            t = c.cget("text")
            if t:
                acc.append(t)
        texts(c, acc)
    return acc
# no launch link at all, and an account whose browser profile doesn't exist
S.LAUNCH_URL_GAME = S.LAUNCH_URL_PRIVATE = S.LAUNCH_URL_CUSTOM = S.LAUNCH_URL = ""
show("Settings")
ctx.run_health()
def a():
    t = texts(ctx.pages["Settings"], [])
    out["texts"] = t
    snap(root, "diag_settings")
    # a launch that failed -> the prompt
    S._lq["fail_msg"] = "no launch link is set - add your game or private-server link in Settings > Launching"
    S._lq["done_t"] = time.time() + 0.5
    root.after(5200, b)
def b():
    d = getattr(ctx, "dialog", None)
    ok = False
    try:
        ok = d is not None and d.winfo_exists()
    except Exception:
        ok = False
    out["prompt_open"] = bool(ok)
    if ok:
        snap(d, "diag_prompt")
        d.destroy()
    out["done"] = True
    json.dump(out, open(os.environ["GW_TEST_OUT"], "w"))
root.after(6500, a)
'''

h = Harness(8804, profile_files={"known_events.json": ["aurora"],
                                 "accounts.json": [{"label": "Main", "browser": "edge", "profile": "Profile 99", "target": "private"}]}, outdir=OUT)
try:
    r = h.run(SCRIPT, wait=90)
    res = r["result"] or {}
    t = res.get("texts") or []
    check("the script ran to the end", res.get("done"), r["log"][-500:])
    check("it lists the key", "Your key" in t, t[:40])
    check("it checks the key server (found, since the sandbox one is up)", "Key server" in t and any("answering" in x for x in t), t[:60])
    check("it spots the missing launch link and offers a fix", "Launch link" in t and any("none set" in x for x in t), t[:60])
    check("it spots an account whose browser profile is gone", "Profile for Main" in t, t[:60])
    check("it checks the browser", "Edge" in t, t[:60])
    check("a failed launch pops up an offer to run the checks", res.get("prompt_open") is True, res)
finally:
    h.close()
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
