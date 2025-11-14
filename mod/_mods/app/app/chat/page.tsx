'use client'

import { useState, useEffect, useRef } from 'react'
import { useUserContext } from '@/app/block/context/UserContext'
import { Loading } from '@/app/block/Loading'
import { PaperAirplaneIcon, MagnifyingGlassIcon } from '@heroicons/react/24/outline'
import { text2color } from '@/app/utils'

interface Message {
  role: 'user' | 'assistant'
  content: string
  timestamp: number
}

export default function ChatPage() {
  const { client } = useUserContext()
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [modelSearch, setModelSearch] = useState('')
  const [selectedModel, setSelectedModel] = useState<any>(null)
  const [availableModels, setAvailableModels] = useState<any[]>([])
  const [searchingModels, setSearchingModels] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  useEffect(() => {
    const searchModels = async () => {
      if (!client || !modelSearch.trim()) {
        setAvailableModels([])
        return
      }
      
      setSearchingModels(true)
      try {
        const mods = await client.call('mods', {})
        const filtered = Array.isArray(mods) ? mods.filter((mod: any) => 
          mod.name?.toLowerCase().includes(modelSearch.toLowerCase())
        ) : []
        setAvailableModels(filtered)
      } catch (err) {
        console.error('Failed to search models:', err)
        setAvailableModels([])
      } finally {
        setSearchingModels(false)
      }
    }

    const debounce = setTimeout(searchModels, 300)
    return () => clearTimeout(debounce)
  }, [modelSearch, client])

  const handleSendMessage = async () => {
    if (!input.trim() || !selectedModel || !client) return

    const userMessage: Message = {
      role: 'user',
      content: input,
      timestamp: Date.now()
    }

    setMessages(prev => [...prev, userMessage])
    setInput('')
    setLoading(true)

    try {
      const response = await client.call('forward', {
        fn: 'generate',
        kwargs: { prompt: input },
        module: selectedModel.name,
        key: selectedModel.key
      })

      const assistantMessage: Message = {
        role: 'assistant',
        content: typeof response === 'string' ? response : JSON.stringify(response),
        timestamp: Date.now()
      }

      setMessages(prev => [...prev, assistantMessage])
    } catch (err: any) {
      const errorMessage: Message = {
        role: 'assistant',
        content: `Error: ${err?.message || 'Failed to get response'}`,
        timestamp: Date.now()
      }
      setMessages(prev => [...prev, errorMessage])
    } finally {
      setLoading(false)
    }
  }

  const modelColor = selectedModel ? text2color(selectedModel.name) : '#00ff00'

  return (
    <div className="min-h-screen bg-gradient-to-br from-black via-gray-900 to-black text-white flex flex-col">
      <div className="flex-1 flex flex-col max-w-6xl mx-auto w-full p-6">
        <div className="mb-6">
          
          <div className="space-y-3">
            <div className="relative">
              <MagnifyingGlassIcon className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" />
              <input
                type="text"
                value={modelSearch}
                onChange={(e) => setModelSearch(e.target.value)}
                placeholder="Search for a model..."
                className="w-full bg-black/50 border-2 border-white/20 text-white pl-11 pr-4 py-3 rounded-lg focus:outline-none focus:border-green-500/50 transition-all"
              />
            </div>

            {searchingModels && (
              <div className="text-sm text-white/60">Searching models...</div>
            )}

            {availableModels.length > 0 && (
              <div className="max-h-48 overflow-y-auto bg-black/50 border-2 border-white/20 rounded-lg">
                {availableModels.map((mod) => (
                  <button
                    key={`${mod.name}-${mod.key}`}
                    onClick={() => {
                      setSelectedModel(mod)
                      setModelSearch('')
                      setAvailableModels([])
                    }}
                    className="w-full text-left px-4 py-3 hover:bg-white/10 transition-all border-b border-white/10 last:border-b-0"
                  >
                    <div className="font-bold" style={{ color: text2color(mod.name) }}>
                      {mod.name}
                    </div>
                    {mod.desc && (
                      <div className="text-sm text-white/60 truncate">{mod.desc}</div>
                    )}
                  </button>
                ))}
              </div>
            )}

            {selectedModel && (
              <div className="p-4 bg-black/50 border-2 rounded-lg" style={{ borderColor: `${modelColor}40` }}>
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-sm text-white/60">Selected Model:</div>
                    <div className="font-black text-xl" style={{ color: modelColor }}>
                      {selectedModel.name}
                    </div>
                  </div>
                  <button
                    onClick={() => setSelectedModel(null)}
                    className="px-4 py-2 bg-red-500/20 text-red-400 border-2 border-red-500/40 rounded-lg hover:bg-red-500/30 transition-all"
                  >
                    Clear
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="flex-1 bg-black/50 border-2 border-white/20 rounded-lg p-6 mb-6 overflow-y-auto" style={{ minHeight: '400px' }}>
          {messages.length === 0 ? (
            <div className="flex items-center justify-center h-full text-white/40">
              <div className="text-center">
                <div className="text-2xl font-bold mb-2">No messages yet</div>
                <div>Select a model and start chatting</div>
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              {messages.map((msg, idx) => (
                <div
                  key={idx}
                  className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
                >
                  <div
                    className={`max-w-[70%] p-4 rounded-lg ${
                      msg.role === 'user'
                        ? 'bg-blue-500/20 border-2 border-blue-500/40'
                        : 'bg-green-500/20 border-2 border-green-500/40'
                    }`}
                  >
                    <div className="text-xs text-white/60 mb-1">
                      {msg.role === 'user' ? 'You' : selectedModel?.name || 'Assistant'}
                    </div>
                    <div className="text-white whitespace-pre-wrap">{msg.content}</div>
                  </div>
                </div>
              ))}
              {loading && (
                <div className="flex justify-start">
                  <div className="bg-green-500/20 border-2 border-green-500/40 p-4 rounded-lg">
                    <div className="flex items-center gap-2">
                      <div className="w-2 h-2 bg-green-400 rounded-full animate-pulse" />
                      <div className="w-2 h-2 bg-green-400 rounded-full animate-pulse" style={{ animationDelay: '0.2s' }} />
                      <div className="w-2 h-2 bg-green-400 rounded-full animate-pulse" style={{ animationDelay: '0.4s' }} />
                    </div>
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        <div className="flex gap-3">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                handleSendMessage()
              }
            }}
            placeholder={selectedModel ? 'Type your message...' : 'Select a model first...'}
            disabled={!selectedModel || loading}
            className="flex-1 bg-black/50 border-2 border-white/20 text-white px-4 py-3 rounded-lg focus:outline-none focus:border-green-500/50 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
          />
          <button
            onClick={handleSendMessage}
            disabled={!input.trim() || !selectedModel || loading}
            className="px-6 py-3 bg-green-500/20 text-green-400 border-2 border-green-500/40 rounded-lg hover:bg-green-500/30 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            <PaperAirplaneIcon className="w-5 h-5" />
            <span className="font-bold">SEND</span>
          </button>
        </div>
      </div>
    </div>
  )
}
