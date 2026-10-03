"""Reads what Roblox writes in its own log files (the same trick Bloxstrap uses for Discord presence): which game and
server a client is in, and the exact reason when it disconnects. Nothing is injected; the files are only read.

Roblox keeps one log per running client in %LOCALAPPDATA%\\Roblox\\logs. psutil tells us which process has which log open.
"""
import os
import re

LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Roblox", "logs")

RE_JOIN = re.compile(r"Joining game '([0-9a-fA-F-]{8,})' place (\d+) at ([\d.]+)")
RE_SERVER = re.compile(r"serverId: ([\d.]+)\|(\d+)")
RE_REASON = re.compile(r"disconnect with reason: (-?\d+)", re.I)
RE_REASON2 = re.compile(r"Disconnected from server for reason: (?:Player: )?(-?\d+)(?: \((\w+)\))?", re.I)
RE_ERRCODE = re.compile(r"error code:? ?(\d{3})", re.I)
RE_CONNLOST = re.compile(r"connection lost", re.I)

ERROR_NAMES = {
    266: "timed out", 267: "kicked by the game", 268: "kicked: unexpected client behaviour", 269: "kicked",
    271: "kicked: another session", 272: "kicked: another session", 273: "joined from another device",
    274: "the server shut down", 275: "the server shut down", 277: "lost connection", 278: "idle for 20 minutes",
    279: "couldn't connect to the game", 280: "failed to join", 282: "couldn't join: version mismatch",
    285: "you left the game", 286: "server full", 288: "couldn't join this server", 289: "couldn't join: not allowed",
    291: "disconnected: server restarting", 292: "disconnected", 522: "couldn't join: server closed", 524: "not allowed to join",
    529: "Roblox is having trouble", 533: "server unavailable", 610: "couldn't join: missing permission",
}


def error_name(code):
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "disconnected"
    return ERROR_NAMES.get(code, f"error {code}")


def parse_line(line):
    """One log line -> an event dict, or None. kinds: join, server, disconnect, left, lost."""
    if "disconnect" in line.lower() and "SignalR" not in line:
        m = RE_REASON2.search(line)
        if m:
            return {"kind": "disconnect", "code": int(m.group(1)), "name": m.group(2) or "", "clean": (m.group(2) or "") == "DisconnectClientInitiated"}
        m = RE_REASON.search(line)
        if m:
            return {"kind": "disconnect", "code": int(m.group(1)), "name": "", "clean": int(m.group(1)) == 285}
        if "replication data" in line.lower():
            return {"kind": "left"}
    m = RE_JOIN.search(line)
    if m:
        return {"kind": "join", "job": m.group(1), "place": int(m.group(2)), "ip": m.group(3)}
    m = RE_SERVER.search(line)
    if m:
        return {"kind": "server", "ip": m.group(1), "port": int(m.group(2))}
    if RE_CONNLOST.search(line):
        return {"kind": "lost"}
    if "leaveUGCGameInternal" in line:
        return {"kind": "left"}
    m = RE_ERRCODE.search(line)
    if m and ("disconnect" in line.lower() or "kicked" in line.lower() or "[FLog::Output]" in line):
        return {"kind": "disconnect", "code": int(m.group(1)), "name": "", "clean": False}
    return None


class Tail:
    """Follows one log file. The first read looks at the last part only, to learn the current game without replaying history."""
    def __init__(self, path):
        self.path, self.pos, self.primed = path, 0, False

    def lines(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        first = not self.primed
        if first:
            self.pos = max(0, size - 400_000)
        if size < self.pos:                                        # the file was replaced
            self.pos = 0
        if size == self.pos:
            self.primed = True
            return []
        try:
            with open(self.path, "rb") as f:
                f.seek(self.pos)
                data = f.read(size - self.pos)
        except OSError:
            return []
        self.pos = size
        self.primed = True
        text = data.decode("utf-8", errors="ignore").splitlines()
        if first and self.pos > 400_000 and text:
            text = text[1:]                                        # a cut-off first line
        return text


def newest_logs(n=6):
    try:
        names = [os.path.join(LOG_DIR, f) for f in os.listdir(LOG_DIR) if f.endswith("_last.log") and "CrashHandler" not in f]
    except OSError:
        return []
    return sorted(names, key=lambda p: os.path.getmtime(p), reverse=True)[:n]


def client_logs():
    """{pid: log path} for the running Roblox clients."""
    out = {}
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name"]):
            if (p.info["name"] or "").lower() == "robloxplayerbeta.exe":
                try:
                    for f in p.open_files():
                        if f.path.lower().endswith(".log") and "crashhandler" not in f.path.lower() and "Roblox" in f.path:
                            out[p.pid] = f.path
                            break
                except Exception:
                    continue
    except Exception:
        pass
    return out
