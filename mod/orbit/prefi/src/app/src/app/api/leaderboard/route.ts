import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8830'

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url)
    const marketId = searchParams.get('market_id')
    const url = marketId
      ? `${API_URL}/leaderboard?market_id=${marketId}`
      : `${API_URL}/leaderboard`

    const res = await fetch(url, { cache: 'no-store' })
    const data = await res.json()
    return NextResponse.json(data)
  } catch {
    return NextResponse.json([], { status: 200 })
  }
}
