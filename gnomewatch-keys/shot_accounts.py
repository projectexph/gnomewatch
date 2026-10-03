import os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from feature_harness import Harness
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.gettempdir()
name = sys.argv[2] if len(sys.argv) > 2 else "accounts_now"
SCRIPT = r'''
import json, os, time
from PIL import ImageGrab
out = {}
def snap(w, n):
    w.lift(); root.update(); w.update()
    x, y, wd, ht = w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + wd, y + ht)).save(os.path.join(os.environ["GW_TEST_SHOTS"], "acc_" + n + ".png"))
show("Accounts")
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
        import traceback
        out["err_%d" % i] = traceback.format_exc()[-600:]
    root.after(wait, lambda: run(i + 1))
po = page_objs["Accounts"]
step(lambda: snap(root, "NAME_1_cards"))
step(lambda: ctx.open_account("Brandon (ALT)"), 1200)
step(lambda: snap(root, "NAME_2_drawer"))
step(lambda: po.__class__ and None, 100)
root.after(2500, run)
'''.replace("NAME", name)
ACC = [{"label": "Exallento (MAIN)", "browser": "edge", "profile": "Default", "target": "private", "color": "#7c6cff", "group": "Mains", "notes": "Main account, farms gnomes", "last": 1790900000},
       {"label": "Brandon (ALT)", "browser": "edge", "profile": "Profile 1", "target": "private", "color": "#2ee59d", "group": "Alts"},
       {"label": "Matilda (ALT 2)", "browser": "chrome", "profile": "Default", "color": "#ffb547", "group": "Alts", "ram_cap": 2048},
       {"label": "Sam", "browser": "edge", "profile": "Profile 2", "lowres": {"on": True, "fps": 20, "quality": 2}}]
h = Harness(8811, profile_files={"known_events.json": ["aurora"], "accounts.json": ACC, "state.json": {"wizard_done": True, "last_version": __import__("gw_version").VERSION}}, outdir=OUT)
try:
    r = h.run(SCRIPT, wait=90, script_delay=2000)
    print(r["result"])
finally:
    h.close()
