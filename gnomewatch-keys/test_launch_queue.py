"""The launch queue in a pretend world (no browser, no Roblox, a fake clock): the cases that made people click Play twice.
    python test_launch_queue.py"""
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


class World:
    def __init__(self, policy):
        self.now = 1_000_000.0
        self.policy = policy                     # policy(call_no, label) -> None (the launch is swallowed) or dict(pid_after, win_after, visible)
        self.procs = []                          # dict(pid, hwnd, label, appear, win_appear, visible, gone_hwnd, gone_pid)
        self.calls, self.cleared, self.assign_json = [], [], {}
        self.launcher_busy_until = 0.0
        self.next_pid = 5000
        self.log = []

    # --- the pretend system
    def hwnds(self):
        return {p["hwnd"] for p in self.procs if p["visible"] and p["win_appear"] <= self.now < p["gone_hwnd"]}

    def pids(self):
        return [p["pid"] for p in self.procs if p["appear"] <= self.now < p["gone_pid"]]

    def windows_by_pid(self):
        out = {}
        for p in self.procs:
            if p["win_appear"] <= self.now < p["gone_hwnd"]:
                out.setdefault(p["pid"], []).append(p["hwnd"])
        return out

    def launch(self, a, retry=False):
        no = len(self.calls) + 1
        self.calls.append((a["label"], retry, self.now))
        res = self.policy(no, a["label"])
        if res:
            self.next_pid += 1
            self.procs.append(dict(pid=self.next_pid, hwnd=self.next_pid * 10, label=a["label"], appear=self.now + res["pid_after"],
                                   win_appear=self.now + res["win_after"], visible=res.get("visible", True), gone_hwnd=1e12, gone_pid=1e12))
            if res.get("busy"):
                self.launcher_busy_until = self.now + res["busy"]
        return True, "ok"

    def close(self, h):
        for p in self.procs:
            if p["hwnd"] == h:
                p["gone_hwnd"], p["gone_pid"] = self.now + 2, self.now + 6
        return {"ok": True}


def install(w):
    clock = {"t": w.now}
    S.time.time = lambda: w.now
    S.time.sleep = lambda s: setattr(w, "now", w.now + s)
    S._roblox_hwnds = w.hwnds
    S._roblox_player_pids = w.pids
    S._windows_of_roblox_processes = w.windows_by_pid
    S._roblox_launcher_busy = lambda: w.now < w.launcher_busy_until
    S._launch_account = w.launch
    S._kill_stale_helpers = lambda min_age=6: (w.cleared.append((min_age, w.now)) or 0)
    S._window_ready = lambda h: True
    S._close_window = w.close
    S._hwnd_pid = lambda h: next((p["pid"] for p in w.procs if p["hwnd"] == h), 0)
    S._json_file = lambda name, default: (w.assign_json if name == "assign.json" else default)
    import psutil
    psutil.pid_exists = lambda pid, w=w: pid in w.pids()
    S._rbx["changed"] = False
    S.MULTI_INSTANCE = False
    S.LAUNCH_DELAY = 0.0
    S.LAUNCH_SMART_GAP = 2.0
    S._assign_now.clear()
    S._pending_launch.clear()
    S._lq.update(cancel=False, running=False)


A = lambda n: {"label": n, "browser": "edge", "profile": "Default"}
good = lambda no, lbl: dict(pid_after=3, win_after=6)

# ---- 1. the healthy case
w = World(good)
install(w)
res = S._launch_queue([A("Main")])
check("a healthy launch works with ONE attempt", res == [("Main", True, "ok")] and len(w.calls) == 1, (res, w.calls))
check("the window is assigned to its account", S._assign_now and S._assign_now[0][1] == "Main", S._assign_now)

# ---- 2. the first attempt is swallowed (what made you click Play twice)
swallowed = lambda no, lbl: None if no == 1 else dict(pid_after=3, win_after=8)
w = World(swallowed)
install(w)
res = S._launch_queue([A("Main")])
check("a swallowed first attempt is retried by itself and succeeds", res == [("Main", True, "ok")] and len(w.calls) == 2 and w.calls[1][1] is True, (res, w.calls))
check("it tried again after about 30 s, not 150 s", 28 <= w.calls[1][2] - w.calls[0][2] <= 36, [c[2] for c in w.calls])
check("stale helpers were cleared before the retry", len(w.cleared) >= 1, w.cleared)

# ---- 3. nothing ever starts
never = lambda no, lbl: None
w = World(never)
install(w)
res = S._launch_queue([A("Main")])
check("three tries, then a clear message", len(w.calls) == 3 and res[0][1] is False and "tick 'Always allow'" in res[0][2], (res, w.calls))
check("the account's pill says it didn't start", S._lq["items"][0]["state"] == "failed")

# ---- 4. Roblox's own launcher is busy for a minute: that's progress, not failure
slow = lambda no, lbl: dict(pid_after=55, win_after=60, busy=55)
w = World(slow)
install(w)
res = S._launch_queue([A("Main")])
check("a slow start while Roblox's launcher works is waited for (no second launch)", res == [("Main", True, "ok")] and len(w.calls) == 1, (res, w.calls))

