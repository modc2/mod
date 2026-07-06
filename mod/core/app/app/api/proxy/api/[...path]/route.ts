import { NextRequest, NextResponse } from 'next/server'

// Server-side only — always hit the local backend directly
const API_URL = process.env.API_URL || 'http://localhost:8000'

async function proxy(request: NextRequest) {
  const path = request.nextUrl.pathname.replace(/^\/api\/proxy\/api/, '')
  const target = `${API_URL}${path}`

  const headers: Record<string, string> = {
    'Content-Type': request.headers.get('Content-Type') || 'application/json',
  }
  const token = request.headers.get('token')
  if (token) headers['token'] = token

  try {
    const res = await fetch(target, {
      method: request.method,
      headers,
      body: request.method !== 'GET' ? await request.text() : undefined,
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
    return NextResponse.json({ error: e.message }, { status: 502 })
  }
}

export const GET = proxy
export const POST = proxy
export const PUT = proxy
export const DELETE = proxy
