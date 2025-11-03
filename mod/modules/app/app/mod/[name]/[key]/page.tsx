'use client'

import { useParams } from 'next/navigation'
import { useEffect, useState, useMemo } from 'react'
import { Client } from '@/app/block/client/client'
import { useUserContext } from '@/app/block/context/UserContext'
import { Loading } from '@/app/block/Loading'
import { ModuleType } from '@/app/types'
import { Package, User, Hash, Calendar, ArrowLeft } from 'lucide-react'
import { CopyButton } from '@/app/block/CopyButton'
import Link from 'next/link'
import { Footer } from '@/app/block/Footer'

const MODULE_COLORS = [
  { from: 'from-purple-500/20', to: 'to-pink-500/20', text: 'text-purple-400', hover: 'from-purple-400 to-pink-400', border: 'border-purple-500/30' },
  { from: 'from-blue-500/20', to: 'to-cyan-500/20', text: 'text-blue-400', hover: 'from-blue-400 to-cyan-400', border: 'border-blue-500/30' },
  { from: 'from-green-500/20', to: 'to-emerald-500/20', text: 'text-green-400', hover: 'from-green-400 to-emerald-400', border: 'border-green-500/30' },
  { from: 'from-orange-500/20', to: 'to-red-500/20', text: 'text-orange-400', hover: 'from-orange-400 to-red-400', border: 'border-orange-500/30' },
  { from: 'from-yellow-500/20', to: 'to-amber-500/20', text: 'text-yellow-400', hover: 'from-yellow-400 to-amber-400', border: 'border-yellow-500/30' },
  { from: 'from-indigo-500/20', to: 'to-violet-500/20', text: 'text-indigo-400', hover: 'from-indigo-400 to-violet-400', border: 'border-indigo-500/30' },
]

export default function ModPage() {
  const params = useParams()
  const { keyInstance } = useUserContext()
  const client = useMemo(() => new Client(undefined, keyInstance), [keyInstance])
  
  const [mod, setMod] = useState<ModuleType | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const modName = params?.name as string
  const modKey = params?.key as string

  useEffect(() => {
    const fetchMod = async () => {
      if (!keyInstance || !modName || !modKey) return
      
      setLoading(true)
      setError(null)
      
      try {
        const response = await client.call('mod', { name: modName, key: modKey })
        if (response && !response.error) {
          setMod(response)
        } else {
          setError(response?.error || 'Module not found')
        }
      } catch (err: any) {
        console.error('Error fetching module:', err)
        setError(err?.message || 'Failed to load module')
      } finally {
        setLoading(false)
      }
    }

    fetchMod()
  }, [keyInstance, modName, modKey])

  const formatDate = (timestamp: number) => {
    return new Date(timestamp * 1000).toLocaleDateString('en-US', {
      month: 'long',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    })
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-black text-white flex items-center justify-center">
        <Loading />
      </div>
    )
  }

  if (error || !mod) {
    return (
      <div className="min-h-screen bg-black text-white">
        <div className="max-w-4xl mx-auto px-6 py-12">
          <Link href="/mod/explore" className="inline-flex items-center gap-2 text-white/60 hover:text-white mb-8 transition-colors">
            <ArrowLeft size={20} />
            Back to Modules
          </Link>
          <div className="p-8 border border-rose-500/30 bg-gradient-to-br from-rose-500/10 to-rose-600/5 rounded-2xl">
            <h2 className="text-2xl font-bold text-rose-400 mb-4">Error Loading Module</h2>
            <p className="text-rose-300/80">{error || 'Module not found'}</p>
          </div>
        </div>
      </div>
    )
  }

  const colorIndex = mod.name.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0) % MODULE_COLORS.length
  const colors = MODULE_COLORS[colorIndex]

  return (
    <div className="min-h-screen bg-black text-white">
      <div className="max-w-4xl mx-auto px-6 py-12">
        <Link href="/mod/explore" className="inline-flex items-center gap-2 text-white/60 hover:text-white mb-8 transition-colors">
          <ArrowLeft size={20} />
          Back to Modules
        </Link>

        <div className={`bg-gradient-to-br ${colors.from} ${colors.to} border ${colors.border} rounded-2xl p-8 backdrop-blur-sm space-y-6`}>
          <div className="flex items-start gap-4">
            <div className={`flex-shrink-0 p-4 bg-gradient-to-br ${colors.from} ${colors.to} rounded-xl border border-white/20`}>
              <Package className={`${colors.text}`} size={32} strokeWidth={2} />
            </div>
            <div className="flex-1 min-w-0">
              <h1 className="text-4xl font-bold text-white mb-2">{mod.name}</h1>
              {mod.desc && (
                <p className="text-xl text-white/70">{mod.desc}</p>
              )}
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="flex items-center gap-3 bg-black/40 border border-white/20 px-4 py-3 rounded-xl backdrop-blur-sm">
              <User className={`${colors.text}/80`} size={20} strokeWidth={2} />
              <div className="flex-1 min-w-0">
                <div className="text-sm text-white/60 font-medium mb-1">Author</div>
                <div className="flex items-center gap-2">
                  <code className="text-white/90 font-mono truncate">{mod.key}</code>
                  <CopyButton text={mod.key} />
                </div>
              </div>
            </div>

            {mod.cid && (
              <div className="flex items-center gap-3 bg-black/40 border border-white/20 px-4 py-3 rounded-xl backdrop-blur-sm">
                <Hash className={`${colors.text}/80`} size={20} strokeWidth={2} />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-white/60 font-medium mb-1">CID</div>
                  <div className="flex items-center gap-2">
                    <code className={`${colors.text} font-mono text-sm truncate`} title={mod.cid}>
                      {mod.cid.slice(0, 16)}...{mod.cid.slice(-8)}
                    </code>
                    <CopyButton text={mod.cid} />
                  </div>
                </div>
              </div>
            )}

            {mod.created && (
              <div className="flex items-center gap-3 bg-black/40 border border-white/20 px-4 py-3 rounded-xl backdrop-blur-sm">
                <Calendar className={`${colors.text}/80`} size={20} strokeWidth={2} />
                <div>
                  <div className="text-sm text-white/60 font-medium mb-1">Created</div>
                  <div className={`text-sm ${colors.text} font-semibold`}>
                    {formatDate(mod.created)}
                  </div>
                </div>
              </div>
            )}

            {mod.updated && (
              <div className="flex items-center gap-3 bg-black/40 border border-white/20 px-4 py-3 rounded-xl backdrop-blur-sm">
                <Calendar className={`${colors.text}/80`} size={20} strokeWidth={2} />
                <div>
                  <div className="text-sm text-white/60 font-medium mb-1">Updated</div>
                  <div className={`text-sm ${colors.text} font-semibold`}>
                    {formatDate(mod.updated)}
                  </div>
                </div>
              </div>
            )}
          </div>

          {mod.content && (
            <div className="bg-black/30 border border-white/20 px-6 py-4 rounded-xl backdrop-blur-sm">
              <h2 className="text-lg font-semibold text-white mb-3">Content</h2>
              <pre className="text-white/80 whitespace-pre-wrap font-mono text-sm leading-relaxed">
                {mod.content}
              </pre>
            </div>
          )}
        </div>
      </div>
      <Footer />
    </div>
  )
}
