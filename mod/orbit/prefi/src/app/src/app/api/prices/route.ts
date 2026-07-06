import { NextResponse } from 'next/server'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8830'

export async function GET() {
  try {
    const res = await fetch(`${API_URL}/prices`, { cache: 'no-store' })
    const data = await res.json()
    return NextResponse.json(data)
  } catch {
    // Fallback: fetch directly from CoinGecko
    try {
      const res = await fetch(
        'https://api.coingecko.com/api/v3/simple/price?ids=ethereum,bitcoin&vs_currencies=usd&include_24hr_change=true',
        { cache: 'no-store' }
      )
      const data = await res.json()
      return NextResponse.json({
        'ETH/USD': {
          price: data.ethereum?.usd || 0,
          change_24h: data.ethereum?.usd_24h_change || 0,
        },
        'BTC/USD': {
          price: data.bitcoin?.usd || 0,
          change_24h: data.bitcoin?.usd_24h_change || 0,
        },
        timestamp: new Date().toISOString(),
      })
    } catch {
      return NextResponse.json({ error: 'Price fetch failed' }, { status: 500 })
    }
  }
}
