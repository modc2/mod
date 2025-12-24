'use client';

import { useState, useMemo, useEffect, useCallback } from 'react';
import { CopyButton } from '@/bloc/ui/CopyButton';
import { useUserContext } from '@/bloc/context';
import { motion, AnimatePresence } from 'framer-motion';
import {
  MagnifyingGlassIcon,
  CodeBracketIcon,
  PlayIcon,
  CommandLineIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline';

type SchemaField = { value: any; type: string };
type SchemaType = {
  input: Record<string, SchemaField>;
  output: SchemaField;
  code?: string;
  hash?: string;
};
type TabType = 'run' | 'code';

const ui = {
  bg:       '#0b0b0b',
  panel:    '#121212',
  panelAlt: '#151515',
  border:   '#2a2a2a',
  text:     '#e7e7e7',
  textDim:  '#a8a8a8',
  focus:    '#3a86ff',
  accent:   '#ffffff',
  danger:   '#ff3b30',
  success:  '#22c55e',
};

export const ModApi = ({ mod }: { mod: any }) => {
  const { user, client } = useUserContext();

  const schema: Record<string, SchemaType> = mod?.schema || {};

  const filteredSchema = useMemo(() => {
    return Object.entries(schema).reduce((acc, [fn, value]) => {
      if (fn === 'self' || fn === 'cls') return acc;
      const filteredInput = Object.entries(value?.input || {}).reduce((ia, [k, v]) => {
        if (k !== 'self' && k !== 'cls') (ia as any)[k] = v;
        return ia;
      }, {} as Record<string, SchemaField>);
      (acc as any)[fn] = { ...value, input: filteredInput };
      return acc;
    }, {} as Record<string, SchemaType>);
  }, [schema]);

  const functionNames = useMemo(() => Object.keys(filteredSchema), [filteredSchema]);

  const [selectedFunction, setSelectedFunction] = useState<string>('');
  const [params, setParams] = useState<Record<string, any>>({});
  const [response, setResponse] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>('');
  const [authHeaders, setAuthHeaders] = useState<any>(null);
  const [urlParams, setUrlParams] = useState<string>('');
  const [searchTerm, setSearchTerm] = useState<string>('');
  const [activeTab, setActiveTab] = useState<TabType>('run');
  const [paramColumns, setParamColumns] = useState<number>(2);
  const [dividerPosition, setDividerPosition] = useState<number>(320);
  const [isDragging, setIsDragging] = useState(false);

  useEffect(() => {
    if (!selectedFunction && functionNames.length > 0) {
      const first = functionNames[0];
      setSelectedFunction(first);
      const d: Record<string, any> = {};
      const inp = filteredSchema[first]?.input || {};
      Object.entries(inp).forEach(([p, det]) => {
        if (det.value !== '_empty' && det.value !== undefined) d[p] = det.value;
      });
      setParams(d);
    }
  }, [selectedFunction, functionNames, filteredSchema]);

  const searchedFunctions = useMemo(() => {
    if (!searchTerm) return functionNames;
    const q = searchTerm.toLowerCase();
    return functionNames.filter((fn) => fn.toLowerCase().includes(q));
  }, [functionNames, searchTerm]);

  const handleParamChange = (p: string, v: string) =>
    setParams((prev) => ({ ...prev, [p]: v }));

  const initializeParams = useCallback((fn: string) => {
    const s = filteredSchema[fn];
    const d: Record<string, any> = {};
    Object.entries(s?.input || {}).forEach(([p, det]) => {
      if (det.value !== '_empty' && det.value !== undefined) d[p] = det.value;
    });
    setParams(d);
  }, [filteredSchema]);

  const executeFunction = async () => {
    if (!selectedFunction) return;
    setLoading(true);
    setError('');
    setAuthHeaders(null);
    setResponse(null);

    try {
      const qs = new URLSearchParams(
        Object.fromEntries(
          Object.entries(params).map(([k, v]) => [
            k,
            typeof v === 'object' ? JSON.stringify(v) : String(v ?? ''),
          ])
        )
      ).toString();
      setUrlParams(qs);

      if (!client) {
        setError('Client not initialized');
        setLoading(false);
        return;
      }
        let call_params = {
          'fn': mod.name + '/' + selectedFunction,
          'params': params
        }
        const res = await client.call('call', call_params);
        setResponse(res);
        setAuthHeaders({
          'X-Function': selectedFunction,
          'X-Params': JSON.stringify(params),
          'X-Timestamp': new Date().toISOString()
        });
    } catch (err: any) {
      setError(err?.message || 'Failed to execute function');
    } finally {
      setLoading(false);
    }
  };

  const handleMouseDown = (e: React.MouseEvent) => {
    setIsDragging(true);
    e.preventDefault();
  };

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isDragging) return;
      const newPos = Math.max(200, Math.min(600, e.clientX));
      setDividerPosition(newPos);
    };

    const handleMouseUp = () => {
      setIsDragging(false);
    };

    if (isDragging) {
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
    }

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging]);

  const isError = !!error;
  const responseColor = isError ? ui.danger : ui.success;

  return (
    <div className="flex h-full font-mono" style={{ backgroundColor: ui.bg, fontSize: '16px' }}>
      <div className="border-r flex flex-col" style={{ width: `${dividerPosition}px`, borderColor: ui.border, backgroundColor: ui.panel }}>
        <div className="border-b px-5 py-5" style={{ borderColor: ui.border }}>
          <h2 className="text-xl font-bold mb-4" style={{ color: ui.text }}>fns</h2>
          <div className="relative">
            <input
              type="text"
              placeholder="Search functions"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full rounded-md px-4 py-3 text-base outline-none"
              style={{
                backgroundColor: ui.panelAlt,
                color: ui.text,
                border: `1px solid ${ui.border}`,
                transition: 'box-shadow 120ms ease, border-color 120ms ease',
              }}
              onFocus={(e) => {
                e.currentTarget.style.borderColor = ui.focus;
                e.currentTarget.style.boxShadow = `0 0 0 3px ${ui.focus}22`;
              }}
              onBlur={(e) => {
                e.currentTarget.style.borderColor = ui.border;
                e.currentTarget.style.boxShadow = 'none';
              }}
            />
            <MagnifyingGlassIcon
              className="pointer-events-none absolute right-4 top-1/2 h-5 w-5 -translate-y-1/2 opacity-60"
              style={{ color: ui.textDim }}
            />
            {searchTerm && (
              <button
                onClick={() => setSearchTerm('')}
                className="absolute left-4 top-1/2 -translate-y-1/2"
                aria-label="Clear"
              >
                <XMarkIcon className="h-5 w-5" style={{ color: ui.textDim }} />
              </button>
            )}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-2">
          {searchedFunctions.map((fn) => {
            const active = selectedFunction === fn;
            return (
              <motion.button
                key={fn}
                onClick={() => {
                  setSelectedFunction(fn);
                  initializeParams(fn);
                  setResponse(null);
                  setError('');
                  setAuthHeaders(null);
                  setUrlParams('');
                  setActiveTab('run');
                }}
                className="w-full text-left whitespace-nowrap rounded-md px-5 py-4 text-base"
                style={{
                  backgroundColor: active ? ui.accent : ui.panelAlt,
                  color: active ? '#000' : ui.text,
                  border: `1px solid ${active ? ui.accent : ui.border}`,
                  transition: 'background-color 120ms ease, border-color 120ms ease',
                }}
                whileTap={{ scale: 0.98 }}
                title={fn}
              >
                {fn}
              </motion.button>
            );
          })}
          {searchedFunctions.length === 0 && (
            <span className="text-base bloc text-center py-8" style={{ color: ui.textDim }}>
              No functions found
            </span>
          )}
        </div>
      </div>

      <div
        onMouseDown={handleMouseDown}
        className="w-1 cursor-col-resize hover:bg-blue-500/50 transition-colors"
        style={{ backgroundColor: isDragging ? ui.focus : ui.border }}
      />

      <div className="flex-1 flex flex-col overflow-hidden">
        {selectedFunction ? (
          <div className="flex h-full flex-col" style={{ backgroundColor: ui.bg }}>
            <div className="flex gap-3 border-b px-6 py-5" style={{ borderColor: ui.border, backgroundColor: ui.panel }}>
              <button
                onClick={executeFunction}
                className="rounded-md px-8 py-4 text-lg font-semibold"
                style={{
                  backgroundColor: activeTab === 'run' ? ui.accent : 'transparent',
                  color: activeTab === 'run' ? '#000' : ui.text,
                  border: `2px solid ${activeTab === 'run' ? ui.accent : ui.border}`,
                }}
              >
                <span className="inline-flex items-center gap-3">
                  <PlayIcon className="h-6 w-6" />
                  Run
                </span>
              </button>
              {filteredSchema[selectedFunction]?.code && (
                <button
                  onClick={() => setActiveTab('code')}
                  className="rounded-md px-8 py-4 text-lg font-semibold"
                  style={{
                    backgroundColor: activeTab === 'code' ? ui.accent : 'transparent',
                    color: activeTab === 'code' ? '#000' : ui.text,
                    border: `2px solid ${activeTab === 'code' ? ui.accent : ui.border}`,
                  }}
                >
                  <span className="inline-flex items-center gap-3">
                    <CodeBracketIcon className="h-6 w-6" />
                    Code
                  </span>
                </button>
              )}
            </div>

            <div className="flex-1 overflow-auto flex flex-col">
              <AnimatePresence mode="wait">
                {activeTab === 'run' ? (
                  <motion.div
                    key="run"
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -6 }}
                    className="flex-1 flex flex-col"
                  >
                    <div className="p-6 space-y-6">
                      <div className="flex items-center gap-4">
                        <label className="text-base font-medium" style={{ color: ui.textDim }}>Columns:</label>
                        <div className="flex gap-2">
                          {[1, 2, 3, 4].map((num) => (
                            <button
                              key={num}
                              onClick={() => setParamColumns(num)}
                              className="rounded px-4 py-2 text-base"
                              style={{
                                backgroundColor: paramColumns === num ? ui.accent : ui.panelAlt,
                                color: paramColumns === num ? '#000' : ui.text,
                                border: `1px solid ${paramColumns === num ? ui.accent : ui.border}`,
                              }}
                            >
                              {num}
                            </button>
                          ))}
                        </div>
                      </div>

                      <div className="space-y-5">
                        <div className={`grid gap-5`} style={{ gridTemplateColumns: `repeat(${paramColumns}, 1fr)` }}>
                          {Object.entries(filteredSchema[selectedFunction]?.input || {}).map(([p, d]) => (
                            <div key={p} className="space-y-2">
                              <label className="text-base font-medium" style={{ color: ui.textDim }}>
                                {p} <span style={{ color: ui.textDim, fontSize: '0.85em' }}>[{d.type}]</span>
                              </label>
                              <input
                                type="text"
                                value={params[p] ?? ''}
                                onChange={(e) => handleParamChange(p, e.target.value)}
                                placeholder={d.value ? String(d.value) : ''}
                                className="w-full rounded-md px-4 py-3 text-base outline-none"
                                style={{
                                  backgroundColor: ui.panelAlt,
                                  color: ui.text,
                                  border: `1px solid ${ui.border}`,
                                  transition: 'box-shadow 120ms ease, border-color 120ms ease',
                                }}
                                onFocus={(e) => {
                                  e.currentTarget.style.borderColor = ui.focus;
                                  e.currentTarget.style.boxShadow = `0 0 0 3px ${ui.focus}22`;
                                }}
                                onBlur={(e) => {
                                  e.currentTarget.style.borderColor = ui.border;
                                  e.currentTarget.style.boxShadow = 'none';
                                }}
                                onKeyDown={(e) => {
                                  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') executeFunction();
                                }}
                              />
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>

                    {(response || error) && (
                      <div className="border-t p-6 space-y-4" style={{ borderColor: ui.border, backgroundColor: ui.panel }}>
                        <div className="flex items-center justify-between">
                          <span className="text-lg font-semibold" style={{ color: responseColor }}>
                            {error ? 'Error' : 'Response'}
                          </span>
                          <CopyButton content={JSON.stringify(response || error, null, 2)} />
                        </div>
                        <pre
                          className="micro-scroll-y overflow-auto rounded-md p-5 text-base max-h-96"
                          style={{
                            backgroundColor: ui.panelAlt,
                            color: responseColor,
                            border: `1px solid ${responseColor}`,
                          }}
                        >
{JSON.stringify(response || error, null, 2)}
                        </pre>
                      </div>
                    )}
                  </motion.div>
                ) : (
                  <motion.div
                    key="code"
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -6 }}
                    className="h-full p-6"
                  >
                    <div
                      className="micro-scroll-y h-full overflow-auto rounded-md"
                      style={{ backgroundColor: ui.panelAlt, border: `1px solid ${ui.border}` }}
                    >
                      <div
                        className="flex items-center justify-between border-b px-5 py-4"
                        style={{ borderColor: ui.border }}
                      >
                        <span className="text-base" style={{ color: ui.textDim }}>
                          Function source
                        </span>
                        <CopyButton content={filteredSchema[selectedFunction]?.code || ''} />
                      </div>
                      <pre className="p-5 text-base" style={{ color: ui.text }}>
                        {filteredSchema[selectedFunction]?.code || 'No code available'}
                      </pre>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </div>
        ) : (
          <div
            className="flex h-full items-center justify-center"
            style={{ color: ui.textDim, backgroundColor: ui.bg }}
          >
            <div className="flex items-center gap-3 text-lg">
              <CommandLineIcon className="h-7 w-7 opacity-60" />
              Select a function to begin
            </div>
          </div>
        )}
      </div>

      <style jsx>{`
        .micro-scroll-y {
          scrollbar-width: thin;
          scrollbar-color: rgba(255,255,255,0.12) transparent;
        }
        .micro-scroll-y::-webkit-scrollbar {
          width: 8px;
          background: transparent;
        }
        .micro-scroll-y::-webkit-scrollbar-thumb {
          background: rgba(255,255,255,0.12);
          border-radius: 9999px;
        }
        .micro-scroll-y::-webkit-scrollbar-track {
          background: transparent;
        }
      `}</style>
    </div>
  );
};

export default ModApi;