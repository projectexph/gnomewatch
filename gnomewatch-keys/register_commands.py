"""Registers the /getkey and /mykey slash commands with Discord (run once, and again if you rename them).

    set DISCORD_APP_ID=...        (Developer Portal > your app > General Information > Application ID)
    set DISCORD_BOT_TOKEN=...     (Developer Portal > Bot > Reset Token)
    python register_commands.py

The token is read from the environment and is never written anywhere.
"""
import json
import os
import sys
import urllib.error
import urllib.request

COMMANDS = [
    {"name": "getkey", "description": "Get your Gnomewatch key (lasts 24 hours)", "type": 1},
    {"name": "mykey", "description": "Show your current Gnomewatch key and how long is left", "type": 1},
]

app_id = os.environ.get("DISCORD_APP_ID", "").strip()
token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
if not app_id or not token:
    sys.exit("Set DISCORD_APP_ID and DISCORD_BOT_TOKEN first (see the top of this file).")

req = urllib.request.Request(
    f"https://discord.com/api/v10/applications/{app_id}/commands", data=json.dumps(COMMANDS).encode(), method="PUT",
    headers={"Authorization": f"Bot {token}", "Content-Type": "application/json", "User-Agent": "gnomewatch-keys (register, 1.0)"})
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        print("Registered:", ", ".join("/" + c["name"] for c in json.loads(r.read())))
except urllib.error.HTTPError as e:
    sys.exit(f"Discord said {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
