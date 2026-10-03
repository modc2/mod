import { readFile, stat } from 'fs/promises'
import path from 'path'

// The whitepaper straight off disk — the module root's whitepaper.md, the
// same file GET /whitepaper on the API serves. Served by the app itself so
// reading the paper never queues behind the API's chain RPC calls.
export const dynamic = 'force-dynamic'

const PAPER = path.join(process.cwd(), '..', 'whitepaper.md')

export async function GET() {
  try {
    const [markdown, s] = await Promise.all([readFile(PAPER, 'utf8'), stat(PAPER)])
    return Response.json({ markdown, updated: Math.floor(s.mtimeMs / 1000) })
  } catch {
    return Response.json({ detail: 'whitepaper.md missing' }, { status: 404 })
  }
}
