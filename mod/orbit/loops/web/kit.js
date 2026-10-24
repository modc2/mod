// Shared client for the build background-agent apps. Each app is one of the
// agents in build ▸ ACCOUNT ▸ AGENT ▸ AGENTS, given its own page. The page
// talks to build's API directly with the build session this browser already
// holds (same origin on the gateway), so build gates every write exactly as
// it does in the console — this page never holds a credential of its own.
(function () {
  const NAME = location.pathname.split("/").filter(Boolean)[0] || "";
  const viaGateway = !location.port || location.port === "80" || location.port === "443";
  const API = viaGateway ? "/api/build" : `${location.protocol}//${location.hostname}:8890`;
  const BUILD_APP = viaGateway ? "/build/" : `${location.protocol}//${location.hostname}:8893/build/`;

  const ls = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
  const token = ls("build_jobs_token");
  const address = ls("build_jobs_address") || "";
  const walletType = ls("build_jobs_wallet_type") || "";

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function ago(ts) {
    if (typeof ts === "string") ts = Date.parse(ts) / 1000;
    if (!ts || isNaN(ts)) return "—";
    const s = Math.floor(Date.now() / 1000) - ts;
    const a = Math.abs(s), fut = s < 0;
    const v = a < 60 ? `${a}s` : a < 3600 ? `${Math.floor(a / 60)}m` : a < 86400 ? `${Math.floor(a / 3600)}h` : `${Math.floor(a / 86400)}d`;
    return fut ? `in ${v}` : `${v} ago`;
  }
  const dur = (s) => (s < 60 ? `${s}s` : s < 3600 ? `${Math.round(s / 60)}m` : s < 86400 ? `${+(s / 3600).toFixed(1)}h` : `${+(s / 86400).toFixed(1)}d`);
  const short = (a) => (a && a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a || "");

  function toast(msg, bad) {
    let box = document.getElementById("toast");
    if (!box) { box = document.createElement("div"); box.id = "toast"; document.body.appendChild(box); }
    const d = document.createElement("div");
    if (bad) d.className = "bad";
    d.textContent = msg;
    box.appendChild(d);
    setTimeout(() => d.remove(), bad ? 6000 : 3200);
  }

  // In-page confirm (no native dialogs in this fleet).
  function ask(msg, okWord = "Confirm", danger = false) {
    return new Promise((resolve) => {
      const v = document.createElement("div");
      v.className = "veil";
      v.innerHTML = `<div class="dlg"><p>${esc(msg)}</p><div class="inline"><button data-a="0">Cancel</button><button data-a="1" class="${danger ? "danger" : "primary"}">${esc(okWord)}</button></div></div>`;
      const done = (ok) => { v.remove(); resolve(ok); };
      v.addEventListener("click", (e) => {
        if (e.target === v) return done(false);
        const a = e.target.closest("button")?.dataset.a;
        if (a) done(a === "1");
      });
      document.body.appendChild(v);
      v.querySelector("button.primary,button.danger")?.focus();
    });
  }

  function sudoMessage(action, target, time, nonce) {
    return ["MOD Build Sudo Authorization", `action: ${action}`, `target: ${target}`, `time: ${time}`, `nonce: ${nonce}`, "",
      "Authorizing a privileged cross-module operation as the owner. Free signature, not a transaction."].join("\n");
  }
  async function mintSudo(action, target) {
    const eth = window.ethereum;
    if (!eth || !address || !(walletType === "metamask" || walletType === "subwallet"))
      throw new Error("this needs a sudo signature — authorize it once in the build console, then retry here");
    const time = Math.floor(Date.now() / 1000);
    const nonce = Array.from(crypto.getRandomValues(new Uint8Array(16))).map((b) => b.toString(16).padStart(2, "0")).join("");
    const signature = await eth.request({ method: "personal_sign", params: [sudoMessage(action, target, time, nonce), address] });
    const raw = JSON.stringify({ action, target, time, nonce, key: address, signature });
    return btoa(unescape(encodeURIComponent(raw))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  // fetch against build's API → { ok, status, data }. Raises the wallet for a
  // sudo signature when build asks for one, then retries once.
  async function api(path, { method = "GET", body, sudo } = {}) {
    const headers = { "Content-Type": "application/json" };
    if (token && token !== "local") headers.Authorization = `Bearer ${token}`;
    if (sudo) headers["x-sudo"] = sudo;
    let res;
    try {
      res = await fetch(API + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    } catch (e) {
      return { ok: false, status: 0, data: { error: `build unreachable (${e.message || e})` } };
    }
    const data = await res.json().catch(() => ({}));
    if (res.status === 401 && data?.sudo_required && !sudo) {
      try {
        const tok = await mintSudo(data.action || "write", data.target || path);
        return api(path, { method, body, sudo: tok });
      } catch (e) {
        return { ok: false, status: 401, data: { error: e.message || String(e) } };
      }
    }
    return { ok: res.ok, status: res.status, data };
  }

  // api() for a write: toasts the outcome, returns data or null.
  async function act(path, body, okMsg, method = "POST") {
    const r = await api(path, { method, body });
    if (!r.ok) { toast(r.data?.error || `failed (${r.status})`, true); return null; }
    if (okMsg) toast(okMsg);
    return r.data;
  }

  const K = {
    NAME, API, BUILD_APP, token, address, owner: "", isOwner: false, signedIn: !!token,
    esc, ago, dur, short, toast, ask, api, act,

    // Header: glyph, title, live state pill slot (#state), identity, build link.
    async init({ glyph, title, sub, accent }) {
      if (accent) document.documentElement.style.setProperty("--accent", accent);
      document.title = `${title} · build agent`;
      const info = await fetch("info").then((r) => r.json()).catch(() => ({}));
      K.owner = info?.build?.owner || "";
      K.isOwner = token === "local" || (!!address && !!K.owner && address.toLowerCase() === K.owner.toLowerCase());
      const who = !token
        ? `<a class="pill warn" href="${BUILD_APP}">sign in at build</a>`
        : `<span class="pill ${K.isOwner ? "on" : ""}" title="${esc(address)}">${token === "local" ? "local" : esc(short(address))}${K.isOwner ? " · owner" : ""}</span>`;
      const hdr = document.getElementById("hdr");
      hdr.className = "hdr";
      hdr.innerHTML = `<div class="glyph">${glyph}</div>
        <div class="grow"><h1>${esc(title)}</h1><div class="sub">${esc(sub || info.description || "")}</div></div>
        <div class="right"><span id="state"></span>${who}<a class="pill acc" href="${BUILD_APP}" title="open the build console">build ↗</a></div>`;
      if (!token) {
        const n = document.createElement("div");
        n.className = "notice";
        n.innerHTML = `Reading as a visitor. Controls need your build session — <a href="${BUILD_APP}">sign in to build</a> in this browser, then come back.`;
        hdr.after(n);
      }
      return info;
    },
    setState(html) { const s = document.getElementById("state"); if (s) s.innerHTML = html; },
    pill(on, onWord = "on", offWord = "off", kind) {
      return `<span class="pill ${kind || (on ? "on" : "")}">${esc(on ? onWord : offWord)}</span>`;
    },
    // Poll fn every ms; pauses while the tab is hidden.
    poll(fn, ms) {
      let t;
      const tick = async () => { clearTimeout(t); if (!document.hidden) { try { await fn(); } catch (e) { console.error(e); } } t = setTimeout(tick, ms); };
      document.addEventListener("visibilitychange", () => { if (!document.hidden) tick(); });
      tick();
      return tick;
    },
    // Two-tap arm for buttons that cost money or make things less safe.
    armed(btn, word, run) {
      if (btn.dataset.armed) { delete btn.dataset.armed; btn.classList.remove("armed"); return run(); }
      const orig = btn.textContent;
      btn.dataset.armed = "1"; btn.classList.add("armed"); btn.textContent = word;
      setTimeout(() => { if (btn.dataset.armed) { delete btn.dataset.armed; btn.classList.remove("armed"); btn.textContent = orig; } }, 3500);
    },
  };
  window.K = K;
})();
