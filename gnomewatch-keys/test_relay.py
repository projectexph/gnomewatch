"""Weather reports end to end: the app's post() -> the key server's relay -> a pretend weather bot.   python test_relay.py"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
PORT, ADMIN = 8830, "GW-TEST-ADMIN-KEY"
tmp = tempfile.mkdtemp(prefix="gw_relay_")
srv = subprocess.Popen([sys.executable, os.path.join(HERE, "dev_server.py"), "--port", str(PORT), "--admin-key", ADMIN, "--file", os.path.join(tmp, "s.json")],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def api(path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode() if body is not None else None, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=5).read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


try:
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2).read()
            break
        except Exception:
            time.sleep(0.25)
    key = api("/dev/getkey?id=11&name=Kyle")["key"]
    key2 = api("/dev/getkey?id=22&name=Sam")["key"]
    os.environ["GW_API"] = f"http://127.0.0.1:{PORT}"
    sys.argv = ["x"]
    os.environ.pop("GW_BOOT", None)
    import sentinel as S
    S.WORKER_URL, S.SECRET = "", ""
    S.GW_SESSION = None
    check("not signed in and no bot of your own: nothing to report to", S._reporting() is False)
    r = S.post({"eventKey": "blizzard", "remainingSeconds": 200})
    check("...so a report is skipped, nothing is sent", r.get("skipped") and not api("/dev/weather", {"ok": True})["forwarded"], r)
    S.GW_SESSION = {"key": key, "name": "Kyle"}
    check("signed in: reporting is on (through the server)", S._reporting() is True and S._relay_ready() and not S._direct_bot())
    r = S.post({"eventKey": "blizzard", "remainingSeconds": 200})
    fw = api("/dev/weather", {"ok": True})["forwarded"]
    check("an event start is relayed to the bot", r.get("ok") and r.get("forwarded") and fw == [{"eventKey": "blizzard", "remainingSeconds": 200}], (r, fw))
    check("it's recorded as connected in the app", S.STATE["worker"]["ok"] is True and S._relay_failed[0] is False, S.STATE["worker"])
    S.GW_SESSION = {"key": key2, "name": "Sam"}
    r = S.post({"eventKey": "blizzard", "remainingSeconds": 197})
    check("a second person reporting the same event is a duplicate, and the bot hears nothing more", r.get("duplicate") and len(api("/dev/weather", {"ok": True})["forwarded"]) == 1, r)
    r = S.post({"upcoming": {"name": "aurora", "inSeconds": 100}})
    check("'next event' countdowns are not sent (only starts are announced)", r.get("skipped") and len(api("/dev/weather", {"ok": True})["forwarded"]) == 1, r)
    api("/dev/weather", {"ok": False})
    r = S.post({"eventKey": "toxic", "remainingSeconds": 120})
    check("if the bot is unreachable the app notes it and will retry", r.get("ok") is False and S._relay_failed[0] is True and S.STATE["worker"]["ok"] is False, (r, S.STATE["worker"]))
    api("/dev/weather", {"ok": True})
    r = S.post({"eventKey": "toxic", "remainingSeconds": 105})
    check("...and the retry goes through", r.get("forwarded") and S._relay_failed[0] is False, r)
    api("/api/admin", {"key": ADMIN, "action": "revoke", "target": key2})
    r = S.post({"eventKey": "cursed", "remainingSeconds": 90})
    check("a revoked key can't report", r.get("ok") is False, r)
    S.GW_SESSION = {"key": "GW-FAKE-FAKE-FAKE", "name": "x"}
    r = S.post({"eventKey": "cursed", "remainingSeconds": 90})
    check("a made-up key can't report", r.get("ok") is False, r)
    S.GW_SESSION = {"key": key, "name": "Kyle"}
    r = S.post({"eventKey": "../../etc", "remainingSeconds": 90})
    check("a bad event name is refused by the server", r.get("ok") is False, r)
    S.WORKER_URL, S.SECRET = "https://example.invalid", "secret"
    check("a bot of your own, if set, is used directly instead (no double reporting)", S._direct_bot() is True and S._reporting() is True)
finally:
    srv.kill()
    sys.stdout.flush()
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.stdout.flush()
sys.exit(1 if fails else 0)
