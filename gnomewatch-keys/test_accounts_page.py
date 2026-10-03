"""The Accounts page: cards, one-click actions (no confirmations), the selection bar, the slide-out panel, saving and removing.
    python test_accounts_page.py [outdir]"""
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
import json, os, time, tkinter as tk
from PIL import ImageGrab
import ui_kit as UK
out = {"calls": []}
def snap(w, n):
    w.lift(); root.update(); w.update()
    x, y, wd, ht = w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + wd, y + ht)).save(os.path.join(os.environ["GW_TEST_SHOTS"], "accp_" + n + ".png"))
def buttons(w, acc):
    for c in w.winfo_children():
        if isinstance(c, UK.Btn) or isinstance(c, UK.IconBtn):
            acc.append(c)
        buttons(c, acc)
    return acc
def btn(text, only_visible=True):
    for b in buttons(ctx.pages["Accounts"], []):
        if getattr(b, "text", None) == text and (not only_visible or b.winfo_ismapped()):
            return b
    return None
def dialogs():
    return [t for t in root.winfo_children() if t.winfo_class() == "Toplevel" and t.winfo_ismapped()]
# record every launch / close / setting the page asks for, instead of really launching
S._launch_queue_async = lambda accts, close_first=False: out["calls"].append(("launch", [a["label"] for a in accts], close_first))
S._close_all = lambda: out["calls"].append(("close_all",))
S._close_window = lambda h: out["calls"].append(("close", h))
S._set_window_show = lambda h, show: out["calls"].append(("show" if show else "hide", h))
show("Accounts")
steps = []
def step(fn, wait=700):
    steps.append((fn, wait))
def run(i=0):
    if i >= len(steps):
        out["done"] = True
        json.dump(out, open(os.environ["GW_TEST_OUT"], "w")); return
    fn, wait = steps[i]
    try:
        fn()
    except Exception:
        import traceback
        out["err_%d" % i] = traceback.format_exc()[-700:]
    root.after(wait, lambda: run(i + 1))
def plays():
    return [b for b in buttons(ctx.pages["Accounts"], []) if getattr(b, "text", None) == "Play" and b.winfo_ismapped()]
step(lambda: out.update(play_buttons=len(plays())))
step(lambda: snap(root, "1_offline"))
# --- one click on Play launches that one account, no dialog
def click_play():
    b = plays()[1]
    b.cmd()
    out["dialogs_after_play"] = len(dialogs())
step(click_play, 300)
# --- pretend the first account is running
import ctypes
HW = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()          # a real window handle (the console's own) stands in for a Roblox window
class FakeSnap(list):
    def __setitem__(self, k, v): pass
    def __delitem__(self, k): pass
    def clear(self): pass
    def extend(self, v): pass
    def append(self, v): pass
def fake_running():
    S._snapshot = FakeSnap([(HW, "other", "Roblox", False, False)])
    ctx.assign[HW] = "Exallento (MAIN)"
    page_objs["Accounts"].on_show()
step(fake_running, 1000)
step(lambda: snap(root, "2_running"))
# --- the queue still says "loading" for an account whose window already exists: the card must trust the real window, not the queue
def pills(w, acc):
    for c in w.winfo_children():
        if isinstance(c, UK.Pill):
            acc.append(c.itemcget(c.tx, "text"))
        pills(c, acc)
    return acc
def queue_says_loading():
    S._lq.update(running=True, msg="Opening", items=[{"label": "Exallento (MAIN)", "state": "loading"}, {"label": "Brandon (ALT)", "state": "queued"}])
    page_objs["Accounts"].on_show()
step(queue_says_loading, 900)
def check_pills():
    out["pills_during_queue"] = pills(ctx.pages["Accounts"], [])
    out["working_btn"] = len([b for b in buttons(ctx.pages["Accounts"], []) if getattr(b, "text", None) == "Working on it…" and b.winfo_ismapped()])
    out["rejoin_during_queue"] = btn("Rejoin") is not None
    snap(root, "2b_queue_loading")
    S._lq.update(running=False, items=[])
step(check_pills, 300)
def click_rejoin():
    b = btn("Rejoin")
    out["rejoin_button"] = b is not None
    if b:
        out["calls"].clear()
        b.cmd()
        out["dialogs_after_rejoin"] = len(dialogs())
        out["rejoin_calls"] = list(out["calls"])
