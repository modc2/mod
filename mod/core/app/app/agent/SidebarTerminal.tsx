"use client"

import { useEffect, useRef, useState, useCallback } from 'react'

interface SidebarTerminalProps {
  width: number
}

export function SidebarTerminal({ width }: SidebarTerminalProps) {
  const termRef = useRef<HTMLDivElement>(null)
  const xtermRef = useRef<any>(null)
  const sessionRef = useRef<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const fitAddonRef = useRef<any>(null)
  const [ready, setReady] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const apiCall = useCallback(async (body: any) => {
    const res = await fetch('/api/terminal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    return res.json()
  }, [])

  useEffect(() => {
    let destroyed = false

    async function init() {
      if (!termRef.current) return

      const { Terminal } = await import('@xterm/xterm')
      const { FitAddon } = await import('@xterm/addon-fit')
      const { WebLinksAddon } = await import('@xterm/addon-web-links')

      if (destroyed) return

      // Inject xterm CSS if not already present
      if (!document.getElementById('xterm-css')) {
        const link = document.createElement('link')
        link.id = 'xterm-css'
        link.rel = 'stylesheet'
        link.href = 'https://cdn.jsdelivr.net/npm/@xterm/xterm@5/css/xterm.min.css'
        document.head.appendChild(link)
        await new Promise((r) => { link.onload = r; setTimeout(r, 500) })
      }

      const fitAddon = new FitAddon()
      fitAddonRef.current = fitAddon

      const term = new Terminal({
        cursorBlink: true,
        fontSize: 12,
        fontFamily: 'JetBrains Mono, Menlo, Monaco, Consolas, monospace',
        theme: {
          background: '#0a0a0a',
          foreground: '#e0e0e0',
          cursor: '#10b981',
          selectionBackground: '#333333',
          black: '#0a0a0a',
          red: '#ff5555',
          green: '#50fa7b',
          yellow: '#f1fa8c',
          blue: '#6272a4',
          magenta: '#ff79c6',
          cyan: '#8be9fd',
          white: '#e0e0e0',
          brightBlack: '#555555',
          brightRed: '#ff6e6e',
          brightGreen: '#69ff94',
          brightYellow: '#ffffa5',
          brightBlue: '#d6acff',
          brightMagenta: '#ff92df',
          brightCyan: '#a4ffff',
          brightWhite: '#ffffff',
        },
        allowProposedApi: true,
      })

      term.loadAddon(fitAddon)
      term.loadAddon(new WebLinksAddon())
      term.open(termRef.current)
      fitAddon.fit()
      xtermRef.current = term

      const basePath = process.env.NEXT_PUBLIC_MOD_PATH || '/Users/broski/mod/mod'

      try {
        const result = await apiCall({
          action: 'create',
          cwd: basePath,
          cols: term.cols,
          rows: term.rows,
        })

        if (result.error) {
          setError(result.error)
          term.writeln(`\r\n\x1b[31mError: ${result.error}\x1b[0m`)
          return
        }

        sessionRef.current = result.sessionId

        term.writeln(`\x1b[90m# terminal — ${basePath}\x1b[0m`)
        term.writeln('')

        if (result.output) {
          term.write(result.output)
        }

        setReady(true)

        term.onData((data: string) => {
          if (!sessionRef.current) return

          if (data === '\x04') {
            apiCall({
              action: 'destroy',
              sessionId: sessionRef.current,
            })
            term.writeln('\r\n\x1b[90m[session ended]\x1b[0m')
            sessionRef.current = null
            return
          }

          apiCall({
            action: 'exec',
            sessionId: sessionRef.current,
            command: data,
          }).then((res: any) => {
            if (res?.blocked) {
              term.writeln(`\r\n\x1b[31m✗ ${res.reason}\x1b[0m`)
            } else if (res?.output) {
              term.write(res.output)
            }
          })
        })

        pollRef.current = setInterval(async () => {
          if (!sessionRef.current) return
          try {
            const res = await apiCall({
              action: 'poll',
              sessionId: sessionRef.current,
            })
            if (res.output) {
              term.write(res.output)
            }
          } catch {
            // Ignore poll errors
          }
        }, 300)
      } catch (err: any) {
        setError(err.message)
        term.writeln(`\r\n\x1b[31mFailed to start terminal: ${err.message}\x1b[0m`)
      }
    }

    init()

    return () => {
      destroyed = true
      if (pollRef.current) clearInterval(pollRef.current)
      if (sessionRef.current) {
        apiCall({ action: 'destroy', sessionId: sessionRef.current })
      }
      if (xtermRef.current) {
        xtermRef.current.dispose()
        xtermRef.current = null
      }
    }
  }, [apiCall])

  // Refit when width changes
  useEffect(() => {
    if (fitAddonRef.current && xtermRef.current) {
      setTimeout(() => {
        fitAddonRef.current.fit()
        if (sessionRef.current) {
          apiCall({
            action: 'resize',
            sessionId: sessionRef.current,
            cols: xtermRef.current.cols,
            rows: xtermRef.current.rows,
          })
        }
      }, 100)
    }
  }, [width, apiCall])

  // Handle window resize
  useEffect(() => {
    const handleResize = () => {
      if (fitAddonRef.current) {
        fitAddonRef.current.fit()
        if (sessionRef.current && xtermRef.current) {
          apiCall({
            action: 'resize',
            sessionId: sessionRef.current,
            cols: xtermRef.current.cols,
            rows: xtermRef.current.rows,
          })
        }
      }
    }
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [apiCall])

  return (
    <div className="flex-1 flex flex-col overflow-hidden" style={{ backgroundColor: '#0a0a0a' }}>
      {/* Status bar */}
      <div
        className="flex items-center justify-between px-3 py-1 shrink-0"
        style={{
          borderBottom: '1px solid var(--border-color)',
          backgroundColor: 'var(--bg-surface)',
        }}
      >
        <div className="flex items-center gap-2">
          <span
            className="text-[10px] font-bold uppercase tracking-wider"
            style={{ color: '#10b981' }}
          >
            terminal
          </span>
        </div>
        {ready && (
          <div className="flex items-center gap-2">
            <span
              className="inline-block w-1.5 h-1.5 rounded-full"
              style={{ background: '#10b981', boxShadow: '0 0 4px #10b981' }}
            />
            <span
              className="text-[10px] uppercase tracking-wider"
              style={{ color: 'var(--text-tertiary)' }}
            >
              connected
            </span>
          </div>
        )}
        {error && (
          <span className="text-[10px]" style={{ color: '#ef4444' }}>{error}</span>
        )}
      </div>

      {/* Terminal body */}
      <div
        ref={termRef}
        className="flex-1"
        style={{
          padding: '4px',
          minHeight: 0,
        }}
      />
    </div>
  )
}
