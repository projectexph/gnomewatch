"""The first-run setup wizard.   python test_wizard.py [outdir]"""
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


HELPERS = r'''
import json, os, time, tkinter as tk
from PIL import ImageGrab
out = {}
def snap(w, name):
    w.lift(); root.update(); w.update()
    x, y, wd, ht = w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + wd, y + ht)).save(os.path.join(os.environ["GW_TEST_SHOTS"], "feat_" + name + ".png"))
def save():
    json.dump(out, open(os.environ["GW_TEST_OUT"], "w"))
def jl(name):
    try:
        return json.load(open(os.path.join(S.DATA, name), encoding="utf-8"))
    except Exception:
        return None
'''

NEW_USER = HELPERS + r'''
def a(tries=0):
    w = ctx.wizard_win["w"]
    if w is None and tries < 50:                      # it opens by itself a few seconds after the console does
        root.after(300, lambda: a(tries + 1))
        return
    out["auto_opened"] = w is not None
    if w is None:
        out["done"] = True; save(); return
    out["step0"] = w.step
    snap(w.dlg, "wizard_1_welcome")
    w.next()
    out["step1"] = w.step
    out["profiles"] = len(w.profiles)
    out["profile"] = w.fv["profile"]
    out["next_enabled_1"] = not w.next_btn.disabled
    snap(w.dlg, "wizard_2_account")
    w.alias_var.set("")                       # an empty name can't go on
    out["next_blocked_empty"] = w.next_btn.disabled
    w.alias_var.set("Main")
    w.next()
    out["step2"] = w.step
    w.fv.update(where="private"); w.render()
    w.link_var.set("http://nope")
    out["next_blocked_http"] = w.next_btn.disabled
    w.link_var.set("https://www.roblox.com/share?code=abc123&type=Server")
    out["next_ok_link"] = not w.next_btn.disabled
    snap(w.dlg, "wizard_3_where")
    w.next()
    out["step3"] = w.step
    S._launch_queue = lambda accts, close_first=False: [(accts[0]["label"], False, "no launch link is set")]      # a failing launch first
    w.test_launch()
    root.after(2500, b)
def b():
    w = ctx.wizard_win["w"]
    out["fail_result"] = list(w.result) if w.result else None
    out["fail_text"] = w.status.cget("text")
    snap(w.dlg, "wizard_4_fail")
    S._launch_queue = lambda accts, close_first=False: [(accts[0]["label"], True, "ok")]
    w.test_launch()
    root.after(2500, c)
def c():
    w = ctx.wizard_win["w"]
    out["ok_result"] = list(w.result) if w.result else None
    out["ok_text"] = w.status.cget("text")
    snap(w.dlg, "wizard_5_ok")
    w.next_after_test()
    out["done_step"] = w.step
    snap(w.dlg, "wizard_6_done")
    w.add_another()
    out["again_step"], out["again_alias"] = w.step, w.fv["alias"]
    w.fv["profile"] = ""
    w.render()
    out["enter_blocked_without_profile"] = w.next_btn.disabled
    w.close(done=True)
    root.after(800, d)
def d():
    out["closed"] = ctx.wizard_win["w"] is None
    out["accounts"] = jl("accounts.json")
    out["state"] = jl("state.json")
    out["target"] = S.LAUNCH_TARGET
    out["private_set"] = bool(S.LAUNCH_URL_PRIVATE)
    out["done"] = True
    save()
root.after(2500, a)
'''

EXISTING = HELPERS + r'''
def a():
    out["wizard_open"] = ctx.wizard_win["w"] is not None
    out["state"] = jl("state.json")
    out["done"] = True
    save()
root.after(3000, a)
'''

print("== a brand-new profile")
h = Harness(8805, profile_files={"known_events.json": ["aurora"]}, outdir=OUT)
try:
    r = h.run(NEW_USER, wait=90, script_delay=1500)
    res = r["result"] or {}
    check("the guide opens by itself on a brand-new profile", res.get("auto_opened"), r["log"][-400:])
    check("it starts at the welcome page, then moves to the account page", res.get("step0") == 0 and res.get("step1") == 1, res)
    check("it finds the browser's profiles and picks one", res.get("profiles", 0) >= 1 and res.get("profile"), res)
    check("the account page lets you carry on, but not with an empty name", res.get("next_enabled_1") and res.get("next_blocked_empty"), res)
    check("a private link must be https", res.get("next_blocked_http") and res.get("next_ok_link"), res)
    check("a failed test launch says why", res.get("fail_result") and res["fail_result"][0] is False and "no launch link" in res.get("fail_text", ""), res)
    check("a working test launch says so", res.get("ok_result") and res["ok_result"][0] is True and "It works" in res.get("ok_text", ""), res)
    accts = res.get("accounts") or []
    check("after the test it goes to the All set page, and can start another account", res.get("done_step") == 4 and res.get("again_step") == 1
          and res.get("again_alias") == "Alt 1" and res.get("enter_blocked_without_profile"), res)
    check("finishing saves the account", len(accts) == 1 and accts[0]["label"] == "Main" and accts[0].get("target") == "private", accts)
    check("it remembers the guide was done, and sets the private link", (res.get("state") or {}).get("wizard_done") is True and res.get("private_set")
          and res.get("target") == "private", res)
    check("the window closed", res.get("closed") is True)
finally:
    h.close()

print("== someone who already has accounts")
h = Harness(8806, profile_files={"known_events.json": ["aurora"], "accounts.json": [{"label": "Main", "browser": "edge", "profile": "Default"}]}, outdir=OUT)
try:
    r = h.run(EXISTING, wait=90, script_delay=7500)
    res = r["result"] or {}
    check("no guide for someone who already set up an account", res.get("wizard_open") is False and (res.get("state") or {}).get("wizard_done") is True, res)
finally:
    h.close()
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
