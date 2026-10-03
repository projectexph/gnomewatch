"""Runs before anything else starts: shows the login page (or signs in with the remembered key) and returns the session.

    session = gw_boot.run(app_dir)      # blocks; exits the program (code 0) if the window is closed without signing in

The login page runs in its own short-lived process and hands the result back through a temporary file, so its window
system never shares anything with the app's own windows. A session is a dict: id, name, key, admin, expires,
avatar_path, data_dir (this user's own folder).
"""
import json
import os
import subprocess
import sys
import tempfile
import time


def _child(app_dir, out):
    """The login process: show the page, write the session to `out`."""
    import gw_license as L
    from gw_login_ui import LoginWindow
    sess = LoginWindow(app_dir, mode="boot", auto_key=L.load_saved(app_dir)).run()
    if sess:
        tmp = out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sess, f)
        os.replace(tmp, out)


def run(app_dir):
    out = os.path.join(tempfile.gettempdir(), f"gw_login_{os.getpid()}_{int(time.time())}.json")
    if getattr(sys, "frozen", False):                                # the packaged app starts itself in login mode
        cmd = [sys.executable, "--gw-login", app_dir, out]
    else:
        cmd = [sys.executable, os.path.abspath(__file__), "--gw-login", app_dir, out]
    try:
        p = subprocess.Popen(cmd, cwd=app_dir, creationflags=0x08000000)       # CREATE_NO_WINDOW
        p.wait()
        with open(out, encoding="utf-8") as f:
            sess = json.load(f)
    except (OSError, ValueError):
        sys.exit(0)                                                  # closed without signing in
    finally:
        try:
            os.remove(out)
        except OSError:
            pass
    return sess


if __name__ == "__main__" and len(sys.argv) >= 4 and sys.argv[1] == "--gw-login":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    _child(sys.argv[2], sys.argv[3])
