// Gnomewatch key service: the rules.
// Pure functions - no network and no Cloudflare APIs - so they can be tested anywhere. index.js wires them to KV and Discord.
//
// A key record (stored under "key:<KEY>"):
//   { key, discordId, name, avatar, created, expires, devices: [hash...], lastSeen, revoked }
// and "user:<discordId>" -> the key text of that person's newest key.

const ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"; // no 0/O/1/I so keys are easy to read out

export function normaliseKey(k) {
  return String(k || "").trim().toUpperCase().replace(/\s+/g, "");
}

export function makeKeyText(bytes) {
  let s = "";
  for (let i = 0; i < 12; i++) s += ALPHABET[bytes[i] % ALPHABET.length];
  return `GW-${s.slice(0, 4)}-${s.slice(4, 8)}-${s.slice(8, 12)}`;
}

export function avatarUrl(rec) {
  if (rec.avatar && /^\d+$/.test(String(rec.discordId))) {
    return `https://cdn.discordapp.com/avatars/${rec.discordId}/${rec.avatar}.png?size=128`;
  }
  return null;
}

function publicInfo(rec, now) {
  return {
    ok: true,
    id: rec.discordId,
    name: rec.name,
    avatar: avatarUrl(rec),
    expires: rec.expires,
    admin: false,
    serverTime: now,
  };
}

// deps: { store:{get,put,list}, now:()=>ms, random:(n)=>Uint8Array, sha256:(text)=>Promise<hex>,
//         adminHash, keyHours, maxDevices }

export async function isAdminKey(deps, key) {
  if (!deps.adminHash || !key) return false;
  const h = await deps.sha256(key);
  // compare every character so timing doesn't reveal how much matched
  let diff = h.length ^ deps.adminHash.length;
  for (let i = 0; i < Math.max(h.length, deps.adminHash.length); i++) {
    diff |= (h.charCodeAt(i) || 0) ^ (deps.adminHash.charCodeAt(i) || 0);
  }
  return diff === 0;
}

async function newKeyFor(deps, user, hours) {
  const now = deps.now();
  let key = "";
  for (let tries = 0; tries < 5; tries++) {
    key = makeKeyText(deps.random(12));
    if (!(await deps.store.get("key:" + key))) break;
  }
  const rec = {
    key,
    discordId: String(user.id),
    name: user.name,
    avatar: user.avatar || null,
    created: now,
    expires: now + hours * 3600 * 1000,
    devices: [],
    lastSeen: 0,
    revoked: false,
  };
  await deps.store.put("key:" + key, rec);
  await deps.store.put("user:" + rec.discordId, key);
  return rec;
}

// /getkey: one active key per person. Asking again returns the same key; after it expires a new one is made.
export async function issueKey(deps, user) {
  const now = deps.now();
  const prev = await deps.store.get("user:" + user.id);
  if (prev) {
    const rec = await deps.store.get("key:" + prev);
    if (rec && !rec.revoked && rec.expires > now) {
      // keep their name and picture current
      if (rec.name !== user.name || rec.avatar !== (user.avatar || null)) {
        rec.name = user.name;
        rec.avatar = user.avatar || null;
        await deps.store.put("key:" + rec.key, rec);
      }
      return { rec, fresh: false };
    }
  }
  return { rec: await newKeyFor(deps, user, deps.keyHours), fresh: true };
}

export async function myKey(deps, userId) {
  const prev = await deps.store.get("user:" + userId);
  if (!prev) return null;
  return await deps.store.get("key:" + prev);
}

// ---- app updates. deps.update = { latest, min, url, sha256, notes } (all optional; nothing set = no updates announced)
export function parseVersion(v) {
  return String(v || "0").trim().replace(/^v/i, "").split(".").map((n) => parseInt(n, 10) || 0);
}

export function cmpVersion(a, b) {
  const x = parseVersion(a), y = parseVersion(b);
  for (let i = 0; i < Math.max(x.length, y.length, 3); i++) {
    const d = (x[i] || 0) - (y[i] || 0);
    if (d) return d < 0 ? -1 : 1;
  }
  return 0;
}

// What this app should be told about updates (null = it is up to date). forced = it is older than the minimum allowed version.
export function updateFor(update, version) {
  if (!update || !update.latest) return null;
  if (cmpVersion(version, update.latest) >= 0) return null;
  return {
    latest: String(update.latest).replace(/^v/i, ""),
    url: update.url || "",
    sha256: (update.sha256 || "").toLowerCase(),
    notes: update.notes || "",
    forced: !!update.min && cmpVersion(version, update.min) < 0,
  };
}

// Sign-in with update rules: copies older than the minimum can't sign in (the admin key always can); others are just told.
export async function login(deps, rawKey, device, version) {
  const upd = updateFor(deps.update, version);
  if (upd && upd.forced && !(await isAdminKey(deps, normaliseKey(rawKey)))) {
    return {
      ok: false,
      error: `Please update Gnomewatch (this is ${version ? "v" + String(version).replace(/^v/i, "") : "an old version"}, v${String(deps.update.min).replace(/^v/i, "")} or newer is needed).`,
      update: upd,
    };
  }
  const res = await loginCore(deps, rawKey, device);
  if (res.ok && upd) res.update = upd;
  return res;
}

