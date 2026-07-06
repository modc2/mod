import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'

const MOD_BASE = path.join(process.env.HOME || '', 'mod', 'mod')

function findModPy(name: string): string | null {
  const candidates = [
    path.join(MOD_BASE, 'orbit', name, name, 'mod.py'),
    path.join(MOD_BASE, 'orbit', name, 'src', 'mod.py'),
    path.join(MOD_BASE, 'orbit', name, 'mod.py'),
  ]
  for (const p of candidates) {
    if (fs.existsSync(p)) return p
  }
  return null
}

/**
 * GET /api/mod-code         → list orbit modules with mod.py
 * GET /api/mod-code?name=X  → return mod.py code for module X
 */
export async function GET(request: NextRequest) {
  const name = request.nextUrl.searchParams.get('name')

  if (!name) {
    // List orbit modules
    const orbitDir = path.join(MOD_BASE, 'orbit')
    try {
      const entries = fs.readdirSync(orbitDir, { withFileTypes: true })
      const modules = entries
        .filter(e => e.isDirectory() && !e.name.startsWith('.'))
        .map(e => ({ name: e.name, has_mod: findModPy(e.name) !== null }))
        .sort((a, b) => a.name.localeCompare(b.name))
      return NextResponse.json({ modules })
    } catch {
      return NextResponse.json({ modules: [] })
    }
  }

  // Validate — only allow alphanumeric, dash, underscore
  if (/[^a-zA-Z0-9_-]/.test(name)) {
    return NextResponse.json({ error: 'Invalid module name' }, { status: 400 })
  }

  const modPath = findModPy(name)
  if (!modPath) {
    return NextResponse.json({ error: 'mod.py not found' }, { status: 404 })
  }

  const code = fs.readFileSync(modPath, 'utf-8')
  const relPath = path.relative(MOD_BASE, modPath)
  return NextResponse.json({ code, path: relPath, name })
}
