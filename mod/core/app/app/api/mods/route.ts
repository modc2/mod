import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'

const CONFIG_PATH = path.join(process.cwd(), 'app', 'config', 'mods.json')
const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

function readCache(): any[] {
  try {
    if (fs.existsSync(CONFIG_PATH)) {
      return JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8'))
    }
  } catch {}
  return []
}

function writeCache(mods: any[]) {
  try {
    const dir = path.dirname(CONFIG_PATH)
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true })
    fs.writeFileSync(CONFIG_PATH, JSON.stringify(mods, null, 2))
  } catch (e) {
    console.error('[mods-cache] write error:', e)
  }
}

/** GET /api/mods — return cached mods from config/mods.json */
export async function GET() {
  return NextResponse.json(readCache())
}

/** POST /api/mods — fetch from backend, save to config/mods.json, return */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    const { token } = body

    const res = await fetch(`${API_URL}/mods`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { token } : {}),
      },
      body: JSON.stringify({ page: 0, page_size: 1000 }),
    })

    if (res.ok) {
      let data = await res.json()
      if (data?.result !== undefined) data = data.result
      const mods = Array.isArray(data) ? data : []
      writeCache(mods)
      return NextResponse.json(mods)
    }
  } catch (e) {
    console.error('[mods-cache] sync error:', e)
  }

  return NextResponse.json(readCache())
}
