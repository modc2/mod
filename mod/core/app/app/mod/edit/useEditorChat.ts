import { useState, useRef, useCallback } from 'react'
import { ChatMessage } from './shared'

interface UseEditorChatOptions {
  modName: string | null
  client: any
  userKey?: string
  modKey?: string
  model?: string
  agentType?: string
  onJobCreated?: (jobId: string) => void
}

export function useEditorChat({ modName, client, userKey, modKey, model, agentType, onJobCreated }: UseEditorChatOptions) {
  const [message, setMessage] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [sending, setSending] = useState(false)
  const chatEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const updateJobStatus = useCallback((jobId: string, status: 'completed' | 'failed') => {
    setMessages(prev => prev.map(m =>
      m.jobId === jobId ? { ...m, status } : m
    ))
  }, [])

  const handleSend = useCallback(async () => {
    if (!message.trim() || !client || !modName) return
    const query = message.trim()
    setMessage('')
    inputRef.current?.focus()

    setMessages(prev => [...prev, {
      role: 'user',
      content: query,
      timestamp: Date.now() / 1000,
    }])

    setSending(true)
    try {
      const params: any = {
        query,
        mod: modName,
        key: userKey || modKey,
        background: true,
      }
      if (model) params.model = model
      if (agentType && agentType !== 'default') params.agent_type = agentType

      const result = await client.call('claude/forward', params, true, {}, 60000)

      if (result?.id) {
        onJobCreated?.(result.id)
        setMessages(prev => [...prev, {
          role: 'agent',
          content: 'Job submitted',
          timestamp: Date.now() / 1000,
          jobId: result.id,
          status: 'running',
        }])
      } else if (result?.cid) {
        setMessages(prev => [...prev, {
          role: 'agent',
          content: 'Done',
          timestamp: Date.now() / 1000,
          cid: result.cid,
          status: 'completed',
        }])
      } else {
        setMessages(prev => [...prev, {
          role: 'agent',
          content: JSON.stringify(result, null, 2),
          timestamp: Date.now() / 1000,
          status: 'completed',
        }])
      }
    } catch (err: any) {
      setMessages(prev => [...prev, {
        role: 'agent',
        content: err?.message || 'Failed to submit edit',
        timestamp: Date.now() / 1000,
        status: 'failed',
      }])
    } finally {
      setSending(false)
    }
  }, [message, client, modName, userKey, modKey, model, agentType, onJobCreated])

  return {
    message,
    setMessage,
    messages,
    sending,
    chatEndRef,
    inputRef,
    handleSend,
    updateJobStatus,
  }
}
