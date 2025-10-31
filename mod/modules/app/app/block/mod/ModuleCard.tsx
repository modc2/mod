'use client'

import React, { useState } from 'react'
import Link from 'next/link'
import { Key, Copy, Check, Clock } from 'lucide-react'

export default function ModuleCard({ mod }) {
  const [copiedKey, setCopiedKey] = useState(false)
  const [copiedContent, setCopiedContent] = useState(false)

  const formatDate = (timestamp: number) => {
    return new Date(timestamp * 1000).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  const truncate = (str: string, start = 6, end = 6) =>
    str.length > start + end ? `${str.slice(0, start)}…${str.slice(-end)}` : str

  const copyText = async (
    text: string,
    e: React.MouseEvent,
    setCopied: (val: boolean) => void
  ) => {
    e.preventDefault()
    e.stopPropagation()
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch (err) {
      console.error('Copy failed:', err)
    }
  }

  return (
    <Link
      href={`/${mod.name}/${mod.key}`}
      className="group relative flex flex-col justify-between overflow-hidden rounded-2xl border border-zinc-800 bg-gradient-to-br from-zinc-950 to-black p-6 shadow-[0_0_20px_rgba(0,0,0,0.45)] hover:border-blue-400/50 hover:shadow-[0_0_25px_rgba(0,140,255,0.25)] transition-all duration-300"
    >
      {/* hover gradient */}
      <div className="absolute inset-0 bg-gradient-to-br from-blue-600/10 via-cyan-500/5 to-purple-500/10 opacity-0 group-hover:opacity-100 transition-opacity duration-500 pointer-events-none" />

      <div className="relative flex flex-col gap-4">
        {/* header */}
        <h3 className="text-2xl font-semibold tracking-tight text-zinc-100 group-hover:text-blue-400 transition-colors truncate">
          {mod.name}
        </h3>

        {/* version + updated time */}
        {mod.content && (
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
            <button
              title="Click to copy version content"
              className="relative cursor-pointer text-left flex-1"
              onClick={(e) => copyText(mod.content, e, setCopiedContent)}
            >
              <p className="text-lg text-zinc-300 leading-snug hover:text-blue-300 transition-colors">
                {mod.content}
              </p>
              {copiedContent ? (
                <Check className="absolute top-0 right-0 w-4 h-4 text-green-400 transition" />
              ) : (
                <Copy className="absolute top-0 right-0 w-4 h-4 text-zinc-600 opacity-0 group-hover:opacity-60 hover:opacity-100 transition" />
              )}
            </button>

            {mod.updated && (
              <div className="flex items-center gap-1 text-sm text-yellow-400/90 mt-0.5 sm:mt-0 sm:ml-2">
                <Clock className="w-4 h-4" />
                <span>{formatDate(mod.updated)}</span>
              </div>
            )}
          </div>
        )}

        {/* metadata (created date) */}
        {mod.created && (
          <div className="flex items-center gap-1 text-sm text-green-400/90">
            <svg
              className="w-4 h-4"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M12 4v16m8-8H4"
              />
            </svg>
            Created {formatDate(mod.created)}
          </div>
        )}
      </div>

      {/* key at the bottom */}
      <button
        className="mt-5 self-start flex items-center gap-2 text-sm font-mono bg-blue-900/20 border border-blue-700/40 text-blue-300 px-3 py-1 rounded-md hover:border-blue-500/60 cursor-pointer transition"
        title="Copy module key"
        onClick={(e) => copyText(mod.key, e, setCopiedKey)}
      >
        <Key className="w-4 h-4" />
        {truncate(mod.key, 8, 8)}
        {copiedKey ? (
          <Check className="w-4 h-4 text-green-400 transition" />
        ) : (
          <Copy className="w-4 h-4 opacity-50 group-hover:opacity-100 transition" />
        )}
      </button>

      {/* glowing bottom line */}
      <div className="absolute bottom-0 left-0 right-0 h-[2px] bg-gradient-to-r from-blue-400 to-cyan-400 scale-x-0 group-hover:scale-x-100 origin-left transition-transform duration-500" />
    </Link>
  )
}
