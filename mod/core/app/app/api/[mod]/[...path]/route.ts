import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'

const MODS_PATH = path.join(process.cwd(), 'app', 'config', 'mods.json')

type ModEntry = {
  name?: string
  url?: string | { api?: string; app?: string } | null
}

function resolveApiUrl(mod: string): string | null {
  if (!fs.existsSync(MODS_PATH)) return null
  let mods: ModEntry[]
  try {
    mods = JSON.parse(fs.readFileSync(MODS_PATH, 'utf-8'))
  } catch {
    return null
  }
  const entry = mods.find((m) => m.name === mod)
  if (!entry || !entry.url) return null
  if (typeof entry.url === 'string') return entry.url
  return entry.url.api || null
}

async function proxy(
  request: NextRequest,
  context: { params: { mod: string; path?: string[] } }
) {
  const { mod, path: rest = [] } = context.params

  const apiUrl = resolveApiUrl(mod)
  if (!apiUrl) {
    return NextResponse.json(
      { error: `Unknown mod '${mod}' or no api url configured` },
      { status: 404 }
    )
  }

  const subPath = rest.length ? '/' + rest.join('/') : ''
  const target = `${apiUrl.replace(/\/$/, '')}${subPath}${request.nextUrl.search}`

  const headers: Record<string, string> = {}
  const ct = request.headers.get('Content-Type')
  if (ct) headers['Content-Type'] = ct
  const auth = request.headers.get('Authorization')
  if (auth) headers['Authorization'] = auth
  const token = request.headers.get('token')
  if (token) headers['token'] = token

  try {
    const res = await fetch(target, {
      method: request.method,
      headers,
      body:
        request.method === 'GET' || request.method === 'HEAD'
          ? undefined
          : await request.text(),
    })

    const contentType = res.headers.get('Content-Type') || ''

    if (contentType.includes('text/event-stream')) {
      return new NextResponse(res.body, {
        status: res.status,
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          'Connection': 'keep-alive',
        },
      })
    }

    const data = await res.text()
    return new NextResponse(data, {
      status: res.status,
      headers: { 'Content-Type': contentType },
    })
  } catch (e: any) {
    return NextResponse.json(
      { error: `Proxy to ${target} failed: ${e.message}` },
      { status: 502 }
    )
  }
}

export const GET = proxy
export const POST = proxy
export const PUT = proxy
export const DELETE = proxy
export const PATCH = proxy
