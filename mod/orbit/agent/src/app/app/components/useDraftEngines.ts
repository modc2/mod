'use client'

// The engines a vibe draft can run on beyond this module's own drafter agent:
// the harness CLIs (the build console, Claude Code, …) the signed-in caller
// is actually allowed to hand a run to. GET /harnesses says what is installed
// on this host; GET /whoami says which of them this token may use (the host
// gets them all, a console's own owner gets that console's) — an engine is
// offered only when both agree, so the picker never shows a choice that 403s.

import { useState, useEffect } from 'react'
import { API_URL } from '../config'

export type DraftEngine = { name: string; label: string }

export function useDraftEngines(token?: string | null): DraftEngine[] {
  const [engines, setEngines] = useState<DraftEngine[]>([])
  useEffect(() => {
    if (!token) { setEngines([]); return }
    let live = true
    Promise.all([
      fetch(`${API_URL}/harnesses`, { signal: AbortSignal.timeout(8000) }).then(r => r.json()),
      fetch(`${API_URL}/whoami?key=${encodeURIComponent(token)}`, { signal: AbortSignal.timeout(8000) }).then(r => r.json()),
    ]).then(([hs, who]) => {
      if (!live) return
      const allowed = new Set<string>(who?.harnesses || [])
      setEngines((hs?.harnesses || [])
        .filter((h: any) => h?.available && allowed.has(h.name))
        .map((h: any) => ({ name: h.name, label: h.label || h.name })))
    }).catch(() => {})
    return () => { live = false }
  }, [token])
  return engines
}
