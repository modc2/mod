"use client";

import { useState, useEffect } from 'react'
import { userContext } from '@/context'
import { ModuleType } from '@/types'
import { text2color } from '@/utils'
import { useEditorJobs } from './useEditorJobs'
import { useEditorChat } from './useEditorChat'
import { EditorChat } from './EditorChat'
import { EditorTasks } from './EditorTasks'

interface ModEditProps {
  mod: ModuleType
  moduleColor?: string
  isSuggestion?: boolean
}

export default function ModEdit({ mod, moduleColor, isSuggestion }: ModEditProps) {
  const { client, user } = userContext()
  const modColor = moduleColor || text2color(mod.name || mod.key)

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

  const {
    recentJobs, jobsError, activeJobId, activeJobOutput, isJobRunning, runningCount,
    fetchJobs, handleCancel, selectJob,
  } = useEditorJobs({
    modName: mod.name,
    token: client?.token || '',
  })

  const {
    message, setMessage, messages, sending, chatEndRef, inputRef, handleSend, updateJobStatus,
  } = useEditorChat({
    modName: mod.name,
    client,
    userKey: user?.key,
    modKey: mod.key,
    model,
    agentType,
    onJobCreated: (jobId) => selectJob(jobId),
  })

  // Wire up job status updates to chat messages
  useEffect(() => {
    fetchJobs(updateJobStatus)
  }, [fetchJobs, updateJobStatus])

  return (
    <div className="flex flex-col" style={{ height: 'calc(100vh - 70px)', fontFamily: 'var(--font-digital), monospace' }}>
      <div className="flex-1 flex overflow-hidden">
        {/* Left: Chat + Live Output */}
        <div className="flex-1 flex flex-col overflow-hidden" style={{ borderRight: '1px solid var(--border-color)' }}>
          <EditorChat
            messages={messages}
            message={message}
            setMessage={setMessage}
            onSend={handleSend}
            sending={sending}
            moduleColor={modColor}
            modName={mod.name}
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
            variant="full"
            placeholder={isSuggestion ? `suggest changes to ${mod.name}...` : `edit ${mod.name}...`}
            emptyTitle={isSuggestion ? `Suggest changes to ${mod.name}` : `Dev Agent for ${mod.name}`}
            emptySubtitle={isSuggestion
              ? 'Describe your suggestion — it will be submitted as a proposal to the owner'
              : 'Describe changes and Claude will edit the module code'}
          />
        </div>

        {/* Right: Task list */}
        <EditorTasks
          jobs={recentJobs}
          activeJobId={activeJobId}
          moduleColor={modColor}
          runningCount={runningCount}
          jobsError={jobsError}
          onSelectJob={(id, output) => selectJob(id, output)}
          onCancel={handleCancel}
          variant="full"
        />
      </div>
    </div>
  )
}
