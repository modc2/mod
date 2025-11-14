'use client'

import { useState, useEffect, useRef } from 'react'
import { useUserContext } from '@/app/block/context/UserContext'
import { Loading } from '@/app/block/Loading'
import { PaperAirplaneIcon, MagnifyingGlassIcon, SparklesIcon, FireIcon, BoltIcon, PlusIcon, TrashIcon, ChevronDoubleLeftIcon, ChevronDoubleRightIcon } from '@heroicons/react/24/outline'
import { text2color } from '@/app/utils'

interface Message {
  role: 'user' | 'assistant'
  content: string
  timestamp: number
}

interface Conversation {
  id: string
  title: string
  messages: Message[]
  modelName?: string
  functionName?: string
  createdAt: number
}

interface FunctionSchema {
  name: string
  input: Record<string, any>
  output: Record<string, any>
  docs: string
}

export default function ChatInterface() {
  const { client } = useUserContext()
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('{}')
  const [loading, setLoading] = useState(false)
  const [modelSearch, setModelSearch] = useState('')
  const [selectedModel, setSelectedModel] = useState<any>(null)
  const [availableModels, setAvailableModels] = useState<any[]>([])
  const [searchingModels, setSearchingModels] = useState(false)
  const [chatSidebarOpen, setChatSidebarOpen] = useState(true)
  const [showWelcome, setShowWelcome] = useState(true)
  const [selectedFunction, setSelectedFunction] = useState<string>('forward')
  const [availableFunctions, setAvailableFunctions] = useState<FunctionSchema[]>([])
  const [loadingFunctions, setLoadingFunctions] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const saved = localStorage.getItem('chat_conversations')
    if (saved) {
      const parsed = JSON.parse(saved)
      setConversations(parsed)
      if (parsed.length > 0) {
        setActiveConversationId(parsed[0].id)
        setMessages(parsed[0].messages)
        setShowWelcome(false)
        if (parsed[0].modelName) {
          setSelectedModel({ name: parsed[0].modelName })
        }
        if (parsed[0].functionName) {
          setSelectedFunction(parsed[0].functionName)
        }
      }
    }
  }, [])

  useEffect(() => {
    if (conversations.length > 0) {
      localStorage.setItem('chat_conversations', JSON.stringify(conversations))
    }
  }, [conversations])

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

  useEffect(() => {
    const fetchFunctions = async () => {
      if (!client || !selectedModel) {
        setAvailableFunctions([])
        return
      }

      setLoadingFunctions(true)
      try {
        const schema = await client.call('schema', {
          module: selectedModel.name,
          key: selectedModel.key
        })
        
        if (schema && typeof schema === 'object') {
          const functions: FunctionSchema[] = Object.entries(schema).map(([name, details]: [string, any]) => ({
            name,
            input: details.input || {},
            output: details.output || {},
            docs: details.docs || 'No documentation available'
          }))
          setAvailableFunctions(functions)
          if (!functions.find(f => f.name === selectedFunction)) {
            setSelectedFunction(functions[0]?.name || 'forward')
          }
        }
      } catch (err) {
        console.error('Failed to fetch functions:', err)
        setAvailableFunctions([])
      } finally {
        setLoadingFunctions(false)
      }
    }

    fetchFunctions()
  }, [selectedModel, client])

  const createNewConversation = () => {
    const newConv: Conversation = {
      id: Date.now().toString(),
      title: 'New Chat',
      messages: [],
      createdAt: Date.now()
    }
    setConversations(prev => [newConv, ...prev])
    setActiveConversationId(newConv.id)
    setMessages([])
    setSelectedModel(null)
    setShowWelcome(true)
  }

  const deleteConversation = (id: string) => {
    setConversations(prev => prev.filter(c => c.id !== id))
    if (activeConversationId === id) {
      setActiveConversationId(null)
      setMessages([])
      setShowWelcome(true)
    }
  }

  const switchConversation = (id: string) => {
    const conv = conversations.find(c => c.id === id)
    if (conv) {
      setActiveConversationId(id)
      setMessages(conv.messages)
      setShowWelcome(conv.messages.length === 0)
      if (conv.modelName) {
        setSelectedModel({ name: conv.modelName })
      }
      if (conv.functionName) {
        setSelectedFunction(conv.functionName)
      }
    }
  }

  const handleSendMessage = async () => {
    if (!input.trim() || !selectedModel || !client) return

    setShowWelcome(false)
    let parsedInput: any
    try {
      parsedInput = JSON.parse(input)
    } catch (e) {
      parsedInput = { prompt: input }
    }

    const userMessage: Message = {
      role: 'user',
      content: JSON.stringify(parsedInput, null, 2),
      timestamp: Date.now()
    }

    const newMessages = [...messages, userMessage]
    setMessages(newMessages)
    setInput('{}')
    setLoading(true)

    try {
      const response = await client.call(selectedFunction, {
        ...parsedInput,
        module: selectedModel.name,
        key: selectedModel.key
      })

      const assistantMessage: Message = {
        role: 'assistant',
        content: typeof response === 'string' ? response : JSON.stringify(response, null, 2),
        timestamp: Date.now()
      }

      const updatedMessages = [...newMessages, assistantMessage]
      setMessages(updatedMessages)

      if (activeConversationId) {
        setConversations(prev => prev.map(c => 
          c.id === activeConversationId 
            ? { ...c, messages: updatedMessages, title: updatedMessages[0]?.content.slice(0, 30) || 'New Chat', modelName: selectedModel.name, functionName: selectedFunction }
            : c
        ))
      } else {
        const newConv: Conversation = {
          id: Date.now().toString(),
          title: userMessage.content.slice(0, 30),
          messages: updatedMessages,
          modelName: selectedModel.name,
          functionName: selectedFunction,
          createdAt: Date.now()
        }
        setConversations(prev => [newConv, ...prev])
        setActiveConversationId(newConv.id)
      }
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
  const currentFunctionSchema = availableFunctions.find(f => f.name === selectedFunction)

  return (
    <div className="min-h-screen bg-black text-white flex relative overflow-hidden">
      <div className="absolute inset-0 opacity-5 pointer-events-none">
        <div className="absolute inset-0" style={{
          backgroundImage: `
            linear-gradient(rgba(0,255,0,0.15) 1px, transparent 1px),
            linear-gradient(90deg, rgba(0,255,0,0.15) 1px, transparent 1px)
          `,
          backgroundSize: '40px 40px'
        }} />
      </div>

      <div className="absolute inset-0 opacity-10 pointer-events-none">
        <div className="absolute inset-0 bg-gradient-to-br from-green-600/10 via-cyan-600/10 to-purple-600/10 animate-pulse" />
      </div>

      {/* CHAT SIDEBAR */}
      <div className={`relative z-10 transition-all duration-300 ${chatSidebarOpen ? 'w-80' : 'w-0'} border-r border-green-500/20 bg-black/95 backdrop-blur-xl overflow-hidden`}>
        <div className="h-full flex flex-col p-4">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-xl font-black text-green-400 uppercase tracking-wider">Conversations</h2>
            <button
              onClick={createNewConversation}
              className="p-2 bg-green-500/10 border border-green-500/40 rounded-lg hover:bg-green-500/20 transition-all"
            >
              <PlusIcon className="w-5 h-5 text-green-400" />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto space-y-2">
            {conversations.map(conv => (
              <div
                key={conv.id}
                onClick={() => switchConversation(conv.id)}
                className={`p-3 rounded-lg cursor-pointer transition-all group ${
                  activeConversationId === conv.id
                    ? 'bg-green-500/20 border border-green-500/40'
                    : 'bg-white/5 border border-white/10 hover:bg-white/10'
                }`}
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="font-bold text-sm truncate text-green-300">{conv.title}</div>
                    <div className="text-xs text-white/40 mt-1">
                      {conv.messages.length} messages • {conv.functionName || 'forward'}
                    </div>
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      deleteConversation(conv.id)
                    }}
                    className="opacity-0 group-hover:opacity-100 p-1 hover:bg-red-500/20 rounded transition-all"
                  >
                    <TrashIcon className="w-4 h-4 text-red-400" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* TOGGLE CHAT SIDEBAR */}
      <button
        onClick={() => setChatSidebarOpen(!chatSidebarOpen)}
        className="absolute left-0 top-1/2 -translate-y-1/2 z-20 p-2 bg-green-500/10 border border-green-500/40 rounded-r-lg hover:bg-green-500/20 transition-all"
        style={{ left: chatSidebarOpen ? '320px' : '0' }}
      >
        {chatSidebarOpen ? <ChevronDoubleLeftIcon className="w-5 h-5 text-green-400" /> : <ChevronDoubleRightIcon className="w-5 h-5 text-green-400" />}
      </button>

      {/* MAIN CHAT AREA */}
      <div className="flex-1 flex flex-col max-w-7xl mx-auto w-full p-6 relative z-10">
        <div className="mb-8">
          <div className="flex items-center gap-4 mb-6">
            <div className="relative">
              <SparklesIcon className="w-14 h-14 animate-pulse" style={{ color: '#00ff00' }} />
              <div className="absolute inset-0 blur-xl opacity-50" style={{ backgroundColor: '#00ff00' }} />
            </div>
            <div>
              <h1 className="text-5xl font-black uppercase tracking-widest bg-gradient-to-r from-green-400 via-cyan-400 to-purple-400 bg-clip-text text-transparent">
                NEURAL INTERFACE
              </h1>
              <div className="text-sm text-green-400/60 font-mono mt-1 tracking-wider">FUNCTION: {selectedFunction.toUpperCase()}</div>
            </div>
            <BoltIcon className="w-10 h-10 text-yellow-400 animate-bounce ml-auto" />
          </div>
          
          <div className="space-y-4">
            <div className="relative group">
              <div className="absolute -inset-1 bg-gradient-to-r from-green-500 via-cyan-500 to-purple-500 rounded-xl blur opacity-25 group-hover:opacity-50 transition duration-300" />
              <div className="relative">
                <MagnifyingGlassIcon className="absolute left-4 top-1/2 transform -translate-y-1/2 w-6 h-6 text-green-400" />
                <input
                  type="text"
                  value={modelSearch}
                  onChange={(e) => setModelSearch(e.target.value)}
                  placeholder="SEARCH NEURAL MODELS..."
                  className="w-full bg-black/80 border-2 border-green-500/40 text-green-300 pl-14 pr-6 py-4 rounded-xl focus:outline-none focus:border-green-400 transition-all font-mono text-lg backdrop-blur-xl placeholder-green-500/40"
                />
              </div>
            </div>

            {searchingModels && (
              <div className="text-sm text-green-400 font-mono animate-pulse flex items-center gap-2">
                <div className="w-2 h-2 bg-green-400 rounded-full animate-ping" />
                SCANNING NEURAL NETWORK...
              </div>
            )}

            {availableModels.length > 0 && (
              <div className="max-h-64 overflow-y-auto bg-black/90 border-2 border-green-500/40 rounded-xl backdrop-blur-xl">
                {availableModels.map((mod) => (
                  <button
                    key={`${mod.name}-${mod.key}`}
                    onClick={() => {
                      setSelectedModel(mod)
                      setModelSearch('')
                      setAvailableModels([])
                    }}
                    className="w-full text-left px-6 py-4 hover:bg-green-500/20 transition-all border-b border-green-500/20 last:border-b-0 group"
                  >
                    <div className="font-black text-lg" style={{ color: text2color(mod.name) }}>
                      >> {mod.name}
                    </div>
                    {mod.desc && (
                      <div className="text-sm text-white/60 truncate font-mono mt-1">{mod.desc}</div>
                    )}
                  </button>
                ))}
              </div>
            )}

            {selectedModel && (
              <div className="relative group">
                <div className="absolute -inset-1 rounded-xl blur opacity-30" style={{ backgroundColor: modelColor }} />
                <div className="relative p-6 bg-black/90 border-2 rounded-xl backdrop-blur-xl" style={{ borderColor: `${modelColor}80` }}>
                  <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center gap-4">
                      <FireIcon className="w-8 h-8" style={{ color: modelColor }} />
                      <div>
                        <div className="text-xs text-white/60 font-mono uppercase tracking-wider">ACTIVE MODEL</div>
                        <div className="font-black text-2xl mt-1" style={{ color: modelColor }}>
                          {selectedModel.name}
                        </div>
                      </div>
                    </div>
                    <button
                      onClick={() => setSelectedModel(null)}
                      className="px-6 py-3 bg-red-500/20 text-red-400 border-2 border-red-500/60 rounded-xl hover:bg-red-500/40 transition-all font-black uppercase tracking-wider"
                    >
                      DISCONNECT
                    </button>
                  </div>

                  {loadingFunctions ? (
                    <div className="text-sm text-green-400 font-mono animate-pulse">Loading functions...</div>
                  ) : availableFunctions.length > 0 && (
                    <div className="space-y-3">
                      <div className="text-xs text-white/60 font-mono uppercase tracking-wider">AVAILABLE FUNCTIONS</div>
                      <div className="flex flex-wrap gap-2">
                        {availableFunctions.map((func) => (
                          <button
                            key={func.name}
                            onClick={() => setSelectedFunction(func.name)}
                            className={`px-4 py-2 rounded-lg font-mono text-sm transition-all ${
                              selectedFunction === func.name
                                ? 'bg-cyan-500/30 border-2 border-cyan-400 text-cyan-300'
                                : 'bg-white/5 border border-white/20 text-white/60 hover:bg-white/10'
                            }`}
                          >
                            {func.name}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}

            {currentFunctionSchema && (
              <div className="relative group">
                <div className="absolute -inset-1 bg-gradient-to-r from-cyan-500/20 to-purple-500/20 rounded-xl blur opacity-30" />
                <div className="relative p-6 bg-black/90 border-2 border-cyan-500/40 rounded-xl backdrop-blur-xl">
                  <div className="text-xs text-white/60 font-mono uppercase tracking-wider mb-3">FUNCTION SCHEMA: {currentFunctionSchema.name}</div>
                  <div className="space-y-3">
                    <div>
                      <div className="text-sm text-cyan-400 font-mono mb-2">Documentation:</div>
                      <div className="text-sm text-white/70 font-mono bg-black/50 p-3 rounded-lg">{currentFunctionSchema.docs}</div>
                    </div>
                    <div>
                      <div className="text-sm text-green-400 font-mono mb-2">Input Schema:</div>
                      <pre className="text-xs text-white/70 font-mono bg-black/50 p-3 rounded-lg overflow-x-auto">{JSON.stringify(currentFunctionSchema.input, null, 2)}</pre>
                    </div>
                    <div>
                      <div className="text-sm text-purple-400 font-mono mb-2">Output Schema:</div>
                      <pre className="text-xs text-white/70 font-mono bg-black/50 p-3 rounded-lg overflow-x-auto">{JSON.stringify(currentFunctionSchema.output, null, 2)}</pre>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="flex-1 relative group mb-6" style={{ minHeight: '400px' }}>
          <div className="absolute -inset-1 bg-gradient-to-r from-green-500/20 via-cyan-500/20 to-purple-500/20 rounded-2xl blur opacity-20 group-hover:opacity-30 transition duration-300" />
          <div className="relative h-full bg-black/80 border-2 border-green-500/40 rounded-2xl p-8 overflow-y-auto backdrop-blur-xl">
            {messages.length === 0 ? (
              <div className="flex items-center justify-center h-full">
                <div className="text-center">
                  <SparklesIcon className="w-20 h-20 text-green-400/40 mx-auto mb-4 animate-pulse" />
                  <div className="text-3xl font-black text-green-400/60 mb-2 uppercase tracking-wider">NEURAL LINK READY</div>
                  <div className="text-green-500/40 font-mono">Select a model and function to begin</div>
                </div>
              </div>
            ) : (
              <div className="space-y-6">
                {messages.map((msg, idx) => (
                  <div
                    key={idx}
                    className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'} animate-fade-in`}
                  >
                    <div
                      className={`max-w-[75%] p-6 rounded-2xl relative group ${
                        msg.role === 'user'
                          ? 'bg-gradient-to-br from-blue-600/30 to-purple-600/30 border-2 border-blue-500/60'
                          : 'bg-gradient-to-br from-green-600/30 to-cyan-600/30 border-2 border-green-500/60'
                      }`}
                    >
                      <div className="absolute -inset-1 rounded-2xl blur opacity-20" style={{
                        backgroundColor: msg.role === 'user' ? '#3b82f6' : '#10b981'
                      }} />
                      <div className="relative">
                        <div className="text-xs font-mono uppercase tracking-widest mb-2" style={{
                          color: msg.role === 'user' ? '#60a5fa' : '#34d399'
                        }}>
                          {msg.role === 'user' ? '>> USER' : `>> ${selectedModel?.name || 'AI'}`}
                        </div>
                        <pre className="text-white font-mono whitespace-pre-wrap leading-relaxed text-sm">{msg.content}</pre>
                      </div>
                    </div>
                  </div>
                ))}
                {loading && (
                  <div className="flex justify-start">
                    <div className="bg-gradient-to-br from-green-600/30 to-cyan-600/30 border-2 border-green-500/60 p-6 rounded-2xl">
                      <div className="flex items-center gap-3">
                        <div className="w-3 h-3 bg-green-400 rounded-full animate-pulse" />
                        <div className="w-3 h-3 bg-cyan-400 rounded-full animate-pulse" style={{ animationDelay: '0.2s' }} />
                        <div className="w-3 h-3 bg-purple-400 rounded-full animate-pulse" style={{ animationDelay: '0.4s' }} />
                        <span className="text-green-400 font-mono text-sm ml-2">PROCESSING...</span>
                      </div>
                    </div>
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>
            )}
          </div>
        </div>

        <div className="relative group">
          <div className="absolute -inset-1 bg-gradient-to-r from-green-500 via-cyan-500 to-purple-500 rounded-xl blur opacity-25 group-hover:opacity-40 transition duration-300" />
          <div className="relative flex gap-4">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && e.ctrlKey) {
                  e.preventDefault()
                  handleSendMessage()
                }
              }}
              placeholder={selectedModel ? 'ENTER JSON PARAMS... (Ctrl+Enter to send)' : 'SELECT MODEL FIRST...'}
              disabled={!selectedModel || loading}
              rows={3}
              className="flex-1 bg-black/90 border-2 border-green-500/60 text-green-300 px-6 py-5 rounded-xl focus:outline-none focus:border-green-400 transition-all disabled:opacity-50 disabled:cursor-not-allowed font-mono text-sm backdrop-blur-xl placeholder-green-500/40 resize-none"
            />
            <button
              onClick={handleSendMessage}
              disabled={!input.trim() || !selectedModel || loading}
              className="px-8 py-5 bg-gradient-to-r from-green-500/30 to-cyan-500/30 text-green-300 border-2 border-green-500/60 rounded-xl hover:from-green-500/50 hover:to-cyan-500/50 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-3 font-black uppercase tracking-wider backdrop-blur-xl"
            >
              <PaperAirplaneIcon className="w-6 h-6" />
              EXECUTE
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
