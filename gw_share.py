"""Shareable macro codes: turn an auto-place recording into one line of text a friend can paste, and back.

    code = gw_share.encode("blizzard", steps, end_steps, name="Kyle's blizzard")      ->  "GW1-eJxV..."
    info, err = gw_share.decode(code, clean_steps)    # info = {"event", "name", "steps", "end_steps", "presses", "seconds"} or err text

A code only ever carries key presses and clicks (as fractions of the window), never account names, keys, links or settings.
Decoding is strict: a size limit, a checksum, and every step goes back through the app's own safety filter (clean_steps), so a
pasted code can't smuggle in anything the recorder itself would refuse. Pure text in, text out - nothing here touches the PC.
"""
import base64
import json
import re
import zlib

PREFIX = "GW2-"                  # GW2: no underscores (chat apps turn _..._ into italics and eat them). GW1 codes still read fine.
LEGACY = "GW1-"
MAX_CODE = 40_000                 # characters pasted
MAX_JSON = 120_000                # bytes after unpacking (a zip bomb can't get past this)
_EVENT = re.compile(r"[A-Za-z0-9 '\-]{1,40}")
_FIND = re.compile(r"GW[12]-[A-Za-z0-9_+\-]{8,}\.[0-9a-fA-F]{8}")


def _b64e(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=").replace("_", "+")        # '+' survives chat formatting, '_' does not


def _b64d(text):
    text = text.replace("+", "_")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def encode(event, steps, end_steps=None, name=None):
    """One line of text for a recording (start steps, and end steps if there are any)."""
    body = {"v": 1, "e": str(event)[:40], "n": str(name or "")[:60], "s": steps or [], "x": end_steps or []}
    raw = json.dumps(body, separators=(",", ":")).encode()
    crc = zlib.crc32(raw) & 0xFFFFFFFF
    return PREFIX + _b64e(zlib.compress(raw, 9)) + "." + format(crc, "08x")


def decode(code, clean_steps):
    """Returns (info dict, None) or (None, a short reason in plain words)."""
    text = "".join(str(code or "").split())            # pasted codes get line-wrapped; whitespace never matters
    for ch in "​‌‍⁠﻿":                 # invisible characters chat apps sometimes add
        text = text.replace(ch, "")
    if not text:
        return None, "Paste a macro code first."
    if len(text) > MAX_CODE:
        return None, "That's too long to be a macro code."
    found = _FIND.search(text)                         # the code may sit inside a message: "here you go `GW1-...`"
    if found:
        text = found.group(0)
    elif PREFIX in text or LEGACY in text:
        return None, "That code is cut off or damaged. Copy the whole thing, from GW2- to the end."
    if not text.startswith((PREFIX, LEGACY)):
        return None, "That doesn't look like a Gnomewatch macro code (it should start with GW2-)."
    try:
        packed, crc = text[len(PREFIX):].rsplit(".", 1)          # both prefixes are 4 characters long
        comp = _b64d(packed)
        d = zlib.decompressobj()
        raw = d.decompress(comp, MAX_JSON)
        if d.unconsumed_tail:
            return None, "That code unpacks to something far too big."
        if (zlib.crc32(raw) & 0xFFFFFFFF) != int(crc, 16):
            return None, "That code is damaged (it didn't copy completely?). Ask for it again."
        body = json.loads(raw.decode())
    except (ValueError, zlib.error, UnicodeDecodeError):
        return None, "That code can't be read. Make sure you copied all of it."
    if not isinstance(body, dict) or body.get("v") != 1:
        return None, "This code was made by a newer Gnomewatch. Update, then try again."
    event = str(body.get("e", "")).strip()
    if not _EVENT.fullmatch(event):
        return None, "The code has no valid event name."
    steps, end = clean_steps(body.get("s")), clean_steps(body.get("x"))
    if not steps and not end:
        return None, "The code has no usable steps in it."
    allsteps = steps + end
    return {"event": event.lower(), "name": str(body.get("n", ""))[:60], "steps": steps, "end_steps": end,
            "presses": sum(1 for s in allsteps if s.get("d")), "seconds": max((s["t"] for s in allsteps), default=0.0)}, None


# ---------------------------------------------------------------------------------------------- the clipboard
def copy_text(text, tk_root=None):
    """Put text on the real Windows clipboard, so it is still there whatever the console window does afterwards. True if it worked."""
    import time
    try:
        import win32clipboard
        import win32con
        for _ in range(8):                                 # another program may be holding the clipboard for a moment
            try:
                win32clipboard.OpenClipboard()
                break
            except Exception:
                time.sleep(0.05)
        else:
            raise OSError("clipboard busy")
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception:
        if tk_root is not None:
            try:
                tk_root.clipboard_clear()
                tk_root.clipboard_append(text)
                tk_root.update()
                return True
            except Exception:
                pass
        return False


def paste_text(tk_root=None):
    """The text on the clipboard, or ''."""
    try:
        import win32clipboard
        import win32con
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return str(win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT))
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        pass
    if tk_root is not None:
        try:
            return tk_root.clipboard_get()
        except Exception:
            pass
    return ""
