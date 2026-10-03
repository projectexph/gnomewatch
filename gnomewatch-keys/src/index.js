// Gnomewatch key service - Cloudflare Worker.
//
//   POST /discord      Discord slash commands (/getkey, /mykey)       - Discord calls this
//   POST /api/login    { key, device }                               - the app, when you sign in
//   POST /api/check    same as login                                 - the app, every few minutes
//   POST /api/admin    { key: <admin key>, action, ... }             - the app's Admin page
//   POST /api/report   { key, device, body: { eventKey, remainingSeconds } } - a weather report, forwarded once to the weather bot
//
// Secrets (wrangler secret put ...):  DISCORD_PUBLIC_KEY, ADMIN_KEY_HASH, WEATHER_SECRET (the weather bot's ingest password)
// KV namespace binding:               KEYS
// Variables (wrangler.toml):          KEY_HOURS (default 24), MAX_DEVICES (default 1)

import { login, admin, issueKey, myKey, avatarUrl, relayReport } from "./logic.js";

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };
const enc = new TextEncoder();

const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });

function hexToBytes(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
  return out;
}

async function sha256(text) {
  const d = await crypto.subtle.digest("SHA-256", enc.encode(text));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function depsFor(env) {
  return {
    store: {
      get: (k) => env.KEYS.get(k, "json"),
      put: (k, v, ttl) => env.KEYS.put(k, JSON.stringify(v), ttl ? { expirationTtl: Math.max(60, ttl) } : undefined),
      delete: (k) => env.KEYS.delete(k),
      list: async (prefix) => {
        const names = [];
        let cursor;
        do {
          const page = await env.KEYS.list({ prefix, cursor });
          names.push(...page.keys.map((x) => x.name));
          cursor = page.list_complete ? undefined : page.cursor;
        } while (cursor && names.length < 500);
        return names;
      },
    },
    now: () => Date.now(),
    random: (n) => crypto.getRandomValues(new Uint8Array(n)),
    sha256,
    adminHash: (env.ADMIN_KEY_HASH || "").trim().toLowerCase(),
    keyHours: Number(env.KEY_HOURS) || 24,
    maxDevices: Number(env.MAX_DEVICES) || 1,
    forward: env.WEATHER_URL && env.WEATHER_SECRET ? async (payload) => {
      const r = await fetch(env.WEATHER_URL.replace(/\/+$/, "") + "/ingest", {
        method: "POST",
        headers: { "content-type": "application/json", "X-Ingest-Secret": env.WEATHER_SECRET, "User-Agent": "gnomewatch-relay/1.0" },
        body: JSON.stringify({ ...payload, name: "Gnomewatch" }),
      });
      let data = {};
      try { data = await r.json(); } catch (e) { /* not JSON */ }
      return r.ok ? { ok: data.ok !== false, outcome: data.outcome, error: data.error } : { ok: false, error: data.error || "HTTP " + r.status };
    } : null,
    update: { latest: (env.LATEST_VERSION || "").trim(), min: (env.MIN_VERSION || "").trim(), url: (env.DOWNLOAD_URL || "").trim(),
              sha256: (env.DOWNLOAD_SHA256 || "").trim(), notes: (env.RELEASE_NOTES || "").trim() },
  };
}

// Discord signs every request; anything unsigned is rejected.
async function verifyDiscord(request, body, publicKeyHex) {
  const sig = request.headers.get("X-Signature-Ed25519");
  const ts = request.headers.get("X-Signature-Timestamp");
  if (!sig || !ts || !publicKeyHex) return false;
  if (!/^[0-9a-f]{128}$/i.test(sig) || !/^[0-9a-f]{64}$/i.test(publicKeyHex.trim())) return false; // bad input = not valid, never a crash
  const data = enc.encode(ts + body);
  const pub = hexToBytes(publicKeyHex.trim());
  const sigBytes = hexToBytes(sig);
  for (const alg of [{ name: "Ed25519" }, { name: "NODE-ED25519", namedCurve: "NODE-ED25519" }]) {
    try {
      const key = await crypto.subtle.importKey("raw", pub, alg, false, ["verify"]);
      return await crypto.subtle.verify(alg, key, sigBytes, data);
    } catch (e) {
      /* this algorithm name isn't supported here - try the next */
    }
  }
  return false;
}

function leftText(ms) {
  const m = Math.max(0, Math.floor(ms / 60000));
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
}

async function discordCommand(env, interaction) {
  const user = (interaction.member && interaction.member.user) || interaction.user;
  const who = { id: user.id, name: user.global_name || user.username, avatar: user.avatar };
  const deps = depsFor(env);
  const name = interaction.data.name;
  const reply = (content) => ({ type: 4, data: { content, flags: 64 } }); // flags 64 = only they can see it

  if (name === "getkey") {
    const only = String(env.ALLOWED_CHANNEL_ID || "").trim(); // optional: /getkey works in this channel only
    if (only && interaction.channel_id !== only) return reply(`Please use /getkey in <#${only}>.`);
    const { rec, fresh } = await issueKey(deps, who);
    const left = leftText(rec.expires - deps.now());
    return reply(
      (fresh ? "Here is your new Gnomewatch key" : "You already have a key") +
        ` (valid for **${left}**, tied to **${rec.name}**):\n\`\`\`\n${rec.key}\n\`\`\`\n` +
        "Paste it into the Gnomewatch login page. It works on one PC and keeps your own settings."
    );
  }
  if (name === "mykey") {
    const rec = await myKey(deps, who.id);
    if (!rec) return reply("You don't have a key yet. Run **/getkey**.");
    if (rec.revoked) return reply("Your key was revoked. Ask an admin.");
    if (rec.expires <= deps.now()) return reply("Your key has expired. Run **/getkey** for a new one.");
    return reply(`Your key: \`${rec.key}\`\nTime left: **${leftText(rec.expires - deps.now())}**\nLocked to ${rec.devices.length ? "a PC" : "no PC yet"}.`);
  }
  return reply("Unknown command.");
}

// a little protection against guessing keys: at most 30 tries a minute per address
async function rateLimited(env, request) {
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  const k = "rl:" + ip + ":" + Math.floor(Date.now() / 60000);
  const n = Number((await env.KEYS.get(k)) || 0);
  if (n >= 30) return true;
  await env.KEYS.put(k, String(n + 1), { expirationTtl: 120 });
  return false;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET") return new Response("Gnomewatch key service is running.", { headers: { "content-type": "text/plain" } });
    if (request.method !== "POST") return json({ ok: false, error: "POST only" }, 405);

    if (url.pathname === "/discord") {
      const body = await request.text();
      if (!(await verifyDiscord(request, body, env.DISCORD_PUBLIC_KEY))) return new Response("bad signature", { status: 401 });
      const interaction = JSON.parse(body);
      if (interaction.type === 1) return json({ type: 1 }); // Discord's ping when you save the endpoint URL
      if (interaction.type === 2) return json(await discordCommand(env, interaction));
      return json({ type: 4, data: { content: "Not supported.", flags: 64 } });
    }

    if (url.pathname === "/api/login" || url.pathname === "/api/check" || url.pathname === "/api/admin") {
      if (await rateLimited(env, request)) return json({ ok: false, error: "Too many tries. Wait a minute." }, 429);
      let body;
      try {
        body = await request.json();
      } catch (e) {
        return json({ ok: false, error: "Bad request." }, 400);
      }
      const deps = depsFor(env);
      if (url.pathname === "/api/admin") return json(await admin(deps, body));
      return json(await login(deps, body.key, body.device, body.version));
    }
    if (url.pathname === "/api/report") {                                       // { key, device, version, body: { eventKey, remainingSeconds } }
      let body;
      try {
        body = await request.json();
      } catch (e) {
        return json({ ok: false, error: "Bad request." }, 400);
      }
      const deps = depsFor(env);
      const who = await login(deps, body.key, body.device, body.version);        // a valid, unexpired, unrevoked key on its own PC
      if (!who.ok) return json({ ok: false, error: who.error }, 401);
      return json(await relayReport(deps, body.body, who));
    }
    return json({ ok: false, error: "Not found." }, 404);
  },
};
