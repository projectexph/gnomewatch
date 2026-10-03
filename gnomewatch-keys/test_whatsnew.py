"""The 'What's new' card after an update.   python test_whatsnew.py [outdir]"""
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
import json, os, time
from PIL import ImageGrab
import gw_version
out = {}
def snap(w, name):
    root.update_idletasks(); w.update_idletasks()
    x, y, wd, ht = w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + wd, y + ht)).save(os.path.join(os.environ["GW_TEST_SHOTS"], "feat_" + name + ".png"))
def state():
    try:
        return json.load(open(os.path.join(S.DATA, "state.json"), encoding="utf-8"))
    except Exception:
        return None
out["before"] = state()
gw_version.VERSION = "9.9.9"
gw_version.NOTES = "• Share a macro as a code\n• Dry run shows clicks without clicking\n• Overlay widget\n• Memory cap per account"
shown = []
root.after(1400, lambda: (snap(ctx.dialog, "whatsnew"), shown.append(1), ctx.dialog.destroy()))
ctx.whats_new()                                   # blocks until the card is closed
out["shown_once"] = len(shown) == 1
out["state_after"] = state()
before = len(shown)
root.after(1200, lambda: out.update(second_open=(len(shown) > before)))
ctx.whats_new()                                   # same version again: nothing opens, so this returns at once
out["second_open"] = len(shown) > before
json.dump(out, open(os.environ["GW_TEST_OUT"], "w"))
'''

h = Harness(8803, profile_files={"known_events.json": ["aurora"], "accounts.json": [{"label": "Main", "browser": "edge", "profile": "Default"}]}, outdir=OUT)
try:
    r = h.run(SCRIPT, wait=80)
    res = r["result"] or {}
    check("the script ran to the end", "shown_once" in res, r["log"][-500:])
    check("an existing user with no record gets the card after an update", res.get("shown_once") is True, res)
    check("the new version is remembered", (res.get("state_after") or {}).get("last_version") == "9.9.9", res)
    check("the same version doesn't show it again", res.get("second_open") is False, res)
finally:
    h.close()
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
