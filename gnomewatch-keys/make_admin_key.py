"""Makes YOUR admin key (never expires, only you should have it).  Run this yourself:   python make_admin_key.py

It prints the key once - save it in your password manager - and the hash to give the Worker:
    npx wrangler secret put ADMIN_KEY_HASH      (paste the hash when asked)
The Worker only ever stores the hash, so a leaked Worker config can't reveal the key.
"""
import hashlib
import secrets

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
key = "GW-" + "-".join("".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(5))
print("\nYour admin key (shown once - keep it private):\n\n    " + key)
print("\nHash for the Worker:\n\n    " + hashlib.sha256(key.encode()).hexdigest() + "\n")
