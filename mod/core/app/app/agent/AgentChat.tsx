"use client"

import { useEffect, useRef } from 'react'

const STATUS_COLORS: Record<string, string> = {
  pending: '#f59e0b',
  running: '#3b82f6',
  completed: '#10b981',
  failed: '#ef4444',
  cancelled: '#6b7280',
}

interface Job {
  id: string
  prompt: string
  model: string
  work_dir: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  output: string
  error: string | null
  pid: number | null
  created_at: number
  updated_at: number
}

interface AgentChatProps {
  job: Job | null
  streamOutput: string
  onCancel: (id: string) => void
}

export default function AgentChat({ job, streamOutput, onCancel }: AgentChatProps) {
  const outputRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (outputRef.current) {
      const el = outputRef.current
      const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 200
      if (isNearBottom) {
        el.scrollTop = el.scrollHeight
      }
    }
  }, [streamOutput, job?.output])

  if (!job) {
    return (
      <div className="flex-1 flex items-center justify-center" style={{ background: 'var(--bg-primary)' }}>
        <div className="text-center space-y-4">
          <div className="w-16 h-16 mx-auto rounded-2xl flex items-center justify-center"
            style={{ background: 'var(--hover-bg)', border: '1px solid var(--border-color)' }}>
            <span style={{ fontSize: '28px', opacity: 0.4 }}>&#x2B21;</span>
          </div>
          <div>
            <p style={{ fontSize: '16px', color: 'var(--text-secondary)', fontWeight: 500 }}>
              No task selected
            </p>
            <p style={{ fontSize: '14px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
              Select a task from the sidebar or submit a new one
            </p>
          </div>
        </div>
      </div>
    )
  }

  const color = STATUS_COLORS[job.status]
  const output = streamOutput || job.output || ''
  const isRunning = job.status === 'running'

  return (
    <div className="flex-1 flex flex-col overflow-hidden" style={{ background: 'var(--bg-primary)' }}>
      {/* Job header */}
      <div
        className="px-5 py-4 flex items-start gap-4"
        style={{ borderBottom: '1px solid var(--border-color)', background: 'var(--bg-secondary)' }}
      >
        <div className="flex-1 min-w-0">
          {/* Status row */}
          <div className="flex items-center gap-2.5 mb-2.5">
            <span
              className="px-2.5 py-1 flex items-center gap-1.5"
              style={{
                fontSize: '13px',
                fontWeight: 600,
                background: `${color}15`,
                border: `1px solid ${color}40`,
                color,
                borderRadius: '6px',
                textTransform: 'uppercase',
                letterSpacing: '0.03em',
              }}
            >
              {isRunning && <span className="inline-block w-2 h-2 rounded-full animate-pulse" style={{ background: color }} />}
              {job.status}
            </span>
            <span style={{
              fontSize: '13px',
              fontWeight: 500,
              color: 'var(--text-tertiary)',
              padding: '2px 8px',
              borderRadius: '4px',
              background: 'var(--hover-bg)',
            }}>
              {job.model.toUpperCase()}
            </span>
            {job.work_dir && (
              <span style={{ fontSize: '13px', color: 'var(--text-tertiary)', fontFamily: 'monospace' }}>
                {job.work_dir.replace(/.*\/orbit\//, '').replace(/.*\/mod\//, '')}
              </span>
            )}
            <span style={{ fontSize: '13px', color: 'var(--text-tertiary)', fontFamily: 'monospace', marginLeft: 'auto', opacity: 0.6 }}>
              {job.id.slice(0, 8)}
            </span>
          </div>

          {/* Prompt */}
          <div
            style={{
              fontSize: '14px',
              color: 'var(--text-primary)',
              lineHeight: '1.6',
              fontFamily: '"JetBrains Mono", "Fira Code", monospace',
              whiteSpace: 'pre-wrap',
              maxHeight: '80px',
              overflow: 'auto',
            }}
          >
            {job.prompt}
          </div>
        </div>

        {isRunning && (
          <button
            onClick={() => onCancel(job.id)}
            className="px-4 py-2 shrink-0 transition-all flex items-center gap-2"
            style={{
              fontSize: '14px',
              fontWeight: 600,
              background: 'rgba(239, 68, 68, 0.1)',
              border: '1px solid rgba(239, 68, 68, 0.3)',
              color: '#ef4444',
              cursor: 'pointer',
              borderRadius: '8px',
            }}
            onMouseEnter={e => { e.currentTarget.style.background = 'rgba(239, 68, 68, 0.2)' }}
            onMouseLeave={e => { e.currentTarget.style.background = 'rgba(239, 68, 68, 0.1)' }}
          >
            &#9632; Stop
          </button>
        )}
      </div>

      {/* Output area */}
      <div
        ref={outputRef}
        className="flex-1 overflow-y-auto p-5"
        style={{ background: 'var(--bg-primary)' }}
      >
        {output ? (
          <pre
            className="m-0 whitespace-pre-wrap"
            style={{
              fontSize: '14px',
              fontFamily: '"JetBrains Mono", "Fira Code", monospace',
              color: 'var(--text-primary)',
              lineHeight: '1.7',
              wordBreak: 'break-word',
            }}
          >
            {output}
            {isRunning && (
              <span className="inline-block animate-pulse" style={{ color }}>&#9610;</span>
            )}
          </pre>
        ) : (
          <div className="flex items-center justify-center h-full">
            <div className="text-center space-y-3">
              {isRunning && (
                <div className="flex items-center justify-center gap-1.5">
                  <span className="w-2 h-2 rounded-full animate-pulse" style={{ background: color }} />
                  <span className="w-2 h-2 rounded-full animate-pulse" style={{ background: color, animationDelay: '0.2s' }} />
                  <span className="w-2 h-2 rounded-full animate-pulse" style={{ background: color, animationDelay: '0.4s' }} />
                </div>
              )}
              <p style={{ fontSize: '14px', color: 'var(--text-tertiary)' }}>
                {job.status === 'pending' ? 'Waiting to start...' :
                 job.status === 'running' ? 'Connecting to stream...' :
                 'No output'}
              </p>
            </div>
          </div>
        )}

        {job.error && (
          <div
            className="mt-4 p-4"
            style={{
              background: 'rgba(239, 68, 68, 0.06)',
              border: '1px solid rgba(239, 68, 68, 0.2)',
              borderRadius: '10px',
            }}
          >
            <span style={{ fontSize: '13px', fontWeight: 600, color: '#ef4444', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Error</span>
            <pre
              className="m-0 mt-2 whitespace-pre-wrap"
              style={{ fontSize: '14px', fontFamily: 'monospace', color: '#ef4444', lineHeight: '1.6' }}
            >
              {job.error}
            </pre>
          </div>
        )}
      </div>
    </div>
  )
}
