// The app's backend is api.py (a loopback REST server, default :8940). Next
// used to reach it through a config rewrite, which turned a stopped backend
// into a bare "Internal Server Error" page -- the module looked broken when it
// was only unstarted.
//
// This handler forwards the same calls, but if the backend is not listening it
// starts it and waits for it to come up. Opening the page is enough to bring
// the module online; a failure that is not self-healing comes back as JSON the
// UI can explain instead of a 500.
//
// It also carries GET, which the rewrite never needed: /api/mcp answers the MCP
// schema on GET and the JSON-RPC transport on POST, and an agent pointed at the
// app's origin has to reach both.

import { spawn } from 'child_process'
import fs from 'fs'
import path from 'path'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

const ORIGIN = process.env.MONERO_API_ORIGIN || 'http://127.0.0.1:8940'
const LOG_DIR = '/tmp/monero'

// Where api.py lives. Normally next runs with the app directory as cwd and the
// module is one level up, but the app is started by several different
// supervisors, so check the plausible roots instead of assuming one.
function moduleDir(): string | null {
  const candidates = [
    process.env.MONERO_MODULE_DIR,
    path.resolve(process.cwd(), '..'),
    process.cwd(),
  ].filter(Boolean) as string[]
  for (const dir of candidates) {
    if (fs.existsSync(path.join(dir, 'api.py'))) return dir
  }
  return null
}

const LOOPBACK = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:|\/|$)/

async function alive(timeoutMs = 1500): Promise<boolean> {
  try {
    const r = await fetch(`${ORIGIN}/health`, {
      cache: 'no-store',
      signal: AbortSignal.timeout(timeoutMs),
    })
    return r.ok
  } catch {
    return false
  }
}

// Only one start attempt at a time, however many requests arrive at once.
let starting: Promise<boolean> | null = null

// The backend is normally supervised as pm2 `monero-api`. Spawning our own
// api.py alongside it makes two owners race for :8940 -- the loser exits on
// bind and pm2 restarts it straight into the same race. Ask the supervisor to
// do it and only spawn when there is no entry.
const PM2_APP = 'monero-api'

function pm2Restart(): Promise<boolean> {
  return new Promise(resolve => {
    let p
    try {
      // No --update-env: this process's environment has the *app's* PORT in
      // it, and handing that to api.py makes it bind :50691 and crash-loop.
      p = spawn('pm2', ['restart', PM2_APP], { stdio: 'ignore' })
    } catch {
      return resolve(false)
    }
    p.on('error', () => resolve(false))
    p.on('exit', code => resolve(code === 0))
  })
}

function launch(): Promise<boolean> {
  return (async () => {
    // Never try to start something we do not own: a remote origin is somebody
    // else's server.
    if (!LOOPBACK.test(ORIGIN)) return false
    const dir = moduleDir()
    if (!dir) return false

    if (await pm2Restart()) {
      for (let i = 0; i < 40; i++) {
        await new Promise(r => setTimeout(r, 500))
        if (await alive(800)) return true
      }
      return false
    }

    const port = new URL(ORIGIN).port || '8940'
    let out: number | 'ignore' = 'ignore'
    try {
      fs.mkdirSync(LOG_DIR, { recursive: true })
      out = fs.openSync(path.join(LOG_DIR, 'rest.log'), 'a')
    } catch { /* logging is best effort */ }

    const child = spawn('python3', ['api.py'], {
      cwd: dir,
      detached: true,
      stdio: ['ignore', out, out],
      env: { ...process.env, PORT: port, MONERO_REST_PORT: port, PYTHONPATH: dir },
    })
    child.on('error', () => { /* reported below as "did not come up" */ })
    child.unref()

    for (let i = 0; i < 40; i++) {          // up to ~20s: uvicorn + first import
      await new Promise(r => setTimeout(r, 500))
      if (await alive(800)) return true
    }
    return false
  })()
}

async function ensureBackend(): Promise<boolean> {
  if (await alive()) return true
  if (!starting) starting = launch().finally(() => { starting = null })
  return starting
}

const DOWN = {
  error:
    'The monero backend (api.py) is not running and could not be started here. ' +
    'Start it with `m monero/serve`, or check /tmp/monero/rest.log.',
}

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || ''

async function proxy(req: Request, fn: string, method: 'GET' | 'POST') {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const auth = req.headers.get('authorization')
  if (auth) headers['Authorization'] = auth
  // Tell the backend how the caller actually reached us. Without this it sees
  // its own loopback Host and hands MCP clients `http://127.0.0.1:8940/mcp`,
  // an address that only exists inside this box -- the one field of the MCP
  // schema a remote agent cannot work out for itself.
  const url = new URL(req.url)
  headers['x-forwarded-host'] =
    req.headers.get('x-forwarded-host') || req.headers.get('host') || url.host
  headers['x-forwarded-proto'] =
    req.headers.get('x-forwarded-proto') || url.protocol.replace(':', '')
  headers['x-forwarded-prefix'] =
    (req.headers.get('x-forwarded-prefix') || '') + `${BASE_PATH}/api`
  const body = method === 'POST' ? await req.text() : undefined
  const query = url.search

  const send = () =>
    fetch(`${ORIGIN}/${fn}${query}`, {
      method,
      headers,
      body: body || (method === 'POST' ? '{}' : undefined),
      cache: 'no-store',
      // A view-key scan can legitimately run for minutes; the browser client
      // gives up on its own schedule and this must not cut it short first.
      signal: AbortSignal.timeout(300_000),
    })

  let res: Response
  try {
    res = await send()
  } catch {
    if (!(await ensureBackend())) {
      return Response.json(DOWN, { status: 503 })
    }
    try {
      res = await send()
    } catch (e: any) {
      return Response.json(
        { error: `monero backend did not answer: ${e?.message || e}` },
        { status: 504 },
      )
    }
  }

  const text = await res.text()
  return new Response(text, {
    status: res.status,
    headers: { 'Content-Type': res.headers.get('content-type') || 'application/json' },
  })
}

// A catch-all rather than a single [fn]: the MCP surface is two segments deep
// (/mcp/tools, /mcp/config), and a one-segment route would 404 them.
export async function POST(req: Request, ctx: { params: { fn: string[] } }) {
  return proxy(req, (ctx.params.fn || []).join('/'), 'POST')
}

export async function GET(req: Request, ctx: { params: { fn: string[] } }) {
  return proxy(req, (ctx.params.fn || []).join('/'), 'GET')
}
