/* passport.js — one sign-in for every module console.
 *
 *   <script src="/passport/passport.js"></script>
 *   const s = await Passport.ready()      // {address, token, scheme} | null
 *   await Passport.signIn('mymod')        // wallet personal_sign, canonical token
 *   Passport.onChange(s => …)             // sign-in / sign-out in ANY console
 *   Passport.signOut()
 *
 * Where a session comes from, in order:
 *   1. the shared slot (localStorage `mod_passport`) — any console signed in
 *   2. the build console's own session on this origin (build_jobs_token)
 *   3. the parent frame, when embedded in build (postMessage mod.session)
 *
 * Tokens are minted in the CANONICAL shape (string data, whole-second time,
 * EIP-191) so the same token verifies in Python modules AND build's Rust API.
 * Served no-cache by the passport module: change it there and every console
 * picks it up on its next load — no rebuilds.
 */
(function () {
  if (window.Passport) return
  const me = document.currentScript && document.currentScript.src
  const BASE = me ? me.replace(/\/passport\.js(\?.*)?$/, '') : '/passport'
  let P = {
    storage_key: 'mod_passport',
    adopt_storage: [['build_jobs_token', 'build_jobs_address']],
    message_request: 'mod.session.request',
    message_reply: 'mod.session',
    message_end: 'mod.session.end',
  }
  const MAX_AGE_MS = 23 * 3600 * 1000
  const subs = new Set()
  const ls = {
    get(k) { try { return localStorage.getItem(k) } catch { return null } },
    set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v) } catch {} },
  }
  const b64url = o => btoa(unescape(encodeURIComponent(JSON.stringify(o))))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  const decode = t => { try { return JSON.parse(decodeURIComponent(escape(atob(t.replace(/-/g, '+').replace(/_/g, '/'))))) } catch { return null } }

  // a session is worth offering only while its token is still fresh
  function fresh(s) {
    if (!s || !s.token || !s.address) return false
    if (s.scheme === 'build') {
      const ts = Number(String(s.token).split(':')[1])
      return !ts || Date.now() - ts * 1000 < MAX_AGE_MS
    }
    const env = decode(s.token)
    return !!env && Date.now() - Number(env.time) * 1000 < MAX_AGE_MS
  }

  function session() {
    try {
      const s = JSON.parse(ls.get(P.storage_key) || 'null')
      if (fresh(s)) return s
    } catch {}
    for (const [tk, ak] of P.adopt_storage) {
      const token = ls.get(tk), address = ls.get(ak)
      if (token && address && token !== 'local' && address.startsWith('0x')) {
        const s = { address: address.toLowerCase(), token, scheme: token.includes(':') ? 'build' : 'mod', via: tk }
        if (fresh(s)) return s
      }
    }
    return null
  }

  function emit(s) { subs.forEach(f => { try { f(s) } catch {} }) }

  function set(s) {
    ls.set(P.storage_key, s ? JSON.stringify(s) : null)
    emit(s)
    return s
  }

  // embedded in build (or any console that answers mod.session.request)
  function askParent(ms = 1500) {
    if (window.parent === window) return Promise.resolve(null)
    return new Promise(res => {
      const done = s => { window.removeEventListener('message', on); res(s) }
      const on = e => {
        const d = e.data || {}
        if (d.type === P.message_reply && d.token && d.address)
          done({ address: String(d.address).toLowerCase(), token: d.token,
                 scheme: String(d.token).includes(':') ? 'build' : 'mod', via: 'parent' })
      }
      window.addEventListener('message', on)
      try { window.parent.postMessage({ type: P.message_request }, '*') } catch {}
      setTimeout(() => done(null), ms)
    })
  }

  async function ready() {
    const s = session()
    if (s) return s
    const p = await askParent()
    return p ? set(p) : null
  }

  async function signIn(scope) {
    const eth = window.ethereum
    if (!eth) throw new Error('no wallet found')
    const [addr] = await eth.request({ method: 'eth_requestAccounts' })
    const address = String(addr || '').toLowerCase()
    if (!address) throw new Error('no account selected')
    const data = String(scope || location.pathname.split('/')[1] || 'mod')
    const time = Math.floor(Date.now() / 1000).toString()
    const signature = await eth.request({ method: 'personal_sign', params: [JSON.stringify({ data, time }), address] })
    return set({ address, token: b64url({ data, time, key: address, signature }), scheme: 'mod' })
  }

  // hand a token minted elsewhere (a local key, a console's own flow) to
  // every other console — the shape is whatever the protocol accepts
  function adopt(address, token, scheme) {
    return set({ address: String(address).toLowerCase(), token, scheme: scheme || (String(token).includes(':') ? 'build' : 'mod') })
  }

  function signOut() { set(null) }

  async function verify(token) {
    const r = await fetch(`${BASE}/verify?token=${encodeURIComponent(token)}`)
    return r.json()
  }

  async function role(mod, token) {
    const t = token || (session() || {}).token
    if (!t) return { signed_in: false }
    const r = await fetch(`${BASE}/role?mod=${encodeURIComponent(mod || '')}&token=${encodeURIComponent(t)}`)
    return r.json()
  }

  window.addEventListener('storage', e => {
    if (e.key === P.storage_key || P.adopt_storage.some(([t]) => t === e.key)) emit(session())
  })
  window.addEventListener('message', e => {
    const d = e.data || {}
    if (d.type === P.message_end && e.source === window.parent) signOut()
  })
  // the protocol can rename slots/messages without shipping new consoles
  fetch(`${BASE}/protocol`).then(r => r.json()).then(p => {
    if (p && p.browser) P = { ...P, ...p.browser }
  }).catch(() => {})

  window.Passport = { session, ready, signIn, signOut, adopt, verify, role,
    onChange(f) { subs.add(f); return () => subs.delete(f) }, base: BASE }
})()
