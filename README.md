# Gnomewatch

A Windows helper for **Roll A Gnome** (Roblox). It watches the weather sign for you, keeps several Roblox accounts running without eating your PC,
and can replay moves you recorded when an event starts.

- **Weather watching:** reads the in-game weather sign and shows the current event with a live countdown (and can announce event starts to a Discord bot).
- **Accounts and instances:** launches accounts one after another from their own browser profiles, tiles the windows, hides or parks them, trims memory, and rejoins after a crash.
- **Auto-place:** record clicks and key presses once for an event, replay them when it starts, preview them with a dry run, and share them as a short code.
- **Extras:** an always-on-top countdown overlay, Discord Rich Presence, a setup guide, a built-in health check, and in-app updates.

## Install (most people)

Download **Gnomewatch-Setup.exe** from the [latest release](../../releases/latest), run it, and follow the setup guide that opens.
You need a key from the Gnomewatch Discord to sign in (type `/getkey`).

### "Windows protected your PC" / my antivirus flags it

Gnomewatch is not code-signed yet, and it is a brand-new program built with PyInstaller. Windows Defender and some antivirus tools flag exactly that
combination with a generic guess (for example `Trojan:Win32/Wacatac.C!ml`). It is a false positive. You do not have to take our word for it:

- The whole source is in this repository, and `packaging/build.py` builds the installer from it. You can build it yourself.
- Each release lists the installer's SHA-256 so you can check what you downloaded.
- It never asks for, reads or stores a Roblox password or cookie.

If you'd rather not run the installer, build from source (below).

## What it does and doesn't do

- It **reads pixels** of your own Roblox windows (Windows screen capture) and uses Windows' built-in text recognition. Nothing is injected into Roblox.
- It opens Roblox the way you would: your own Edge or Chrome profile opens the game link. Only a label and a browser profile name are stored per account.
- Auto-place and the anti-idle click send ordinary mouse and keyboard input to the Roblox window you choose. Automation may be against a game's rules:
  use it at your own risk. This project is not affiliated with Roblox or the creators of Roll A Gnome.
- Sign-in keys come from a small Cloudflare Worker (`gnomewatch-keys/`). Your settings, accounts and macros stay on your PC, in `profiles/<your Discord id>/`.

## Build from source

Windows 10/11 and Python 3.12.

```powershell
pip install -r requirements.txt
copy gw_config.example.json gw_config.json     # your own key server address (see gnomewatch-keys/README.md), and your Discord invite
python sentinel.py                             # run it from source
python packaging/build.py                      # build the installer into packaging/dist
```

The game's own banner artwork is not part of this repository, so source builds show a plain background on the little dashboard page.

Tests live in `gnomewatch-keys/` (`python test_logic.py`, `python test_share.py`, and so on). Many of them start a sandbox copy of the real app.

## Your own key server

Sign-in needs the Worker in `gnomewatch-keys/`. Setup, step by step (free Cloudflare plan): `gnomewatch-keys/README.md`.
Copy `gnomewatch-keys/wrangler.example.toml` to `wrangler.toml` and fill in your own values. Secrets (admin key hash, Discord keys, the weather bot password)
are set with `wrangler secret put` and are never stored in this repository.

## License

MIT, see [LICENSE](LICENSE). Third-party fonts and icons are listed in [THIRD_PARTY.md](THIRD_PARTY.md).
