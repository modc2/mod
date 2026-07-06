"use client"

import { colorWithOpacity } from '@/utils'
import { Clock } from 'lucide-react'
import { Job, STATUS_CONFIG, timeSince } from './shared'
import { StatusIcon } from './StatusIcon'

interface EditorTasksProps {
  jobs: Job[]
  activeJobId: string | null
  moduleColor: string
  runningCount: number
  jobsError?: boolean
  onSelectJob: (jobId: string, output?: string) => void
  onCancel: (jobId: string) => void
  variant?: 'full' | 'compact'
}

export function EditorTasks({
  jobs, activeJobId, moduleColor, runningCount, jobsError,
  onSelectJob, onCancel, variant = 'full',
}: EditorTasksProps) {
  const isCompact = variant === 'compact'

  // Compact: render inline (no wrapper chrome needed, caller wraps)
  // Full: render as a sidebar column

  if (isCompact) {
    if (jobs.length === 0) return null
    return (
      <div
        className="overflow-y-auto border-t shrink-0"
        style={{
          maxHeight: '300px',
          borderColor: 'var(--border-color)',
          background: 'var(--bg-surface)',
          scrollbarWidth: 'thin',
        }}
      >
        {/* Header */}
        <div className="px-3 py-2 border-b sticky top-0 z-10" style={{ borderColor: 'var(--border-color)', background: 'var(--bg-surface)' }}>
          <div className="flex items-center gap-2">
            <Clock size={12} style={{ color: 'var(--text-primary)' }} />
            <span className="text-[11px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-primary)' }}>TASKS</span>
            <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full" style={{ backgroundColor: colorWithOpacity(moduleColor, 0.12), color: moduleColor }}>
              {jobs.length}
            </span>
            {runningCount > 0 && (
              <span className="text-[10px] font-bold flex items-center gap-1" style={{ color: '#3b82f6' }}>
                <span className="inline-block w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: '#3b82f6' }} />
                {runningCount} active
              </span>
            )}
          </div>
        </div>

        {/* Cards */}
        {jobs.map((job) => (
          <TaskCard
            key={job.id}
            job={job}
            isActive={job.id === activeJobId}
            onSelect={() => onSelectJob(job.id, job.output)}
            onCancel={() => onCancel(job.id)}
          />
        ))}
      </div>
    )
  }

  // Full variant — sidebar column
  return (
    <div className="w-72 shrink-0 flex flex-col overflow-hidden" style={{ background: 'var(--bg-surface)' }}>
      {/* Header */}
      <div className="px-3 py-2.5 border-b shrink-0" style={{ borderColor: 'var(--border-color)' }}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Clock size={12} style={{ color: 'var(--text-primary)' }} />
            <span className="text-[11px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-primary)' }}>TASKS</span>
            <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full" style={{ backgroundColor: colorWithOpacity(moduleColor, 0.12), color: moduleColor }}>
              {jobs.length}
            </span>
          </div>
          {runningCount > 0 && (
            <span className="text-[10px] font-bold flex items-center gap-1" style={{ color: '#3b82f6' }}>
              <span className="inline-block w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: '#3b82f6' }} />
              {runningCount}
            </span>
          )}
        </div>
      </div>

      {/* Cards */}
      <div className="flex-1 overflow-y-auto" style={{ scrollbarWidth: 'thin' }}>
        {jobsError ? (
          <p className="text-[11px] p-3" style={{ color: 'var(--text-tertiary)' }}>Jobs server offline</p>
        ) : jobs.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-2" style={{ opacity: 0.3 }}>
            <Clock size={24} style={{ color: 'var(--text-tertiary)' }} />
            <p className="text-[11px]" style={{ color: 'var(--text-tertiary)' }}>No tasks yet</p>
          </div>
        ) : (
          jobs.map((job) => (
            <TaskCard
              key={job.id}
              job={job}
              isActive={job.id === activeJobId}
              onSelect={() => onSelectJob(job.id, job.output)}
              onCancel={() => onCancel(job.id)}
            />
          ))
        )}
      </div>
    </div>
  )
}

function TaskCard({ job, isActive, onSelect, onCancel }: {
  job: Job; isActive: boolean; onSelect: () => void; onCancel: () => void
}) {
  const sc = STATUS_CONFIG[job.status] || STATUS_CONFIG.pending
  return (
    <button
      onClick={onSelect}
      className="w-full text-left transition-all group"
      style={{
        borderBottom: '1px solid var(--border-color)',
        borderLeft: `3px solid ${isActive ? sc.color : 'transparent'}`,
        background: isActive ? colorWithOpacity(sc.color, 0.06) : 'transparent',
      }}
    >
      <div className="px-3 py-2.5">
        <div className="flex items-center justify-between mb-1.5">
          <div className="flex items-center gap-1.5">
            <StatusIcon status={job.status} size={11} />
            <span className="text-[10px] font-bold uppercase tracking-wide" style={{ color: sc.color }}>
              {sc.label}
            </span>
          </div>
          <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
            {timeSince(job.created_at)}
          </span>
        </div>

        <p
          className="text-[11px] leading-relaxed"
          style={{
            color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
          }}
        >
          {job.prompt}
        </p>

        {(job.status === 'running' || job.status === 'pending') && (
          <div className="mt-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
            <span
              onClick={(e) => { e.stopPropagation(); onCancel() }}
              className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded cursor-pointer transition-all"
              style={{
                color: '#ef4444',
                border: '1px solid rgba(239,68,68,0.25)',
                background: 'rgba(239,68,68,0.06)',
              }}
            >
              CANCEL
            </span>
          </div>
        )}
      </div>
    </button>
  )
}