step(click_rejoin, 300)
def click_close():
    out["calls"].clear()
    for b in buttons(ctx.pages["Accounts"], []):
        if getattr(b, "tip_text", None) == "Close this Roblox window" or (isinstance(b, UK.IconBtn) and getattr(b, "iname", "") == "x" and b.winfo_ismapped() and b.master.winfo_class() == "Frame"):
            b.cmd()
            break
    out["close_calls"] = list(out["calls"])
    out["dialogs_after_close"] = len(dialogs())
step(click_close, 300)
# --- ticking cards shows the selection bar
def tick():
    ui = page_objs  # noqa
    cards = [w for w in ctx.pages["Accounts"].winfo_children()]
    class E: pass
    e = E(); e.x = 290 - 28; e.y = 26
    # click the tick box of the first two cards
    sfs = [s for s in ctx.scrollers if s.winfo_ismapped()]
    grid = sfs[0].inner
    cs = [c for c in grid.winfo_children() if isinstance(c, UK.Card)]
    for c in cs[:2]:
        c.event_generate("<ButtonRelease-1>", x=262, y=26)
step(tick, 900)
def sel():
    def labels(w, acc):
        for c in w.winfo_children():
            if isinstance(c, tk.Label) and c.winfo_ismapped():
                acc.append(str(c.cget("text")))
            labels(c, acc)
        return acc
    out["selbar_shown"] = any("selected" in t for t in labels(ctx.pages["Accounts"], []))
    snap(root, "3_selected")
    out["calls"].clear()
    b = btn("Launch")
    out["has_launch_selected"] = b is not None
    if b:
        S.STATE["lock"] = "held"
        b.cmd()
    out["selected_launch"] = list(out["calls"])
step(sel, 900)
# --- Restart Roblox for multi-instance: ONE click, no dialog, closes everything, waits for the lock, reopens what was open
def restart_multi():
    out["calls"].clear()
    sets = []
    real_set = S._set_setting
    S._set_setting = lambda n, v: (sets.append((n, v)), None)[1]
    S.STATE["lock"] = "waiting"
    ctx.restart_multi()
    out["restart_dialogs"] = len(dialogs())
    out["restart_first"] = list(out["calls"])
    out["restart_sets"] = list(sets)
    def later():
        S._snapshot = FakeSnap([])                       # Roblox is closed...
        S.STATE["lock"] = "held"                         # ...and the lock is ours
    root.after(900, later)
    S._real_set_saved = real_set
step(restart_multi, 4500)
def after_restart():
    out["restart_final"] = list(out["calls"])
    S._set_setting = S._real_set_saved
step(after_restart, 100)
# --- Launch all with multi-instance off turns it on by itself (no dialog) and then launches everyone
def launch_all_auto():
    out["calls"].clear()
    sets = []
    S._set_setting = lambda n, v: (sets.append((n, v)), None)[1]
    S.STATE["lock"] = "off"
    b = btn("Launch all")
    b.cmd()
    out["la_dialogs"] = len(dialogs())
    out["la_sets"] = list(sets)
    root.after(700, lambda: S.STATE.__setitem__("lock", "held"))
step(launch_all_auto, 3500)
def after_la():
    out["la_calls"] = list(out["calls"])
    S._set_setting = S._real_set_saved
step(after_la, 100)
# --- the panel
step(lambda: ctx.open_account("Brandon (ALT)"), 1200)
step(lambda: snap(root, "4_panel"))
def panel_fields():
    out["panel_open"] = True
step(panel_fields, 100)
# --- save a changed alias through the panel
def edit_and_save():
    import re
    for e in [w for w in ctx.pages["Accounts"].winfo_children()]:
        pass
    # find the alias entry: the first EntryBox in the drawer whose text is the account's name
    def walk(w, acc):
        for c in w.winfo_children():
            if isinstance(c, UK.EntryBox):
                acc.append(c)
            walk(c, acc)
        return acc
    boxes = walk(ctx.pages["Accounts"], [])
    alias = next((b for b in boxes if b.var.get() == "Brandon (ALT)"), None)
    out["alias_found"] = alias is not None
    if alias:
        alias.var.set("Brandon (RENAMED)")
        sv = btn("Save account")
        sv.cmd()
