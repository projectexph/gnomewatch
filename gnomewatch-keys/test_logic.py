"""Runs src/logic.js (the key rules) in an embedded JavaScript engine (pip install quickjs) with an in-memory store.
    python test_logic.py
"""
import hashlib
import json
import os
import re
import sys

import quickjs

HERE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(HERE, "src", "logic.js"), encoding="utf-8").read()
src = re.sub(r"^export ", "", src, flags=re.M)

ctx = quickjs.Context()
ctx.add_callable("py_sha256", lambda t: hashlib.sha256(t.encode()).hexdigest())
ADMIN = "GW-ADMIN-TESTKEY-0001"
ctx.eval(src)
ctx.eval("""
const mem = new Map();
let NOW = 1000000000000;
let seed = 7;
const deps = {
  store: {
    get: async (k) => mem.has(k) ? JSON.parse(mem.get(k)) : null,
    put: async (k, v, ttl) => { mem.set(k, JSON.stringify(v)); },
    delete: async (k) => { mem.delete(k); },
    list: async (p) => [...mem.keys()].filter(k => k.startsWith(p)),
  },
  now: () => NOW,
  random: (n) => { const a = new Uint8Array(n); for (let i = 0; i < n; i++) { seed = (seed * 1103515245 + 12345) & 0x7fffffff; a[i] = seed >> 8 & 255; } return a; },
  sha256: async (t) => py_sha256(t),
  adminHash: py_sha256(%s),
  keyHours: 24,
  maxDevices: 1,
};
""" % json.dumps(ADMIN))


def run(js):
    ctx.eval("globalThis.__r = undefined; (async () => { %s })().then(v => { globalThis.__r = JSON.stringify(v === undefined ? null : v); }, e => { globalThis.__r = JSON.stringify('ERR ' + e); });" % js)
    for _ in range(10000):
        if not ctx.execute_pending_job():
            break
    return json.loads(ctx.eval("globalThis.__r"))


fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


u1 = "{id:'111', name:'Kyle', avatar:'abc123'}"
r = run(f"const a = await issueKey(deps, {u1}); const b = await issueKey(deps, {u1}); return {{a: a.rec, af: a.fresh, b: b.rec.key, bf: b.fresh}};")
key1 = r["a"]["key"]
check("key looks like GW-XXXX-XXXX-XXXX", re.fullmatch(r"GW-[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", key1) is not None, key1)
check("first /getkey is a fresh key, lasts 24h", r["af"] is True and r["a"]["expires"] - 1000000000000 == 24 * 3600 * 1000)
check("asking again returns the same key", r["b"] == key1 and r["bf"] is False)

r = run(f"return await issueKey(deps, {{id:'111', name:'Kyle R', avatar:null}});")
check("name change keeps the same key and updates the name", r["rec"]["key"] == key1 and r["rec"]["name"] == "Kyle R")

r = run(f"return await login(deps, '{key1.lower()}  ', 'devA');")
check("login works (lower-case and spaces forgiven)", r["ok"] is True and r["name"] == "Kyle R" and r["admin"] is False and r["id"] == "111")
check("avatar url built", r.get("avatar") is None)  # avatar was cleared by the second call above
r = run(f"await issueKey(deps, {u1}); return await login(deps, '{key1}', 'devA');")
check("avatar url present when the user has one", (r.get("avatar") or "").startswith("https://cdn.discordapp.com/avatars/111/abc123.png"))
r = run(f"return await login(deps, '{key1}', 'devB');")
check("second PC is refused (1 PC per key)", r["ok"] is False and "another PC" in r["error"], r)
r = run(f"return await login(deps, '{key1}', 'devA');")
check("same PC still fine", r["ok"] is True)
r = run("return await login(deps, 'GW-NOPE-NOPE-NOPE', 'devA');")
check("unknown key refused", r["ok"] is False and "isn't valid" in r["error"])
r = run("return await login(deps, '', 'devA');")
check("empty key refused", r["ok"] is False)
r = run(f"return await login(deps, '{key1}', '');")
check("missing device id refused", r["ok"] is False)

