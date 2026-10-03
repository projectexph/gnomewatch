# Gnomewatch key service — setup (about 15 minutes, free)

Players type `/getkey` in your Discord server, get a key with their name on it, and paste it into Gnomewatch.
The service is a Cloudflare Worker + KV store. Nothing here needs a server of your own.

## 1. Discord application
1. https://discord.com/developers/applications → **New Application** → name it "Gnomewatch".
2. **General Information**: copy the **Application ID** and the **Public Key**.
3. **Bot** tab → **Reset Token** → copy it (only needed for step 5; don't share it).
4. **OAuth2 → URL Generator**: scopes `applications.commands` + `bot` → open the URL and add the bot to your server.

## 2. Cloudflare Worker
```
cd gnomewatch-keys
npx wrangler login
npx wrangler kv namespace create KEYS      # paste the id it prints into wrangler.toml
npx wrangler secret put DISCORD_PUBLIC_KEY # paste the Public Key from step 1
python make_admin_key.py                   # YOU run this; save the key, copy the hash
npx wrangler secret put ADMIN_KEY_HASH     # paste the hash
npx wrangler deploy                        # prints https://gnomewatch-keys.<you>.workers.dev
```

## 3. Connect Discord to the Worker
In the Developer Portal → **General Information → Interactions Endpoint URL** set
`https://gnomewatch-keys.<you>.workers.dev/discord` and save (Discord pings it; it must say saved).

Then register the commands (PowerShell):
```
$env:DISCORD_APP_ID="..."; $env:DISCORD_BOT_TOKEN="..."; python register_commands.py
```

## 4. Point the app at it
Edit `gw_config.json` next to Gnomewatch (or put it in the installer build):
```
{ "api": "https://gnomewatch-keys.<you>.workers.dev", "invite": "https://discord.gg/yourinvite" }
```
Start Gnomewatch and sign in with your admin key — you get the **Admin** page (list, revoke, reset PC lock, add time, create keys).

## Rules in this version
* One active key per person; `/getkey` again returns the same key until it expires. 24 hours (`KEY_HOURS` in `wrangler.toml`).
* One PC per key (`MAX_DEVICES`); Admin → Reset PC lock lets them move.
* The admin key never expires and is only stored as a hash.
* Changing `KEY_HOURS` later only affects new keys.

## Local testing without Cloudflare
`python dev_server.py --admin-key TESTKEY` runs the same rules locally (`/dev/getkey?id=1&name=Me`), and
`python test_logic.py`, `test_client.py`, `test_login_ui.py`, `test_app.py` exercise everything.
