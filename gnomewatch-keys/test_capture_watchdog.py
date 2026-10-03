"""A capture that stops delivering frames is restarted; a healthy one is left alone.   python test_capture_watchdog.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.argv = ["x"]
os.environ.pop("GW_BOOT", None)
import sentinel as S

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


import numpy as np
started = []
stopped = []


class Ctl:
    def stop(self):
        stopped.append(1)


def fake_start(hwnd):
    started.append(hwnd)
    S._caps[hwnd] = {"hwnd": hwnd, "ctl": Ctl(), "frame": np.zeros((20, 20, 4), dtype="uint8"), "closed": False, "t": S.time.time()}


S._wgc_start = fake_start
frame = np.zeros((20, 20, 4), dtype="uint8")
win = (0, 0, 800, 600, 4242)
now = S.time.time()

S._caps[4242] = {"hwnd": 4242, "ctl": Ctl(), "frame": frame, "closed": False, "t": now - 1}
S.capture_win(win)
check("a window sending pictures is left alone", started == [], started)

S._caps[4242] = {"hwnd": 4242, "ctl": Ctl(), "frame": frame, "closed": False, "t": now - 30}
S.capture_win(win)
check("pictures stopped 30 s ago: a fresh capture is started", started == [4242] and stopped, (started, stopped))
S._caps[4242]["frame"] = frame
check("the new capture is marked as just restarted", S._caps[4242].get("restarts") == 1)

S._caps[4242].update(t=now - 30, frame=frame)
S.capture_win(win)
check("...and it isn't restarted again straight away (backs off)", started == [4242], started)

S._caps[4242].update(t=now - 30, frame=frame, restarted=now - 60)
S.capture_win(win)
check("after the back-off it may be restarted again", started == [4242, 4242], started)

S._caps[4242] = {"hwnd": 4242, "ctl": Ctl(), "frame": None, "closed": False, "t": 0}
check("no frame yet is not 'stale' (it is just starting)", S._capture_stale(S._caps[4242]) is False)
check("a closed capture is not 'stale' (it is restarted the normal way)", S._capture_stale({"closed": True, "frame": frame, "t": 0}) is False)
S._ensure_capture(4242)
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.stdout.flush()
sys.exit(1 if fails else 0)
