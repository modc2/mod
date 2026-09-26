import { NextRequest, NextResponse } from 'next/server'
import { callBridge } from '@/lib/python-bridge'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

const ALLOWED = new Set([
  'put',
  'get',
  'cat',
  'rm',
  'pin_add',
  'pin_rm',
  'pins',
  'pinned',
  'stats',
  'gc',
  'add_file',
])

export async function POST(req: NextRequest) {
  let body: { method?: string; args?: Record<string, unknown> } = {}
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ ok: false, error: 'invalid json body' }, { status: 400 })
  }

  const method = body.method
  if (!method || !ALLOWED.has(method)) {
    return NextResponse.json(
      { ok: false, error: `method not allowed: ${method ?? '(missing)'}` },
      { status: 400 },
    )
  }

  const result = await callBridge(method, body.args ?? {})
  return NextResponse.json(result, { status: result.ok ? 200 : 500 })
}
