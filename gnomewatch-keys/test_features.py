"""The 1.1 features, tested inside a sandbox copy of the real console (own folder, safe settings, own lock).
    python test_features.py [outdir]  [only]       (only = macro | ram | overlay | notes | diag | wizard)"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from feature_harness import Harness

OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.gettempdir()
ONLY = sys.argv[2] if len(sys.argv) > 2 else None
fails = []


def check(name, cond, extra=""):
    print((("PASS " if cond else "FAIL ") + name + (f"   {str(extra)[:300]}" if extra and not cond else "")).encode("ascii", "replace").decode())
    if not cond:
        fails.append(name)


STEPS = [{"t": 0.4, "k": 0x31, "d": 1}, {"t": 0.5, "k": 0x31, "d": 0}, {"t": 1.0, "m": "L", "d": 1, "x": 0.5, "y": 0.62},
         {"t": 1.1, "m": "L", "d": 0, "x": 0.5, "y": 0.62}, {"t": 1.8, "m": "L", "d": 1, "x": 0.3, "y": 0.4}, {"t": 1.9, "m": "L", "d": 0, "x": 0.3, "y": 0.4}]
END = [{"t": 0.3, "k": 0x32, "d": 1}, {"t": 0.4, "k": 0x32, "d": 0}]

# ====================================================================================================== macros: share, import, dry run
MACRO_SCRIPT = r'''
import json, os
from PIL import ImageGrab
out = {}
def snap(w, name):
    root.update_idletasks(); w.update_idletasks()
    x, y, wd, ht = w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + wd, y + ht)).save(os.path.join(os.environ["GW_TEST_SHOTS"], "feat_" + name + ".png"))
def save():
    json.dump(out, open(os.environ["GW_TEST_OUT"], "w"))
po = page_objs["Auto-place"]
show("Auto-place")
def a():
    po.share("aurora")
    import subprocess
    ext = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"], capture_output=True, text=True, creationflags=0x08000000).stdout.strip()
    out["ext_clip"] = ext                         # what ANOTHER program sees on the clipboard
    out["code"] = ext or root.clipboard_get()
    d = po.import_dialog(out["code"])
    out["same_msg"] = d["msg"].cget("text")
    out["same_ok"] = d["state"]["info"] is not None
    out["dlg"] = d
    root.after(900, lambda: snap(d["box"].winfo_toplevel(), "macro_import"))
    root.after(3200, b)
def b():
    d = out.pop("dlg")
    d["do"]()
    m = S._macros_load().get("aurora", {})
    out["after_same"] = {"enabled": m.get("enabled"), "n": len(m.get("steps", [])), "end": len(m.get("end_steps", []))}
    # a code for a brand-new event
    import gw_share
    code2 = gw_share.encode("blizzard", [{"t": 0.3, "k": 0x33, "d": 1}, {"t": 0.4, "k": 0x33, "d": 0}], [], name="Friend")
    d2 = po.import_dialog(code2)
    d2["do"]()
    m2 = S._macros_load().get("blizzard", {})
    out["new_event"] = {"enabled": m2.get("enabled"), "n": len(m2.get("steps", [])), "end": len(m2.get("end_steps", []))}
    out["aurora_untouched"] = len(S._macros_load()["aurora"]["steps"])
    d3 = po.import_dialog("GW1-garbage")
    out["bad_msg"] = d3["msg"].cget("text")
    out["bad_blocked"] = d3["state"]["info"] is None
    d3["close"]()
    before = set(root.winfo_children())
    po.dry("aurora", "start")                       # no account assigned: a stand-in preview opens
    new = [w for w in root.winfo_children() if w not in before and w.winfo_class() == "Toplevel"]
    out["preview_opened"] = len(new) == 1
    root.after(1500, lambda: snap(new[0], "macro_preview") if new else None)
    root.after(2600, lambda: (snap(root, "macro_autoplace"), c()))
def c():
    out["done"] = True
    save()
root.after(100, a)
'''

if ONLY in (None, "macro"):
    print("== macro share / import / dry run")
    h = Harness(8801, profile_files={"known_events.json": ["aurora", "toxic"], "macros.json": {"aurora": {"account": None, "enabled": True, "steps": STEPS, "end_steps": END}}},
                outdir=OUT)
    try:
        r = h.run(MACRO_SCRIPT, wait=60)
        res = r["result"] or {}
        check("the script ran to the end", res.get("done"), r["log"][-600:])
        check("share puts a GW2- code on the real clipboard, where another program can read it", str(res.get("ext_clip", "")).startswith("GW2-"), res.get("ext_clip"))
        check("importing over an existing recording warns it replaces it", "replaces" in res.get("same_msg", "") and res.get("same_ok"), res.get("same_msg"))
        check("an import arrives switched OFF and keeps the steps", res.get("after_same") == {"enabled": False, "n": 6, "end": 2}, res.get("after_same"))
        check("a new event is created, switched off", res.get("new_event") == {"enabled": False, "n": 2, "end": 0}, res.get("new_event"))
        check("other macros are left alone", res.get("aurora_untouched") == 6)
        check("garbage is refused with a reason", res.get("bad_blocked") and "code" in res.get("bad_msg", "").lower(), res.get("bad_msg"))
        check("a dry run with no live window opens a stand-in preview", res.get("preview_opened"))
    finally:
        h.close()

print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
