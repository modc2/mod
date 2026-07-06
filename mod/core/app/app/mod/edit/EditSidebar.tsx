"use client"

import { useState, useEffect } from 'react'
import { usePathname } from 'next/navigation'
import { useLayoutContext } from '@/context/LayoutContext'
import { userContext } from '@/context'
import { ModuleType } from '@/types'
import { text2color, colorWithOpacity } from '@/utils'
import { X, Loader2, Wrench } from 'lucide-react'
import { useEditorJobs } from './useEditorJobs'
import { useEditorChat } from './useEditorChat'
import { EditorChat } from './EditorChat'
import { EditorTasks } from './EditorTasks'

function getModuleFromPath(pathname: string): string | null {
  const modMatch = pathname.match(/^\/mod\/([^/]+)/)
  if (modMatch) return modMatch[1]
  const knownRoutes = ['mod', 'mods', 'user', 'cid', 'chat', 'docs', 'quests', 'create', 'safe', 'bridge', 'contracts', 'treasury', 'jobs', 'traders', 'network', 'home', 'transactions', 'buidl', 'apps', 'chain', 'balancer', 'workers']
  const twoSegMatch = pathname.match(/^\/([^/]+)\/([^/]+)$/)
  if (twoSegMatch && !knownRoutes.includes(twoSegMatch[1])) return twoSegMatch[1]
  const singleMatch = pathname.match(/^\/([^/]+)$/)
  if (singleMatch && !knownRoutes.includes(singleMatch[1])) return singleMatch[1]
  return null
}

export const EDIT_SIDEBAR_WIDTH = 420

export function EditSidebar() {
  const { isEditSidebarOpen, setEditSidebarOpen } = useLayoutContext()
  const { client, user } = userContext()
  const pathname = usePathname()
  const activeModule = getModuleFromPath(pathname)

  const [mod, setMod] = useState<ModuleType | null>(null)
  const [loading, setLoading] = useState(false)

  const [model, setModel] = useState(() => {
    if (typeof window !== 'undefined') return localStorage.getItem('editor_model') || 'sonnet'
    return 'sonnet'
  })
  const [agentType, setAgentType] = useState(() => {
    if (typeof window !== 'undefined') return localStorage.getItem('editor_agent_type') || 'default'
    return 'default'
  })

  useEffect(() => { localStorage.setItem('editor_model', model) }, [model])
  useEffect(() => { localStorage.setItem('editor_agent_type', agentType) }, [agentType])

  const moduleColor = mod ? text2color(mod.name || mod.key) : activeModule ? text2color(activeModule) : '#a78bfa'

  // Fetch module info when sidebar opens or module changes
  useEffect(() => {
    if (!isEditSidebarOpen || !activeModule || !client) {
      setMod(null)
      return
    }
    setLoading(true)
    client.call('mod', { mod: activeModule, expand: true, schema: true })
      .then((data: any) => {
        if (data && !data.error) setMod(data as ModuleType)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [isEditSidebarOpen, activeModule, client])

  const {
    recentJobs, activeJobId, activeJobOutput, isJobRunning, runningCount,
    fetchJobs, handleCancel, selectJob,
  } = useEditorJobs({
    modName: mod?.name || null,
    token: client?.token || '',
    enabled: isEditSidebarOpen && !!mod,
  })

  const {
    message, setMessage, messages, sending, chatEndRef, inputRef, handleSend, updateJobStatus,
  } = useEditorChat({
    modName: mod?.name || null,
    client,
    userKey: user?.key,
    modKey: mod?.key,
    model,
    agentType,
    onJobCreated: (jobId) => selectJob(jobId),
  })

  // Wire up job status updates
  useEffect(() => {
    fetchJobs(updateJobStatus)
  }, [fetchJobs, updateJobStatus])

  // Focus input when sidebar opens
  useEffect(() => {
    if (isEditSidebarOpen) {
      setTimeout(() => inputRef.current?.focus(), 200)
    }
  }, [isEditSidebarOpen, inputRef])

  if (!isEditSidebarOpen || !activeModule) return null

  return (
    <div
      className="fixed right-0 z-[65] flex flex-col"
      style={{
        top: '48px',
        bottom: 0,
        width: `${EDIT_SIDEBAR_WIDTH}px`,
        background: 'var(--bg-primary)',
        borderLeft: '1px solid var(--border-color)',
        fontFamily: 'var(--font-digital), monospace',
        animation: 'slideInRight 0.2s ease-out',
      }}
    >
      {/* Header */}
      <div
        className="flex items-center justify-between px-3 py-2 shrink-0"
        style={{ borderBottom: '1px solid var(--border-color)', background: 'var(--bg-surface)' }}
      >
        <div className="flex items-center gap-2 min-w-0">
          <Wrench size={14} style={{ color: moduleColor }} />
          <span
            className="text-xs font-bold uppercase tracking-wider truncate"
            style={{ color: moduleColor, textShadow: `0 0 8px ${colorWithOpacity(moduleColor, 0.4)}` }}
          >
            {activeModule}
          </span>
          {mod && (
            <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
              [{mod.schema ? Object.keys(mod.schema).length : 0}]
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => setEditSidebarOpen(false)}
            className="flex items-center justify-center transition-all rounded"
            style={{ width: '24px', height: '24px', color: 'var(--text-tertiary)' }}
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex-1 flex items-center justify-center">
          <Loader2 size={20} className="animate-spin" style={{ color: moduleColor }} />
        </div>
      ) : (
        <div className="flex-1 flex flex-col overflow-hidden">
          <EditorChat
            messages={messages}
            message={message}
            setMessage={setMessage}
            onSend={handleSend}
            sending={sending}
            disabled={!mod}
            moduleColor={moduleColor}
            modName={activeModule}
            chatEndRef={chatEndRef}
            inputRef={inputRef}
            activeJobOutput={activeJobOutput}
            activeJobId={activeJobId}
            isJobRunning={isJobRunning}
            onCancel={handleCancel}
            model={model}
            onModelChange={setModel}
            agentType={agentType}
            onAgentTypeChange={setAgentType}
            variant="compact"
            emptyTitle={`Edit ${activeModule}`}
          />

          <EditorTasks
            jobs={recentJobs}
            activeJobId={activeJobId}
            moduleColor={moduleColor}
            runningCount={runningCount}
            onSelectJob={(id, output) => selectJob(id, output)}
            onCancel={handleCancel}
            variant="compact"
          />
        </div>
      )}

      <style jsx global>{`
        @keyframes slideInRight {
          from { transform: translateX(100%); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
      `}</style>
    </div>
  )
}
