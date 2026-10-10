import { NextRequest, NextResponse } from 'next/server'
import { callBridge } from '@/lib/python-bridge'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export async function POST(req: NextRequest) {
  const form = await req.formData().catch(() => null)
  if (!form) {
    return NextResponse.json({ ok: false, error: 'expected multipart/form-data' }, { status: 400 })
  }
  const file = form.get('file')
  const pinValue = form.get('pin')
  const pin = pinValue === null ? true : String(pinValue) !== 'false'

  if (!(file instanceof File)) {
    return NextResponse.json({ ok: false, error: 'missing file field' }, { status: 400 })
  }

  const buf = Buffer.from(await file.arrayBuffer())
  const result = await callBridge<{ cid: string }>('put', {
    data_b64: buf.toString('base64'),
    pin,
  })

  if (!result.ok) {
    return NextResponse.json(result, { status: 500 })
  }
  return NextResponse.json({
    ok: true,
    data: { ...result.data, name: file.name, size: buf.length },
  })
}
