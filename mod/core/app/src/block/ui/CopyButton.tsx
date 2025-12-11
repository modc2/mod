'use client'

import { useState } from 'react'
import { ClipboardDocumentIcon, CheckIcon } from '@heroicons/react/24/outline'
import { copyToClipboard, shorten } from '../utils'

interface CopyButtonProps {
  text?: string
  content?: string
  size?: 'sm' | 'md' | 'lg'
  showShortened?: boolean
}

export function CopyButton({ text, content, size = 'md', showShortened = false }: CopyButtonProps) {
  const [copied, setCopied] = useState(false)
  const copyContent = text || content || ''
  const displayText = showShortened ? shorten(copyContent) : copyContent

  const handleCopy = async (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    const success = await copyToClipboard(copyContent)
    if (success) {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  const sizeClasses = {
    sm: 'h-10 w-10 p-2.5',
    md: 'h-12 w-12 p-3',
    lg: 'h-14 w-14 p-3.5'
  }

  const iconSizes = {
    sm: 'w-5 h-5',
    md: 'w-6 h-6',
    lg: 'w-7 h-7'
  }

  return (
    <button
      onClick={handleCopy}
      className={`${sizeClasses[size]} inline-flex items-center justify-center rounded-xl hover:bg-white/10 transition-colors flex-shrink-0`}
      title={copied ? 'Copied!' : `Copy ${copyContent} to clipboard`}
    >
      {showShortened && <span className="mr-2 text-white/70">{displayText}</span>}
      {copied ? (
        <CheckIcon className={`${iconSizes[size]} text-green-400`} />
      ) : (
        <ClipboardDocumentIcon className={`${iconSizes[size]} text-white/70 hover:text-white`} />
      )}
    </button>
  )
}