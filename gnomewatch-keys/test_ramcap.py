"""The per-account memory cap logic with fake clients (no Roblox needed).   python test_ramcap.py"""
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


trimmed, notes, sess = [], [], []
S._hwnd_pid = lambda h: {101: 5001, 102: 5002}.get(h, 0)
S._trim_pid = lambda pid: (trimmed.append(pid) or True)
S.notify = lambda t, m, event=None: notes.append((t, m))
S._session_add = lambda a, k, d="": sess.append((a, k, d))
S._clients.clear()
S._clients.update({5001: {"rss": 3_400_000_000}, 5002: {"rss": 1_200_000_000}})
S.RAMCAP.clear()
S._ramcap_seen.clear()
caps = {"Main": 3072, "Alt": 2048}
assign = {"101": "Main", "102": "Alt", "999": "Ghost"}
T = 10_000.0

S._ramcap_pass(caps, assign, T)
check("a client over its cap is trimmed", trimmed == [5001], trimmed)
check("a client under its cap is left alone", 5002 not in trimmed)
check("the page can see who is over", S.RAMCAP["Main"]["over"] is True and S.RAMCAP["Alt"]["over"] is False, S.RAMCAP)
check("no warning straight away", not notes)
S._ramcap_pass(caps, assign, T + 30)
check("not trimmed again within 90 s", trimmed == [5001])
S._ramcap_pass(caps, assign, T + 95)
check("trimmed again after 90 s while still over", trimmed == [5001, 5001], trimmed)
S._ramcap_pass(caps, assign, T + 160)
check("still over a minute after a trim: one warning, and it names the account", len(notes) == 1 and "Main" in notes[0][0] and sess and sess[0][1] == "memory", (notes, sess))
S._ramcap_pass(caps, assign, T + 200)
S._ramcap_pass(caps, assign, T + 260)
check("warned at most once per 15 minutes", len(notes) == 1, notes)
S._clients[5001]["rss"] = 1_000_000_000
S._ramcap_pass(caps, assign, T + 300)
check("back under the cap clears it", S.RAMCAP["Main"]["over"] is False)
S._clients[5001]["rss"] = 3_600_000_000
S._ramcap_pass(caps, assign, T + 400)
check("going over again is trimmed again", trimmed.count(5001) >= 3, trimmed)
S._ramcap_pass({}, assign, T + 500)
check("no caps set: nothing tracked", not S.RAMCAP and not S._ramcap_seen)
S._ramcap_pass(caps, {"101": "Main"}, T + 600)
check("a label that is not assigned a window is not reported", "Alt" not in S.RAMCAP)
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.stdout.flush()
sys.exit(1 if fails else 0)
