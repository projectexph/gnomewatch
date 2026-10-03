"""Macro share codes: round trip, damage, abuse.   python test_share.py"""
import base64
import json
import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gw_share as G

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def clean(steps):                      # a stand-in for the app's filter: keeps well-formed steps, drops the rest
    out = []
    for s in steps if isinstance(steps, list) else []:
        try:
            if "k" in s and 0x30 <= int(s["k"]) <= 0x5A:
                out.append({"t": round(float(s["t"]), 3), "k": int(s["k"]), "d": 1 if int(s["d"]) else 0})
            elif s.get("m") in ("L", "R") and 0 <= float(s["x"]) <= 1 and 0 <= float(s["y"]) <= 1:
                out.append({"t": round(float(s["t"]), 3), "m": s["m"], "d": 1 if int(s["d"]) else 0, "x": float(s["x"]), "y": float(s["y"])})
        except (KeyError, TypeError, ValueError):
            pass
    return out


steps = [{"t": 0.4, "k": 0x31, "d": 1}, {"t": 0.5, "k": 0x31, "d": 0}, {"t": 1.2, "m": "L", "d": 1, "x": 0.5, "y": 0.62}, {"t": 1.3, "m": "L", "d": 0, "x": 0.5, "y": 0.62}]
end = [{"t": 0.2, "k": 0x32, "d": 1}, {"t": 0.3, "k": 0x32, "d": 0}]
code = G.encode("blizzard", steps, end, name="Kyle's blizzard")
check("a code is one line and starts with GW2-", code.startswith("GW2-") and "\n" not in code and len(code) < 600, len(code))
check("a code never contains an underscore (chat apps eat them)", "_" not in code and "*" not in code and "~" not in code)
info, err = G.decode(code, clean)
check("round trip keeps the steps", err is None and info["steps"] == steps and info["end_steps"] == end and info["event"] == "blizzard", (info, err))
check("summary numbers", info["presses"] == 3 and abs(info["seconds"] - 1.3) < 1e-9, info)
check("the name comes along", info["name"] == "Kyle's blizzard")
wrapped = "\n".join(code[i:i + 40] for i in range(0, len(code), 40))
check("a line-wrapped / spaced paste still works", G.decode("  " + wrapped + "  ", clean)[1] is None)
i2, e2 = G.decode(code[:-3] + ("000" if not code.endswith("000") else "111"), clean)
check("a damaged code is refused", i2 is None and "damaged" in e2, e2)
i2, e2 = G.decode(code[:30], clean)
check("a cut-off code is refused", i2 is None, e2)
for wrap_in, label in (("here you go `%s` enjoy", "inside a sentence with backticks"), ("> %s", "quoted"), ("**%s**", "bold"), ("<%s>", "angle brackets"),
                       ("%s.", "followed by a full stop"), ("​%s​", "with invisible characters")):
    r_, e_ = G.decode(wrap_in % code if "%s" in wrap_in else wrap_in, clean)
    check(f"a code pasted {label} still works", e_ is None and r_["event"] == "blizzard", e_)
check("a code with its tail chopped off says so", "cut off" in (G.decode(code[:-12] + " thanks", clean)[1] or "") or G.decode(code[:-12], clean)[0] is None)
legacy_raw = json.dumps({"v": 1, "e": "toxic", "n": "old", "s": steps, "x": []}, separators=(",", ":")).encode()
legacy = "GW1-" + base64.urlsafe_b64encode(zlib.compress(legacy_raw)).decode().rstrip("=") + "." + format(zlib.crc32(legacy_raw) & 0xFFFFFFFF, "08x")
i3, e3 = G.decode(legacy, clean)
check("a code from version 1.1.0 (GW1-, with underscores) still imports", e3 is None and i3["event"] == "toxic" and i3["steps"] == steps, e3)
many = G.encode("aurora", [{"t": 0.1 * i, "m": "L", "d": i % 2, "x": 0.25 + i / 400, "y": 0.5} for i in range(60)], [], name="x" * 50)
check("a long macro's code is still short enough for one chat message", len(many) < 1900 and "_" not in many, len(many))
check("empty is refused", G.decode("", clean)[0] is None)
check("random text is refused", "GW2-" in G.decode("hello there", clean)[1])


def forge(body):
    raw = json.dumps(body).encode()
    return G.PREFIX + G._b64e(zlib.compress(raw)) + "." + format(zlib.crc32(raw) & 0xFFFFFFFF, "08x")


i2, e2 = G.decode(forge({"v": 1, "e": "x", "s": [{"t": 1, "k": 0x1B, "d": 1}, {"t": 2, "m": "L", "d": 1, "x": 9, "y": 9}], "x": []}), clean)
check("steps the recorder would refuse are dropped, and an empty result is refused", i2 is None and "no usable" in e2, e2)
i2, e2 = G.decode(forge({"v": 1, "e": "x", "s": [{"t": 1, "k": 0x41, "d": 1}, {"t": 2, "k": 0x1B, "d": 1}], "x": []}), clean)
check("only the safe steps survive", i2 and len(i2["steps"]) == 1, (i2, e2))
i2, e2 = G.decode(forge({"v": 1, "e": "../../etc", "s": [{"t": 1, "k": 0x41, "d": 1}], "x": []}), clean)
check("a path-like event name is refused", i2 is None and "event" in e2, e2)
i2, e2 = G.decode(forge({"v": 2, "e": "x", "s": [], "x": []}), clean)
check("a newer format says to update", i2 is None and "newer" in e2, e2)
bomb = G.PREFIX + G._b64e(zlib.compress(b"0" * 5_000_000)) + ".00000000"
i2, e2 = G.decode(bomb, clean)
check("a zip bomb is refused cheaply", i2 is None and ("big" in e2 or "damaged" in e2 or "read" in e2), e2)
check("an oversized paste is refused", G.decode("GW1-" + "A" * 50_000, clean)[0] is None)

print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
