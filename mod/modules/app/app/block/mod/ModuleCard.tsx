'use client'

import React, {  useState } from 'react'
import Link from 'next/link'
import { Key, Copy, Check, Clock, Sparkles, ExternalLink } from 'lucide-react'

type SortKey = 'recent' | 'name' | 'author'
type FilterKey = 'all' | 'available' | 'unavailable'


interface ModuleCardProps {
  mod: {
    name: string
    key: string
    content?: string
    created?: number
    updated?: number
  }
}

export default function ModuleCard({ mod }: ModuleCardProps) {
  const [copied, setCopied] = useState<{ key?: boolean; content?: boolean }>({})

  const formatDate = (timestamp?: number) => {
    if (!timestamp) return ''
    const now = Date.now()
    const then = timestamp * 1000
    const diff = now - then
    const minutes = Math.floor(diff / 60000)
    const hours = Math.floor(diff / 3600000)
    const days = Math.floor(diff / 86400000)

    if (minutes < 1) return 'just now'
    if (minutes < 60) return `${minutes}m ago`
    if (hours < 24) return `${hours}h ago`
    if (days < 7) return `${days}d ago`
    
    return new Date(then).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: now - then > 31536000000 ? 'numeric' : undefined,
    })
  }

  const truncate = (str: string, start = 8, end = 6) =>
    str.length > start + end ? `${str.slice(0, start)}…${str.slice(-end)}` : str

  const handleCopy = async (
    text: string,
    key: 'key' | 'content',
    e: React.MouseEvent
  ) => {
    e.preventDefault()
    e.stopPropagation()
    try {
      await navigator.clipboard.writeText(text)
      setCopied((prev) => ({ ...prev, [key]: true }))
      setTimeout(() => setCopied((prev) => ({ ...prev, [key]: false })), 2000)
    } catch (err) {
      console.error('Copy failed:', err)
    }
  }

  const hasKey = mod.key && mod.key.trim() !== ''

  return (
    <Link
      href={`/${mod.name}/${mod.key}`}
      className="group relative flex flex-col overflow-hidden rounded-3xl border border-white/[0.08] bg-gradient-to-br from-zinc-900/50 via-black to-zinc-900/30 backdrop-blur-xl transition-all duration-500 hover:border-white/20 hover:shadow-[0_8px_32px_rgba(255,255,255,0.06)]"
    >
      {/* Noise texture overlay for depth */}
      <div className="absolute inset-0 opacity-[0.015] bg-[url('data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIzMDAiIGhlaWdodD0iMzAwIj48ZmlsdGVyIGlkPSJhIiB4PSIwIiB5PSIwIj48ZmVUdXJidWxlbmNlIGJhc2VGcmVxdWVuY3k9Ii43NSIgc3RpdGNoVGlsZXM9InN0aXRjaCIgdHlwZT0iZnJhY3RhbE5vaXNlIi8+PGZlQ29sb3JNYXRyaXggdHlwZT0ic2F0dXJhdGUiIHZhbHVlcz0iMCIvPjwvZmlsdGVyPjxyZWN0IHdpZHRoPSIxMDAlIiBoZWlnaHQ9IjEwMCUiIGZpbHRlcj0idXJsKCNhKSIvPjwvc3ZnPg==')] pointer-events-none" />
      
      {/* Animated gradient orb */}
      <div className="absolute -top-24 -right-24 w-48 h-48 bg-gradient-to-br from-blue-500/20 via-purple-500/20 to-pink-500/20 rounded-full blur-3xl opacity-0 group-hover:opacity-100 transition-opacity duration-1000 pointer-events-none" />
      <div className="absolute -bottom-24 -left-24 w-48 h-48 bg-gradient-to-tr from-emerald-500/10 via-cyan-500/10 to-blue-500/10 rounded-full blur-3xl opacity-0 group-hover:opacity-100 transition-opacity duration-1000 delay-100 pointer-events-none" />

      {/* Shimmer effect */}
      <div className="absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-500">
        <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/[0.03] to-transparent -translate-x-full group-hover:translate-x-full transition-transform duration-1500 ease-out" />
      </div>

      {/* Main content */}
      <div className="relative z-10 p-8">
        {/* Header section */}
        <div className="flex items-start justify-between gap-4 mb-6">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 mb-2">
              <h3 className="text-4xl font-semibold tracking-tight text-white/95 group-hover:text-transparent group-hover:bg-clip-text group-hover:bg-gradient-to-r group-hover:from-white group-hover:via-blue-100 group-hover:to-purple-200 transition-all duration-500 truncate">
                {mod.name}
              </h3>
              <ExternalLink className="w-5 h-5 text-white/20 opacity-0 group-hover:opacity-100 transition-all duration-300 flex-shrink-0" />
            </div>
            
            {/* Timestamp */}
            <div className="flex items-center gap-2">
              {mod.updated ? (
                <div className="flex items-center gap-1.5 text-sm text-amber-400/70">
                  <Clock className="w-3.5 h-3.5" />
                  <span className="font-medium">Updated {formatDate(mod.updated)}</span>
                </div>
              ) : mod.created ? (
                <div className="flex items-center gap-1.5 text-sm text-emerald-400/70">
                  <Sparkles className="w-3.5 h-3.5" />
                  <span className="font-medium">Created {formatDate(mod.created)}</span>
                </div>
              ) : null}
            </div>
          </div>

          {/* Key status badge */}
          <div className="flex flex-col items-end gap-2">
            {hasKey ? (
              <button
                onClick={(e) => handleCopy(mod.key, 'key', e)}
                title="Copy module key"
                className="group/key flex items-center gap-2 text-sm font-mono text-emerald-300 bg-gradient-to-br from-emerald-500/15 to-emerald-600/10 border border-emerald-500/25 px-4 py-2 rounded-xl hover:from-emerald-500/25 hover:to-emerald-600/20 hover:border-emerald-400/40 hover:shadow-[0_0_20px_rgba(52,211,153,0.15)] transition-all duration-300 hover:scale-105 active:scale-95"
              >
                <Key className="w-4 h-4" />
                <span className="hidden sm:inline tracking-tight">{truncate(mod.key, 6, 4)}</span>
                {copied.key ? (
                  <Check className="w-4 h-4 text-emerald-200" />
                ) : (
                  <Copy className="w-4 h-4 opacity-50 group-hover/key:opacity-100 transition" />
                )}
              </button>
            ) : (
              <div className="flex items-center gap-2 text-sm font-mono text-rose-300/50 bg-gradient-to-br from-rose-500/10 to-rose-600/5 border border-rose-500/20 px-4 py-2 rounded-xl">
                <Key className="w-4 h-4" />
                <span className="hidden sm:inline">No Key</span>
              </div>
            )}
            
            {/* Status pill */}
            <div className="flex items-center gap-2 px-3 py-1 bg-white/5 rounded-full border border-white/10">
              <div className={`w-1.5 h-1.5 rounded-full ${hasKey ? 'bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.8)]' : 'bg-rose-400/50'} transition-all duration-300`} />
              <span className="text-xs font-semibold text-white/50 uppercase tracking-wider">
                {hasKey ? 'Active' : 'Inactive'}
              </span>
            </div>
          </div>
        </div>

        {/* Content address section */}
        {mod.content && (
          <button
            onClick={(e) => handleCopy(mod.content!, 'content', e)}
            title="Copy content address"
            className="group/content relative w-full text-left p-5 bg-gradient-to-br from-white/[0.07] to-white/[0.03] hover:from-white/[0.12] hover:to-white/[0.06] border border-white/10 hover:border-white/20 rounded-2xl transition-all duration-300 hover:shadow-[0_4px_16px_rgba(255,255,255,0.04)]"
          >
            <div className="flex items-center justify-between gap-4">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-2">
                  <div className="text-xs font-bold text-white/30 uppercase tracking-widest">Content Address</div>
                  <div className="h-px flex-1 bg-gradient-to-r from-white/20 to-transparent" />
                </div>
                <p className="text-lg font-mono text-white/80 group-hover/content:text-blue-300 transition-colors truncate">
                  {truncate(mod.content, 14, 12)}
                </p>
              </div>
              {copied.content ? (
                <div className="flex items-center gap-2 text-emerald-300">
                  <Check className="w-5 h-5" />
                  <span className="text-sm font-medium">Copied!</span>
                </div>
              ) : (
                <Copy className="w-5 h-5 text-white/30 opacity-0 group-hover/content:opacity-100 transition-all duration-300 flex-shrink-0" />
              )}
            </div>
            
            {/* Hover underline effect */}
            <div className="absolute bottom-0 left-5 right-5 h-[2px] bg-gradient-to-r from-blue-400 via-purple-400 to-pink-400 scale-x-0 group-hover/content:scale-x-100 origin-left transition-transform duration-500" />
          </button>
        )}
      </div>

      {/* Bottom gradient accent */}
      <div className="absolute bottom-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-blue-500/50 to-transparent scale-x-0 group-hover:scale-x-100 origin-center transition-transform duration-700 ease-out" />
    </Link>
  )
}
