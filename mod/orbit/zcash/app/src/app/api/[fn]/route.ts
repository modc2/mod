// The app's backend is api.py (a loopback REST server, default :8930). Next
// used to reach it through a config rewrite, which turned a stopped backend
// into a bare "Internal Server Error" page -- the module looked broken when it
// was only unstarted.
//
// This handler forwards the same calls, but if the backend is not listening it
// starts it and waits for it to come up. Opening the page is enough to bring
// the module online; a failure that is not self-healing comes back as JSON the
// UI can explain instead of a 500.

import { spawn } from 'child_process'
import fs from 'fs'
import path from 'path'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

const ORIGIN = process.env.ZCASH_API_ORIGIN || 'http://127.0.0.1:8930'
const LOG_DIR = '/tmp/zcash'
// '' when the app is served standalone, '/zcash' behind the gateway.
const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || ''

// Where api.py lives. Normally next runs with the app directory as cwd and the
// module is one level up, but the app is started by several different
// supervisors, so check the plausible roots instead of assuming one.
function moduleDir(): string | null {
  const candidates = [
    process.env.ZCASH_MODULE_DIR,
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

// The backend is normally supervised as pm2 `zcash-api`. Spawning our own
// api.py alongside it makes two owners race for :8930 -- the loser exits on
// bind and pm2 restarts it straight into the same race (seen crash-looping 26
// times). Ask the supervisor to do it and only spawn when there is no entry.
const PM2_APP = 'zcash-api'

function pm2Restart(): Promise<boolean> {
  return new Promise(resolve => {
    let p
    try {
      // No --update-env: this process's environment has the *app's* PORT in
      // it, and handing that to api.py makes it bind :50149 and crash-loop.
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

    const port = new URL(ORIGIN).port || '8930'
    let out: number | 'ignore' = 'ignore'
    try {
      fs.mkdirSync(LOG_DIR, { recursive: true })
      out = fs.openSync(path.join(LOG_DIR, 'rest.log'), 'a')
    } catch { /* logging is best effort */ }

    const child = spawn('python3', ['api.py'], {
      cwd: dir,
      detached: true,
      stdio: ['ignore', out, out],
      env: { ...process.env, PORT: port, ZCASH_REST_PORT: port, PYTHONPATH: dir },
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
    'The zcash backend (api.py) is not running and could not be started here. ' +
    'Start it with `m zcash/serve`, or check /tmp/zcash/rest.log.',
}

async function proxy(req: Request, fn: string, method: 'GET' | 'POST') {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const auth = req.headers.get('authorization')
  if (auth) headers['Authorization'] = auth

  // Tell the backend how the caller actually reached it. The MCP schema names
  // its own endpoint, and a client sent to 127.0.0.1:8930 when it arrived at
  // https://host/zcash/api/mcp would be pointed at a port it cannot see.
  const url = new URL(req.url)
  const fwdHost = req.headers.get('x-forwarded-host') || req.headers.get('host')
  if (fwdHost) headers['x-forwarded-host'] = fwdHost
  headers['x-forwarded-proto'] =
    req.headers.get('x-forwarded-proto') || url.protocol.replace(':', '')
  headers['x-forwarded-prefix'] = `${BASE_PATH}/api`

  const body = method === 'POST' ? await req.text() : undefined

  const send = () =>
    fetch(`${ORIGIN}/${fn}`, {
      method,
      headers,
      body: body || (method === 'POST' ? '{}' : undefined),
      cache: 'no-store',
      signal: AbortSignal.timeout(60_000),
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
        { error: `zcash backend did not answer: ${e?.message || e}` },
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

export async function POST(req: Request, ctx: { params: { fn: string } }) {
  return proxy(req, ctx.params.fn, 'POST')
}

export async function GET(req: Request, ctx: { params: { fn: string } }) {
  return proxy(req, ctx.params.fn, 'GET')
}
