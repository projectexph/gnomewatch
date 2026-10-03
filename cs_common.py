"""Shared constants and helpers for the console (v2) and its pages."""
import json
import os

APP_NAME = "Gnomewatch"
TAGLINE = "weather watcher"
W, H, SIDE, PADX = 1180, 760, 216, 28
CW = W - SIDE - 2 * PADX                              # width of a page: 908
BODY_H = 618                                          # height available under the header
SCROLL_W = CW - 12                                    # scrolling pages leave a slim gutter for the scroll thumb
MINI_W, MINI_H = 500, 96

NAV = [("Overview", "layout-dashboard"), ("Instances", "monitor"), ("Accounts", "users"), ("Auto-place", "wand-sparkles"),
       ("Performance", "gauge"), ("Activity", "activity"), ("Settings", "settings"), ("Help", "info")]
SUBS = {"Overview": "Live status at a glance", "Instances": "Every Roblox window, live",
        "Accounts": "Browser profiles you can launch", "Auto-place": "Place your gnome when an event starts or ends",
        "Performance": "Modes, live numbers and auto-switch rules",
        "Admin": "Keys, who has them, and what they can do",
        "Activity": "What has been happening",
        "Settings": "Everything you can tune",
        "Help": "How things work, and what to do when they don't"}

PRESETS = {
    "Quiet":    {"POLL_SECONDS": 5.0, "PARK_CORES": 1, "IDLE_BACKOFF": True},
    "Balanced": {"POLL_SECONDS": 2.0, "PARK_CORES": 1, "IDLE_BACKOFF": True},
    "Sharp":    {"POLL_SECONDS": 1.0, "PARK_CORES": 2, "IDLE_BACKOFF": False},
}
DEFAULT_SCENES = {
    "AFK":     {"HIDE_WINDOW": True, "HIDE_ALL": True, "THROTTLE_PARKED": True, "PARK_CORES": 1, "KEEPALIVE": True,
                "NOTIFY": True, "POLL_SECONDS": 5.0, "IDLE_BACKOFF": True, "PANEL_MINI": True, "_windows": "auto"},
    "Working": {"HIDE_WINDOW": True, "HIDE_ALL": False, "NOTIFY": False, "PANEL_MINI": True, "POLL_SECONDS": 2.0,
                "_windows": "auto"},
    "Stealth": {"HIDE_WINDOW": True, "HIDE_ALL": True, "NOTIFY": False, "NOTIFY_SOUND": False, "NOTIFY_TOAST": False,
                "_tray": False, "_windows": "hide", "_panel": "hide"},
    "Full":    {"HIDE_WINDOW": False, "HIDE_ALL": False, "NOTIFY": True, "NOTIFY_SOUND": True, "NOTIFY_TOAST": True,
                "PANEL_MINI": False, "POLL_SECONDS": 2.0, "PARK_CORES": 0, "THROTTLE_PARKED": False, "_tray": True,
                "_windows": "show"},
}
SCENE_ICONS = {"AFK": "moon", "Working": "monitor", "Stealth": "eye-off", "Full": "maximize-2"}
SCENE_KEYS = ["HIDE_WINDOW", "HIDE_ALL", "THROTTLE_PARKED", "PARK_CORES", "KEEPALIVE", "KEEPALIVE_MINUTES", "NOTIFY",
              "NOTIFY_SOUND", "NOTIFY_TOAST", "POLL_SECONDS", "IDLE_BACKOFF", "PANEL_MINI"]


class Ctx:
    """Everything a page needs from the console shell. Filled in by console._run_once."""
    pass


def same(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return a == b


def ago(sec):
    if sec is None:
        return "never"
    sec = max(0, int(sec))
    return "just now" if sec < 3 else f"{sec}s ago" if sec < 90 else f"{sec // 60} min ago" if sec < 5400 else f"{sec // 3600} h ago"


def browser_exe(kind):
    rel = {"edge": r"Microsoft\Edge\Application\msedge.exe", "chrome": r"Google\Chrome\Application\chrome.exe"}[kind]
    for base in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""),
                 os.environ.get("LOCALAPPDATA", "")):
        p = os.path.join(base, rel)
        if base and os.path.exists(p):
            return p
    return None


def browser_profiles(kind):
    root = {"edge": r"Microsoft\Edge\User Data", "chrome": r"Google\Chrome\User Data"}[kind]
    path = os.path.join(os.environ.get("LOCALAPPDATA", ""), root, "Local State")
    try:
        cache = json.load(open(path, encoding="utf-8"))["profile"]["info_cache"]
    except (OSError, ValueError, KeyError):
        return []
    return [(d, (info.get("name") or d)) for d, info in cache.items()]


def filter_actions(items, query):
    """items: [(label, keywords, fn, icon)]. Every word of the query must appear in label+keywords."""
    words = query.lower().split()
    return [it for it in items if all(x in (it[0] + " " + it[1]).lower() for x in words)]


LOG_TAGS = (("ok", "OK"), ("next", "#6fa8ff"), ("start", "WARN"), ("bad", "BAD"), ("dim", "FAINT"), ("plain", "DIM"))


def classify(line):
    low = line.lower()
    if "error" in low or "!!" in low or "couldn't" in low or "unknown" in low or "failed" in low:
        return "bad"
    if " active" in low:
        return "ok"
    if " start " in low or "keep-alive" in low:
        return "start"
    if " next " in low:
        return "next"
    if "parked" in low or "roblox window" in low or "setting " in low or "scene:" in low:
        return "dim"
    return "plain"