// The app calls this when you sign in and every few minutes after.
async function loginCore(deps, rawKey, device) {
  const key = normaliseKey(rawKey);
  const now = deps.now();
  if (!key) return { ok: false, error: "Enter your key." };
  if (await isAdminKey(deps, key)) {
    return { ok: true, id: "admin", name: "Admin", avatar: null, expires: null, admin: true, serverTime: now };
  }
  const rec = await deps.store.get("key:" + key);
  if (!rec) return { ok: false, error: "That key isn't valid." };
  if (rec.revoked) return { ok: false, error: "This key was revoked." };
  if (rec.expires && now > rec.expires) return { ok: false, error: "This key has expired. Run /getkey for a new one." };
  const dev = String(device || "");
  if (!dev) return { ok: false, error: "This copy of the app didn't send a device ID." };
  let changed = false;
  if (!rec.devices.includes(dev)) {
    if (rec.devices.length >= deps.maxDevices) {
      return { ok: false, error: "This key is already in use on another PC." };
    }
    rec.devices.push(dev);
    changed = true;
  }
  if (now - (rec.lastSeen || 0) > 30 * 60 * 1000) {      // not on every heartbeat: KV writes are limited
    rec.lastSeen = now;
    changed = true;
  }
  if (changed) await deps.store.put("key:" + key, rec);
  return publicInfo(rec, now);
}

// Admin actions. body = { key: <admin key>, action, ... }
export async function admin(deps, body) {
  if (!(await isAdminKey(deps, normaliseKey(body.key)))) return { ok: false, error: "Not an admin key." };
  const now = deps.now();
  const act = body.action;
  if (act === "list") {
    const names = await deps.store.list("key:");
    const out = [];
    for (const n of names.slice(0, 500)) {
      const rec = await deps.store.get(n);
      if (rec) out.push(rec);
    }
    out.sort((a, b) => b.created - a.created);
    return { ok: true, keys: out, serverTime: now };
  }
  const target = normaliseKey(body.target);
  if (act === "create") {
    const hours = Math.max(1, Math.min(24 * 365, Number(body.hours) || 24));
    const name = String(body.name || "").trim().slice(0, 40);
    if (!name) return { ok: false, error: "Give it a name." };
    const rec = await newKeyFor(deps, { id: "manual-" + name.toLowerCase().replace(/[^a-z0-9]+/g, "-"), name }, hours);
    return { ok: true, key: rec };
  }
  const rec = await deps.store.get("key:" + target);
  if (!rec) return { ok: false, error: "No such key." };
  if (act === "revoke") rec.revoked = true;
  else if (act === "unrevoke") rec.revoked = false;
  else if (act === "reset") rec.devices = [];
  else if (act === "extend") {
    const hours = Math.max(1, Math.min(24 * 365, Number(body.hours) || 24));
    rec.expires = Math.max(now, rec.expires || now) + hours * 3600 * 1000;
  } else return { ok: false, error: "Unknown action." };
  await deps.store.put("key:" + target, rec);
  return { ok: true, key: rec };
}


// ---- weather reports: any signed-in copy of the app can report that an event started; the first one is forwarded to the weather bot,
// the rest are ignored. The bot's ingest secret lives only on this server (deps.forward uses it), never in the app.
export const REPORT_DEDUP_MS = 150 * 1000;           // the same event reported again inside this window is a duplicate

export function cleanReport(p) {
  if (!p || typeof p !== "object") return null;
  const name = String(p.eventKey || "").trim().toLowerCase();
  if (!/^[a-z0-9][a-z0-9 '\-]{0,39}$/.test(name)) return null;
  const out = { eventKey: name };
  if (p.remainingSeconds !== undefined && p.remainingSeconds !== null) {
    const n = Number(p.remainingSeconds);
    if (!Number.isFinite(n) || n < 0 || n > 7200) return null;
    out.remainingSeconds = Math.round(n);
  }
  return out;
}

// deps.forward(payload) -> { ok, ... } the bot's answer;  deps.store needs get / put(k, v, ttlSeconds) / delete
export async function relayReport(deps, body, who) {
  const payload = cleanReport(body);
  if (!payload) return { ok: false, error: "That isn't a valid weather report." };
  if (!deps.forward) return { ok: true, skipped: "The weather bot isn't connected on the server yet." };
  const now = deps.now();
  const claim = "rep:" + payload.eventKey;
  const have = await deps.store.get(claim);
  if (have && now - have.t < REPORT_DEDUP_MS) return { ok: true, duplicate: true };
  await deps.store.put(claim, { t: now, by: who && who.name ? String(who.name).slice(0, 40) : "" }, 300);       // claim it first, so a second copy doesn't also send
  let res;
  try {
    res = await deps.forward(payload);
  } catch (e) {
    res = { ok: false, error: "The weather bot couldn't be reached." };
  }
  if (!res || !res.ok) {
    if (deps.store.delete) await deps.store.delete(claim);                    // it didn't go through: let another copy (or a retry) try
    return { ok: false, error: (res && res.error) || "The weather bot refused it." };
  }
  return { ok: true, forwarded: true, outcome: res.outcome || null };
}