# ---- 5. Launch all: every account opens, in order, and each window goes to the right account
w = World(good)
install(w)
res = S._launch_queue([A("Main"), A("Alt"), A("Alt2")])
check("Launch all opens all three", [r[1] for r in res] == [True, True, True] and len(w.calls) == 3, (res, w.calls))
check("each window is assigned to its own account", [lbl for _h, lbl in S._assign_now] == ["Main", "Alt", "Alt2"] and len({h for h, _l in S._assign_now}) == 3, S._assign_now)

# ---- 6. Launch all where the SECOND account's first attempt is swallowed (the bug you saw)
def second_swallowed(no, lbl):
    if lbl == "Alt" and no == 2:
        return None
    return dict(pid_after=3, win_after=7)


w = World(second_swallowed)
install(w)
res = S._launch_queue([A("Main"), A("Alt"), A("Alt2")])
check("the second account recovers by itself, and the third still opens", [r[1] for r in res] == [True, True, True] and len(w.calls) == 4, (res, w.calls))
check("the second window is assigned to the second account", [lbl for _h, lbl in S._assign_now] == ["Main", "Alt", "Alt2"], S._assign_now)

# ---- 7. the next account waits for Roblox's launcher to finish the last one
w = World(lambda no, lbl: dict(pid_after=3, win_after=6, busy=12))
install(w)
res = S._launch_queue([A("Main"), A("Alt")])
check("the second launch waits until the launcher is quiet", w.calls[1][2] - w.calls[0][2] >= 12, [c[2] for c in w.calls])

# ---- 8. a hidden / parked window is found by its process
w = World(lambda no, lbl: dict(pid_after=3, win_after=6, visible=False))
install(w)
res = S._launch_queue([A("Main")])
check("a window that is hidden or parked still counts (found by its process)", res == [("Main", True, "ok")] and S._assign_now, (res, S._assign_now))

# ---- 9. Rejoin: its window and process are fully gone before the new launch, and stale helpers are cleared first
w = World(good)
install(w)
S._launch_queue([A("Main")])
h = w.procs[0]["hwnd"]
w.assign_json = {str(h): "Main"}
S._assign_now.clear()
w.calls.clear()
w.cleared.clear()
t_before = w.now
res = S._launch_queue([A("Main")], close_first=True)
check("rejoin works with one attempt", res == [("Main", True, "ok")] and len(w.calls) == 1, (res, w.calls))
check("the old process was gone before the new launch", w.calls[0][2] - t_before >= 5, (w.calls, t_before))
check("stale helpers were cleared between the close and the launch", any(c[0] == 3 and c[1] <= w.calls[0][2] for c in w.cleared), w.cleared)

# ---- 10. cancel stops cleanly
w = World(never)
install(w)
import threading
S._lq["cancel"] = False
orig = w.launch


def cancelling(a, retry=False):
    r = orig(a, retry)
    S._lq["cancel"] = True
    return r


S._launch_account = cancelling
res = S._launch_queue([A("Main"), A("Alt")])
check("cancel stops the queue", all(r[1] is False for r in res) and len(w.calls) <= 2, (res, w.calls))

# ---- 11. "loaded" comes from Roblox's own log, so a slow / failing picture check can't leave it saying "loading"
import ctypes
class _U:
    IsWindow = staticmethod(lambda h: 1)
class _Win:
    user32 = _U()
_real_windll = ctypes.windll
ctypes.windll = _Win()
try:
    w = World(good)
    install(w)
    S._window_ready = lambda h: False                       # the picture check never says yes
    t_game = {"t": None}
    S._client_for_hwnd = lambda h: {"state": "in game" if t_game["t"] and w.now >= t_game["t"] else "starting"}
    orig_launch = w.launch

    def launch_and_mark(a, retry=False):
        r = orig_launch(a, retry)
        t_game["t"] = w.now + 14                            # the log says "in game" 14 s after the launch
        return r

    S._launch_account = launch_and_mark
    t0 = w.now
    res = S._launch_queue([A("Main")])
    check("it's 'in game' as soon as Roblox's log says so (not after a minute of waiting)", res == [("Main", True, "ok")] and w.now - t0 < 30, (res, w.now - t0))
    check("the queue's last state for it is 'in game'", S._lq["items"][0]["state"] == "in game", S._lq["items"])
finally:
    ctypes.windll = _real_windll

# ---- 12. a client whose log never said "joined" but whose window has been up well over a minute counts as in game
class FakeProc:
    def __init__(self, created):
        self.created = created

    def memory_info(self):
        return type("M", (), {"rss": 1_500_000_000})()

    def cpu_percent(self, x):
        return 2.0

    def create_time(self):
        return self.created


w = World(good)
install(w)
S._roblox_player_pids = lambda: [4242, 4243]
S._procs.clear()
S._procs[4242] = FakeProc(w.now - 100)
S._procs[4243] = FakeProc(w.now - 20)
S._clients.clear()
S._tails.clear()
S._gwl.client_logs = lambda: {}
S._clients_pass(True)
check("up for 100 s with no 'joined' line in the log: shown as in game", S._clients[4242]["state"] == "in game" and S._clients[4242].get("assumed"), S._clients[4242])
check("up for only 20 s: still loading", S._clients[4243]["state"] == "starting", S._clients[4243])

print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.stdout.flush()
sys.exit(1 if fails else 0)
