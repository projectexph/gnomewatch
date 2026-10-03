"""The sidebar with the admin's extra entry (nine items) must not run into the user card.   python test_nav_admin.py [outdir]"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from feature_harness import Harness

OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.gettempdir()
SCRIPT = r'''
import json, os
from PIL import ImageGrab
out = {}
show("Admin")
def a():
    root.lift(); root.update()
    x, y, wd, ht = root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + wd, y + ht)).save(os.path.join(os.environ["GW_TEST_SHOTS"], "press_admin_nav.png"))
    out["done"] = True
    json.dump(out, open(os.environ["GW_TEST_OUT"], "w"))
root.after(1500, a)
'''
h = Harness(8809, profile_files={"known_events.json": ["aurora"], "state.json": {"wizard_done": True, "last_version": __import__("gw_version").VERSION}}, outdir=OUT, admin=True)
try:
    r = h.run(SCRIPT, wait=90, script_delay=2000)
    print(r["result"])
finally:
    h.close()
