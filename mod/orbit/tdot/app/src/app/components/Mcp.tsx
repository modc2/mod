'use client'

import { useEffect, useMemo, useState } from 'react'
import { api, type MapAction, type McpInfo, type McpSchema, type McpTool } from '@/lib/api'

type Props = {
  /** Tools that move the map do it here, to the map behind this panel. */
  onAction: (action: MapAction) => void
  onClose: () => void
}

/**
 * The MCP server, made visible.
 *
 * The module has always spoken MCP, but only into a pipe — you had to already
 * know it was there. This panel is the server's front door: what it is, how to
 * connect a client to it, every tool it publishes, and a runner that calls
 * those tools over the *real* `/mcp` endpoint rather than a private path. So
 * what you try here is exactly what an outside client gets.
 */
export default function Mcp({ onAction, onClose }: Props) {
  const [info, setInfo] = useState<McpInfo | null>(null)
  const [schema, setSchema] = useState<McpSchema | null>(null)
  const [config, setConfig] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([api.mcpInfo(), api.mcpSchema(), api.mcpConfig()])
      .then(([i, s, c]) => {
        setInfo(i)
        setSchema(s)
        setConfig(JSON.stringify(c, null, 2))
      })
      .catch((e) => setError(String(e.message ?? e)))
  }, [])

  const groups = useMemo(() => {
    const term = q.trim().toLowerCase()
    const hits = (schema?.tools ?? []).filter(
      (t) => !term || t.name.toLowerCase().includes(term) ||
             t.description.toLowerCase().includes(term))
    const by = new Map<string, McpTool[]>()
    for (const t of hits) {
      const g = t.group || 'Tools'
      by.set(g, [...(by.get(g) ?? []), t])
    }
    return [...by.entries()]
  }, [schema, q])

  const shown = groups.reduce((n, [, ts]) => n + ts.length, 0)

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-start gap-2 border-b border-line px-3.5 py-2.5">
        <div className="min-w-0 flex-1">
          <h2 className="flex items-center gap-1.5 text-[12.5px] font-semibold leading-tight text-ink">
            MCP server
            <span className={`h-1.5 w-1.5 rounded-full ${info ? 'bg-good' : 'bg-muted'}`}
                  title={info ? 'reachable' : 'connecting'} />
          </h2>
          <p className="truncate text-[10px] leading-tight text-muted">
            {info
              ? `${info.server.name} ${info.server.version} · protocol ${info.protocolVersion} · ${info.tools} tools`
              : 'connecting…'}
          </p>
        </div>
        <button onClick={onClose} aria-label="Close"
                className="rounded-ctl p-1 text-muted hover:bg-fill-hover hover:text-ink">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none">
            <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5"
                  strokeLinecap="round" />
          </svg>
        </button>
      </header>

      <div className="flex-1 overflow-y-auto">
        {error && (
          <p className="m-2.5 rounded-ctl bg-inset px-2.5 py-1.5 text-[11.5px] text-bad">{error}</p>
        )}

        {info && (
          <section className="border-b border-line px-3 py-2.5">
            <p className="text-[11.5px] leading-relaxed text-ink-2">
              Every tool the Ask panel plays is also published over the Model
              Context Protocol, so any MCP client can drive this map.
            </p>
            <div className="mt-2 space-y-1.5">
              <Snippet label="Claude Code · HTTP"
                       value={`claude mcp add --transport http tdot ${info.transports.http}`} />
              <Snippet label="Claude Code · stdio"
                       value={`claude mcp add tdot -- ${info.transports.stdio}`} />
              <Snippet label="mcpServers config" value={config} multiline />
            </div>
          </section>
        )}

        <div className="sticky top-0 z-10 border-b border-line bg-surface px-2.5 py-2">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={`Filter ${schema?.count ?? ''} tools…`}
            className="w-full rounded-ctl bg-inset px-2.5 py-1.5 text-[12px] text-ink placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent"
          />
          {q && (
            <p className="px-0.5 pt-1 text-[10px] text-muted">
              {shown} of {schema?.count} tools
            </p>
          )}
        </div>

        {groups.map(([group, ts]) => (
          <section key={group}>
            <h3 className="px-3 pb-1 pt-2.5 text-[9.5px] font-semibold uppercase tracking-wider text-muted">
              {group}
            </h3>
            <ul>
              {ts.map((t) => (
                <ToolRow
                  key={t.name}
                  tool={t}
                  open={open === t.name}
                  onToggle={() => setOpen((o) => (o === t.name ? null : t.name))}
                  onAction={onAction}
                />
              ))}
            </ul>
          </section>
        ))}

        {schema && !groups.length && (
          <p className="px-3 py-3 text-[11.5px] text-muted">No tool matches “{q}”.</p>
        )}
      </div>
    </div>
  )
}