r = run(f"return await login(deps, {json.dumps(ADMIN)}, 'whatever');")
check("admin key works and never expires", r["ok"] is True and r["admin"] is True and r["expires"] is None and r["name"] == "Admin")
r = run("return await login(deps, 'GW-ADMIN-TESTKEY-0002', 'x');")
check("near-miss admin key refused", r["ok"] is False)

r = run(f"NOW += 23*3600*1000; return await login(deps, '{key1}', 'devA');")
check("still valid at 23h", r["ok"] is True)
r = run(f"NOW += 2*3600*1000; return await login(deps, '{key1}', 'devA');")
check("expired after 24h", r["ok"] is False and "expired" in r["error"])
r = run(f"return await issueKey(deps, {u1});")
check("after expiry /getkey makes a NEW key", r["fresh"] is True and r["rec"]["key"] != key1)
key2 = r["rec"]["key"]

r = run(f"return await admin(deps, {{key:'nope', action:'list'}});")
check("admin actions refuse a normal key", r["ok"] is False)
r = run(f"return await admin(deps, {{key:{json.dumps(ADMIN)}, action:'list'}});")
check("admin can list keys (newest first)", r["ok"] is True and len(r["keys"]) == 2 and r["keys"][0]["key"] == key2)
r = run(f"return await admin(deps, {{key:{json.dumps(ADMIN)}, action:'revoke', target:'{key2}'}});")
check("revoke", r["ok"] is True and r["key"]["revoked"] is True)
r = run(f"return await login(deps, '{key2}', 'devA');")
check("revoked key can't log in", r["ok"] is False and "revoked" in r["error"])
r = run(f"return await admin(deps, {{key:{json.dumps(ADMIN)}, action:'unrevoke', target:'{key2}'}});")
check("unrevoke", r["ok"] is True and r["key"]["revoked"] is False)
r = run(f"await login(deps, '{key2}', 'devA'); const x = await login(deps, '{key2}', 'devZ'); const y = await admin(deps, {{key:{json.dumps(ADMIN)}, action:'reset', target:'{key2}'}}); const z = await login(deps, '{key2}', 'devZ'); return {{x: x.ok, y: y.key.devices.length, z: z.ok}};")
check("reset device lock lets the key move PC", r == {"x": False, "y": 0, "z": True}, r)
r = run(f"const before = (await deps.store.get('key:{key2}')).expires; const e = await admin(deps, {{key:{json.dumps(ADMIN)}, action:'extend', target:'{key2}', hours:12}}); return e.key.expires - before;")
check("extend adds hours", r == 12 * 3600 * 1000, r)
r = run(f"return await admin(deps, {{key:{json.dumps(ADMIN)}, action:'create', name:'Friend One', hours:48}});")
check("admin can create a key for a name", r["ok"] is True and r["key"]["name"] == "Friend One" and r["key"]["expires"] - 1000000000000 > 48 * 3600 * 1000 - 1)
mk = r["key"]["key"]
r = run(f"return await login(deps, '{mk}', 'devQ');")
check("the created key logs in", r["ok"] is True and r["name"] == "Friend One")
r = run(f"return await admin(deps, {{key:{json.dumps(ADMIN)}, action:'create', name:'  ', hours:5}});")
check("create needs a name", r["ok"] is False)
r = run(f"return await admin(deps, {{key:{json.dumps(ADMIN)}, action:'explode', target:'{mk}'}});")
check("unknown admin action refused", r["ok"] is False)

