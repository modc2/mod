'use client'

import { useEffect, useMemo, useRef, useState } from 'react'

// Everything goes through grokchat's python API, which relays to grokbot
// verbatim — identity, keys, bots and the run ledger all live in grokbot.
const API = '/grokchat/_api'
// Same localStorage slot as the grokbot console: on one origin, signing in to
// either one signs you in to both.
const TKEY = 'grokbot.token'
const AKEY = 'grokbot.token.at'

const short = (a) => (a && a.length > 12 ? a.slice(0, 6) + '…' + a.slice(-4) : a || '')

// someone else's bot reads as name @0xabcd…1234
function refLabel(ref) {
  const i = String(ref).lastIndexOf('/')
  return i < 0 ? ref : ref.slice(i + 1) + ' @' + short(ref.slice(0, i))
}

// The grokbot mark — the same two strokes the grokbot console draws.
function Mark({ size = 20, className = 'mark' }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 8 8" aria-hidden="true">
      <path d="M1 1h2l4 6h-2z" fill="#e8eaf2" />
      <path d="M5 1h2l-4 6h-2z" fill="#6ee7ff" />
    </svg>
  )
}

const Icon = ({ d, size = 16 }) => (
  <svg className="icon" width={size} height={size} viewBox="0 0 24 24">
    {d}
  </svg>
)

