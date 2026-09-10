'use client'

import { useState } from 'react'

/**
 * A command you are meant to run, with a button that puts it on the clipboard.
 *
 * Docs pages are read on the machine the command has to run on, and an MCP
 * connect string is long enough that retyping it is where the typo comes from.
 * The confirmation is the button's own label rather than a toast — it is
 * already where the eye is.
 */
export default function CopyLine({ cmd, label }: { cmd: string; label?: string }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(cmd)
    } catch {
      return // clipboard blocked (insecure origin) — the text is selectable anyway
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 1400)
  }

  return (
    <div>
      {label && (
        <p className="pixel mb-2 text-[7.5px] leading-[2] text-nes-ink3">{label}</p>
      )}
      <div className="flex items-stretch gap-2">
        <code
          className="min-w-0 flex-1 overflow-x-auto whitespace-pre border-[3px] border-black
                     bg-black px-3 py-2.5 text-[12.5px] leading-relaxed text-nes-coin"
        >
          {cmd}
        </code>
        <button
          onClick={copy}
          aria-label={`Copy: ${cmd}`}
          className={`btn pixel tap shrink-0 px-3 text-[7.5px] ${copied ? 'btn-on' : ''}`}
        >
          {copied ? 'OK' : 'COPY'}
        </button>
      </div>
    </div>
  )
}
