import { NextRequest, NextResponse } from 'next/server'

/**
 * The chain console's bridge to the chain module's API (:8800).
 *
 * The chain module isn't in the app namespace (it has no public app of its
 * own), so middleware can't route it — this handler bridges the console to it.
 * Compiles and fleet deploys are slow on purpose: no timeout here.
 */

const CHAIN_API = process.env.CHAIN_API_URL || 'http://localhost:8800'

export async function proxyToChainApi(request: NextRequest, path: string[]) {
  const target = `${CHAIN_API}/${path.join('/')}${request.nextUrl.search}`
  const body = request.method === 'GET' || request.method === 'DELETE'
    ? undefined
    : await request.text()

  try {
    const res = await fetch(target, {
      method: request.method,
      headers: { 'Content-Type': 'application/json' },
      body,
      cache: 'no-store',
    })
    const text = await res.text()
    return new NextResponse(text, {
      status: res.status,
      headers: { 'Content-Type': res.headers.get('Content-Type') || 'application/json' },
    })
  } catch (e: any) {
    return NextResponse.json(
      { detail: `chain api unreachable at ${CHAIN_API}: ${e?.message || e}` },
      { status: 502 },
    )
  }
}