// ── a mod-protocol token: personal_sign over {data,time}, base64url'd ──
function b64url(obj) {
  const bytes = new TextEncoder().encode(JSON.stringify(obj))
  let bin = ''
  bytes.forEach((b) => (bin += String.fromCharCode(b)))
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function wallet() {
  const eth = typeof window !== 'undefined' ? window.ethereum : null
  if (!eth) return null
  if (Array.isArray(eth.providers) && eth.providers.length)
    return eth.providers.find((p) => p.isMetaMask) || eth.providers[0]
  return eth
}

async function mint(provider, address) {
  const data = { mod: 'grokbot' }
  const time = (Date.now() / 1000).toString()
  const signature = await provider.request({
    method: 'personal_sign',
    params: [JSON.stringify({ data, time }), address],
  })
  return b64url({ data, time, key: address, signature })
}

const EMPTY_BOT = { name: '', system: '', model: '', search: '', description: '', public: false }

export default function Page() {
  const [token, setToken] = useState('')
  const [you, setYou] = useState(null)
  const [health, setHealth] = useState(null)
  const [models, setModels] = useState([])
  const [model, setModel] = useState('')
  const [search, setSearch] = useState('')
  const [bots, setBots] = useState([])
  const [pub, setPub] = useState([])
  const [bot, setBot] = useState(null) // own name, or <address>/<name>
  const [convos, setConvos] = useState([])
  const [active, setActive] = useState(null)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [veil, setVeil] = useState(null) // 'bots' | 'explore' | 'key' | 'token'
  const [draft, setDraft] = useState(EMPTY_BOT)
  const [editing, setEditing] = useState(null)
  const [pubQ, setPubQ] = useState('')
  const [pubScope, setPubScope] = useState('others')
  const [keyIn, setKeyIn] = useState('')
  const [tokenIn, setTokenIn] = useState('')
  const [pending, setPending] = useState(null) // {name} awaiting delete confirm
  const [toast, setToast] = useState(null)
  const [side, setSide] = useState(true)
  const abortRef = useRef(null)
  const threadRef = useRef(null)
  const tokenRef = useRef('')

  const signed = !!(you && you.address)
  const hasKey = !!(you && you.key && you.key.key)

  function say(msg, bad) {
    setToast({ msg, bad })
    clearTimeout(say.t)
    say.t = setTimeout(() => setToast(null), 3200)
  }

  async function api(path, opts = {}) {
    const headers = { 'content-type': 'application/json' }
    if (tokenRef.current) headers.authorization = 'Bearer ' + tokenRef.current
    const r = await fetch(API + path, {
      method: opts.method || 'GET',
      headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    })
    const text = await r.text()
    let data
    try {
      data = JSON.parse(text)
    } catch {
      data = { error: text }
    }
    if (!r.ok) throw new Error(data.error || 'HTTP ' + r.status)
    return data
  }

  function keepToken(t) {
    tokenRef.current = t || ''
    setToken(t || '')
    if (t) {
      localStorage.setItem(TKEY, t)
      localStorage.setItem(AKEY, String(Date.now()))
    } else {
      localStorage.removeItem(TKEY)
      localStorage.removeItem(AKEY)
    }
  }

  // ── loading ─────────────────────────────────────────────────────
  async function refresh() {
    let me = null
    try {
      me = await api('/me')
    } catch (e) {
      say(e.message, true)
    }
    if (me && !me.signed_in && tokenRef.current) keepToken('')
    setYou(me)
    const addr = (me && me.address) || 'anon'
    let cs = []
    try {
      cs = JSON.parse(localStorage.getItem('grokbot.convos.' + addr) || '[]')
    } catch {}
    setConvos(cs)
    setActive(cs.length ? cs[0].id : null)
    loadPublic()
    if (me && me.address) {
      loadBots()
      if (me.key && me.key.key) loadModels()
    } else {
      setBots([])
      setModels([])
    }
  }

  async function loadModels() {
    try {
      setModels((await api('/models')).models || [])
    } catch {
      setModels([])
    }
  }
  async function loadBots() {
    try {
      setBots((await api('/bots')).bots || [])
    } catch {
      setBots([])
    }
  }
  async function loadPublic() {
    try {
      setPub((await api('/public')).bots || [])
    } catch {
      setPub([])
    }
  }

  useEffect(() => {
    tokenRef.current = localStorage.getItem(TKEY) || ''
    setToken(tokenRef.current)
    if (matchMedia('(max-width:760px)').matches) setSide(false)
    fetch(`${API}/health`)
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => setHealth({ ok: false }))
    refresh()
    const onKey = (e) => e.key === 'Escape' && setVeil(null)
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  // ── conversations: per address, in this browser (same slot as grokbot) ──
  function saveConvos(next) {
    const cut = next.slice(0, 60)
    setConvos(cut)
    try {
      localStorage.setItem('grokbot.convos.' + ((you && you.address) || 'anon'), JSON.stringify(cut))
    } catch {}
  }
  const convo = convos.find((c) => c.id === active) || null

  useEffect(() => {
    const t = threadRef.current
    if (t && t.scrollHeight - t.scrollTop - t.clientHeight < 200) t.scrollTop = t.scrollHeight
  }, [convos, active])

  // ── signing in ──────────────────────────────────────────────────
  async function signIn(pick) {
    const p = wallet()
    if (!p) return setVeil('token')
    try {
      if (pick) {
        try {
          await p.request({ method: 'wallet_requestPermissions', params: [{ eth_accounts: {} }] })
        } catch {}
      }
      const accounts = await p.request({ method: 'eth_requestAccounts' })
      const address = ((accounts && accounts[0]) || '').toLowerCase()
      if (!address) throw new Error('the wallet returned no account')
      keepToken(await mint(p, address))
      await refresh()
      say('signed in as ' + short(address))
    } catch (e) {
      say(e.code === 4001 ? 'the signature was refused' : e.message || 'the wallet refused', true)
    }
  }
  function signOut() {
    keepToken('')
    setBot(null)
    refresh().then(() => say('signed out'))
  }
  async function useToken() {
    const t = tokenIn.trim()
    if (!t) return say('paste a token first', true)
    keepToken(t)
    setTokenIn('')
    setVeil(null)
    await refresh()
  }

  // ── key ─────────────────────────────────────────────────────────
  async function saveKey() {
    if (!keyIn.trim()) return say('paste your xAI key first', true)
    try {
      await api('/key', { method: 'POST', body: { key: keyIn.trim() } })
      setKeyIn('')
      say('key saved')
      await refresh()
    } catch (e) {
      say(e.message, true)
    }
  }
  async function forgetKey() {
    try {
      await api('/key', { method: 'DELETE' })
      say('key forgotten')
      await refresh()
    } catch (e) {
      say(e.message, true)
    }
  }

  // ── bots ────────────────────────────────────────────────────────
  function newBot() {
    if (!signed) return say('connect a wallet first — grokbots are per account', true)
    setEditing(null)
    setDraft(EMPTY_BOT)
    setVeil('bots')
  }
  function editBot(b) {
    setEditing(b.name)
    setDraft({
      name: b.name,
      system: b.system || '',
      model: b.model || '',
      search: b.search || '',
      description: b.description || '',
      public: !!b.public,
    })
    setVeil('bots')
  }
  async function saveBot() {
    const body = {
      name: draft.name.trim(),
      system: draft.system,
      model: draft.model || null,
      search: draft.search || null,
      description: draft.description.trim() || null,
      public: draft.public,
    }
    if (!body.name) return say('a bot needs a name', true)
    try {
      await api('/bots', { method: 'POST', body })
      // renaming = save the new name, drop the old one
      if (editing && editing !== body.name)
        await api('/bots?name=' + encodeURIComponent(editing), { method: 'DELETE' })
      say((body.public ? 'published ' : 'saved ') + body.name)
      setEditing(body.name)
      loadBots()
      loadPublic()
    } catch (e) {
      say(e.message, true)
    }
  }
  async function togglePublish(b) {
    try {
      await api('/bots/publish', { method: 'POST', body: { name: b.name, public: !b.public } })
      say(!b.public ? 'published ' + b.name : b.name + ' is private')
      loadBots()
      loadPublic()
    } catch (e) {
      say(e.message, true)
    }
  }
  async function deleteBot(name) {
    try {
      await api('/bots?name=' + encodeURIComponent(name), { method: 'DELETE' })
      if (bot === name) setBot(null)
      if (editing === name) {
        setEditing(null)
        setDraft(EMPTY_BOT)
      }
      setPending(null)
      say('deleted ' + name)
      loadBots()
      loadPublic()
    } catch (e) {
      say(e.message, true)
    }
  }
  async function copyBot(ref) {
    try {
      const got = await api('/bots/copy', { method: 'POST', body: { ref } })
      say('copied as ' + got.name)
      loadBots()
    } catch (e) {
      say(e.message, true)
    }
  }
  function arm(ref) {
    if (!signed) return say('connect a wallet first — chats spend your own credits', true)
    const next = bot === ref ? null : ref
    setBot(next)
    say(next ? 'chatting as ' + refLabel(next) : 'no bot — plain Grok')
  }

  const shown = useMemo(() => {
    const q = pubQ.trim().toLowerCase()
    return pub.filter(
      (b) =>
        (pubScope === 'all' || (pubScope === 'mine' ? b.mine : !b.mine)) &&
        (!q || [b.name, b.system, b.description, b.owner].join(' ').toLowerCase().includes(q))
    )
  }, [pub, pubQ, pubScope])
  const others = pub.filter((b) => !b.mine).length

  // ── chat ────────────────────────────────────────────────────────
  async function send() {
    if (busy) return
    if (!signed) return say('connect a wallet in the sidebar first', true)
    const text = input.trim()
    if (!text) return
    let c = convo
    let list = convos
    if (!c) {
      c = { id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), title: '', msgs: [], ts: Date.now() }
      list = [c, ...convos]
      setActive(c.id)
    }
    const history = [...c.msgs, { role: 'user', text }]
    const base = { ...c, title: c.title || text.slice(0, 48), bot: bot || c.bot || null }
    const put = (msgs) => {
      list = list.map((x) => (x.id === base.id ? { ...base, msgs } : x))
      return list
    }
    setConvos(put([...history, { role: 'assistant', text: '', live: true, bot }]))
    setInput('')
    setBusy(true)

    const body = {
      stream: true,
      messages: history.filter((m) => m.role !== 'err').map((m) => ({ role: m.role, content: m.text })),
    }
    if (model) body.model = model
    if (search) body.search = search
    if (bot) body.bot = bot

    const ctl = new AbortController()
    abortRef.current = ctl
    let reply = { role: 'assistant', text: '', cites: null, bot }
    try {
      const headers = { 'content-type': 'application/json' }
      if (tokenRef.current) headers.authorization = 'Bearer ' + tokenRef.current
      const r = await fetch(`${API}/chat`, { method: 'POST', headers, body: JSON.stringify(body), signal: ctl.signal })
      const ctype = r.headers.get('content-type') || ''
      if (!r.ok || !ctype.includes('event-stream')) {
        const t = await r.text()
        let d
        try {
          d = JSON.parse(t)
        } catch {
          d = { error: t }
        }
        throw new Error(d.error || 'HTTP ' + r.status)
      }
      const reader = r.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        let nl
        while ((nl = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, nl).trim()
          buf = buf.slice(nl + 1)
          if (!line.startsWith('data:')) continue
          const payload = line.slice(5).trim()
          if (!payload || payload === '[DONE]') continue
          let o
          try {
            o = JSON.parse(payload)
          } catch {
            continue
          }
          if (o.error) throw new Error(typeof o.error === 'string' ? o.error : o.error.message || JSON.stringify(o.error))
          const delta = o.choices?.[0]?.delta?.content
          if (delta) reply = { ...reply, text: reply.text + delta }
          if (o.citations?.length) reply = { ...reply, cites: o.citations }
        }
        setConvos(put([...history, { ...reply, live: true }]))
      }
      if (!reply.text) reply.text = '(no text)'
      saveConvos(put([...history, reply]))
    } catch (e) {
      if (e.name === 'AbortError') saveConvos(put(reply.text ? [...history, reply] : history))
      else saveConvos(put([...history, { role: 'err', text: e.message }]))
    } finally {
      abortRef.current = null
      setBusy(false)
    }
  }

  const grokbotUp = health?.grokbot_ok
  const hint = [
    bot && 'bot: ' + refLabel(bot),
    !signed && 'connect a wallet in the sidebar to chat',
    signed && !hasKey && 'no xAI key yet — open KEY in the top bar',
  ].filter(Boolean)

  return (
    <div className="shell">
      {/* ── sidebar: chats, your grokbots, explore, account ── */}
      <nav className={`sidebar ${side ? '' : 'hide'}`}>
        <div className="sidehead">
          <Mark />
          <h1>
            GROK<span>CHAT</span>
          </h1>
          <button className="ghost" onClick={() => setSide(false)} title="hide the sidebar">
            <Icon d={<path d="M15 6l-6 6 6 6" />} />
          </button>
        </div>
        <button className="newchat" onClick={() => setActive(null)}>
          <Icon d={<path d="M12 5v14M5 12h14" />} /> New chat
        </button>

        <div className="sidelabel">Chats</div>
        <div className="convos">
          {convos.length === 0 && <div className="muted pad">nothing yet — chats stay in this browser, per address</div>}
          {convos.map((c) => (
            <div key={c.id} className={`convo ${c.id === active ? 'on' : ''}`} onClick={() => setActive(c.id)}>
              <span className="t">{c.title || 'New chat'}</span>
              <button
                className="x"
                title="delete"
                onClick={(e) => {
                  e.stopPropagation()
                  const next = convos.filter((x) => x.id !== c.id)
                  saveConvos(next)
                  if (active === c.id) setActive(next.length ? next[0].id : null)
                }}
              >
                ×
              </button>
            </div>
          ))}
        </div>

        <div className="sidelabel">
          My grokbots
          <button className="plus" onClick={newBot} title="make a grokbot">
            +
          </button>
        </div>
        <div className="sidebots">
          {!signed && <div className="muted pad">sign in below — your grokbots hang off your address</div>}
          {signed && bots.length === 0 && (
            <div className="muted pad">none yet — hit + to make one. A grokbot is a name, a model and a system prompt.</div>
          )}
          {bots.map((b) => (
            <div key={b.name} className={`sbot ${bot === b.name ? 'on' : ''}`} title={b.system || ''} onClick={() => arm(b.name)}>
              <span className="bdot" />
              <span className="t">{b.name}</span>
              {b.public && <span className="pub">PUB</span>}
              <button
                className="x"
                title="edit"
                onClick={(e) => {
                  e.stopPropagation()
                  editBot(b)
                }}
              >
                ✎
              </button>
            </div>
          ))}
        </div>
        <button className="explore" onClick={() => (loadPublic(), setVeil('explore'))} title="bots other people published">
          <Icon
            d={
              <>
                <circle cx="12" cy="12" r="9" />
                <path d="M15.5 8.5l-2 5-5 2 2-5z" />
              </>
            }
          />
          Explore bots {others > 0 && <span className="n">{others}</span>}
        </button>

        <div className="sidefoot">
          {!signed ? (
            <div className="acctcard">
              <Mark size={34} className="gatemark" />
              <p>One signature is the account — the same one grokbot uses. It owns your xAI key, your bots and your runs.</p>
              <button className="go" onClick={() => signIn(false)}>
                CONNECT WALLET
              </button>
              <div className="alt">
                no wallet? paste a token from <code>m.mod("auth")().token({'{}'})</code>
              </div>
            </div>
          ) : (
            <div className="acct">
              <span className="who">
                <span className={`dot ${you.role === 'owner' ? 'owner' : ''}`} />
                <span className="addr">{short(you.address)}</span>
                {you.role === 'owner' && <span className="muted">owner</span>}
              </span>
              <span className="acts">
                <button className="small" onClick={() => signIn(true)}>
                  SWITCH
                </button>
                <button className="small" onClick={signOut}>
                  SIGN OUT
                </button>
              </span>
            </div>
          )}
        </div>
      </nav>

      {/* ── chat column ── */}
      <div className="app">
        <div className="topbar">
          {!side && (
            <button className="ghost" onClick={() => setSide(true)} title="show the sidebar">
              <Icon d={<path d="M4 6h16M4 12h16M4 18h16" />} />
            </button>
          )}
          <BotPicker bots={bots} pub={pub} bot={bot} onPick={(r) => setBot(r)} onManage={newBot} onExplore={() => setVeil('explore')} />
          <select value={model} onChange={(e) => setModel(e.target.value)} title="model">
            <option value="">model: default</option>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id}
                {m.prompt_usd_m != null ? ` — $${m.prompt_usd_m}/M in` : ''}
              </option>
            ))}
          </select>
          <select value={search} onChange={(e) => setSearch(e.target.value)} title="live search">
            <option value="">no search</option>
            <option value="auto">search: auto</option>
            <option value="on">search: on</option>
          </select>
          <span className="grow" />
          <span className="status" title="grokbot on :50890">
            <span className={`dot ${grokbotUp ? '' : health ? 'bad' : 'off'}`} />
            {grokbotUp ? 'grokbot' : health ? 'grokbot down' : '…'}
          </span>
          <button className={`chipbtn ${hasKey ? 'on' : ''}`} onClick={() => setVeil('key')}>
            KEY {hasKey ? 'SET' : 'NONE'}
          </button>
        </div>

        <div className="thread" ref={threadRef}>
          <div className="col">
            {(!convo || convo.msgs.length === 0) && (
              <div className="hero">
                <div className="orb">
                  <Mark size={44} className="gatemark" />
                </div>
                <h2>{bot ? refLabel(bot) : 'What do you want to know?'}</h2>
                <p className="muted">
                  {bot
                    ? (bots.find((b) => b.name === bot) || pub.find((b) => b.ref === bot) || {}).description ||
                      'Your messages go through this grokbot’s system prompt.'
                    : 'Grok through grokbot — pick one of your grokbots, or explore what other people published.'}
                </p>
                {!bot && others > 0 && (
                  <div className="seeds">
                    {pub
                      .filter((b) => !b.mine)
                      .slice(0, 4)
                      .map((b) => (
                        <button key={b.ref} className="seed" onClick={() => arm(b.ref)}>
                          <b>{b.name}</b>
                          <span>by {short(b.owner)}</span>
                        </button>
                      ))}
                  </div>
                )}
              </div>
            )}
            {convo?.msgs.map((m, i) =>
              m.role === 'user' ? (
                <div key={i} className="msg user">
                  <div className="bubble">{m.text}</div>
                </div>
              ) : (
                <div key={i} className={`msg ${m.role === 'err' ? 'err' : 'assistant'}`}>
                  <span className="ava">
                    <Mark size={14} className="" />
                  </span>
                  <div className={`bubble ${m.live ? 'live' : ''}`}>
                    {m.bot && <div className="by">{refLabel(m.bot)}</div>}
                    {m.text}
                    {m.cites?.length > 0 && (
                      <div className="cites">
                        {m.cites.map((c, j) => (
                          <a key={j} href={c} target="_blank" rel="noreferrer">
                            [{j + 1}] {host(c)}
                          </a>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )
            )}
          </div>
        </div>

        <div className="composer">
          <div className="compwrap">
            <textarea
              rows={1}
              value={input}
              placeholder={bot ? `Message ${refLabel(bot)}…` : 'Ask Grok anything…'}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  send()
                }
              }}
            />
            <button
              className={`send ${busy ? 'stop' : ''}`}
              title={busy ? 'stop' : 'send — enter'}
              onClick={() => (busy && abortRef.current ? abortRef.current.abort() : send())}
            >
              {busy ? (
                <Icon d={<rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor" stroke="none" />} />
              ) : (
                <Icon d={<path d="M12 19V5M5 12l7-7 7 7" />} />
              )}
            </button>
          </div>
          <div className="compfoot">
            {hint.length ? hint.join(' · ') : 'Grok can make mistakes. Every call spends your own xAI credits.'}
          </div>
        </div>
      </div>

      {/* ── manage bots: same fields grokbot's settings use ── */}
      {veil === 'bots' && (
        <Veil title="My grokbots" onClose={() => setVeil(null)}>
          <div className="botlist">
            {bots.length === 0 && <div className="muted">no bots yet — a bot is a name, a model and a system prompt.</div>}
            {bots.map((b) => (
              <div key={b.name} className={`bot ${editing === b.name ? 'on' : ''}`}>
                <span>
                  <b>{b.name}</b>
                  <span className="why">
                    {b.model || 'default'}
                    {b.search ? ' · search ' + b.search : ''}
                    {b.uses ? ` · ${b.uses} chat${b.uses === 1 ? '' : 's'}` : ''}
                    {b.from ? ' · copied from ' + refLabel(b.from) : ''}
                  </span>
                </span>
                <span className="acts">
                  <button className="small" onClick={() => arm(b.name)}>
                    {bot === b.name ? 'ON' : 'USE'}
                  </button>
                  <button className="small" onClick={() => editBot(b)}>
                    EDIT
                  </button>
                  <button
                    className={`small ${b.public ? 'pubon' : ''}`}
                    title={b.public ? 'published — click to make private' : 'private — click to publish'}
                    onClick={() => togglePublish(b)}
                  >
                    {b.public ? 'PUBLIC' : 'PRIVATE'}
                  </button>
                  {pending === b.name ? (
                    <>
                      <button className="small danger" onClick={() => deleteBot(b.name)}>
                        DELETE?
                      </button>
                      <button className="small" onClick={() => setPending(null)}>
                        KEEP
                      </button>
                    </>
                  ) : (
                    <button className="small" onClick={() => setPending(b.name)}>
                      X
                    </button>
                  )}
                </span>
              </div>
            ))}
          </div>
          <div className="sidelabel flat">
            {editing ? 'Edit ' + editing : 'New grokbot'}
            {editing && (
              <button
                className="plus"
                onClick={() => {
                  setEditing(null)
                  setDraft(EMPTY_BOT)
                }}
              >
                + new
              </button>
            )}
          </div>
          <div className="row">
            <input placeholder="name — e.g. skeptic" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          </div>
          <div className="row">
            <textarea
              rows={4}
              placeholder="system prompt — what this bot is"
              value={draft.system}
              onChange={(e) => setDraft({ ...draft, system: e.target.value })}
            />
          </div>
          <div className="row">
            <select value={draft.model} onChange={(e) => setDraft({ ...draft, model: e.target.value })}>
              <option value="">model: default</option>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.id}
                </option>
              ))}
              {draft.model && !models.find((m) => m.id === draft.model) && <option value={draft.model}>{draft.model}</option>}
            </select>
            <select value={draft.search} onChange={(e) => setDraft({ ...draft, search: e.target.value })}>
              <option value="">search: off</option>
              <option value="auto">search: auto</option>
              <option value="on">search: on</option>
            </select>
          </div>
          <div className="row">
            <input
              placeholder="one line — what it is for (shown when published)"
              value={draft.description}
              onChange={(e) => setDraft({ ...draft, description: e.target.value })}
            />
          </div>
          <label className="check">
            <input type="checkbox" checked={draft.public} onChange={(e) => setDraft({ ...draft, public: e.target.checked })} />
            publish — anyone can see it, chat with it on their own credits, or copy it
          </label>
          <div className="row">
            <button className="go" onClick={saveBot}>
              SAVE BOT
            </button>
          </div>
        </Veil>
      )}

      {/* ── explore: everyone's published bots ── */}
      {veil === 'explore' && (
        <Veil title="Published grokbots" onClose={() => setVeil(null)}>
          <div className="row">
            <input autoFocus placeholder="search name, prompt or owner" value={pubQ} onChange={(e) => setPubQ(e.target.value)} />
          </div>
          <div className="pills">
            {[
              ['others', 'OTHER PEOPLE', others],
              ['mine', 'YOURS', pub.length - others],
              ['all', 'ALL', pub.length],
            ].map(([k, label, n]) => (
              <button key={k} className={`pill ${pubScope === k ? 'on' : ''}`} onClick={() => setPubScope(k)}>
                {label} <span className="n">{n}</span>
              </button>
            ))}
          </div>
          {shown.length === 0 && (
            <div className="muted">
              {pub.length
                ? 'nothing here'
                : 'nobody has published a bot yet — make one under My grokbots and tick publish to be the first'}
            </div>
          )}
          {shown.map((b) => {
            const run = b.mine ? b.name : b.ref
            return (
              <div key={b.ref} className={`pbot ${bot === run ? 'on' : ''}`}>
                <div className="hd">
                  <b>{b.name}</b>
                  {b.mine ? <span className="mine">YOURS</span> : <span className="tag">by {short(b.owner)}</span>}
                  <span className="tag">
                    {b.model || 'default'}
                    {b.search ? ' · search ' + b.search : ''}
                    {b.uses ? ` · ${b.uses} chat${b.uses === 1 ? '' : 's'}` : ''}
                  </span>
                  <span className="acts">
                    <button
                      className="small"
                      onClick={() => {
                        arm(run)
                        if (signed && bot !== run) setVeil(null)
                      }}
                    >
                      {bot === run ? 'ON' : 'CHAT'}
                    </button>
                    {signed && !b.mine && (
                      <button className="small" onClick={() => copyBot(b.ref)}>
                        COPY
                      </button>
                    )}
                  </span>
                </div>
                {b.description && <div className="sys ink">{b.description}</div>}
                <div className="sys">{b.system || '(no system prompt)'}</div>
              </div>
            )
          })}
          <div className="muted foot">
            Chatting with someone's bot spends your own xAI credits. COPY puts a private, editable copy on your account.
          </div>
        </Veil>
      )}

      {/* ── key: stored by grokbot against your address ── */}
      {veil === 'key' && (
        <Veil title="Your xAI key" onClose={() => setVeil(null)}>
          {!signed ? (
            <div className="gate">Sign in first — your xAI key and your bots hang off your wallet address in grokbot.</div>
          ) : (
            <>
              <div className="row">
                {hasKey ? (
                  <span className="chip">
                    <span className="dot" />
                    key {you.key.fingerprint || 'set'} <span className="muted">from {you.key.source}</span>
                  </span>
                ) : (
                  <span className="chip">
                    <span className="dot off" />
                    no key yet
                  </span>
                )}
              </div>
              <div className="row">
                <input type="password" placeholder="xai-…" autoComplete="off" value={keyIn} onChange={(e) => setKeyIn(e.target.value)} />
              </div>
              <div className="row">
                <button className="go" onClick={saveKey}>
                  SAVE KEY
                </button>
                {hasKey && <button onClick={forgetKey}>FORGET</button>}
                <span className="muted">
                  from{' '}
                  <a href="https://console.x.ai" target="_blank" rel="noreferrer">
                    console.x.ai
                  </a>
                </span>
              </div>
              <div className="muted">Saved by grokbot (0600, ~/.mod/grokbot) — the same key the grokbot console uses.</div>
            </>
          )}
        </Veil>
      )}

      {veil === 'token' && (
        <Veil title="Sign in with a token" onClose={() => setVeil(null)}>
          <p className="muted">
            No wallet extension in this browser. Install MetaMask and reload — or paste a mod-protocol token minted on a machine that holds
            your key:
          </p>
          <pre className="recipe">m.mod("auth")().token({'{}'})</pre>
          <div className="row">
            <textarea rows={3} placeholder="eyJkYXRhIjp7…" value={tokenIn} onChange={(e) => setTokenIn(e.target.value)} />
          </div>
          <div className="row">
            <button className="go" onClick={useToken}>
              USE TOKEN
            </button>
          </div>
        </Veil>
      )}

      {toast && <div className={`toast ${toast.bad ? 'bad' : ''}`}>{toast.msg}</div>}
    </div>
  )
}

