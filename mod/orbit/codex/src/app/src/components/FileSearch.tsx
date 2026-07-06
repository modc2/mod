"use client";

import { useState, useEffect, useRef } from "react";

interface SearchResult {
  path: string;
  filename: string;
  matches: number;
}

interface FileSearchProps {
  workDir: string;
  onFileSelect: (path: string) => void;
  isOpen: boolean;
  onClose: () => void;
  apiUrl?: string;
}

export default function FileSearch({ workDir, onFileSelect, isOpen, onClose, apiUrl }: FileSearchProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      inputRef.current?.focus();
      setQuery("");
      setResults([]);
    }
  }, [isOpen]);

  useEffect(() => {
    if (!query.trim()) {
      setResults([]);
      return;
    }

    const timeoutId = setTimeout(() => {
      searchFiles();
    }, 300);

    return () => clearTimeout(timeoutId);
  }, [query, workDir]);

  const searchFiles = async () => {
    if (!query.trim()) return;

    setLoading(true);
    try {
      const res = await fetch(
        `${apiUrl || `/api${process.env.NEXT_PUBLIC_BASE_PATH || "/claude"}`}/files/search?path=${encodeURIComponent(workDir)}&query=${encodeURIComponent(query)}`
      );
      if (!res.ok) throw new Error("Search failed");
      const data = await res.json();
      setResults(data.results || []);
      setSelectedIndex(0);
    } catch (err) {
      console.error("File search error:", err);
      setResults([]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      onClose();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((prev) => Math.min(prev + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((prev) => Math.max(prev - 1, 0));
    } else if (e.key === "Enter" && results[selectedIndex]) {
      onFileSelect(results[selectedIndex].path);
      onClose();
    }
  };

  if (!isOpen) return null;

  return (
    <div
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: "rgba(0, 0, 0, 0.8)",
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        paddingTop: "100px",
        zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        style={{
          backgroundColor: "#0a0a0a",
          border: "1px solid #333",
          borderRadius: "8px",
          width: "600px",
          maxHeight: "500px",
          display: "flex",
          flexDirection: "column",
          boxShadow: "0 8px 32px rgba(0, 0, 0, 0.9)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Search Input */}
        <div style={{ padding: "16px", borderBottom: "1px solid #333" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span style={{ color: "#00aaff", fontSize: "11px" }}>🔍</span>
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Search files by name..."
              style={{
                flex: 1,
                background: "transparent",
                border: "none",
                outline: "none",
                color: "#fff",
                fontSize: "10px",
                fontFamily: "monospace",
              }}
            />
            {loading && (
              <span style={{ color: "#666", fontSize: "8px" }}>Searching...</span>
            )}
          </div>
        </div>

        {/* Results */}
        <div style={{ flex: 1, overflowY: "auto" }}>
          {results.length === 0 && query && !loading && (
            <div
              style={{
                padding: "32px",
                textAlign: "center",
                color: "#666",
                fontFamily: "monospace",
                fontSize: "9px",
              }}
            >
              No files found
            </div>
          )}

          {results.map((result, idx) => (
            <div
              key={result.path}
              onClick={() => {
                onFileSelect(result.path);
                onClose();
              }}
              style={{
                padding: "12px 16px",
                cursor: "pointer",
                backgroundColor: idx === selectedIndex ? "rgba(0, 170, 255, 0.2)" : "transparent",
                borderLeft: idx === selectedIndex ? "2px solid #00aaff" : "2px solid transparent",
                display: "flex",
                flexDirection: "column",
                gap: "4px",
              }}
              onMouseEnter={() => setSelectedIndex(idx)}
            >
              <div
                style={{
                  color: "#fff",
                  fontFamily: "monospace",
                  fontSize: "9px",
                  fontWeight: "500",
                }}
              >
                {result.filename}
              </div>
              <div
                style={{
                  color: "#666",
                  fontFamily: "monospace",
                  fontSize: "8px",
                }}
              >
                {result.path}
              </div>
            </div>
          ))}
        </div>

        {/* Footer */}
        <div
          style={{
            padding: "8px 16px",
            borderTop: "1px solid #333",
            display: "flex",
            justifyContent: "space-between",
            fontSize: "8px",
            color: "#666",
            fontFamily: "monospace",
          }}
        >
          <span>↑↓ Navigate</span>
          <span>Enter Select</span>
          <span>Esc Close</span>
        </div>
      </div>
    </div>
  );
}
