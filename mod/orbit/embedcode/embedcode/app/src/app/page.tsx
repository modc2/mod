'use client';

import { useState, useEffect } from 'react';
import axios from 'axios';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8920';

type Tab = 'search' | 'embed' | 'collections';

interface SearchResult {
  path: string;
  preview: string;
  score: number;
  chunk_index: number;
  total_chunks: number;
  collection: string;
}

interface Collection {
  name: string;
  chunks: number;
  files: number;
  paths: string[];
}

export default function Home() {
  const [tab, setTab] = useState<Tab>('search');
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [embedPath, setEmbedPath] = useState('');
  const [embedName, setEmbedName] = useState('');
  const [embedding, setEmbedding] = useState(false);
  const [embedResult, setEmbedResult] = useState<any>(null);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [selectedCollection, setSelectedCollection] = useState<string>('');
  const [status, setStatus] = useState<any>(null);
  const [expandedResult, setExpandedResult] = useState<number | null>(null);

  useEffect(() => {
    fetchCollections();
    fetchStatus();
  }, []);

  const fetchStatus = async () => {
    try {
      const res = await axios.get(`${API}/status`);
      setStatus(res.data);
    } catch { }
  };

  const fetchCollections = async () => {
    try {
      const res = await axios.get(`${API}/collections`);
      setCollections(res.data.details || []);
    } catch { }
  };

  const handleSearch = async () => {
    if (!query.trim()) return;
    setSearching(true);
    setResults([]);
    try {
      const res = await axios.post(`${API}/search`, {
        query,
        collection: selectedCollection || undefined,
        top_k: 20,
      });
      setResults(res.data);
    } catch (e: any) {
      console.error(e);
    }
    setSearching(false);
  };

  const handleEmbed = async () => {
    if (!embedPath.trim()) return;
    setEmbedding(true);
    setEmbedResult(null);
    try {
      const res = await axios.post(`${API}/embed`, {
        path: embedPath,
        collection: embedName || undefined,
      });
      setEmbedResult(res.data);
      fetchCollections();
    } catch (e: any) {
      setEmbedResult({ error: e.response?.data?.detail || e.message });
    }
    setEmbedding(false);
  };

  const handleDelete = async (name: string) => {
    try {
      await axios.delete(`${API}/collections/${name}`);
      fetchCollections();
    } catch { }
  };

  const tabItems: { id: Tab; label: string }[] = [
    { id: 'search', label: 'Search' },
    { id: 'embed', label: 'Embed' },
    { id: 'collections', label: 'Collections' },
  ];

  return (
    <div className="min-h-screen bg-gray-950">
      {/* Header */}
      <header className="border-b border-gray-800 bg-gray-950/80 backdrop-blur sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-emerald-500/20 flex items-center justify-center text-emerald-400 text-sm font-bold font-mono">
              ec
            </div>
            <h1 className="text-lg font-semibold text-gray-100">embedcode</h1>
            <span className="text-xs text-gray-600 font-mono">local embeddings</span>
          </div>
          <div className="flex items-center gap-4">
            {status && (
              <span className="text-xs text-gray-500">
                {status.collections || 0} collections
              </span>
            )}
          </div>
        </div>
        <div className="max-w-6xl mx-auto px-6">
          <nav className="flex gap-1">
            {tabItems.map(t => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                  tab === t.id
                    ? 'border-emerald-400 text-emerald-400'
                    : 'border-transparent text-gray-500 hover:text-gray-300'
                }`}
              >
                {t.label}
              </button>
            ))}
          </nav>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-8">

        {/* Search Tab */}
        {tab === 'search' && (
          <div className="space-y-6">
            <div className="flex gap-3">
              <input
                type="text"
                value={query}
                onChange={e => setQuery(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleSearch()}
                placeholder="Search code semantically..."
                className="flex-1 bg-gray-900 border border-gray-800 rounded-lg px-4 py-3 text-sm text-gray-100 placeholder-gray-600 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20"
              />
              {collections.length > 0 && (
                <select
                  value={selectedCollection}
                  onChange={e => setSelectedCollection(e.target.value)}
                  className="bg-gray-900 border border-gray-800 rounded-lg px-3 py-3 text-sm text-gray-300 focus:outline-none focus:border-emerald-500/50"
                >
                  <option value="">All collections</option>
                  {collections.map(c => (
                    <option key={c.name} value={c.name}>{c.name} ({c.chunks})</option>
                  ))}
                </select>
              )}
              <button
                onClick={handleSearch}
                disabled={searching || !query.trim()}
                className="px-6 py-3 bg-emerald-600 hover:bg-emerald-500 disabled:bg-gray-800 disabled:text-gray-600 text-white text-sm font-medium rounded-lg transition-colors"
              >
                {searching ? 'Searching...' : 'Search'}
              </button>
            </div>

            {results.length > 0 && (
              <div className="space-y-2">
                <p className="text-xs text-gray-500">{results.length} results</p>
                {results.map((r, i) => (
                  <div
                    key={i}
                    className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden hover:border-gray-700 transition-colors"
                  >
                    <button
                      onClick={() => setExpandedResult(expandedResult === i ? null : i)}
                      className="w-full px-4 py-3 flex items-center justify-between text-left"
                    >
                      <div className="flex items-center gap-3 min-w-0">
                        <span className="text-gray-500 shrink-0 text-xs">{expandedResult === i ? 'v' : '>'}</span>
                        <span className="text-sm text-gray-300 truncate font-mono">{r.path}</span>
                        <span className="text-xs text-gray-600 shrink-0">
                          chunk {r.chunk_index + 1}/{r.total_chunks}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 shrink-0 ml-3">
                        <div className="w-16 h-1.5 bg-gray-800 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-emerald-500 rounded-full"
                            style={{ width: `${Math.round(r.score * 100)}%` }}
                          />
                        </div>
                        <span className="text-xs text-emerald-400 font-mono w-12 text-right">
                          {(r.score * 100).toFixed(1)}%
                        </span>
                      </div>
                    </button>
                    {expandedResult === i && (
                      <div className="px-4 pb-4 border-t border-gray-800">
                        <pre className="mt-3 text-xs text-gray-400 font-mono whitespace-pre-wrap leading-relaxed max-h-80 overflow-y-auto">
                          {r.preview}
                        </pre>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {!searching && results.length === 0 && query && (
              <div className="text-center py-16 text-gray-600 text-sm">
                No results. Embed some code first.
              </div>
            )}

            {!query && (
              <div className="text-center py-16 text-gray-600 text-sm">
                Search your codebase by meaning, not just keywords.
              </div>
            )}
          </div>
        )}

        {/* Embed Tab */}
        {tab === 'embed' && (
          <div className="max-w-2xl space-y-6">
            <div>
              <h2 className="text-lg font-semibold text-gray-100 mb-1">Embed Code</h2>
              <p className="text-sm text-gray-500">
                Point to a folder or file to generate embeddings using a local model.
              </p>
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-xs text-gray-500 mb-1.5 uppercase tracking-wider">Path</label>
                <input
                  type="text"
                  value={embedPath}
                  onChange={e => setEmbedPath(e.target.value)}
                  placeholder="/path/to/your/code"
                  className="w-full bg-gray-900 border border-gray-800 rounded-lg px-4 py-3 text-sm text-gray-100 placeholder-gray-600 font-mono focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1.5 uppercase tracking-wider">Collection Name (optional)</label>
                <input
                  type="text"
                  value={embedName}
                  onChange={e => setEmbedName(e.target.value)}
                  placeholder="auto-generated from path"
                  className="w-full bg-gray-900 border border-gray-800 rounded-lg px-4 py-3 text-sm text-gray-100 placeholder-gray-600 font-mono focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20"
                />
              </div>
              <button
                onClick={handleEmbed}
                disabled={embedding || !embedPath.trim()}
                className="px-6 py-3 bg-emerald-600 hover:bg-emerald-500 disabled:bg-gray-800 disabled:text-gray-600 text-white text-sm font-medium rounded-lg transition-colors"
              >
                {embedding ? 'Embedding...' : 'Embed'}
              </button>
            </div>

            {embedResult && (
              <div className={`p-4 rounded-lg border ${
                embedResult.error
                  ? 'bg-red-500/10 border-red-500/20 text-red-400'
                  : 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400'
              }`}>
                {embedResult.error ? (
                  <p className="text-sm">{embedResult.error}</p>
                ) : (
                  <div className="space-y-1 text-sm font-mono">
                    <p>collection: {embedResult.collection}</p>
                    <p>files: {embedResult.files}</p>
                    <p>chunks: {embedResult.chunks}</p>
                    <p>model: {embedResult.model}</p>
                    <p>dim: {embedResult.dim}</p>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Collections Tab */}
        {tab === 'collections' && (
          <div className="space-y-6">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold text-gray-100">Collections</h2>
              <button
                onClick={fetchCollections}
                className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
              >
                refresh
              </button>
            </div>

            {collections.length === 0 ? (
              <div className="text-center py-16 text-gray-600 text-sm">
                No collections yet. Embed some code to get started.
              </div>
            ) : (
              <div className="space-y-3">
                {collections.map(c => (
                  <div
                    key={c.name}
                    className="bg-gray-900 border border-gray-800 rounded-lg p-4 hover:border-gray-700 transition-colors"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <h3 className="text-sm font-mono text-gray-200">{c.name}</h3>
                      <button
                        onClick={() => handleDelete(c.name)}
                        className="text-gray-600 hover:text-red-400 transition-colors text-xs"
                      >
                        delete
                      </button>
                    </div>
                    <div className="flex gap-4 text-xs text-gray-500">
                      <span>{c.files} files</span>
                      <span>{c.chunks} chunks</span>
                    </div>
                    {c.paths.length > 0 && (
                      <div className="mt-2 space-y-0.5">
                        {c.paths.slice(0, 5).map((p, i) => (
                          <p key={i} className="text-xs text-gray-600 font-mono truncate">{p}</p>
                        ))}
                        {c.paths.length > 5 && (
                          <p className="text-xs text-gray-700">+{c.paths.length - 5} more</p>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
