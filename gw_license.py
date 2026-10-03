"""Gnomewatch sign-in: the device ID, talking to the key service, remembering the key on this PC, and each user's own folder.

Keys are made by the Discord bot (/getkey) and checked by the key service (gnomewatch-keys/). Everything a person sets up
(settings, accounts, scenes, auto-place recordings, history) lives in profiles/<their Discord id>/, so it follows the person,
not the key: a new key tomorrow opens the same data. The admin key opens profiles/admin/.

Nothing here ever sees a password or cookie.
"""
import hashlib
import json
import os
import shutil
import ssl
import time
import urllib.error
import urllib.request

from gw_version import VERSION

CONFIG_NAME = "gw_config.json"
SESSION_NAME = "session.json"
DEFAULT_CONFIG = {"api": "", "invite": "", "app_name": "Gnomewatch"}

# What an existing install already has: copied into profiles/admin the first time the admin key is used.
MIGRATE_FILES = [".env", "secrets.env", "accounts.json", "assign.json", "scenes.json", "macros.json", "known_events.json",
                 "history.jsonl", "sessions.jsonl", "mode_prev.json"]
MIGRATE_DIRS = ["icons", "backups"]


def _ssl():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def config(app_dir):
    path = os.path.join(app_dir, CONFIG_NAME)
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.load(open(path, encoding="utf-8")))
    except (OSError, ValueError):
        try:
            json.dump(DEFAULT_CONFIG, open(path, "w", encoding="utf-8"), indent=2)
        except OSError:
            pass
    env_api = os.environ.get("GW_API")
    if env_api:
        cfg["api"] = env_api
    cfg["api"] = (cfg.get("api") or "").rstrip("/")
    return cfg


def device_id():
    """A stable hashed ID for this PC (from Windows' machine GUID). Not reversible, and nothing personal is in it."""
    guid = ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            guid = winreg.QueryValueEx(k, "MachineGuid")[0]
    except Exception:
        import uuid
        guid = str(uuid.getnode())
    return hashlib.sha256(("gnomewatch|" + guid).encode()).hexdigest()[:32]


def post(api, path, payload, timeout=10):
    """POST JSON. Returns (dict or None, error text or None)."""
    if not api:
        return None, "The key server isn't set up yet (gw_config.json has no \"api\")."
    req = urllib.request.Request(api + path, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "Gnomewatch/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl()) as r:
            return json.loads(r.read().decode()), None
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode()), None
        except Exception:
            return None, f"The key server answered with an error ({e.code})."
    except urllib.error.URLError:
        return None, "Can't reach the key server. Check your internet and try again."
    except Exception as e:
        return None, f"Couldn't check the key: {str(e)[:60]}"


def login(app_dir, key):
    """Check a key. Returns the server's answer plus 'error' for any problem (never raises)."""
    cfg = config(app_dir)
    res, err = post(cfg["api"], "/api/login", {"key": key, "device": device_id(), "version": VERSION})
    if res is None:
        return {"ok": False, "error": err, "network": True}
    if not res.get("ok"):
        res.setdefault("error", "That key didn't work.")
    return res


def check(app_dir, key):
    """The same check, used as a heartbeat while the app runs."""
    cfg = config(app_dir)
    res, err = post(cfg["api"], "/api/check", {"key": key, "device": device_id(), "version": VERSION})
    if res is None:
        return {"ok": False, "error": err, "network": True}
    return res


def report(app_dir, key, body, timeout=10):
    """Send a weather report (an event started) to the key server, which forwards the first copy to the weather bot. Never raises."""
    cfg = config(app_dir)
    res, err = post(cfg["api"], "/api/report", {"key": key, "device": device_id(), "version": VERSION, "body": body}, timeout=timeout)
    if res is None:
        return {"ok": False, "error": err, "network": True}
    return res


