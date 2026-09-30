/**
 * shamir.js — the browser twin of shamir/__init__.py. Same GF(256), same
 * `ss1.<set>.<k>.<x>.<payload>` share format, so a share made here combines
 * in the CLI and vice versa. Runs entirely in the page: the secret is never
 * sent anywhere. Works as a <script> (window.Shamir) and under node (require).
 */
(function (root) {
  "use strict";
  const VERSION = "ss1", CHECK = 4, MAX = 255;
  const EXP = new Uint8Array(512), LOG = new Uint8Array(256);
  for (let i = 0, v = 1; i < 255; i++) {
    EXP[i] = v; LOG[v] = i;
    v ^= (v << 1) ^ (v & 0x80 ? 0x11b : 0);   // v *= 3
    v &= 0xff;
  }
  for (let i = 255; i < 512; i++) EXP[i] = EXP[i - 255];
  const mul = (a, b) => (a && b ? EXP[LOG[a] + LOG[b]] : 0);
  const div = (a, b) => (a ? EXP[(LOG[a] - LOG[b] + 255) % 255] : 0);

  const cryptoObj = root.crypto || (typeof require === "function" ? require("crypto").webcrypto : null);
  const rand = (n) => cryptoObj.getRandomValues(new Uint8Array(n));
  const sha256 = async (b) => new Uint8Array(await cryptoObj.subtle.digest("SHA-256", b));

  const b64e = (b) => {
    let s = "";
    for (const c of b) s += String.fromCharCode(c);
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  };
  const b64d = (s) => {
    s = s.replace(/-/g, "+").replace(/_/g, "/");
    s += "=".repeat((4 - (s.length % 4)) % 4);
    return Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
  };
  const hex = (b) => Array.from(b, (c) => c.toString(16).padStart(2, "0")).join("");
  const toBytes = (s) => (typeof s === "string" ? new TextEncoder().encode(s) : new Uint8Array(s));
  const eq = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);

  async function split(secret, n = 5, k = 3) {
    const sec = toBytes(secret);
    n = parseInt(n, 10); k = parseInt(k, 10);
    if (!sec.length) throw new Error("nothing to split: secret is empty");
    if (!(2 <= k && k <= n && n <= MAX)) throw new Error(`need 2 <= k <= n <= ${MAX} (got k=${k}, n=${n})`);
    const check = (await sha256(sec)).slice(0, CHECK);
    const data = new Uint8Array(sec.length + CHECK);
    data.set(sec); data.set(check, sec.length);
    const setId = hex(rand(4));
    const ys = Array.from({ length: n }, () => new Uint8Array(data.length));
    for (let i = 0; i < data.length; i++) {
      const coeffs = [data[i], ...rand(k - 1)];
      for (let x = 1; x <= n; x++) {
        let acc = 0;
        for (let c = coeffs.length - 1; c >= 0; c--) acc = mul(acc, x) ^ coeffs[c];
        ys[x - 1][i] = acc;
      }
    }
    return ys.map((y, i) => `${VERSION}.${setId}.${k}.${i + 1}.${b64e(y)}`);
  }

  function parse(share) {
    const parts = String(share || "").trim().split(".");
    if (parts.length !== 5 || parts[0] !== VERSION) throw new Error(`not a ${VERSION} share: ${String(share).slice(0, 24)}`);
    const [, set, ks, xs, payload] = parts;
    let y;
    try { y = b64d(payload); } catch { throw new Error(`corrupt share: ${String(share).slice(0, 24)}`); }
    const k = parseInt(ks, 10), x = parseInt(xs, 10);
    if (!(x >= 1 && x <= MAX && k >= 2 && k <= MAX && y.length > CHECK)) throw new Error(`corrupt share: ${String(share).slice(0, 24)}`);
    return { set, k, x, y };
  }

  async function combine(shares) {
    if (typeof shares === "string") shares = shares.split(/[\s,]+/).filter(Boolean);
    const byX = new Map();
    for (const s of shares) { const p = parse(s); byX.set(p.x, p); }
    if (!byX.size) throw new Error("no shares given");
    const all = [...byX.values()], first = all[0];
    for (const p of all) {
      if (p.set !== first.set) throw new Error("shares come from different splits");
      if (p.k !== first.k || p.y.length !== first.y.length) throw new Error("shares disagree on threshold or length — corrupt share?");
    }
    if (all.length < first.k) throw new Error(`need ${first.k} shares, have ${all.length}`);
    const pts = all.slice(0, first.k), xs = pts.map((p) => p.x);
    const out = new Uint8Array(first.y.length);
    for (let i = 0; i < out.length; i++) {
      let acc = 0;
      pts.forEach((pj, j) => {
        let num = 1, den = 1;
        xs.forEach((xm, m) => { if (m !== j) { num = mul(num, xm); den = mul(den, xm ^ pj.x); } });
        acc ^= mul(pj.y[i], div(num, den));
      });
      out[i] = acc;
    }
    const secret = out.slice(0, -CHECK), check = out.slice(-CHECK);
    if (!eq((await sha256(secret)).slice(0, CHECK), check)) throw new Error("checksum failed — a share is corrupt or tampered with");
    return secret;
  }

  function inspect(share) {
    const p = parse(share);
    return { set: p.set, k: p.k, x: p.x, secret_bytes: p.y.length - CHECK };
  }

  const api = { split, combine, parse, inspect, VERSION };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.Shamir = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
