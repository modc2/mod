'use client'

import { useState, useEffect } from 'react'
import { useUserContext } from '@/bloc/context'
import { ModuleType } from '@/bloc/types'
import { Clock, GitBranch, Hash } from 'lucide-react'
import { CopyButton } from '@/bloc/ui/CopyButton'

interface ModVersionsProps {
  mod: ModuleType
}

interface Version {
  version: number
  cid: string
  url: string
  timestamp: number
  hash: string
}

const ui = {
  bg: '#0b0b0b',
  panel: '#121212',
  panelAlt: '#151515',
  border: '#2a2a2a',
  text: '#e7e7e7',
  textDim: '#a8a8a8',
  blue: '#3b82f6',
}

export default function ModVersions({ mod }: ModVersionsProps) {
  const { client } = useUserContext()
  const [versions, setVersions] = useState<Version[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const fetchVersions = async () => {
      if (!client || !mod.key) return
      setLoading(true)
      setError(null)
      try {
        const vers = await client.call('txs', { key: mod.key, type: 'versions' })
        setVersions(vers || [])
      } catch (err: any) {
        console.error('Failed to fetch versions:', err)
        setError(err?.message || 'Failed to load versions')
      } finally {
        setLoading(false)
      }
    }
    fetchVersions()
  }, [client, mod.key])

  if (loading) {
    return (
      <div className="flex items-center justify-center p-12" style={{ backgroundColor: ui.panel }}>
        <div className="animate-spin rounded-full h-8 w-8 border-2 border-t-transparent" style={{ borderColor: ui.blue }} />
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-6 rounded-xl border-2" style={{ backgroundColor: ui.panel, borderColor: '#ef4444' }}>
        <p className="text-red-400 font-mono">{error}</p>
      </div>
    )
  }

  return (
    <div className="space-y-4" style={{ backgroundColor: ui.bg }}>
      <div className="p-4 rounded-xl border" style={{ backgroundColor: ui.panel, borderColor: ui.border }}>
        <h3 className="text-xl font-bold mb-4" style={{ color: ui.text }}>Version History</h3>
        {versions.length === 0 ? (
          <p className="text-center py-8" style={{ color: ui.textDim }}>No versions found</p>
        ) : (
          <div className="space-y-3">
            {versions.map((ver, idx) => (
              <div
                key={idx}
                className="p-4 rounded-lg border hover:bg-opacity-80 transition-all"
                style={{ backgroundColor: ui.panelAlt, borderColor: ui.border }}
              >
                <div className="flex items-start justify-between">
                  <div className="space-y-2">
                    <div className="flex items-center gap-2">
                      <GitBranch className="w-5 h-5" style={{ color: ui.blue }} />
                      <span className="font-bold text-lg" style={{ color: ui.blue }}>v{ver.version}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Hash className="w-4 h-4" style={{ color: ui.textDim }} />
                      <code className="text-sm font-mono" style={{ color: ui.text }}>
                        {ver.cid.slice(0, 20)}...{ver.cid.slice(-12)}
                      </code>
                      <CopyButton text={ver.cid} size="sm" />
                    </div>
                    <div className="flex items-center gap-2">
                      <Clock className="w-4 h-4" style={{ color: ui.textDim }} />
                      <span className="text-sm" style={{ color: ui.textDim }}>
                        {new Date(ver.timestamp).toLocaleString()}
                      </span>
                    </div>
                    {ver.url && (
                      <a
                        href={ver.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-sm underline hover:opacity-80"
                        style={{ color: ui.blue }}
                      >
                        {ver.url}
                      </a>
                    )}
                  </div>
                  <div className="text-right">
                    <div className="text-xs font-mono" style={{ color: ui.textDim }}>
                      {ver.hash.slice(0, 12)}...
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
