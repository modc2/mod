import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8830'

export async function GET() {
  try {
    const res = await fetch(`${API_URL}/markets`, { cache: 'no-store' })
    const data = await res.json()
    return NextResponse.json(data)
  } catch {
    return NextResponse.json([], { status: 200 })
  }
}

export async function POST(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url)
    const asset = searchParams.get('asset') || 'ETH/USD'
    const token = searchParams.get('token_address') || '0x0000000000000000000000000000000000000000'
    const duration = searchParams.get('duration') || '86400'

    const res = await fetch(
      `${API_URL}/markets?asset=${encodeURIComponent(asset)}&token_address=${encodeURIComponent(token)}&duration=${duration}`,
      { method: 'POST' }
    )
    const data = await res.json()
    return NextResponse.json(data)
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 })
  }
}