def admin_call(app_dir, key, action, **kw):
    cfg = config(app_dir)
    res, err = post(cfg["api"], "/api/admin", {"key": key, "action": action, **kw}, timeout=15)
    if res is None:
        return {"ok": False, "error": err, "network": True}
    return res


# ---------------------------------------------------------------------------------------------- remember me
def load_saved(app_dir):
    try:
        return json.load(open(os.path.join(app_dir, SESSION_NAME), encoding="utf-8")).get("key")
    except (OSError, ValueError):
        return None


def save_key(app_dir, key):
    try:
        with open(os.path.join(app_dir, SESSION_NAME), "w", encoding="utf-8") as f:
            json.dump({"key": key, "saved": int(time.time())}, f)
    except OSError:
        pass


def forget(app_dir):
    try:
        os.remove(os.path.join(app_dir, SESSION_NAME))
    except OSError:
        pass


# ---------------------------------------------------------------------------------------------- the user's own folder
def _safe(s):
    return "".join(c for c in str(s) if c.isalnum() or c in "-_") or "user"


def profile_dir(app_dir, user_id):
    return os.path.join(app_dir, "profiles", _safe(user_id))


def ensure_profile(app_dir, sess):
    """Make the user's folder. The admin's first folder is filled from the existing install so nothing is lost."""
    d = profile_dir(app_dir, sess["id"])
    fresh = not os.path.isdir(d)
    os.makedirs(d, exist_ok=True)
    if fresh and sess.get("admin"):
        for name in MIGRATE_FILES:
            src = os.path.join(app_dir, name)
            if os.path.isfile(src):
                try:
                    shutil.copy2(src, os.path.join(d, name))
                except OSError:
                    pass
        for name in MIGRATE_DIRS:
            src = os.path.join(app_dir, name)
            if os.path.isdir(src):
                try:
                    shutil.copytree(src, os.path.join(d, name), dirs_exist_ok=True)
                except OSError:
                    pass
    elif fresh:
        defaults = os.path.join(app_dir, "defaults.env")
        try:
            if os.path.isfile(defaults):
                shutil.copy2(defaults, os.path.join(d, ".env"))
            seed = os.path.join(app_dir, "assets", "seed_icons")
            if os.path.isdir(seed):
                shutil.copytree(seed, os.path.join(d, "icons"), dirs_exist_ok=True)
        except OSError:
            pass
    return d


def fetch_avatar(url, dest):
    """Download the Discord profile picture (small, public). Returns the path or None."""
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Gnomewatch/2.0"})
        with urllib.request.urlopen(req, timeout=6, context=_ssl()) as r:
            data = r.read(400_000)
        with open(dest, "wb") as f:
            f.write(data)
        return dest
    except Exception:
        return dest if os.path.isfile(dest) else None


def make_session(app_dir, key, res):
    """The session dict the rest of the app uses, built from a successful server answer."""
    sess = {"key": key, "id": str(res.get("id") or "user"), "name": res.get("name") or "there", "expires": res.get("expires"),
            "admin": bool(res.get("admin")), "avatar_url": res.get("avatar"), "signed_in": int(time.time()),
            "skew": (res.get("serverTime") or 0) / 1000.0 - time.time() if res.get("serverTime") else 0.0,
            "update": res.get("update")}
    sess["data_dir"] = ensure_profile(app_dir, sess)
    sess["avatar_path"] = fetch_avatar(sess["avatar_url"], os.path.join(sess["data_dir"], "avatar.png")) if sess["avatar_url"] else None
    return sess


def seconds_left(sess):
    """Seconds until the key expires (None = never), by the server's clock."""
    if not sess or sess.get("expires") is None:
        return None
    return sess["expires"] / 1000.0 - (time.time() + sess.get("skew", 0.0))


def fmt_left(sec):
    if sec is None:
        return "no expiry"
    sec = max(0, int(sec))
    return f"{sec // 3600}h {sec % 3600 // 60}m" if sec >= 3600 else f"{sec // 60}m"