/**
 * One tool: its description, its parameters, and a Run button.
 *
 * The form is generated from the tool's own JSON Schema — the same document the
 * server hands a model — so there is no second copy of the tool list here to
 * fall out of date when a tool gains an argument.
 */
function ToolRow({ tool, open, onToggle, onAction }: {
  tool: McpTool
  open: boolean
  onToggle: () => void
  onAction: (a: MapAction) => void
}) {
  const props = tool.inputSchema?.properties ?? {}
  const required = tool.inputSchema?.required ?? []
  const [args, setArgs] = useState<Record<string, string>>({})
  const [out, setOut] = useState<{ text: string; bad: boolean } | null>(null)
  const [busy, setBusy] = useState(false)

  async function run() {
    setBusy(true)
    setOut(null)
    try {
      // Only send what was filled in: the server's own defaults are better
      // than empty strings, and a blank optional field means "unset".
      const payload: Record<string, any> = {}
      for (const [k, v] of Object.entries(args)) {
        if (v === '') continue
        const t = props[k]?.type
        payload[k] = t === 'number' || t === 'integer' ? Number(v)
          : t === 'boolean' ? v === 'true'
          : v
      }
      const reply = await api.mcpCall(tool.name, payload)
      if (reply.error) throw new Error(reply.error.message)
      const text = reply.result?.content?.[0]?.text ?? ''
      const bad = !!reply.result?.isError

      // Tools that drive the map carry a `__map__` action in their result.
      // Applying it is what makes this a console rather than a JSON viewer.
      if (!bad) {
        try {
          const parsed = JSON.parse(text)
          if (parsed && typeof parsed === 'object' && parsed.__map__) {
            onAction(parsed.__map__ as MapAction)
          }
        } catch {
          // a tool that returns prose rather than JSON simply has no action
        }
      }

      let pretty = text
      try { pretty = JSON.stringify(JSON.parse(text), null, 2) } catch {}
      setOut({ text: pretty, bad })
    } catch (e: any) {
      setOut({ text: String(e.message ?? e), bad: true })
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="border-b border-line/60">
      <button onClick={onToggle}
              className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-fill-hover">
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-1.5 font-mono text-[11.5px] leading-snug text-ink">
            {tool.name}
            {tool.drives_map && (
              <span className="rounded-full bg-accent/15 px-1.5 py-px text-[8.5px] font-sans font-medium uppercase tracking-wide text-accent-soft"
                    title="Changes the map behind this panel">
                map
              </span>
            )}
          </p>
          {!open && (
            <p className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-muted">
              {tool.description}
            </p>
          )}
        </div>
        <span className={`mt-0.5 shrink-0 text-[9px] text-muted transition-transform ${
          open ? 'rotate-90' : ''}`}>▶</span>
      </button>

      {open && (
        <div className="space-y-2 px-3 pb-3">
          <p className="text-[11px] leading-relaxed text-ink-2">{tool.description}</p>

          {Object.entries(props).map(([name, spec]) => (
            <label key={name} className="block">
              <span className="text-[10px] text-muted">
                {name}
                {required.includes(name) && <span className="text-bad"> *</span>}
                {spec.type ? <span className="text-muted/70"> · {spec.type}</span> : null}
              </span>
              <input
                value={args[name] ?? ''}
                onChange={(e) => setArgs((a) => ({ ...a, [name]: e.target.value }))}
                placeholder={spec.default !== undefined ? String(spec.default) : spec.description}
                className="mt-0.5 w-full rounded-ctl bg-inset px-2 py-1 text-[11.5px] text-ink placeholder:text-muted/70 focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </label>
          ))}

          <button
            onClick={run}
            disabled={busy}
            className="rounded-ctl bg-accent px-2.5 py-1 text-[11px] text-accent-ink disabled:opacity-40"
          >
            {busy ? 'Running…' : 'Run'}
          </button>

          {out && (
            <pre className={`max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-ctl bg-inset px-2 py-1.5 text-[10.5px] leading-snug ${
              out.bad ? 'text-bad' : 'text-ink-2'}`}>
              {out.text}
            </pre>
          )}
        </div>
      )}
    </li>
  )
}

/** A copyable command or config block. */
function Snippet({ label, value, multiline }: {
  label: string
  value: string
  multiline?: boolean
}) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } catch {
      // clipboard is origin-gated; the text is selectable either way
    }
  }

  return (
    <div className="rounded-ctl bg-inset px-2 py-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[9.5px] uppercase tracking-wider text-muted">{label}</span>
        <button onClick={copy}
                className="shrink-0 text-[10px] text-muted hover:text-ink">
          {copied ? 'copied' : 'copy'}
        </button>
      </div>
      <pre className={`mt-0.5 overflow-x-auto whitespace-pre-wrap break-all font-mono text-[10px] leading-snug text-ink-2 ${
        multiline ? 'max-h-32 overflow-y-auto' : ''}`}>
        {value}
      </pre>
    </div>
  )
}
