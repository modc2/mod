'use client'

import { useState, useEffect } from 'react'
import { useUserContext } from '@/bloc/context'
import { ModuleType } from '@/bloc/types'
import { Clock, GitBranch, Hash, RotateCcw, ArrowUpDown } from 'lucide-react'
import { CopyButton } from '@/bloc/ui/CopyButton'

interface ModVersionsProps {
  mod: ModuleType
}

interface Version {
  cid: string
  comment: string | null
  updated: string
}

const ui = {
  bg: '#0b0b0b',
  panel: '#121212',
  panelAlt: '#151515',
  border: '#2a2a2a',
  text: '#e7e7e7',
  textDim: '#a8a8a8',
  blue: '#3b82f6',
  green: '#10b981',
}

export default function ModVersions({ mod }: ModVersionsProps) {
  const { client } = useUserContext()
  const [versions, setVersions] = useState<Version[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [sortAsc, setSortAsc] = useState(false)

  useEffect(() => {
    const fetchVersions = async () => {
      if (!client || !mod.key) return
      setLoading(true)
      setError(null)
      try {
        const vers = await client.call('history', { key: mod.key, mod: mod.name })
        setVersions(Array.isArray(vers) ? vers : [])
      } catch (err: any) {
        setError(err?.message || 'Failed to load versions')
      } finally {
        setLoading(false)
      }
    }
    fetchVersions()
  }, [client, mod.key])

  const handleSetVersion = async (cid: string, versionNum: number) => {
    if (!client || !mod.key) return
    try {
      await client.call('set_version', { key: mod.key, mod: mod.name, cid })
    } catch (err: any) {
      alert(`Failed to set version: ${err?.message || 'Unknown error'}`)
    }
  }

  const sortedVersions = [...versions].sort((a, b) => {
    const timeA = new Date(a.updated).getTime()
    const timeB = new Date(b.updated).getTime()
    return sortAsc ? timeA - timeB : timeB - timeA
  })

  if (loading) {
    return (
      <div className="flex items-center justify-center p-8" style={{ backgroundColor: ui.panel }}>
        <div className="animate-spin rounded-full h-6 w-6 border-2 border-t-transparent" style={{ borderColor: ui.blue }} />
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-4 rounded-lg border" style={{ backgroundColor: ui.panel, borderColor: '#ef4444' }}>
        <p className="text-red-400 text-sm font-mono">{error}</p>
      </div>
    )
  }

  return (
    <div className="p-3 rounded-lg border" style={{ backgroundColor: ui.panel, borderColor: ui.border }}>
      <div className="flex items-center justify-between mb-3">
          <h3 className="text-xl font-bold" style={{ color: ui.text }}>Versions</h3>
        <button
          onClick={() => setSortAsc(!sortAsc)}
          className="px-2 py-1 rounded hover:opacity-70 transition-all flex items-center gap-1 text-xs"
          style={{ backgroundColor: ui.panelAlt, color: ui.textDim }}
        >
          <ArrowUpDown className="w-3 h-3" />
          {sortAsc ? 'Oldest' : 'Newest'}
        </button>
      </div>
      {versions.length === 0 ? (
        <p className="text-center py-6 text-sm" style={{ color: ui.textDim }}>No versions found</p>
      ) : (
        <div className="max-h-96 overflow-y-auto space-y-2 pr-1" style={{ scrollbarWidth: 'thin' }}>
          {sortedVersions.map((ver, idx) => {
            const originalIdx = versions.indexOf(ver)
            return (
              <div
                key={originalIdx}
                className="p-3 rounded border hover:bg-opacity-90 transition-all"
                style={{ backgroundColor: ui.panelAlt, borderColor: ui.border }}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="space-y-1.5 flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <GitBranch className="w-4 h-4 flex-shrink-0" style={{ color: ui.blue }} />
                      <span className="font-bold text-sm" style={{ color: ui.blue }}>v{originalIdx + 1}</span>
                      <Clock className="w-3 h-3 flex-shrink-0" style={{ color: ui.textDim }} />
                      <span className="text-xs truncate" style={{ color: ui.textDim }}>{ver.updated}</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <Hash className="w-3 h-3 flex-shrink-0" style={{ color: ui.textDim }} />
                      <code className="text-xs font-mono truncate" style={{ color: ui.text }}>
                        {ver.cid.slice(0, 12)}...{ver.cid.slice(-8)}
                      </code>
                      <CopyButton text={ver.cid} size="sm" />
                    </div>
                    {ver.comment && (
                      <p className="text-xs truncate" style={{ color: ui.textDim }}>{ver.comment}</p>
                    )}
                  </div>
                  <button
                    onClick={() => handleSetVersion(ver.cid, originalIdx + 1)}
                    className="px-2 py-1 rounded text-xs font-semibold hover:opacity-80 transition-all flex items-center gap-1 flex-shrink-0"
                    style={{ backgroundColor: ui.green, color: '#fff' }}
                  >
                    <RotateCcw className="w-3 h-3" />
                    Set
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
