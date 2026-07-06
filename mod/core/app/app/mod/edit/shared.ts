export const JOBS_API = process.env.NEXT_PUBLIC_CLAUDE_JOBS_URL || 'http://localhost:8820'

export interface Job {
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

export interface ChatMessage {
  role: 'user' | 'agent' | 'system'
  content: string
  timestamp: number
  jobId?: string
  status?: 'pending' | 'running' | 'completed' | 'failed'
  cid?: string
}

export function timeSince(ts: number): string {
  const seconds = Math.floor(Date.now() / 1000 - ts)
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

export const STATUS_CONFIG: Record<string, { color: string; label: string }> = {
  running:   { color: '#3b82f6', label: 'RUNNING' },
  pending:   { color: '#fbbf24', label: 'PENDING' },
  completed: { color: '#22c55e', label: 'COMPLETED' },
  failed:    { color: '#ef4444', label: 'FAILED' },
  cancelled: { color: '#64748b', label: 'CANCELLED' },
}

export const MODELS = [
  { value: 'opus', label: 'Opus', color: '#a78bfa' },
  { value: 'sonnet', label: 'Sonnet', color: '#60a5fa' },
  { value: 'haiku', label: 'Haiku', color: '#34d399' },
]

export interface AgentType {
  value: string
  label: string
  icon: string
  prompt: string
}

export const AGENT_TYPES: AgentType[] = [
  { value: 'default', label: 'Terminal', icon: '>_', prompt: '' },
  { value: 'architect', label: 'Architect', icon: '\u25B3', prompt: 'You are a senior software architect. Design systems, plan implementations, and reason about tradeoffs. Think in systems. Favor simplicity. Plan before building.' },
  { value: 'reviewer', label: 'Reviewer', icon: '\u25C9', prompt: 'You are an expert code reviewer. Find bugs, suggest improvements, ensure code quality. Be thorough. Be constructive. Prioritize correctness > security > performance > style.' },
  { value: 'debugger', label: 'Debugger', icon: '\u2B21', prompt: 'You are an expert debugger. Find root causes, not symptoms. Reproduce first, trace the data, question assumptions, fix the root cause.' },
  { value: 'builder', label: 'Builder', icon: '\u25C6', prompt: 'You are a rapid builder. Ship features fast with production quality. Read first, understand patterns, then build. Test your changes.' },
  { value: 'refactorer', label: 'Refactorer', icon: '\u27F3', prompt: 'You are a refactoring specialist. Improve code structure without changing behavior. Test first, make incremental improvements, follow existing patterns.' },
]
