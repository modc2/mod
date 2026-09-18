'use client'

import { useEffect, useRef, useState } from 'react'

const API = '/grokchat/_api'
const KEY_LS = 'grokchat.key'

export default function Page() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [models, setModels] = useState([])
  const [model, setModel] = useState('grok-4-fast')
  const [xaiKey, setXaiKey] = useState('')
  const [search, setSearch] = useState(false)
  const [busy, setBusy] = useState(false)
  const [health, setHealth] = useState(null)
  const [showKey, setShowKey] = useState(false)
  const endRef = useRef(null)

  const headers = (key) => {
    const h = { 'content-type': 'application/json' }
    const k = key !== undefined ? key : xaiKey
    if (k) h['x-xai-key'] = k
    return h
  }

  useEffect(() => {
    const saved = localStorage.getItem(KEY_LS) || ''
    setXaiKey(saved)
    fetch(`${API}/health`).then((r) => r.json()).then(setHealth).catch(() => setHealth({ ok: false }))
    loadModels(saved)
  }, [])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  async function loadModels(key) {
    try {
      const r = await fetch(`${API}/models`, { headers: headers(key) })
      const j = await r.json()
      const list = (j.models || j.data || []).map((m) => m.id || m.name).filter(Boolean)
      if (list.length) {
        setModels(list)
        if (!list.includes('grok-4-fast')) setModel(list[0])
      }
    } catch {
      /* no key yet — the hint in the header covers this */
    }
  }

  function saveKey(v) {
    setXaiKey(v)
    localStorage.setItem(KEY_LS, v)
    if (v.startsWith('xai-')) loadModels(v)
  }

  async function send() {
    const prompt = input.trim()
    if (!prompt || busy) return
    setInput('')
    setBusy(true)
    const history = [...messages, { role: 'user', text: prompt }]
    setMessages([...history, { role: 'assistant', text: '', pending: true }])

    const body = {
      messages: history.map((m) => ({ role: m.role, content: m.text })),
      model,
      stream: true,
    }
    if (search) body.search = 'auto'

    try {
      const r = await fetch(`${API}/chat`, {
        method: 'POST',
        headers: headers(),
        body: JSON.stringify(body),
      })
      const ctype = r.headers.get('content-type') || ''
      if (!r.ok || !ctype.includes('event-stream')) {
        const j = await r.json().catch(() => ({}))
        const err = j.error || j.text || `request failed (${r.status})`
        setLast({ role: 'assistant', text: j.text || '', error: j.text ? null : String(err), citations: j.citations })
        return
      }
      const reader = r.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      let text = ''
      let citations = null
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const frames = buf.split('\n')
        buf = frames.pop()
        for (const line of frames) {
          const data = line.startsWith('data:') ? line.slice(5).trim() : null
          if (!data || data === '[DONE]') continue
          try {
            const chunk = JSON.parse(data)
            const delta = chunk.choices?.[0]?.delta?.content
            if (delta) text += delta
            if (chunk.citations?.length) citations = chunk.citations
          } catch {
            /* partial frame — wait for more bytes */
          }
        }
        setLast({ role: 'assistant', text, citations, pending: true })
      }
      setLast({ role: 'assistant', text: text || '(empty reply)', citations })
    } catch (e) {
      setLast({ role: 'assistant', text: '', error: String(e) })
    } finally {
      setBusy(false)
    }
  }

  function setLast(msg) {
    setMessages((cur) => [...cur.slice(0, -1), msg])
  }

  const grokbotUp = health?.grokbot_ok

  return (
    <main className="shell">
      <header className="bar">
        <span className="logo">✽ GROKCHAT</span>
        <span className={`dot ${grokbotUp ? 'up' : health ? 'down' : ''}`} title="grokbot on :50890" />
        <span className="sub">{grokbotUp ? 'grokbot connected' : health ? 'grokbot unreachable' : 'checking…'}</span>
        <span className="spacer" />
        <select value={model} onChange={(e) => setModel(e.target.value)}>
          {(models.length ? models : [model]).map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
        <button className={`pill ${search ? 'on' : ''}`} onClick={() => setSearch(!search)}>
          LIVE SEARCH {search ? 'ON' : 'OFF'}
        </button>
        <button className={`pill ${xaiKey ? 'on' : ''}`} onClick={() => setShowKey(!showKey)}>
          KEY {xaiKey ? 'SET' : 'NONE'}
        </button>
      </header>

      {showKey && (
        <div className="keyrow">
          <input
            type="password"
            placeholder="xai-… (stays in this browser, sent per-request, stored nowhere server-side)"
            value={xaiKey}
            onChange={(e) => saveKey(e.target.value)}
          />
          <span className="hint">
            No key? grokbot falls back to the operator key if one is configured.
            Get one at console.x.ai.
          </span>
        </div>
      )}

      <section className="log">
        {messages.length === 0 && (
          <div className="empty">
            <p>Chat with Grok through the grokbot module.</p>
            <p className="hint">Pick a model, toggle live search over X and the web, paste your own xAI key under KEY.</p>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div className="who">{m.role === 'user' ? 'YOU' : model}</div>
            <div className="text">
              {m.error ? <span className="err">{m.error}</span> : m.text}
              {m.pending && <span className="cursor">▌</span>}
            </div>
            {m.citations?.length > 0 && (
              <div className="cites">
                {m.citations.map((c, j) => (
                  <a key={j} href={c} target="_blank" rel="noreferrer">[{j + 1}] {shortUrl(c)}</a>
                ))}
              </div>
            )}
          </div>
        ))}
        <div ref={endRef} />
      </section>

      <footer className="composer">
        <textarea
          value={input}
          placeholder={busy ? 'streaming…' : 'ask grok'}
          disabled={busy}
          rows={2}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              send()
            }
          }}
        />
        <button className="send" onClick={send} disabled={busy || !input.trim()}>
          {busy ? '…' : 'SEND'}
        </button>
      </footer>
    </main>
  )
}

function shortUrl(u) {
  try {
    return new URL(u).hostname.replace(/^www\./, '')
  } catch {
    return u.slice(0, 40)
  }
}
