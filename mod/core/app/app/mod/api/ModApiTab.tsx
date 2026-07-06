"use client";

import { useState, useMemo } from 'react'
import { ModuleType } from '@/types'
import { userContext } from '@/context'
import { text2color, colorWithOpacity, getModApiUrl } from '@/utils'
import { SchemaParamsPanel } from '@/chat/components/SchemaParamsPanel'
import { CopyButton } from '@/ui/CopyButton'
import LogsPanel from '@/mod/LogsPanel'

interface ModApiTabProps {
  mod: ModuleType
  moduleColor?: string
}

// Built-in system functions available on every module
const BUILTIN_FUNCTIONS: Record<string, any> = {
  serve: {
    docs: 'Start the module server',
    input: [],
    output: 'dict',
    _builtin: true,
  },
  kill: {
    docs: 'Stop the module server',
    input: [],
    output: 'dict',
    _builtin: true,
  },
  edit: {
    docs: 'Edit the module code with AI (claude/forward)',
    input: [{ name: 'query', type: 'str', value: '' }],
    output: 'dict',
    _builtin: true,
  },
}

export default function ModApiTab({ mod, moduleColor }: ModApiTabProps) {
  const { client, user } = userContext()
  const modColor = moduleColor || text2color(mod.name || mod.key)
  const schema: Record<string, any> = mod.schema && typeof mod.schema === 'object' ? mod.schema as Record<string, any> : {}
  // Merge module schema with built-in functions (builtins at end)
  const allSchema = useMemo(() => ({ ...schema, ...BUILTIN_FUNCTIONS }), [schema])
  const functions = Object.keys(allSchema)

  const [selectedFn, setSelectedFn] = useState<string>(Object.keys(schema)[0] || functions[0] || '')
  const [params, setParams] = useState<Record<string, any>>({})
  const [result, setResult] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showCode, setShowCode] = useState(false)
  const [codeLang, setCodeLang] = useState<'python' | 'javascript' | 'curl'>('python')
  const [search, setSearch] = useState('')

  const filteredFunctions = useMemo(() => {
    if (!search.trim()) return functions
    const q = search.toLowerCase()
    return functions.filter((fn) => {
      const fnSchema = allSchema[fn]
      const docs = fnSchema?.docs || ''
      return fn.toLowerCase().includes(q) || docs.toLowerCase().includes(q)
    })
  }, [functions, allSchema, search])

  const apiUrl = getModApiUrl(mod)

  const handleParamChange = (key: string, value: string) => {
    setParams(prev => ({ ...prev, [key]: value }))
  }

  const handleResetParams = () => {
    setParams({})
  }

  const isBuiltin = (fn: string) => !!BUILTIN_FUNCTIONS[fn]

  const handleExecute = async () => {
    if (!client || !selectedFn) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      let res: any
      if (selectedFn === 'serve') {
        res = await client.call(`${mod.name}/serve`, params)
      } else if (selectedFn === 'kill') {
        res = await client.call('kill_app', { name: mod.name })
      } else if (selectedFn === 'edit') {
        res = await client.call('claude/forward', {
          query: params.query || '',
          mod: mod.name,
          key: user?.key || mod.key,
          background: true,
        }, true, {}, 60000)
      } else {
        const fn = `${mod.name}/${selectedFn}`
        res = await client.call(fn, params)
      }
      setResult(res)
    } catch (err: any) {
      setError(err?.message || 'Execution failed')
    } finally {
      setLoading(false)
    }
  }

  const handleFnSelect = (fn: string) => {
    setSelectedFn(fn)
    setParams({})
    setResult(null)
    setError(null)
  }

  const generateCode = (fn: string, lang: 'python' | 'javascript' | 'curl'): string => {
    const fnSchema = allSchema[fn]
    if (!fnSchema) return ''
    const inputs: { name: string; type: string; value: any }[] = Array.isArray(fnSchema.input)
      ? fnSchema.input.filter((p: any) => p.name !== 'kwargs' && p.name !== 'self')
      : []
    const output = fnSchema.output || 'any'
    const endpoint = `${mod.name}/${fn}`
    const url = apiUrl || 'http://localhost:8000'

    if (lang === 'python') {
      const paramLines = inputs.map((p) => {
        const defVal = p.value !== undefined && p.value !== null ? JSON.stringify(p.value) : 'None'
        return `    ${p.name}: ${p.type || 'any'} = ${defVal === 'undefined' ? 'None' : defVal}`
      }).join(',\n')
      const paramDict = inputs.map((p) => `        "${p.name}": ${p.name}`).join(',\n')
      const doc = fnSchema.docs ? `    """${fnSchema.docs.split('\n')[0].trim()}"""\n` : ''
      return `from mod import Client\n\nclient = Client()\n\ndef ${fn}(\n${paramLines}\n) -> ${output}:\n${doc}    return client.call("${endpoint}", {\n${paramDict}\n    })\n\nresult = ${fn}(${inputs.map(p => p.value !== undefined && p.value !== null ? JSON.stringify(p.value) : '').filter(Boolean).join(', ')})\nprint(result)`
    }

    if (lang === 'javascript') {
      const paramObj = inputs.map((p) => {
        const val = p.value !== undefined && p.value !== null ? JSON.stringify(p.value) : 'null'
        return `  ${p.name}: ${val}`
      }).join(',\n')
      return `import { Client } from 'mod'\n\nconst client = new Client()\n\nconst result = await client.call("${endpoint}", {\n${paramObj}\n})\n\nconsole.log(result)`
    }

    // curl
    const paramObj: Record<string, any> = {}
    inputs.forEach(p => { paramObj[p.name] = p.value !== undefined ? p.value : null })
    return `curl -X POST ${url} \\\n  -H "Content-Type: application/json" \\\n  -d '${JSON.stringify({ fn: endpoint, params: paramObj }, null, 2)}'`
  }

  return (
    <div className="font-mono" style={{ fontFamily: 'var(--font-digital), monospace' }}>
      {/* Header */}
      <div
        className="px-4 py-2.5 flex items-center justify-between"
        style={{
          backgroundColor: 'var(--bg-surface)',
          border: '1px solid var(--border-color)',
        }}
      >
        <div className="flex items-center gap-3">
          <span className="text-[11px] font-bold" style={{ color: modColor }}>[API]</span>
          <span className="text-[12px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-primary)' }}>
            {mod.name}
          </span>
          <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
            {functions.length} fn{functions.length !== 1 ? 's' : ''}
          </span>
        </div>
        {apiUrl && (
          <span className="text-[10px] font-mono" style={{ color: 'var(--text-tertiary)' }}>
            {apiUrl}
          </span>
        )}
      </div>

      <div className="flex gap-0 mt-0">
        {/* Function list sidebar */}
        <div
          className="w-60 max-h-[600px] flex flex-col"
          style={{
            backgroundColor: 'var(--bg-surface)',
            borderRight: '1px solid var(--border-color)',
            borderBottom: '1px solid var(--border-color)',
            borderLeft: '1px solid var(--border-color)',
          }}
        >
          {/* Search input */}
          <div className="px-2 pt-2 pb-1">
            <div className="relative">
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="search functions..."
                className="w-full text-[11px] font-mono px-2.5 py-1.5 pl-7 outline-none"
                style={{
                  backgroundColor: 'var(--bg-input)',
                  border: `1px solid ${search ? colorWithOpacity(modColor, 0.4) : 'var(--border-color)'}`,
                  color: 'var(--text-primary)',
                  caretColor: modColor,
                }}
              />
              <span
                className="absolute left-2 top-1/2 -translate-y-1/2 text-[11px]"
                style={{ color: 'var(--text-tertiary)' }}
              >
                /
              </span>
              {search && (
                <button
                  onClick={() => setSearch('')}
                  className="absolute right-1.5 top-1/2 -translate-y-1/2 text-[10px] px-1"
                  style={{ color: 'var(--text-tertiary)' }}
                >
                  ✕
                </button>
              )}
            </div>
            <div className="flex items-center justify-between mt-1.5 px-0.5">
              <span className="text-[9px] font-bold tracking-wider uppercase" style={{ color: 'var(--text-tertiary)' }}>
                Functions
              </span>
              <span className="text-[9px]" style={{ color: 'var(--text-tertiary)' }}>
                {filteredFunctions.length}/{functions.length}
              </span>
            </div>
          </div>

          {/* Function list */}
          <div
            className="flex-1 overflow-y-auto py-1"
            style={{
              scrollbarWidth: 'thin',
              scrollbarColor: 'var(--scrollbar-thumb) transparent',
            }}
          >
            {filteredFunctions.length === 0 ? (
              <div className="px-3 py-4 text-center">
                <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                  no matches for "{search}"
                </span>
              </div>
            ) : (
              filteredFunctions.map((fn) => {
                const isActive = selectedFn === fn
                const fnSchema = allSchema[fn]
                const builtin = isBuiltin(fn)
                const inputCount = Array.isArray(fnSchema?.input) ? fnSchema.input.filter((p: any) => p.name !== 'kwargs' && p.name !== 'self').length : 0
                const docs = fnSchema?.docs ? fnSchema.docs.split('\n')[0].trim() : ''
                return (
                  <button
                    key={fn}
                    onClick={() => handleFnSelect(fn)}
                    className="w-full text-left px-3 py-1.5 text-[11px] font-mono font-medium transition-all group"
                    style={{
                      backgroundColor: isActive ? colorWithOpacity(modColor, 0.08) : 'transparent',
                      color: isActive ? modColor : builtin ? 'var(--text-tertiary)' : 'var(--text-secondary)',
                      borderLeft: isActive ? `2px solid ${modColor}` : '2px solid transparent',
                    }}
                  >
                    <div className="flex items-center justify-between">
                      <span className="truncate">{builtin ? `⚙ ${fn}` : fn}</span>
                      {inputCount > 0 && (
                        <span className="text-[9px] ml-1 flex-shrink-0" style={{ color: 'var(--text-tertiary)' }}>
                          ({inputCount})
                        </span>
                      )}
                    </div>
                    {docs && (
                      <div
                        className="text-[9px] mt-0.5 truncate"
                        style={{ color: 'var(--text-tertiary)', opacity: isActive ? 0.8 : 0.5 }}
                      >
                        {docs.slice(0, 50)}
                      </div>
                    )}
                  </button>
                )
              })
            )}
          </div>
        </div>

        {/* Execution panel */}
        <div
          className="flex-1 max-h-[600px] overflow-y-auto"
          style={{
            border: '1px solid var(--border-color)',
            borderLeft: 'none',
          }}
        >
          {selectedFn ? (
            <div>
              {/* Selected function header */}
              <div
                className="px-4 py-3 flex items-center justify-between"
                style={{
                  backgroundColor: 'var(--bg-surface)',
                  borderBottom: '1px solid var(--border-color)',
                }}
              >
                <div className="flex items-center gap-2">
                  <span className="text-[12px] font-bold" style={{ color: 'var(--text-primary)' }}>
                    {selectedFn}
                  </span>
                  {allSchema[selectedFn]?.docs && (
                    <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                      {allSchema[selectedFn].docs.split('\n')[0].trim().slice(0, 80)}
                    </span>
                  )}
                </div>
                <button
                  onClick={() => setShowCode(!showCode)}
                  className="text-[10px] font-bold uppercase tracking-wider px-2.5 py-1 transition-all"
                  style={{
                    color: showCode ? modColor : 'var(--text-tertiary)',
                    border: `1px solid ${showCode ? colorWithOpacity(modColor, 0.4) : 'var(--border-color)'}`,
                    backgroundColor: showCode ? colorWithOpacity(modColor, 0.08) : 'transparent',
                  }}
                >
                  {'</>'}  CODE
                </button>
              </div>

              {/* Code snippet panel */}
              {showCode && (
                <div style={{ borderBottom: '1px solid var(--border-color)' }}>
                  <div className="flex items-center gap-0" style={{ backgroundColor: 'var(--bg-surface)' }}>
                    {(['python', 'javascript', 'curl'] as const).map((lang) => (
                      <button
                        key={lang}
                        onClick={() => setCodeLang(lang)}
                        className="px-3 py-2 text-[10px] font-bold uppercase tracking-wider transition-all"
                        style={{
                          color: codeLang === lang ? modColor : 'var(--text-tertiary)',
                          borderBottom: codeLang === lang ? `2px solid ${modColor}` : '2px solid transparent',
                          backgroundColor: codeLang === lang ? colorWithOpacity(modColor, 0.04) : 'transparent',
                        }}
                      >
                        {lang}
                      </button>
                    ))}
                    <div className="flex-1" />
                    <div className="pr-2">
                      <CopyButton content={generateCode(selectedFn, codeLang)} />
                    </div>
                  </div>
                  <pre
                    className="px-4 py-3 text-[11px] leading-relaxed overflow-x-auto"
                    style={{
                      backgroundColor: 'var(--bg-primary)',
                      color: 'var(--text-secondary)',
                      fontFamily: 'var(--font-digital), monospace',
                      maxHeight: '280px',
                      overflowY: 'auto',
                      scrollbarWidth: 'thin',
                      scrollbarColor: 'var(--scrollbar-thumb) transparent',
                    }}
                  >
                    {generateCode(selectedFn, codeLang)}
                  </pre>
                </div>
              )}

              {/* Params */}
              <div className="p-4 space-y-4" style={{ backgroundColor: 'var(--bg-primary)' }}>
                {allSchema[selectedFn] && (
                  <SchemaParamsPanel
                    selectedFunction={selectedFn}
                    schema={allSchema}
                    params={params}
                    handleParamChange={handleParamChange}
                    handleResetParams={handleResetParams}
                    numColumns={2}
                    modColor={modColor}
                  />
                )}

                {/* Execute button */}
                <button
                  onClick={handleExecute}
                  disabled={loading}
                  className="w-full px-4 py-2.5 font-bold text-[11px] uppercase tracking-wider transition-all flex items-center justify-center gap-2 disabled:opacity-30"
                  style={{
                    background: loading ? 'transparent' : colorWithOpacity(modColor, 0.08),
                    border: `1px solid ${colorWithOpacity(modColor, loading ? 0.15 : 0.3)}`,
                    color: loading ? colorWithOpacity(modColor, 0.5) : modColor,
                  }}
                >
                  {loading ? (
                    <>
                      <span className="animate-pulse">_</span>
                      <span>EXECUTING...</span>
                    </>
                  ) : (
                    <span>{isBuiltin(selectedFn) ? `⚡ EXECUTE ${selectedFn.toUpperCase()}` : 'EXECUTE'}</span>
                  )}
                </button>

                {/* Error */}
                {error && (
                  <div
                    className="overflow-hidden"
                    style={{
                      border: '1px solid rgba(239, 68, 68, 0.2)',
                      backgroundColor: 'rgba(239, 68, 68, 0.04)',
                    }}
                  >
                    <div
                      className="flex items-center justify-between px-4 py-2"
                      style={{ borderBottom: '1px solid rgba(239, 68, 68, 0.1)' }}
                    >
                      <span className="text-[11px] font-bold text-red-400">[ERR]</span>
                      <CopyButton content={error} />
                    </div>
                    <pre className="text-red-400/70 text-[12px] font-mono p-4 whitespace-pre-wrap break-words max-h-48 overflow-y-auto">
                      {error}
                    </pre>
                  </div>
                )}

                {/* Result */}
                {result !== null && (
                  <div
                    className="overflow-hidden"
                    style={{
                      border: '1px solid var(--border-color)',
                      backgroundColor: 'var(--bg-surface)',
                    }}
                  >
                    <div
                      className="flex items-center justify-between px-4 py-2"
                      style={{ borderBottom: '1px solid var(--border-color)' }}
                    >
                      <div className="flex items-center gap-3">
                        <span className="text-[11px] font-bold" style={{ color: modColor }}>[RES]</span>
                        {/* Show CID if present */}
                        {result && typeof result === 'object' && result.cid && (
                          <span className="text-[10px] font-mono" style={{ color: 'var(--text-tertiary)' }}>
                            CID: {result.cid}
                          </span>
                        )}
                        {/* Show job ID if present */}
                        {result && typeof result === 'object' && result.id && !result.cid && (
                          <span className="text-[10px] font-mono" style={{ color: 'var(--text-tertiary)' }}>
                            JOB: {typeof result.id === 'string' ? result.id.slice(0, 12) + '...' : result.id}
                          </span>
                        )}
                      </div>
                      <CopyButton content={typeof result === 'string' ? result : JSON.stringify(result, null, 2)} />
                    </div>
                    <pre
                      className="text-[12px] font-mono p-4 whitespace-pre-wrap break-words max-h-96 overflow-y-auto"
                      style={{ color: colorWithOpacity(modColor, 0.85) }}
                    >
                      {typeof result === 'string' ? result : JSON.stringify(result, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div
              className="flex items-center justify-center py-16"
              style={{ backgroundColor: 'var(--bg-primary)' }}
            >
              <p
                className="text-[12px] font-bold uppercase tracking-wider"
                style={{ color: 'var(--text-tertiary)' }}
              >
                Select a function
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Logs panel */}
      <LogsPanel modName={mod.name || ''} moduleColor={modColor} filter="api" />
    </div>
  )
}
