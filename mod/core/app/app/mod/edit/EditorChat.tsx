"use client"

import { useState, useEffect, useRef } from 'react'
import { colorWithOpacity } from '@/utils'
import { Send, Loader2, GitBranch, CheckCircle, Clock, XCircle, Terminal, ChevronDown } from 'lucide-react'
import { ChatMessage, MODELS, AGENT_TYPES } from './shared'

function MiniDropdown({ value, options, onChange, accentColor }: {
  value: string
  options: { value: string; label: string; icon?: string; color?: string }[]
  onChange: (v: string) => void
  accentColor: string
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const active = options.find(o => o.value === value) || options[0]
  const displayColor = active.color || accentColor

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 px-2 py-0.5 transition-all"
        style={{
          fontSize: '10px',
          fontWeight: 600,
          background: `${displayColor}15`,
          border: `1px solid ${displayColor}35`,
          color: displayColor,
          borderRadius: '4px',
          cursor: 'pointer',
        }}
      >
        {active.icon && <span style={{ fontSize: '10px', opacity: 0.7 }}>{active.icon}</span>}
        <span>{active.label}</span>
        <ChevronDown size={8} style={{ opacity: 0.5 }} />
      </button>

      {open && (
        <div
          className="absolute bottom-full left-0 mb-1 z-50 overflow-hidden"
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-color)',
            borderRadius: '6px',
            boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
            minWidth: '120px',
          }}
        >
          {options.map(opt => {
            const optColor = opt.color || accentColor
            return (
              <button
                key={opt.value}
                onClick={() => { onChange(opt.value); setOpen(false) }}
                className="w-full text-left px-2.5 py-1.5 transition-all flex items-center gap-1.5"
                style={{
                  fontSize: '10px',
                  fontWeight: value === opt.value ? 600 : 400,
                  color: value === opt.value ? optColor : 'var(--text-secondary)',
                  background: value === opt.value ? `${optColor}12` : 'transparent',
                  cursor: 'pointer',
                  border: 'none',
                }}
                onMouseEnter={e => (e.currentTarget.style.background = 'var(--hover-bg)')}
                onMouseLeave={e => (e.currentTarget.style.background = value === opt.value ? `${optColor}12` : 'transparent')}
              >
                {opt.icon && <span style={{ fontSize: '11px', opacity: 0.7 }}>{opt.icon}</span>}
                <span>{opt.label}</span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

interface EditorChatProps {
  messages: ChatMessage[]
  message: string
  setMessage: (msg: string) => void
  onSend: () => void
  sending: boolean
  disabled?: boolean
  moduleColor: string
  modName: string
  placeholder?: string
  emptyTitle?: string
  emptySubtitle?: string
  chatEndRef: React.RefObject<HTMLDivElement>
  inputRef: React.RefObject<HTMLInputElement>
  // Output panel
  activeJobOutput?: string
  activeJobId?: string | null
  isJobRunning?: boolean
  onCancel?: (jobId: string) => void
  // Model + agent type
  model?: string
  onModelChange?: (model: string) => void
  agentType?: string
  onAgentTypeChange?: (agentType: string) => void
  // Sizing — 'compact' for sidebar, 'full' for page
  variant?: 'full' | 'compact'
}

export function EditorChat({
  messages, message, setMessage, onSend, sending, disabled,
  moduleColor, modName, placeholder, emptyTitle, emptySubtitle,
  chatEndRef, inputRef,
  activeJobOutput, activeJobId, isJobRunning, onCancel,
  model, onModelChange, agentType, onAgentTypeChange,
  variant = 'full',
}: EditorChatProps) {
  const outputEndRef = useRef<HTMLDivElement>(null)
  const isCompact = variant === 'compact'

  // Scroll chat
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, chatEndRef])

  // Scroll output
  useEffect(() => {
    outputEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [activeJobOutput])

  const iconSize = isCompact ? 9 : 10
  const textSize = isCompact ? 'text-[12px]' : 'text-[13px]'
  const statusTextSize = isCompact ? 'text-[8px]' : 'text-[9px]'
  const jobIdSize = isCompact ? 'text-[8px]' : 'text-[9px]'
  const cidSize = isCompact ? 'text-[9px]' : 'text-[10px]'
  const maxWidth = isCompact ? 'max-w-[90%]' : 'max-w-[85%]'
  const outputSlice = isCompact ? -3000 : -5000
  const outputMaxH = isCompact ? '150px' : '200px'

  const activeAgent = AGENT_TYPES.find(a => a.value === agentType) || AGENT_TYPES[0]
  const showSelectors = onModelChange || onAgentTypeChange

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Chat messages */}
      <div className={`flex-1 overflow-y-auto ${isCompact ? 'px-3 py-3 space-y-2.5' : 'p-4 space-y-3'}`} style={{ scrollbarWidth: 'thin' }}>
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-3" style={{ opacity: 0.4 }}>
            <Terminal size={isCompact ? 28 : 32} style={{ color: moduleColor }} />
            <p className={`${isCompact ? 'text-[11px]' : 'text-sm'} font-bold uppercase tracking-wider text-center`} style={{ color: 'var(--text-tertiary)' }}>
              {emptyTitle || `Dev Agent for ${modName}`}
            </p>
            <p className={`${isCompact ? 'text-[10px]' : 'text-xs'} text-center ${isCompact ? 'px-4' : ''}`} style={{ color: 'var(--text-tertiary)' }}>
              {emptySubtitle || 'Describe changes and Claude will edit the module code'}
            </p>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className={`${maxWidth} ${isCompact ? 'px-2.5 py-1.5' : 'px-3 py-2'} rounded-lg`}
              style={{
                background: msg.role === 'user'
                  ? colorWithOpacity(moduleColor, 0.12)
                  : msg.role === 'system'
                  ? 'rgba(251, 191, 36, 0.08)'
                  : 'var(--bg-surface)',
                border: msg.role === 'user'
                  ? `1px solid ${colorWithOpacity(moduleColor, 0.25)}`
                  : msg.role === 'system'
                  ? '1px solid rgba(251, 191, 36, 0.2)'
                  : '1px solid var(--border-color)',
              }}
            >
              {msg.status && (
                <div className={`flex items-center gap-1.5 ${isCompact ? 'mb-0.5' : 'mb-1'}`}>
                  {msg.status === 'running' && <Loader2 size={iconSize} className="animate-spin" style={{ color: '#3b82f6' }} />}
                  {msg.status === 'completed' && <CheckCircle size={iconSize} style={{ color: '#22c55e' }} />}
                  {msg.status === 'failed' && <XCircle size={iconSize} style={{ color: '#ef4444' }} />}
                  {msg.status === 'pending' && <Clock size={iconSize} style={{ color: '#fbbf24' }} />}
                  <span className={`${statusTextSize} font-bold uppercase tracking-wider`} style={{
                    color: msg.status === 'running' ? '#3b82f6'
                      : msg.status === 'completed' ? '#22c55e'
                      : msg.status === 'failed' ? '#ef4444'
                      : '#fbbf24'
                  }}>
                    {msg.status}
                  </span>
                  {msg.jobId && (
                    <span className={`${jobIdSize} font-mono`} style={{ color: 'var(--text-tertiary)' }}>
                      {msg.jobId.slice(0, 8)}
                    </span>
                  )}
                </div>
              )}

              <p className={`${textSize} whitespace-pre-wrap break-words`} style={{
                color: msg.role === 'user' ? 'var(--text-primary)'
                  : msg.role === 'system' ? '#fbbf24'
                  : 'var(--text-secondary)',
              }}>
                {msg.content}
              </p>

              {msg.cid && (
                <div className={`flex items-center gap-1${isCompact ? '' : '.5'} mt-1${isCompact ? '' : '.5'}`}>
                  <GitBranch size={isCompact ? 9 : 10} style={{ color: moduleColor }} />
                  <span className={`${cidSize} font-mono`} style={{ color: moduleColor }}>
                    {msg.cid}
                  </span>
                </div>
              )}

              {!isCompact && (
                <span className="text-[9px] mt-1 block" style={{ color: 'var(--text-tertiary)', opacity: 0.5 }}>
                  {(() => {
                    const seconds = Math.floor(Date.now() / 1000 - msg.timestamp)
                    if (seconds < 60) return `${seconds}s ago`
                    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
                    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
                    return `${Math.floor(seconds / 86400)}d ago`
                  })()}
                </span>
              )}
            </div>
          </div>
        ))}
        <div ref={chatEndRef} />
      </div>

      {/* Live output panel */}
      {activeJobId && activeJobOutput && (
        <div
          className="overflow-y-auto border-t shrink-0"
          style={{
            maxHeight: outputMaxH,
            borderColor: 'var(--border-color)',
            background: 'var(--bg-primary)',
          }}
        >
          <div className={`flex items-center justify-between ${isCompact ? 'px-2.5 py-1' : 'px-3 py-1.5'}`} style={{ borderBottom: '1px solid var(--border-color)', background: 'var(--bg-surface)' }}>
            <div className="flex items-center gap-1.5">
              <Terminal size={isCompact ? 10 : 12} style={{ color: isJobRunning ? '#3b82f6' : '#22c55e' }} />
              <span className={`${isCompact ? 'text-[9px]' : 'text-[10px]'} font-bold uppercase tracking-wider`} style={{
                color: isJobRunning ? '#3b82f6' : '#22c55e'
              }}>
                {isJobRunning ? 'LIVE OUTPUT' : 'OUTPUT'}
              </span>
            </div>
            {isJobRunning && onCancel && (
              <button
                onClick={() => onCancel(activeJobId)}
                className={`${isCompact ? 'text-[8px]' : 'text-[9px]'} font-bold uppercase px-1.5 py-0.5 rounded transition-all`}
                style={{ color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)' }}
              >
                CANCEL
              </button>
            )}
          </div>
          <pre
            className={`${isCompact ? 'px-2.5 py-2 text-[10px]' : 'p-3 text-[11px]'} leading-relaxed whitespace-pre-wrap break-words`}
            style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-digital), monospace' }}
          >
            {activeJobOutput.slice(outputSlice)}
            <span ref={outputEndRef} />
          </pre>
        </div>
      )}

      {/* Input bar */}
      <div className={`shrink-0 ${isCompact ? 'px-2.5 py-2' : 'p-3'} border-t`} style={{ borderColor: 'var(--border-color)', background: 'var(--bg-surface)' }}>
        {/* Model + Agent type selectors */}
        {showSelectors && (
          <div className={`flex items-center gap-1.5 ${isCompact ? 'mb-1.5' : 'mb-2'}`}>
            {onModelChange && (
              <MiniDropdown
                value={model || 'sonnet'}
                options={MODELS}
                onChange={onModelChange}
                accentColor={moduleColor}
              />
            )}
            {onAgentTypeChange && (
              <MiniDropdown
                value={agentType || 'default'}
                options={AGENT_TYPES}
                onChange={onAgentTypeChange}
                accentColor={moduleColor}
              />
            )}
          </div>
        )}

        <div className={`flex ${isCompact ? 'gap-1.5' : 'gap-2'} items-center`}>
          <input
            ref={inputRef}
            type="text"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && onSend()}
            placeholder={placeholder || `${activeAgent.icon}  edit ${modName}...`}
            disabled={sending || disabled}
            className={`flex-1 ${isCompact ? 'px-2.5 py-2 text-[12px]' : 'px-3 py-2.5 text-[13px]'} rounded-lg border outline-none`}
            style={{
              backgroundColor: 'var(--bg-input)',
              borderColor: sending ? colorWithOpacity(moduleColor, 0.2) : 'var(--border-color)',
              color: 'var(--text-primary)',
              fontFamily: 'var(--font-digital), monospace',
            }}
          />
          <button
            onClick={onSend}
            disabled={!message.trim() || sending || disabled}
            className={`${isCompact ? 'px-3 py-2' : 'px-4 py-2.5'} rounded-lg font-bold transition-all disabled:opacity-30`}
            style={{
              backgroundColor: colorWithOpacity(moduleColor, 0.12),
              border: `1px solid ${colorWithOpacity(moduleColor, 0.3)}`,
              color: moduleColor,
            }}
          >
            {sending ? <Loader2 size={isCompact ? 14 : 16} className="animate-spin" /> : <Send size={isCompact ? 14 : 16} />}
          </button>
        </div>
      </div>
    </div>
  )
}
