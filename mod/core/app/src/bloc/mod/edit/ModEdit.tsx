'use client'

import { useState } from 'react'
import { useUserContext } from '@/bloc/context'
import { ModuleType } from '@/bloc/types'
import { Send, Loader2, MessageSquare, Clock, GitBranch, AlertCircle, CheckCircle } from 'lucide-react'
import ModVersions from '@/bloc/mod/versions/ModVersions'

interface ModEditProps {
  mod: ModuleType
}

const ui = {
  bg: '#0b0b0b',
  panel: '#121212',
  panelAlt: '#151515',
  border: '#2a2a2a',
  text: '#e7e7e7',
  textDim: '#a8a8a8',
  purple: '#a855f7',
  yellow: '#fbbf24',
}

export default function ModEdit({ mod }: ModEditProps) {
  const { client } = useUserContext()
  const [message, setMessage] = useState('')
  const [chat, setChat] = useState<Array<{ role: 'user' | 'assistant', content: string }>>([])
  const [loading, setLoading] = useState(false)
  const [pendingTx, setPendingTx] = useState<{ message: string, cid?: string } | null>(null)
  const [pendingVersion, setPendingVersion] = useState<{ cid: string, comment: string | null, updated: string } | null>(null)

  const handleSend = async () => {
    if (!message.trim() || !client) return
    const userMessage = message
    setMessage('')
    setChat(prev => [...prev, { role: 'user', content: userMessage }])
    setLoading(true)
    setPendingTx({ message: 'Processing your edit request...' })

    try {
      const response = await client.call('edit', {
        mod: mod.name,
        key: mod.key,
        message: userMessage,
        history: chat
      })
      
      setChat(prev => [...prev, { role: 'assistant', content: response.content || response }])
      
      if (response.cid) {
        setPendingVersion({
          cid: response.cid,
          comment: userMessage,
          updated: new Date().toISOString().replace('T', ' ').slice(0, 19)
        })
        setPendingTx({ message: 'Transaction pending - new version will be finalized shortly', cid: response.cid })
      } else {
        setPendingTx(null)
      }
    } catch (err: any) {
      console.error('Edit failed:', err)
      setChat(prev => [...prev, { role: 'assistant', content: `Error: ${err?.message || 'Failed to process edit'}` }])
      setPendingTx(null)
    } finally {
      setLoading(false)
      }
    }
  
    return (
      <div className="flex flex-col h-[600px]" style={{ backgroundColor: ui.bg }}>
        <div className="p-4 border-b" style={{ backgroundColor: ui.panel, borderColor: ui.border }}>
          <div className="flex items-center gap-2">
            <MessageSquare className="w-5 h-5" style={{ color: ui.purple }} />
            <h3 className="text-xl font-bold" style={{ color: ui.text }}>Edit Module</h3>
          </div>
          <p className="text-sm mt-1" style={{ color: ui.textDim }}>Chat with AI to make edits to your module</p>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4" style={{ backgroundColor: ui.panelAlt }}>
          {chat.length === 0 ? (
            <div className="flex items-center justify-center h-full">
              <p style={{ color: ui.textDim }}>Start a conversation to edit your module...</p>
            </div>
          ) : (
            chat.map((msg, idx) => (
              <div
                key={idx}
                className={`p-4 rounded-lg max-w-[80%] ${msg.role === 'user' ? 'ml-auto' : 'mr-auto'}`}
                style={{
                  backgroundColor: msg.role === 'user' ? ui.purple + '30' : ui.panel,
                  borderLeft: msg.role === 'assistant' ? `3px solid ${ui.purple}` : 'none'
                }}
              >
                  <div className="text-xs font-bold mb-1" style={{ color: ui.textDim }}>
                    {msg.role === 'user' ? 'You' : 'AI Assistant'}
                  </div>
                  <div style={{ color: ui.text }}>{msg.content}</div>
                </div>
              ))
            )}
          </div>

          {(pendingTx || error) && (
            <div
              className={`p-4 rounded-lg border-2 ${
                  error ? 'bg-gradient-to-br from-red-500/10 border-red-500/40'
                  : loading
                  ? 'bg-gradient-to-br from-yellow-500/10 border-yellow-500/40'
                  : 'bg-gradient-to-br from-green-500/10 border-green-500/40'
              }`}
            >
              <div className="flex items-center gap-2 mb-2">
                {error ? (
                  <>
                    <AlertCircle className="w-4 h-4" style={{ color: '#ef4444' }} />
                    <span className="font-bold text-sm" style={{ color: '#ef4444' }}>ERROR</span>
                  </>
                ) : loading ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" style={{ color: ui.yellow }} />
                    <span className="font-bold text-sm" style={{ color: ui.yellow }}>PENDING</span>
                  </>
                ) : (
                  <>
                    <CheckCircle className="w-4 h-4" style={{ color: '#22c55e' }} />
                    <span className="font-bold text-sm" style={{ color: '#22c55e' }}>SUCCESS</span>
                  </>
                )}
              </div>
              <p className="text-sm" style={{ color: ui.textDim }}>
                {error || pendingTx?.message || 'Processing...'}
              </p>
            </div>
          )}

          {pendingVersion && (
            <div className="p-4 rounded-lg border-2" style={{ backgroundColor: ui.panel, borderColor: ui.purple }}>
              <div className="flex items-center gap-2 mb-2">
                <GitBranch className="w-4 h-4" style={{ color: ui.purple }} />
                <span className="font-bold text-sm" style={{ color: ui.purple }}>PENDING VERSION</span>
              </div>
              <div className="space-y-1 text-xs" style={{ color: ui.textDim }}>
                <div>CID: {pendingVersion.cid}</div>
                <div>Comment: {pendingVersion.comment || 'No comment'}</div>
                <div>Updated: {pendingVersion.updated}</div>
              </div>
            </div>
          )}

        <div className="p-4 border-t" style={{ backgroundColor: ui.panel, borderColor: ui.border }}>
          <div className="flex gap-2">
            <input
              type="text"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleSend()}
              placeholder="Describe the changes you want to make..."
              disabled={loading}
              className="flex-1 px-4 py-3 rounded-lg border outline-none"
              style={{
                backgroundColor: ui.panelAlt,
                borderColor: ui.border,
                color: ui.text
              }}
            />
            <button
              onClick={handleSend}
              disabled={!message.trim() || loading}
              className="px-6 py-3 rounded-lg font-bold transition-all disabled:opacity-50"
              style={{
                backgroundColor: ui.purple + '40',
                borderColor: ui.purple,
                color: ui.purple,
                border: '2px solid'
              }}
            >
              {loading ? <Loader2 className="w-5 h-5 animate-spin" /> : <Send className="w-5 h-5" />}
            </button>
          </div>
        </div>

        <div className="p-4 border-t" style={{ backgroundColor: ui.panel, borderColor: ui.border }}>
          <ModVersions mod={mod} />
        </div>
      </div>
  )
}
