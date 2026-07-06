"use client"

import { useState } from 'react'

const STATUS_COLORS: Record<string, string> = {
  pending: '#f59e0b',
  running: '#3b82f6',
  completed: '#10b981',
  failed: '#ef4444',
  cancelled: '#6b7280',
}

const STATUS_ICONS: Record<string, string> = {
  pending: '\u25CB',
  running: '\u25CF',
  completed: '\u2713',
  failed: '\u2715',
  cancelled: '\u25A0',
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

function timeSince(ts: number): string {
  const s = Math.floor(Date.now() / 1000 - ts)
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86400)}d`
}

const FILTER_TABS = [
  { key: 'all', label: 'All' },
  { key: 'running', label: 'Running' },
  { key: 'completed', label: 'Done' },
  { key: 'failed', label: 'Failed' },
]

interface AgentSidebarProps {
  jobs: Job[]
  selectedJob: string | null
  onSelectJob: (job: Job) => void
  onCancelJob: (id: string) => void
  onDeleteJob: (id: string) => void
  collapsed: boolean
  onToggle: () => void
  embedded?: boolean
  modules?: string[]
  workDir?: string
  onSelectModule?: (name: string) => void
  onClearModule?: () => void
  prompt?: string
  onPromptChange?: (value: string) => void
  onSubmit?: () => void
  submitting?: boolean
  model?: string
  personalityIcon?: string
  personalityName?: string
}

export default function AgentSidebar({
  jobs, selectedJob, onSelectJob, onCancelJob, onDeleteJob, collapsed, onToggle, embedded,
  modules, workDir, onSelectModule, onClearModule,
  prompt, onPromptChange, onSubmit, submitting, model, personalityIcon, personalityName,
}: AgentSidebarProps) {
  const [filter, setFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [modsExpanded, setModsExpanded] = useState(true)
  const [modSearch, setModSearch] = useState('')

  const filtered = jobs.filter(j => {
    if (filter !== 'all' && j.status !== filter) return false
    if (search && !j.prompt.toLowerCase().includes(search.toLowerCase())) return false
    return true
  })

  if (collapsed && !embedded) {
    return (
      <div
        className="h-full flex flex-col items-center py-3 cursor-pointer"
        style={{
          width: '40px',
          borderRight: '1px solid var(--border-color)',
          background: 'var(--bg-secondary)',
        }}
        onClick={onToggle}
      >
        <span style={{ fontSize: '13px', color: 'var(--text-secondary)', writingMode: 'vertical-rl', fontWeight: 600, letterSpacing: '0.1em' }}>
          TASKS
        </span>
        {jobs.filter(j => j.status === 'running').length > 0 && (
          <span className="mt-2 w-2 h-2 rounded-full animate-pulse" style={{ background: '#3b82f6' }} />
        )}
      </div>
    )
  }

  return (
    <div
      className={embedded ? "flex-1 flex flex-col overflow-hidden min-h-0" : "h-full flex flex-col overflow-hidden"}
      style={embedded ? {} : {
        width: '340px',
        minWidth: '340px',
        borderRight: '1px solid var(--border-color)',
        background: 'var(--bg-secondary)',
      }}
    >
      {/* Header */}
      <div className="px-4 py-3 flex items-center justify-between shrink-0" style={{ borderBottom: '1px solid var(--border-color)' }}>
        <div className="flex items-center gap-2.5">
          <span style={{ fontSize: '16px', fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '0.02em' }}>
            Tasks
          </span>
          <span
            className="px-2 py-0.5"
            style={{
              fontSize: '13px',
              fontWeight: 500,
              color: 'var(--text-tertiary)',
              background: 'var(--hover-bg)',
              borderRadius: '10px',
            }}
          >
            {jobs.length}
          </span>
        </div>
        {!embedded && (
          <button
            onClick={onToggle}
            className="w-7 h-7 flex items-center justify-center rounded-md transition-colors"
            style={{ color: 'var(--text-tertiary)', background: 'transparent' }}
            onMouseEnter={e => (e.currentTarget.style.background = 'var(--hover-bg)')}
            onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="15 18 9 12 15 6" />
            </svg>
          </button>
        )}
      </div>

      {/* Prompt input section */}
      {onPromptChange && onSubmit && (
        <div className="shrink-0 p-3" style={{ borderBottom: '1px solid var(--border-color)', background: 'var(--bg-tertiary)' }}>
          <div className="flex flex-col gap-2">
            <textarea
              value={prompt || ''}
              onChange={e => onPromptChange(e.target.value)}
              placeholder={workDir
                ? `Working in ${workDir.split('/').pop()}...`
                : `${personalityIcon || '🤖'} Enter task...`
              }
              rows={5}
              disabled={submitting}
              className="resize-none"
              style={{
                fontSize: '15px',
                fontFamily: '"JetBrains Mono", "Fira Code", monospace',
                background: 'var(--bg-input)',
                border: '1px solid var(--border-color)',
                color: 'var(--text-primary)',
                padding: '12px 14px',
                outline: 'none',
                lineHeight: '1.6',
                borderRadius: '8px',
                transition: 'border-color 0.15s',
                width: '100%',
              }}
              onKeyDown={e => {
                if (e.key === 'Enter' && e.metaKey) {
                  e.preventDefault()
                  onSubmit()
                }
              }}
              onFocus={e => (e.currentTarget.style.borderColor = 'var(--accent-primary)')}
              onBlur={e => (e.currentTarget.style.borderColor = 'var(--border-color)')}
            />
            <button
              onClick={onSubmit}
              disabled={submitting || !prompt?.trim()}
              className="px-4 py-2.5 transition-all w-full"
              style={{
                fontSize: '14px',
                fontWeight: 600,
                background: submitting || !prompt?.trim() ? 'var(--hover-bg)' : 'var(--accent-primary)',
                border: 'none',
                color: submitting || !prompt?.trim() ? 'var(--text-tertiary)' : '#000',
                cursor: submitting || !prompt?.trim() ? 'not-allowed' : 'pointer',
                borderRadius: '8px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '6px',
              }}
            >
              {submitting ? (
                <>
                  <span className="inline-block w-2 h-2 rounded-full animate-pulse" style={{ background: 'var(--text-tertiary)' }} />
                  Running...
                </>
              ) : (
                <>
                  <span>▶</span>
                  <span>Run Task</span>
                  <span style={{ opacity: 0.5, fontSize: '11px', marginLeft: 'auto' }}>⌘↵</span>
                </>
              )}
            </button>
          </div>
          {personalityName && (
            <div className="mt-2 flex items-center gap-2" style={{ fontSize: '11px', color: 'var(--text-tertiary)', opacity: 0.7 }}>
              <span>{personalityIcon}</span>
              <span>{personalityName}</span>
              {model && (
                <>
                  <span>·</span>
                  <span style={{ textTransform: 'uppercase' }}>{model}</span>
                </>
              )}
            </div>
          )}
        </div>
      )}

      {/* Mods section */}
      {modules && modules.length > 0 && onSelectModule && (
        <div className="shrink-0" style={{ borderBottom: '1px solid var(--border-color)' }}>
          <div
            className="px-3 py-2 flex items-center justify-between cursor-pointer"
            onClick={() => setModsExpanded(!modsExpanded)}
            onMouseEnter={e => (e.currentTarget.style.background = 'var(--hover-bg)')}
            onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
          >
            <div className="flex items-center gap-2 flex-1 min-w-0">
              <span style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)' }}>
                Module
              </span>
              {workDir && (
                <span
                  className="px-2 py-0.5 truncate"
                  style={{
                    fontSize: '11px',
                    fontWeight: 700,
                    color: '#10b981',
                    background: 'rgba(16, 185, 129, 0.12)',
                    border: '1px solid rgba(16, 185, 129, 0.3)',
                    borderRadius: '5px',
                    fontFamily: 'monospace',
                    maxWidth: '140px',
                  }}
                >
                  {workDir.replace(/.*\/orbit\//, '').replace(/~\/mod\/mod\/orbit\//, '')}
                </span>
              )}
            </div>
            <div className="flex items-center gap-1 shrink-0">
              {workDir && onClearModule && (
                <button
                  onClick={e => { e.stopPropagation(); onClearModule() }}
                  className="w-6 h-6 flex items-center justify-center rounded transition-colors"
                  style={{ color: 'var(--text-tertiary)', background: 'transparent', border: 'none', cursor: 'pointer', fontSize: '14px' }}
                  onMouseEnter={e => {
                    e.currentTarget.style.color = '#ef4444'
                    e.currentTarget.style.background = 'rgba(239, 68, 68, 0.1)'
                  }}
                  onMouseLeave={e => {
                    e.currentTarget.style.color = 'var(--text-tertiary)'
                    e.currentTarget.style.background = 'transparent'
                  }}
                  title="Clear module"
                >
                  ×
                </button>
              )}
              <svg
                width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                style={{ color: 'var(--text-tertiary)', transform: modsExpanded ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform 0.15s' }}
              >
                <polyline points="6 9 12 15 18 9" />
              </svg>
            </div>
          </div>

          {modsExpanded && (
            <div className="pb-2">
              <div className="px-2 pb-2">
                <input
                  value={modSearch}
                  onChange={e => setModSearch(e.target.value)}
                  placeholder="Search modules..."
                  autoFocus
                  style={{
                    width: '100%',
                    fontSize: '14px',
                    fontFamily: '"JetBrains Mono", "Fira Code", monospace',
                    background: 'var(--bg-input)',
                    border: '1px solid var(--border-color)',
                    color: 'var(--text-primary)',
                    padding: '8px 12px',
                    outline: 'none',
                    borderRadius: '6px',
                  }}
                  onFocus={e => (e.currentTarget.style.borderColor = '#10b981')}
                  onBlur={e => (e.currentTarget.style.borderColor = 'var(--border-color)')}
                />
              </div>
              <div className="overflow-y-auto px-2" style={{ maxHeight: '240px' }}>
                {modules
                  .filter(m => !modSearch || m.toLowerCase().includes(modSearch.toLowerCase()))
                  .map(m => {
                    const isActive = workDir === `~/mod/mod/orbit/${m}`
                    return (
                      <div
                        key={m}
                        onClick={() => { onSelectModule(m); setModsExpanded(false); setModSearch('') }}
                        className="px-2.5 py-2 rounded-md cursor-pointer transition-colors flex items-center gap-2"
                        style={{
                          fontSize: '13px',
                          fontFamily: '"JetBrains Mono", "Fira Code", monospace',
                          color: isActive ? '#10b981' : 'var(--text-primary)',
                          background: isActive ? 'rgba(16, 185, 129, 0.1)' : 'transparent',
                          border: isActive ? '1px solid rgba(16, 185, 129, 0.25)' : '1px solid transparent',
                          fontWeight: isActive ? 700 : 500,
                          marginBottom: '2px',
                        }}
                        onMouseEnter={e => {
                          if (!isActive) {
                            e.currentTarget.style.background = 'var(--hover-bg)'
                            e.currentTarget.style.borderColor = 'var(--border-color)'
                          }
                        }}
                        onMouseLeave={e => {
                          if (!isActive) {
                            e.currentTarget.style.background = 'transparent'
                            e.currentTarget.style.borderColor = 'transparent'
                          }
                        }}
                      >
                        <span style={{ fontSize: '8px', opacity: isActive ? 0.8 : 0.4 }}>◆</span>
                        {m}
                      </div>
                    )
                  })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Filter tabs */}
      <div className="shrink-0 flex px-2 py-2 gap-1" style={{ borderBottom: '1px solid var(--border-color)', background: 'var(--bg-secondary)' }}>
        {FILTER_TABS.map(tab => {
          const isActive = filter === tab.key
          const count = tab.key === 'all'
            ? jobs.length
            : jobs.filter(j => j.status === tab.key).length
          return (
            <button
              key={tab.key}
              onClick={() => setFilter(tab.key)}
              className="flex-1 px-2 py-1.5 transition-all flex items-center justify-center gap-1.5"
              style={{
                fontSize: '13px',
                fontWeight: isActive ? 700 : 500,
                background: isActive ? 'var(--hover-bg)' : 'transparent',
                color: isActive ? 'var(--text-primary)' : 'var(--text-tertiary)',
                border: isActive ? '1px solid var(--border-color)' : '1px solid transparent',
                borderRadius: '7px',
                cursor: 'pointer',
              }}
            >
              <span>{tab.label}</span>
              {count > 0 && (
                <span
                  style={{
                    fontSize: '10px',
                    fontWeight: 600,
                    background: isActive ? 'var(--bg-secondary)' : 'var(--hover-bg)',
                    color: 'var(--text-tertiary)',
                    padding: '1px 5px',
                    borderRadius: '8px',
                    minWidth: '18px',
                    textAlign: 'center',
                  }}
                >
                  {count}
                </span>
              )}
            </button>
          )
        })}
      </div>

      {/* Search */}
      <div className="shrink-0 px-2 py-2" style={{ borderBottom: '1px solid var(--border-color)' }}>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search tasks..."
          style={{
            width: '100%',
            fontSize: '14px',
            fontFamily: 'inherit',
            background: 'var(--bg-input)',
            border: '1px solid var(--border-color)',
            color: 'var(--text-primary)',
            padding: '8px 12px',
            outline: 'none',
            borderRadius: '7px',
          }}
          onFocus={e => (e.currentTarget.style.borderColor = 'var(--accent-primary)')}
          onBlur={e => (e.currentTarget.style.borderColor = 'var(--border-color)')}
        />
      </div>

      {/* Job list */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {filtered.length === 0 ? (
          <div className="p-6 text-center">
            <div className="w-12 h-12 mx-auto mb-3 rounded-xl flex items-center justify-center" style={{ background: 'var(--hover-bg)' }}>
              <span style={{ fontSize: '22px', opacity: 0.3 }}>{jobs.length === 0 ? '\u25B7' : '\u2300'}</span>
            </div>
            <p style={{ color: 'var(--text-tertiary)', fontSize: '15px', fontWeight: 500 }}>
              {jobs.length === 0 ? 'No tasks yet' : 'No matching tasks'}
            </p>
            {jobs.length === 0 && (
              <p style={{ color: 'var(--text-tertiary)', fontSize: '13px', marginTop: '6px', opacity: 0.6 }}>
                Enter a task above to get started
              </p>
            )}
          </div>
        ) : (
          <div className="px-2 py-2 space-y-2">
            {filtered.map(job => {
              const isSelected = selectedJob === job.id
              const color = STATUS_COLORS[job.status]
              return (
                <div
                  key={job.id}
                  onClick={() => onSelectJob(job)}
                  className="px-3 py-3 cursor-pointer transition-all rounded-lg"
                  style={{
                    background: isSelected ? `${color}12` : 'var(--bg-secondary)',
                    border: `1px solid ${isSelected ? `${color}40` : 'var(--border-color)'}`,
                    position: 'relative',
                  }}
                  onMouseEnter={e => {
                    if (!isSelected) {
                      e.currentTarget.style.background = 'var(--hover-bg)'
                      e.currentTarget.style.borderColor = 'var(--text-tertiary)'
                    }
                  }}
                  onMouseLeave={e => {
                    if (!isSelected) {
                      e.currentTarget.style.background = 'var(--bg-secondary)'
                      e.currentTarget.style.borderColor = 'var(--border-color)'
                    }
                  }}
                >
                  {/* Status indicator line */}
                  <div
                    style={{
                      position: 'absolute',
                      left: 0,
                      top: 0,
                      bottom: 0,
                      width: '3px',
                      background: color,
                      borderTopLeftRadius: '8px',
                      borderBottomLeftRadius: '8px',
                      opacity: isSelected ? 1 : 0.6,
                    }}
                  />

                  {/* Status + Time */}
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <span style={{ color, fontSize: '13px', fontWeight: 700 }}>
                        {STATUS_ICONS[job.status]}
                      </span>
                      <span
                        className="px-2 py-0.5"
                        style={{
                          fontSize: '12px',
                          fontWeight: 700,
                          background: `${color}15`,
                          border: `1px solid ${color}35`,
                          color,
                          borderRadius: '5px',
                          textTransform: 'uppercase',
                          letterSpacing: '0.04em',
                        }}
                      >
                        {job.status}
                      </span>
                    </div>
                    <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', fontFamily: 'monospace', fontWeight: 500 }}>
                      {timeSince(job.created_at)}
                    </span>
                  </div>

                  {/* Prompt preview */}
                  <p
                    style={{
                      fontSize: '14px',
                      color: 'var(--text-primary)',
                      lineHeight: '1.5',
                      display: '-webkit-box',
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: 'vertical',
                      overflow: 'hidden',
                      marginBottom: '8px',
                    }}
                  >
                    {job.prompt}
                  </p>

                  {/* Bottom row: Work dir + Model + Actions */}
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 flex-1 min-w-0">
                      <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', opacity: 0.6 }}>
                        {job.model}
                      </span>
                      {job.work_dir && (
                        <>
                          <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', opacity: 0.4 }}>•</span>
                          <span
                            className="truncate"
                            style={{
                              fontSize: '12px',
                              color: 'var(--text-tertiary)',
                              fontFamily: 'monospace',
                              opacity: 0.6,
                            }}
                          >
                            {job.work_dir.replace(/.*\/orbit\//, '').replace(/.*\/mod\//, '')}
                          </span>
                        </>
                      )}
                    </div>

                    {/* Actions */}
                    <div className="flex gap-1.5 shrink-0">
                      {job.status === 'running' && (
                        <button
                          onClick={e => { e.stopPropagation(); onCancelJob(job.id) }}
                          className="px-2 py-1 transition-colors"
                          style={{
                            fontSize: '11px',
                            fontWeight: 600,
                            color: '#ef4444',
                            background: 'rgba(239, 68, 68, 0.1)',
                            border: '1px solid rgba(239, 68, 68, 0.25)',
                            borderRadius: '4px',
                            cursor: 'pointer',
                          }}
                          onMouseEnter={e => {
                            e.currentTarget.style.background = 'rgba(239, 68, 68, 0.15)'
                          }}
                          onMouseLeave={e => {
                            e.currentTarget.style.background = 'rgba(239, 68, 68, 0.1)'
                          }}
                        >
                          Cancel
                        </button>
                      )}
                      {['completed', 'failed', 'cancelled'].includes(job.status) && (
                        <button
                          onClick={e => { e.stopPropagation(); onDeleteJob(job.id) }}
                          className="w-6 h-6 flex items-center justify-center transition-colors"
                          style={{
                            fontSize: '14px',
                            color: 'var(--text-tertiary)',
                            background: 'transparent',
                            border: '1px solid var(--border-color)',
                            borderRadius: '4px',
                            cursor: 'pointer',
                          }}
                          onMouseEnter={e => {
                            e.currentTarget.style.background = 'rgba(239, 68, 68, 0.1)'
                            e.currentTarget.style.borderColor = 'rgba(239, 68, 68, 0.3)'
                            e.currentTarget.style.color = '#ef4444'
                          }}
                          onMouseLeave={e => {
                            e.currentTarget.style.background = 'transparent'
                            e.currentTarget.style.borderColor = 'var(--border-color)'
                            e.currentTarget.style.color = 'var(--text-tertiary)'
                          }}
                          title="Delete task"
                        >
                          ×
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
