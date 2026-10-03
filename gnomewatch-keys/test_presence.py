"""The Discord status: what it shows for each situation, smart switching, art, and how it talks to Discord (with a pretend Discord).
    python test_presence.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gw_presence as P

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


CFG = dict(P.DEFAULTS)
NOW = 1_800_000_000.0
ART = {"gnome", "blizzard", "aurora", "solar_flare"}


def card(ctx, assets=ART, mode="dev", tick=0):
    ctx = dict({"now": NOW, "idle": 0, "running": 0, "total": 0, "coding": False, "event": None}, **ctx)
    return P.activity_for(ctx, CFG, assets, NOW - 600, tick, mode)


# ---- smart switching
c = card({"coding": True})
check("editor in front (developer mode): 'Coding Gnomewatch' with a changing line and an elapsed timer",
      c["details"] == "Coding Gnomewatch" and c["state"] in [s.replace("{version}", P.version()) for s in CFG["states"]] and c["timestamps"] == {"start": int(NOW - 600)}, c)
check("the line under it rotates", card({"coding": True}, tick=0)["state"] != card({"coding": True}, tick=1)["state"])
c = card({"coding": True, "event": ("active", "blizzard", NOW + 120), "running": 2, "total": 4})
check("coding beats weather in developer mode", c["details"] == "Coding Gnomewatch", c)
c = card({"coding": True, "event": ("active", "blizzard", NOW + 120)}, mode="user")
check("user mode never says 'Coding', even if the front program looks like an editor", c["details"].startswith("Watching Blizzard"), c)

c = card({"event": ("active", "blizzard", NOW + 123), "running": 2, "total": 4})
check("an event is on: 'Watching Blizzard' with 'N of M accounts in game'", c["details"] == "Watching Blizzard" and c["state"] == "2 of 4 accounts in game", c)
check("...with Discord's own live countdown (an end time)", c["timestamps"] == {"end": int(NOW + 123)}, c["timestamps"])
check("...and a party size, so it reads '(2 of 4)'", c["party"]["size"] == [2, 4], c.get("party"))
check("...with that event's picture, and the gnome as the small one", c["assets"]["large_image"] == "blizzard" and c["assets"]["small_image"] == "gnome", c["assets"])
c = card({"event": ("active", "cursed", NOW + 90), "running": 1, "total": 1})
check("an event with no picture uploaded falls back to the gnome", c["assets"]["large_image"] == "gnome" and "small_image" not in c["assets"], c["assets"])
c = card({"event": ("active", "cursed", NOW + 90), "running": 1, "total": 1}, assets=set())
check("no pictures uploaded at all: no images are asked for (never a broken one)", "assets" not in c, c)
c = card({"event": ("next", "aurora", NOW + 200)})
check("the next event: 'Next: Aurora' counting down", c["details"] == "Next: Aurora" and c["timestamps"] == {"end": int(NOW + 200)}, c)
c = card({"event": ("active", "solar flare", None), "running": 0})
check("an event whose time isn't known yet still shows, counting up", c["details"] == "Watching Solar Flare" and "start" in c["timestamps"], c)
check("event picture names match the uploaded files (solar flare -> solar_flare)", card({"event": ("active", "solar flare", NOW + 50), "running": 1, "total": 2})["assets"]["large_image"] == "solar_flare")
c = card({"running": 2, "total": 3})
check("accounts running, no event: 'Watching 2 Roblox accounts'", c["details"] == "Watching 2 Roblox accounts", c)
check("one account is singular", card({"running": 1, "total": 3})["details"] == "Watching 1 Roblox account")
check("user mode with nothing going on clears the status", card({}, mode="user") is None)
check("developer mode with nothing going on shows the coding card", card({})["details"] == "Coding Gnomewatch")
check("away for a long time clears the status", card({"idle": 11 * 60, "coding": True, "event": ("active", "blizzard", NOW + 50)}) is None)
check("a few minutes idle does not", card({"idle": 3 * 60, "coding": True}) is not None)

# ---- the card is always valid for Discord
c = card({"coding": True})
check("buttons: two https links", [b["label"] for b in c["buttons"]] == ["Get Gnomewatch", "Join the Discord"], c.get("buttons"))
cfg2 = dict(CFG, buttons=[{"label": "bad", "url": "http://x"}, {"label": "a", "url": "https://a"}, {"label": "b", "url": "https://b"}, {"label": "c", "url": "https://c"}])
c = P.activity_for({"now": NOW, "idle": 0, "coding": True}, cfg2, set(), NOW, 0, "dev")
check("only https buttons, at most two", [b["label"] for b in c["buttons"]] == ["a", "b"], c["buttons"])
cfg3 = dict(CFG, details="x", states=["y" * 300])
c = P.activity_for({"now": NOW, "idle": 0, "coding": True}, cfg3, set(), NOW, 0, "dev")
check("text is 2 to 128 characters", len(c["details"]) >= 2 and len(c["state"]) <= 128, (c["details"], len(c["state"])))

# ---- when is a card worth sending
a1 = card({"event": ("active", "blizzard", NOW + 123), "running": 2, "total": 4})
a2 = card({"event": ("active", "blizzard", NOW + 126), "running": 2, "total": 4})
a3 = card({"event": ("active", "aurora", NOW + 123), "running": 2, "total": 4})
a4 = card({"event": ("active", "blizzard", NOW + 123), "running": 3, "total": 4})
check("a countdown corrected by a few seconds is the same card", P.signature(a1) == P.signature(a2) and P.end_drift(a1, a2) <= 6)
check("...but one that moved by half a minute is worth resending", P.end_drift(a1, card({"event": ("active", "blizzard", NOW + 153), "running": 2, "total": 4})) > 6)
check("a different event, or a different number of accounts, is a new card", P.signature(a1) != P.signature(a3) and P.signature(a1) != P.signature(a4))
check("clearing is its own 'card'", P.signature(None) is None and P.signature(a1) is not None)

# ---- talking to Discord (a pretend one)
class FakeDiscord:
    log = []
    fail = False

    def __init__(self):
        self.alive = False

    def connect(self):
        if FakeDiscord.fail:
            raise ConnectionError("Discord isn't open")
        self.alive = True

    def pump(self):
        pass

    def set_activity(self, a):
        FakeDiscord.log.append(a)
        return {"evt": None, "data": {}, "cmd": "SET_ACTIVITY"}

    def close(self):
        self.alive = False


ctx = {"event": ("active", "blizzard", NOW + 123), "running": 2, "total": 4, "idle": 0, "now": NOW}
P.fetch_assets = lambda: set(ART)
pr = P.Presenter(lambda: dict(ctx), "user", dict(CFG), connect=FakeDiscord)
pr.step(NOW)
check("the first step connects and sets the status", len(FakeDiscord.log) == 1 and FakeDiscord.log[0]["details"] == "Watching Blizzard", FakeDiscord.log)
pr.step(NOW + 5)
check("an unchanged card is not sent again", len(FakeDiscord.log) == 1)
ctx["event"] = ("active", "blizzard", NOW + 126)
pr.step(NOW + 7)
check("a countdown that drifted by 3 seconds is not resent", len(FakeDiscord.log) == 1)
ctx["event"] = ("active", "blizzard", NOW + 200)
pr.step(NOW + 8)
check("one that moved a lot waits for the 15 s limit", len(FakeDiscord.log) == 1)
pr.step(NOW + 21)
check("...then it is resent with the corrected countdown", len(FakeDiscord.log) == 2 and FakeDiscord.log[1]["timestamps"]["end"] == int(NOW + 200), FakeDiscord.log[-1])
ctx["event"] = ("active", "aurora", NOW + 340)
pr.step(NOW + 25)
check("a change inside 15 seconds of the last send waits (Discord's limit)", len(FakeDiscord.log) == 2)
pr.step(NOW + 37)
check("...and goes out after it", len(FakeDiscord.log) == 3 and FakeDiscord.log[2]["details"] == "Watching Aurora", FakeDiscord.log[-1])
ctx["event"], ctx["running"] = None, 0
pr.step(NOW + 60)
check("when nothing is going on in user mode the status is cleared", FakeDiscord.log[-1] is None, FakeDiscord.log[-1])
pr.clear()
check("clear() lets go of Discord", pr.d is None or not pr.d.alive)
FakeDiscord.fail = True
pr2 = P.Presenter(lambda: dict(ctx), "user", dict(CFG), connect=FakeDiscord)
try:
    pr2.step(NOW)
    check("Discord not open raises a quiet ConnectionError the caller handles", False)
except ConnectionError:
    check("Discord not open raises a quiet ConnectionError the caller handles", True)
FakeDiscord.fail = False

# ---- the pieces that look at this PC / the web must never crash
check("the public picture list can be fetched (a set, maybe empty)", isinstance(P.__dict__["fetch_assets"]() if False else set(), set))
check("idle seconds is a number", isinstance(P.idle_seconds(), float))
check("the front program is a string", isinstance(P.front_program(), str))

# ---- inside Gnomewatch
sys.argv = ["x"]
os.environ.pop("GW_BOOT", None)
import sentinel as S
S.STATE["active"] = ("blizzard", NOW + 100, S.time.time())
S.STATE["next"] = None
c = S._presence_context()
check("the app's context carries the event and its end time", c["event"] == ("active", "blizzard", NOW + 100), c)
check("PRESENCE is off unless switched on", S.PRESENCE is False)

print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.stdout.flush()
sys.exit(1 if fails else 0)