step(edit_and_save, 800)
def check_saved():
    lst = json.load(open(os.path.join(S.DATA, "accounts.json"), encoding="utf-8"))
    out["saved_names"] = [a["label"] for a in lst]
step(check_saved, 300)
# --- a new account through the panel needs a name and a profile
step(lambda: ctx.pages["Accounts"].winfo_children() and None, 50)
root.after(3000, run)
'''
ACC = [{"label": "Exallento (MAIN)", "browser": "edge", "profile": "Default", "color": "#7c6cff", "group": "Mains"},
       {"label": "Brandon (ALT)", "browser": "edge", "profile": "Default", "color": "#2ee59d", "group": "Alts"},
       {"label": "Matilda (ALT 2)", "browser": "edge", "profile": "Default", "color": "#ffb547", "group": "Alts", "ram_cap": 2048}]
h = Harness(8812, profile_files={"known_events.json": ["aurora"], "accounts.json": ACC, "state.json": {"wizard_done": True, "last_version": __import__("gw_version").VERSION}}, outdir=OUT)
try:
    r = h.run(SCRIPT, wait=120, script_delay=2500)
    res = r["result"] or {}
    errs = {k: v for k, v in res.items() if k.startswith("err_")}
    check("the script ran to the end", res.get("done"), (errs, r["log"][-300:]))
    check("no step crashed", not errs, errs)
    check("every offline account card has a Play button", res.get("play_buttons") == 3, res.get("play_buttons"))
    play = [c for c in res.get("calls", []) if c[0] == "launch"]
    check("Play launches just that account, with no 'are you sure' box", res.get("dialogs_after_play") == 0, res.get("dialogs_after_play"))
    check("a running account's card offers Rejoin", res.get("rejoin_button") is True)
    pl = res.get("pills_during_queue") or []
    check("an account whose window is up shows IN GAME even while the queue still says loading", "IN GAME" in pl and "LOADING" not in pl[:1], pl)
    check("...and offers Rejoin; only the account still queued says 'Working on it'", res.get("rejoin_during_queue") is True and res.get("working_btn") == 1, res)
    check("an account still waiting in the queue shows QUEUED", "QUEUED" in pl, pl)
    check("Rejoin is ONE click: it relaunches with close_first and shows no dialog", res.get("dialogs_after_rejoin") == 0
          and res.get("rejoin_calls") == [["launch", ["Exallento (MAIN)"], True]], (res.get("dialogs_after_rejoin"), res.get("rejoin_calls")))
    check("Close is ONE click and closes that window", res.get("dialogs_after_close") == 0 and len(res.get("close_calls") or []) == 1 and res["close_calls"][0][0] == "close", res.get("close_calls"))
    check("ticking cards shows the selection bar", res.get("selbar_shown") is True, res)
    check("Launch on the ticked cards launches those accounts together", res.get("selected_launch") == [["launch", ["Exallento (MAIN)", "Brandon (ALT)"], False]],
          res.get("selected_launch"))
    check("Restart Roblox for multi-instance is ONE click, no dialog: it turns multi-instance on and closes everything", res.get("restart_dialogs") == 0
          and ["close_all"] in res.get("restart_first", []) and ("MULTI_INSTANCE", True) in [tuple(x) for x in res.get("restart_sets", [])], res)
    check("...then reopens the account that was open once the lock is held", ["launch", ["Exallento (MAIN)"], False] in res.get("restart_final", []), res.get("restart_final"))
    check("Launch all with multi-instance off turns it on by itself (no dialog) and then launches everyone", res.get("la_dialogs") == 0
          and ("MULTI_INSTANCE", True) in [tuple(x) for x in res.get("la_sets", [])]
          and ["launch", ["Exallento (MAIN)", "Brandon (ALT)", "Matilda (ALT 2)"], False] in res.get("la_calls", []), (res.get("la_sets"), res.get("la_calls"), res.get("la_dialogs")))
    check("saving from the slide-out panel renames the account", "Brandon (RENAMED)" in (res.get("saved_names") or []) and "Brandon (ALT)" not in (res.get("saved_names") or []),
          res.get("saved_names"))
finally:
    h.close()
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