// The bot dropdown: a dot, the name, and a mono sub-line — mine first, then
// everyone else's published bots.
function BotPicker({ bots, pub, bot, onPick, onManage, onExplore }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  useEffect(() => {
    const off = (e) => ref.current && !ref.current.contains(e.target) && setOpen(false)
    document.addEventListener('mousedown', off)
    return () => document.removeEventListener('mousedown', off)
  }, [])
  const theirs = pub.filter((b) => !b.mine)
  const cur = bot ? bots.find((b) => b.name === bot) || pub.find((b) => b.ref === bot) : null
  const pick = (r) => {
    onPick(r)
    setOpen(false)
  }
  const Row = ({ id, name, sub, live }) => (
    <button className={`dd-row ${bot === id ? 'on' : ''}`} onClick={() => pick(id)}>
      <span className={`ring ${live ? 'live' : ''}`} />
      <span className="dd-txt">
        <span className="dd-name">{name}</span>
        <span className="dd-sub">{sub}</span>
      </span>
    </button>
  )
  return (
    <div className="dd" ref={ref}>
      <button className={`dd-btn ${open ? 'open' : ''}`} onClick={() => setOpen(!open)}>
        <span className={`ring ${bot ? 'live' : ''}`} />
        <span className="dd-k">BOT</span>
        <span className="dd-v">{bot ? (cur ? cur.name : refLabel(bot)) : 'Grok'}</span>
        <span className="dd-sub">{cur ? cur.model || 'default' : bot ? 'published' : 'no system prompt'}</span>
        <span className="caret">▾</span>
      </button>
      {open && (
        <div className="dd-menu">
          <Row id={null} name="Grok" sub="plain · no system prompt" />
          {bots.length > 0 && <div className="dd-sep">MINE</div>}
          {bots.map((b) => (
            <Row key={b.name} id={b.name} name={b.name} sub={(b.model || 'default') + (b.public ? ' · public' : ' · private')} live={b.public} />
          ))}
          {theirs.length > 0 && <div className="dd-sep">PUBLISHED BY OTHERS</div>}
          {theirs.slice(0, 12).map((b) => (
            <Row key={b.ref} id={b.ref} name={b.name} sub={'by ' + short(b.owner) + ' · ' + (b.model || 'default')} live />
          ))}
          <div className="dd-foot">
            <button
              className="small"
              onClick={() => {
                setOpen(false)
                onManage()
              }}
            >
              + NEW BOT
            </button>
            <button
              className="small"
              onClick={() => {
                setOpen(false)
                onExplore()
              }}
            >
              EXPLORE ALL
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function Veil({ title, onClose, children }) {
  return (
    <div className="veil" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <h2>
          {title}
          <button className="close" onClick={onClose}>
            ×
          </button>
        </h2>
        {children}
      </div>
    </div>
  )
}

function host(u) {
  try {
    return new URL(u).hostname.replace(/^www\./, '')
  } catch {
    return u.slice(0, 40)
  }
}
