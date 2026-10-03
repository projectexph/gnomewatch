"""Local web dashboard for the sentinel. Serves dashboard.html + a small JSON API.

Two kinds of visitor:
  * this PC (127.0.0.1)      - full control; state-changing requests also need the per-run token that is
                               embedded in the page itself, and the Host header must be localhost.
  * other devices (Wi-Fi)    - only when lan=True, and only with the access key (link ?key=...). They get a
                               read-only view: no screen frame, no window list, no buttons - unless
                               lan_control=True is set.
"""
import json
import os
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".ico": "image/x-icon",
         ".svg": "image/svg+xml", ".json": "application/json"}
LOCAL = ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def start(port, state_fn, frame_fn, actions, lan=False, key=None, lan_control=False, share_url=None):
    """state_fn() -> dict, frame_fn() -> JPEG bytes or None, actions: {name: fn(payload) -> dict}."""
    token = secrets.token_urlsafe(24)
    local_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    host_re = re.compile(rf"^[A-Za-z0-9.\-\[\]:]+:{port}$")

    class H(BaseHTTPRequestHandler):
        server_version = "Sentinel"

        def log_message(self, *a):
            pass

        # ---- helpers
        def _remote(self):
            return self.client_address[0] not in LOCAL

        def _send(self, code, body, ctype="application/json", cache="no-store", cookie=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(body)

        def _key_ok(self, query):
            """Return (allowed, set-cookie header or None)."""
            if not self._remote():
                return True, None
            if not (lan and key):
                return False, None
            given = (query.get("key") or [""])[0]
            m = re.search(r"(?:^|;\s*)sk=([^;]+)", self.headers.get("Cookie", ""))
            cookie_val = m.group(1) if m else ""
            if given and secrets.compare_digest(given, key):
                return True, f"sk={key}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000"
            if cookie_val and secrets.compare_digest(cookie_val, key):
                return True, None
            return False, None

        def _host_ok(self):
            host = self.headers.get("Host", "")
            ok = (host in local_hosts) if not self._remote() else bool(host_re.match(host))
            if not ok:
                self._send(403, {"ok": False, "error": "bad host"})
            return ok

        def _full(self):
            """Does this visitor get the controls / screen frame?"""
            return (not self._remote()) or lan_control

        # ---- GET
        def do_GET(self):
            if not self._host_ok():
                return
            u = urlparse(self.path)
            path, query = u.path, parse_qs(u.query)
            allowed, cookie = self._key_ok(query)
            if not allowed:
                self._send(403, "<h3>Access key needed</h3><p>Open the full link you were given (it ends in ?key=…).</p>",
                           "text/html; charset=utf-8")
                return
            try:
                if path in ("/", "/index.html"):
                    html = open(os.path.join(HERE, "dashboard.html"), encoding="utf-8").read()
                    html = html.replace("__TOKEN__", token if self._full() else "")
                    html = html.replace("__READONLY__", "0" if self._full() else "1")
                    self._send(200, html, "text/html; charset=utf-8", cookie=cookie)
                elif path == "/api/state":
                    st = dict(state_fn())
                    if not self._full():
                        st.update(instances=[], can_launch=False, readonly=True)
                    else:
                        st["readonly"] = False
                        if share_url and not self._remote():
                            st["share"] = share_url
                    self._send(200, st)
                elif path == "/api/frame.jpg":
                    data = frame_fn() if self._full() else None
                    if data:
                        self._send(200, data, "image/jpeg")
                    else:
                        self._send(404, b"", "text/plain")
                elif path.startswith("/assets/"):
                    name = os.path.basename(path)
                    full = os.path.join(ASSETS, name)
                    ext = os.path.splitext(name)[1].lower()
                    if ext in TYPES and os.path.isfile(full):
                        self._send(200, open(full, "rb").read(), TYPES[ext], cache="max-age=3600")
                    else:
                        self._send(404, b"", "text/plain")
                else:
                    self._send(404, b"", "text/plain")
            except Exception as e:
                self._send(500, {"ok": False, "error": str(e)})

        # ---- POST
        def do_POST(self):
            if not self._host_ok():
                return
            allowed, _ = self._key_ok({})
            if (not allowed or not self._full() or self.path != "/api/action"
                    or self.headers.get("X-Token", "") != token):
                self._send(403, {"ok": False, "error": "forbidden"})
                return
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if n > 4096:
                    raise ValueError("too large")
                payload = json.loads(self.rfile.read(n) or b"{}")
                fn = actions.get(payload.get("action"))
                if fn is None:
                    self._send(400, {"ok": False, "error": "unknown action"})
                    return
                self._send(200, fn(payload) or {"ok": True})
            except Exception as e:
                self._send(500, {"ok": False, "error": str(e)})

    srv = ThreadingHTTPServer(("0.0.0.0" if lan else "127.0.0.1", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
