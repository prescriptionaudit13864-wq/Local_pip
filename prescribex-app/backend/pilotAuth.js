// Doctor sign-in for the pilot (PILOT_MODE=1). The app is reached from the internet through one link, so every
// request needs a signed session cookie; doctors get it by typing their name and personal access code once.
//
//   pilot/doctors.json  { "<doctor id>": { "name": "Dr ...", "salt": "...", "hash": sha256(salt + code) } }
//   pilot/secret         random key that signs the session cookies
// Both are made by pilot/add_doctor.ps1 and are never committed. Codes are stored only as hashes.
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const PILOT_DIR = path.resolve(process.cwd(), '..', 'pilot');
const COOKIE = 'px_session';
const SESSION_HOURS = 12;
const MAX_FAILS = 5;            // per address in 15 minutes
const fails = new Map();

const readJson = (file, fallback) => { try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return fallback; } };
const doctors = () => readJson(path.join(PILOT_DIR, 'doctors.json'), {});   // re-read: add a doctor without restarting
function secret() {
  const file = path.join(PILOT_DIR, 'secret');
  if (!fs.existsSync(file)) { fs.mkdirSync(PILOT_DIR, { recursive: true }); fs.writeFileSync(file, crypto.randomBytes(32).toString('hex')); }
  return fs.readFileSync(file, 'utf8').trim();
}
const sign = (value) => crypto.createHmac('sha256', secret()).update(value).digest('base64url');

function sessionDoctor(req) {
  const raw = (req.headers.cookie || '').split(';').map(s => s.trim()).find(s => s.startsWith(`${COOKIE}=`));
  if (!raw) return null;
  const [id, expires, mac] = decodeURIComponent(raw.slice(COOKIE.length + 1)).split('|');
  if (!id || !mac || Number(expires) < Date.now()) return null;
  const good = sign(`${id}|${expires}`);
  if (mac.length !== good.length || !crypto.timingSafeEqual(Buffer.from(mac), Buffer.from(good))) return null;
  const doc = doctors()[id];
  return doc ? { id, name: doc.name } : null;      // removing a doctor from the list ends their session
}

const page = (msg = '') => `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>PrescribeX pilot sign-in</title>
<style>body{font-family:system-ui,sans-serif;background:#f4f6f8;margin:0;display:grid;place-items:center;min-height:100vh}
form{background:#fff;padding:32px;border-radius:12px;box-shadow:0 2px 12px #0002;width:min(340px,90vw)}
h1{font-size:20px;margin:0 0 4px}p{color:#555;font-size:14px;margin:0 0 20px}label{display:block;font-size:14px;margin:12px 0 4px}
input{width:100%;box-sizing:border-box;padding:10px;font-size:16px;border:1px solid #bbb;border-radius:8px}
button{margin-top:20px;width:100%;padding:12px;font-size:16px;border:0;border-radius:8px;background:#0b6efd;color:#fff;cursor:pointer}
.err{color:#b00020;font-size:14px;margin-top:12px}</style></head><body>
<form method="post" action="/pilot/login"><h1>PrescribeX</h1><p>Pilot sign-in. Use the access code you were given.</p>
<label for="code">Access code</label><input id="code" name="code" autocomplete="one-time-code" autofocus required>
<button type="submit">Sign in</button>${msg ? `<div class="err">${msg}</div>` : ''}</form></body></html>`;

function clientKey(req) {
  return String(req.headers['x-forwarded-for'] || req.socket.remoteAddress || '').split(',')[0].trim();
}

function login(req, res) {
  let body = '';
  req.on('data', c => { body += c; if (body.length > 2000) req.destroy(); });
  req.on('end', () => {
    const key = clientKey(req);
    const now = Date.now();
    const recent = (fails.get(key) || []).filter(t => now - t < 15 * 60 * 1000);
    const html = (status, msg) => { res.statusCode = status; res.setHeader('Content-Type', 'text/html; charset=utf-8'); res.end(page(msg)); };
    if (recent.length >= MAX_FAILS) return html(429, 'Too many tries. Wait 15 minutes, or ask for your code again.');
    const code = (new URLSearchParams(body).get('code') || '').trim().toUpperCase().replace(/[\s-]/g, '');
    const hit = Object.entries(doctors()).find(([, d]) => {
      const h = crypto.createHash('sha256').update(d.salt + code).digest('hex');
      return h.length === d.hash.length && crypto.timingSafeEqual(Buffer.from(h), Buffer.from(d.hash));
    });
    if (!hit) { fails.set(key, [...recent, now]); return html(401, 'That code is not right. Check it and try again.'); }
    fails.delete(key);
    const expires = now + SESSION_HOURS * 3600 * 1000;
    const value = `${hit[0]}|${expires}`;
    const secure = req.headers['x-forwarded-proto'] === 'https' || /\.ts\.net$/i.test(String(req.headers.host || '').split(':')[0]);
    res.statusCode = 303;
    res.setHeader('Set-Cookie', `${COOKIE}=${encodeURIComponent(`${value}|${sign(value)}`)}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${SESSION_HOURS * 3600}${secure ? '; Secure' : ''}`);
    res.setHeader('Location', '/');
    console.log(`[pilot] sign-in: ${hit[1].name} (${new Date().toISOString()})`);
    res.end();
  });
}

function handler(req, res, next) {
  const url = (req.url || '').split('?')[0];
  if (url === '/pilot/login') {
    if (req.method === 'POST') return login(req, res);
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    return res.end(page());
  }
  if (url === '/pilot/logout') {
    res.statusCode = 303;
    res.setHeader('Set-Cookie', `${COOKIE}=; Path=/; Max-Age=0`);
    res.setHeader('Location', '/pilot/login');
    return res.end();
  }
  const doctor = sessionDoctor(req);
  if (!doctor) {
    if (req.method === 'GET' && !url.startsWith('/api') && !url.startsWith('/audit-api')) {
      res.statusCode = 303; res.setHeader('Location', '/pilot/login'); return res.end();
    }
    res.statusCode = 401; res.setHeader('Content-Type', 'application/json');
    return res.end(JSON.stringify({ success: false, error: 'Signed out: reload the page and sign in again.' }));
  }
  req.pilotDoctor = doctor;
  if (url === '/pilot/me') { res.setHeader('Content-Type', 'application/json'); return res.end(JSON.stringify(doctor)); }
  next();
}

export default function pilotAuth() {
  const on = process.env.PILOT_MODE === '1';
  // Registered before the other plugins' middleware, so nothing (pages, saving, model calls) is reachable signed out.
  return {
    name: 'pilot-auth',
    enforce: 'pre',
    configureServer(server) { if (on) server.middlewares.use(handler); },
    configurePreviewServer(server) { if (on) server.middlewares.use(handler); },
  };
}
