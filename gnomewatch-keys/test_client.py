"""Starts the local key server and runs the app's sign-in library against it.   python test_client.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)
import gw_license as L

PORT = 8791
ADMIN = "GW-TEST-ADMIN-KEY"
tmp = tempfile.mkdtemp(prefix="gw_client_")
srv = subprocess.Popen([sys.executable, os.path.join(HERE, "dev_server.py"), "--port", str(PORT), "--admin-key", ADMIN,
                        "--file", os.path.join(tmp, "store.json")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def get(path):
    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=5).read())


def clock(hours):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/dev/clock", data=json.dumps({"hours": hours}).encode(), method="POST")
    urllib.request.urlopen(req, timeout=5).read()


try:
    for _ in range(40):
        try:
            get("/")
            break
        except Exception:
            time.sleep(0.25)
    app = os.path.join(tmp, "app")
    os.makedirs(app)
    json.dump({"api": f"http://127.0.0.1:{PORT}", "invite": "https://discord.gg/example"}, open(os.path.join(app, "gw_config.json"), "w"))
    for n, c in {".env": "NOTIFY=1\n", "accounts.json": '[{"label":"Main","browser":"edge","profile":"Default"}]'}.items():
        open(os.path.join(app, n), "w").write(c)
    os.makedirs(os.path.join(app, "icons"))
    open(os.path.join(app, "icons", "aurora.png"), "wb").write(b"png")
    open(os.path.join(app, "defaults.env"), "w").write("NOTIFY=0\n")

    k = get("/dev/getkey?id=4242&name=Kyle")
    key = k["key"]
    r = L.login(app, key)
    check("valid key signs in", r["ok"] and r["name"] == "Kyle" and r["id"] == "4242", r)
    check("device id is stable and hashed", L.device_id() == L.device_id() and len(L.device_id()) == 32)
    sess = L.make_session(app, key, r)
    check("user folder is profiles/<discord id>", sess["data_dir"].endswith(os.path.join("profiles", "4242")) and os.path.isdir(sess["data_dir"]))
    check("a new user gets defaults, not the admin's files", open(os.path.join(sess["data_dir"], ".env")).read() == "NOTIFY=0\n"
          and not os.path.exists(os.path.join(sess["data_dir"], "accounts.json")))
    left = L.seconds_left(sess)
    check("time left is about 24h", 23.9 * 3600 < left <= 24 * 3600, left)
    check("heartbeat check works", L.check(app, key)["ok"])

    real = L.device_id
    L.device_id = lambda: "ANOTHER-PC"
    r2 = L.login(app, key)
    L.device_id = real
    check("a second PC is refused", not r2["ok"] and "another PC" in r2["error"], r2)
    check("bad key gives a clear error", "isn't valid" in L.login(app, "GW-AAAA-BBBB-CCCC")["error"])

    ra = L.login(app, ADMIN)
    check("admin key signs in with no expiry", ra["ok"] and ra["admin"] and ra["expires"] is None)
    sa = L.make_session(app, ADMIN, ra)
    check("admin folder is filled from the existing install (copies, originals stay)",
          os.path.exists(os.path.join(sa["data_dir"], "accounts.json")) and os.path.exists(os.path.join(sa["data_dir"], "icons", "aurora.png"))
          and os.path.exists(os.path.join(app, "accounts.json")))
    check("admin time left is None", L.seconds_left(sa) is None)
    lst = L.admin_call(app, ADMIN, "list")
    check("admin list works", lst["ok"] and len(lst["keys"]) == 1 and lst["keys"][0]["name"] == "Kyle")
    check("admin call refused for a normal key", not L.admin_call(app, key, "list")["ok"])
    made = L.admin_call(app, ADMIN, "create", name="Friend", hours=2)
    check("admin can create a key", made["ok"] and L.login(app, made["key"]["key"])["ok"])
    check("revoke logs them out", L.admin_call(app, ADMIN, "revoke", target=key)["ok"] and "revoked" in L.check(app, key)["error"])
    L.admin_call(app, ADMIN, "unrevoke", target=key)

    L.save_key(app, key)
    check("stay signed in saves the key", L.load_saved(app) == key)
    L.forget(app)
    check("sign out forgets it", L.load_saved(app) is None)

    clock(25)
    check("key expires after 24h", "expired" in L.check(app, key)["error"])
    check("admin key still fine 25h later", L.check(app, ADMIN)["ok"])
    clock(0)

    srv.kill()
    time.sleep(0.5)
    r = L.login(app, key)
    check("server down gives a friendly message (no crash)", not r["ok"] and r.get("network") and "reach" in r["error"], r)
    json.dump({"api": ""}, open(os.path.join(app, "gw_config.json"), "w"))
    check("no server configured says so", "isn't set up" in L.login(app, key)["error"])
finally:
    srv.kill()
    shutil.rmtree(tmp, ignore_errors=True)
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