# ---- app updates
r = run("return [cmpVersion('1.0.0','1.0.1'), cmpVersion('1.10.0','1.9.9'), cmpVersion('v2.0','2.0.0'), cmpVersion(undefined,'1.0.0'), cmpVersion('1.2','1.2.0')];")
check("version compare (numeric, v-prefix, missing parts)", r == [-1, 1, 0, -1, 0], r)
run("deps.update = { latest: '1.2.0', min: '', url: 'https://example.com/s.exe', sha256: 'ABCD', notes: 'Faster' };")
r = run(f"return await login(deps, '{mk}', 'devQ', '1.1.0');")
check("older app signs in and is told an update exists (not forced)", r["ok"] is True and r["update"]["latest"] == "1.2.0" and r["update"]["forced"] is False and r["update"]["sha256"] == "abcd", r)
r = run(f"return await login(deps, '{mk}', 'devQ', '1.2.0');")
check("current app gets no update note", r["ok"] is True and "update" not in r, r)
run("deps.update.min = '1.1.5';")
r = run(f"return await login(deps, '{mk}', 'devQ', '1.1.0');")
check("below the minimum: can't sign in, told to update", r["ok"] is False and r["update"]["forced"] is True and "update" in r["error"].lower(), r)
r = run(f"return await login(deps, '{mk}', 'devQ');")
check("a copy that sends no version counts as old", r["ok"] is False and r["update"]["forced"] is True, r)
r = run(f"return await login(deps, {json.dumps(ADMIN)}, 'x', '0.0.1');")
check("the admin key is never locked out by the minimum", r["ok"] is True and r["admin"] is True, r)
r = run(f"return await login(deps, '{mk}', 'devQ', '1.1.5');")
check("at the minimum: signs in with an optional update note", r["ok"] is True and r["update"]["forced"] is False, r)
run("deps.update = {};")
r = run(f"return await login(deps, '{mk}', 'devQ');")
check("no update configured: everything as before", r["ok"] is True and "update" not in r, r)

# ---- weather reports relayed to the bot
run("globalThis.FWD = []; globalThis.BOT_OK = true; deps.forward = async (p) => { FWD.push(p); return BOT_OK ? {ok: true, outcome: 'started'} : {ok: false, error: 'unauthorized'}; };")
r = run("return [cleanReport({eventKey: ' Blizzard ', remainingSeconds: 212.6, extra: 'x'}), cleanReport({eventKey: '../x'}), cleanReport({eventKey: 'aurora', remainingSeconds: -1}), cleanReport({eventKey: 'aurora', remainingSeconds: 99999}), cleanReport(null), cleanReport({eventKey: ''})];")
check("a report is cleaned: lower-cased, seconds rounded, extras dropped", r[0] == {"eventKey": "blizzard", "remainingSeconds": 213}, r[0])
check("bad names / times / shapes are refused", r[1:] == [None, None, None, None, None], r)
r = run("return await relayReport(deps, {eventKey: 'Blizzard', remainingSeconds: 230}, {name: 'Kyle'});")
check("the first report of an event is forwarded to the bot", r["ok"] and r["forwarded"] and r["outcome"] == "started", r)
check("what the bot received is exactly the cleaned report", run("return FWD;") == [{"eventKey": "blizzard", "remainingSeconds": 230}], run("return FWD;"))
r = run("return await relayReport(deps, {eventKey: 'blizzard', remainingSeconds: 228}, {name: 'Sam'});")
check("the same event reported again right away is a duplicate and is NOT forwarded", r.get("duplicate") is True and len(run("return FWD;")) == 1, r)
r = run("return await relayReport(deps, {eventKey: 'aurora', remainingSeconds: 120}, {name: 'Sam'});")
check("a different event is forwarded", r.get("forwarded") is True and len(run("return FWD;")) == 2, r)
r = run("NOW += 200 * 1000; return await relayReport(deps, {eventKey: 'blizzard', remainingSeconds: 250}, {name: 'Sam'});")
check("after the window the same event is a new one and is forwarded", r.get("forwarded") is True and len(run("return FWD;")) == 3, r)
run("BOT_OK = false;")
r = run("return await relayReport(deps, {eventKey: 'toxic', remainingSeconds: 100}, {name: 'Kyle'});")
check("if the bot refuses, the report fails and says so", r["ok"] is False and "unauthorized" in r["error"], r)
run("BOT_OK = true;")
r = run("return await relayReport(deps, {eventKey: 'toxic', remainingSeconds: 98}, {name: 'Sam'});")
check("...and a failed report doesn't block the next copy from sending it", r.get("forwarded") is True, r)
run("const keep = deps.forward; deps.forward = null; globalThis.keep = keep;")
r = run("return await relayReport(deps, {eventKey: 'cursed', remainingSeconds: 100}, {name: 'Kyle'});")
check("with no bot connected on the server it is skipped, not an error", r["ok"] is True and r.get("skipped"), r)
run("deps.forward = globalThis.keep;")

print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
