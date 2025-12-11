'use client';
import { useState, useEffect } from 'react';

const DEFAULT_API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export function NodeUrlSettings() {
  const [nodeUrl, setNodeUrl] = useState<string>(DEFAULT_API_URL);
  const [inputUrl, setInputUrl] = useState<string>('');

  useEffect(() => {
    const saved = localStorage.getItem('custom_node_url');
    if (saved) {
      setNodeUrl(saved);
      setInputUrl(saved);
    } else {
      setInputUrl(DEFAULT_API_URL);
    }
  }, []);

  const handleSave = () => {
    if (inputUrl.trim()) {
      localStorage.setItem('custom_node_url', inputUrl.trim());
      setNodeUrl(inputUrl.trim());
      window.location.reload();
    }
  };

  const handleReset = () => {
    localStorage.removeItem('custom_node_url');
    setNodeUrl(DEFAULT_API_URL);
    setInputUrl(DEFAULT_API_URL);
    window.location.reload();
  };

  return (
    <div className="flex items-center gap-2 px-4">
      <input
        type="text"
        value={inputUrl}
        onChange={(e) => setInputUrl(e.target.value)}
        placeholder="Enter node URL"
        className="max-w-xs bg-black/20 border border-white/10 text-white placeholder:text-white/40 px-3 py-2 rounded-md focus:outline-none focus:ring-2 focus:ring-white/20"
      />
      <button 
        onClick={handleSave} 
        className="px-4 py-2 text-sm border border-white/20 hover:bg-white/10 rounded-md transition-colors"
      >
        Save
      </button>
      <button 
        onClick={handleReset} 
        className="px-4 py-2 text-sm hover:bg-white/10 rounded-md transition-colors"
      >
        Reset
      </button>
    </div>
  );
}