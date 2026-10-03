"""A local copy of the Gnomewatch key service, for testing the app without Cloudflare.

It runs the SAME rules (src/logic.js) in an embedded JavaScript engine, behind plain HTTP, with the keys saved in a JSON file.

    pip install quickjs
    python dev_server.py --admin-key GW-TEST-ADMIN --port 8787
    (in the app's gw_config.json set "api": "http://127.0.0.1:8787")

Dev-only extras:  GET /dev/getkey?id=123&name=Kyle   makes a key like /getkey does
                  POST /dev/clock {"hours": 25}       moves the server clock forward (to test expiry)
"""
import argparse
import hashlib
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import quickjs

HERE = os.path.dirname(os.path.abspath(__file__))


class Service:
    def __init__(self, admin_key, store_file, hours=24, max_devices=1, update=None):
        self.update = update or {}
        self.lock = threading.Lock()
        self.store_file = store_file
        self.data = {}
        self.offset_ms = 0
        if os.path.exists(store_file):
            try:
                self.data = json.load(open(store_file, encoding="utf-8"))
            except (OSError, ValueError):
                self.data = {}
        src = re.sub(r"^export ", "", open(os.path.join(HERE, "src", "logic.js"), encoding="utf-8").read(), flags=re.M)
        self.ctx = quickjs.Context()
        self.ctx.add_callable("py_sha256", lambda t: hashlib.sha256(t.encode()).hexdigest())
        self.forwarded = []                                              # what the pretend weather bot received
        self.weather_ok = True
        self.ctx.add_callable("py_forward", self._forward)
        self.ctx.add_callable("py_delete", lambda k: self.data.pop(k, None) and None)
        self.ctx.add_callable("py_get", lambda k: self.data.get(k, ""))
        self.ctx.add_callable("py_put", self._put)
        self.ctx.add_callable("py_list", lambda p: json.dumps([k for k in self.data if k.startswith(p)]))
        self.ctx.add_callable("py_now", lambda: int(time.time() * 1000) + self.offset_ms)
        self.ctx.add_callable("py_rand", lambda n: json.dumps(list(os.urandom(int(n)))))
        self.ctx.eval(src)
        self.ctx.eval("""
        const deps = {
          store: { get: async (k) => { const v = py_get(k); return v ? JSON.parse(v) : null; },
                   put: async (k, v, ttl) => { py_put(k, JSON.stringify(v)); },
                   delete: async (k) => { py_delete(k); },
                   list: async (p) => JSON.parse(py_list(p)) },
          forward: async (p) => JSON.parse(py_forward(JSON.stringify(p))),
          now: () => py_now(),
          random: (n) => new Uint8Array(JSON.parse(py_rand(n))),
          sha256: async (t) => py_sha256(t),
          adminHash: py_sha256(%s), keyHours: %d, maxDevices: %d, update: %s,
        };""" % (json.dumps(admin_key), hours, max_devices, json.dumps(self.update)))

    def _forward(self, text):
        payload = json.loads(text)
        self.forwarded.append(payload)
        return json.dumps({"ok": True, "outcome": "ok"} if self.weather_ok else {"ok": False, "error": "unauthorized"})

    def _put(self, k, v):
        self.data[k] = v
        tmp = self.store_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.store_file)

    def call(self, js):
        with self.lock:
            self.ctx.eval("globalThis.__r = undefined; (async () => { %s })().then(v => { globalThis.__r = JSON.stringify(v === undefined ? null : v); },"
                          " e => { globalThis.__r = JSON.stringify({ok:false,error:'server error: ' + e}); });" % js)
            for _ in range(10000):
                if not self.ctx.execute_pending_job():
                    break
            return json.loads(self.ctx.eval("globalThis.__r"))


def make_handler(svc):
    class H(BaseHTTPRequestHandler):
        def _send(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/dev/getkey":
                q = parse_qs(u.query)
                user = {"id": q.get("id", ["1"])[0], "name": q.get("name", ["Tester"])[0], "avatar": None}
                r = svc.call("const x = await issueKey(deps, %s); return {key: x.rec.key, expires: x.rec.expires, fresh: x.fresh};" % json.dumps(user))
                return self._send(r)
            self._send({"ok": True, "service": "gnomewatch dev key server"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send({"ok": False, "error": "Bad request."}, 400)
            if self.path in ("/api/login", "/api/check"):
                return self._send(svc.call("return await login(deps, %s, %s, %s);" % (json.dumps(body.get("key")), json.dumps(body.get("device")), json.dumps(body.get("version")))))
            if self.path == "/api/report":
                who = svc.call("return await login(deps, %s, %s, %s);" % (json.dumps(body.get("key")), json.dumps(body.get("device")), json.dumps(body.get("version"))))
                if not who.get("ok"):
                    return self._send({"ok": False, "error": who.get("error")}, 401)
                return self._send(svc.call("return await relayReport(deps, %s, %s);" % (json.dumps(body.get("body")), json.dumps(who))))
            if self.path == "/dev/weather":
                svc.weather_ok = bool(body.get("ok", True))
                if body.get("reset"):
                    svc.forwarded.clear()
                return self._send({"ok": True, "forwarded": svc.forwarded})
            if self.path == "/api/admin":
                return self._send(svc.call("return await admin(deps, %s);" % json.dumps(body)))
            if self.path == "/dev/clock":
                svc.offset_ms = int(float(body.get("hours", 0)) * 3600 * 1000)
                return self._send({"ok": True, "offsetHours": svc.offset_ms / 3600000})
            self._send({"ok": False, "error": "Not found."}, 404)

    return H


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--admin-key", default=os.environ.get("ADMIN_KEY", "GW-TEST-ADMIN"))
    ap.add_argument("--file", default=os.path.join(HERE, "dev_store.json"))
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--max-devices", type=int, default=1)
    for a in ("latest", "min", "url", "sha256", "notes"):
        ap.add_argument("--" + a, default="")
    a = ap.parse_args()
    upd = {"latest": a.latest, "min": a.min, "url": a.url, "sha256": a.sha256, "notes": a.notes}
    svc = Service(a.admin_key, a.file, a.hours, a.max_devices, upd if a.latest else {})
    print(f"dev key server on http://127.0.0.1:{a.port}  (admin key: {a.admin_key})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(svc)).serve_forever()
