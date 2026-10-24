import { readFile, stat } from 'fs/promises'
import path from 'path'

// The Yuma subnet paper — yuma/whitepaper.md, the same file GET
// /yuma/whitepaper on the API serves. Read off disk per request, like /paper.
export const dynamic = 'force-dynamic'

const PAPER = path.join(process.cwd(), '..', 'yuma', 'whitepaper.md')

export async function GET() {
  try {
    const [markdown, s] = await Promise.all([readFile(PAPER, 'utf8'), stat(PAPER)])
    return Response.json({ markdown, updated: Math.floor(s.mtimeMs / 1000) })
  } catch {
    return Response.json({ detail: 'yuma/whitepaper.md missing' }, { status: 404 })
  }
}
