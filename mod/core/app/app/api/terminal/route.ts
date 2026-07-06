import { NextResponse } from 'next/server'

let pty: any = null
try {
  pty = require('node-pty')
} catch {
  // node-pty not available — terminal disabled
}

interface Session {
  pty: any
  buffer: string
  lastAccess: number
}

const sessions = new Map<string, Session>()

// Clean up stale sessions every 5 minutes
setInterval(() => {
  const now = Date.now()
  for (const [id, session] of sessions) {
    if (now - session.lastAccess > 10 * 60 * 1000) {
      try { session.pty.kill() } catch {}
      sessions.delete(id)
    }
  }
}, 5 * 60 * 1000)

function genId(): string {
  return Math.random().toString(36).slice(2, 10) + Date.now().toString(36)
}

export async function POST(request: Request) {
  if (!pty) {
    return NextResponse.json({ error: 'Terminal not available (node-pty not installed)' }, { status: 501 })
  }

  try {
    const body = await request.json()
    const { action } = body

    if (action === 'create') {
      const { cwd, cols = 80, rows = 24 } = body
      const shell = process.env.SHELL || '/bin/bash'
      const id = genId()

      const term = pty.spawn(shell, [], {
        name: 'xterm-256color',
        cols,
        rows,
        cwd: cwd || process.env.HOME || '/tmp',
        env: { ...process.env, TERM: 'xterm-256color' },
      })

      const session: Session = { pty: term, buffer: '', lastAccess: Date.now() }

      term.onData((data: string) => {
        session.buffer += data
      })

      term.onExit(() => {
        sessions.delete(id)
      })

      sessions.set(id, session)

      // Give shell a moment to print prompt
      await new Promise(r => setTimeout(r, 150))
      const output = session.buffer
      session.buffer = ''

      return NextResponse.json({ sessionId: id, output })
    }

    if (action === 'exec') {
      const { sessionId, command } = body
      const session = sessions.get(sessionId)
      if (!session) return NextResponse.json({ error: 'Session not found' }, { status: 404 })
      session.lastAccess = Date.now()
      session.pty.write(command)
      return NextResponse.json({ ok: true })
    }

    if (action === 'poll') {
      const { sessionId } = body
      const session = sessions.get(sessionId)
      if (!session) return NextResponse.json({ error: 'Session not found' }, { status: 404 })
      session.lastAccess = Date.now()
      const output = session.buffer
      session.buffer = ''
      return NextResponse.json({ output })
    }

    if (action === 'resize') {
      const { sessionId, cols, rows } = body
      const session = sessions.get(sessionId)
      if (!session) return NextResponse.json({ error: 'Session not found' }, { status: 404 })
      session.lastAccess = Date.now()
      try { session.pty.resize(cols, rows) } catch {}
      return NextResponse.json({ ok: true })
    }

    if (action === 'destroy') {
      const { sessionId } = body
      const session = sessions.get(sessionId)
      if (session) {
        try { session.pty.kill() } catch {}
        sessions.delete(sessionId)
      }
      return NextResponse.json({ ok: true })
    }

    return NextResponse.json({ error: 'Unknown action' }, { status: 400 })
  } catch (error: any) {
    console.error('Terminal API error:', error)
    return NextResponse.json({ error: error.message || 'Terminal error' }, { status: 500 })
  }
}
