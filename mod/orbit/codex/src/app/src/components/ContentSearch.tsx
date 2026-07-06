"use client";

import { useState, useEffect, useRef } from "react";

interface ContentMatch {
  path: string;
  filename: string;
  line: number;
  content: string;
  matchStart: number;
  matchEnd: number;
}

interface ContentSearchProps {
  workDir: string;
  onFileSelect: (path: string, line?: number) => void;
  isOpen: boolean;
  onClose: () => void;
  apiUrl?: string;
}

export default function ContentSearch({ workDir, onFileSelect, isOpen, onClose, apiUrl }: ContentSearchProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ContentMatch[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [useRegex, setUseRegex] = useState(false);
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
      searchContent();
    }, 300);

    return () => clearTimeout(timeoutId);
  }, [query, workDir, caseSensitive, useRegex]);

  const searchContent = async () => {
    if (!query.trim()) return;

    setLoading(true);
    try {
      const params = new URLSearchParams({
        path: workDir,
        query: query,
        caseSensitive: caseSensitive.toString(),
        regex: useRegex.toString(),
      });

      const res = await fetch(
        `${apiUrl || `/api${process.env.NEXT_PUBLIC_BASE_PATH || "/claude"}`}/files/grep?${params}`
      );
      if (!res.ok) throw new Error("Search failed");
      const data = await res.json();
      setResults(data.matches || []);
      setSelectedIndex(0);
    } catch (err) {
      console.error("Content search error:", err);
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
      onFileSelect(results[selectedIndex].path, results[selectedIndex].line);
      onClose();
    }
  };

  const highlightMatch = (content: string, start: number, end: number) => {
    const before = content.substring(0, start);
    const match = content.substring(start, end);
    const after = content.substring(end);

    return (
      <>
        {before}
        <span style={{ backgroundColor: "#ffb000", color: "#000", fontWeight: "bold" }}>
          {match}
        </span>
        {after}
      </>
    );
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
          width: "700px",
          maxHeight: "600px",
          display: "flex",
          flexDirection: "column",
          boxShadow: "0 8px 32px rgba(0, 0, 0, 0.9)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Search Input */}
        <div style={{ padding: "16px", borderBottom: "1px solid #333" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "12px" }}>
            <span style={{ color: "#00aaff", fontSize: "11px" }}>🔎</span>
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Search file contents..."
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

          {/* Options */}
          <div style={{ display: "flex", gap: "16px", fontSize: "8px" }}>
            <label style={{ display: "flex", alignItems: "center", gap: "6px", cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={caseSensitive}
                onChange={(e) => setCaseSensitive(e.target.checked)}
                style={{ cursor: "pointer" }}
              />
              <span style={{ color: "#ccc", fontFamily: "monospace" }}>Case sensitive (Aa)</span>
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: "6px", cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={useRegex}
                onChange={(e) => setUseRegex(e.target.checked)}
                style={{ cursor: "pointer" }}
              />
              <span style={{ color: "#ccc", fontFamily: "monospace" }}>Regex (.*)</span>
            </label>
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
              No matches found
            </div>
          )}

          {results.map((result, idx) => (
            <div
              key={`${result.path}-${result.line}-${idx}`}
              onClick={() => {
                onFileSelect(result.path, result.line);
                onClose();
              }}
              style={{
                padding: "12px 16px",
                cursor: "pointer",
                backgroundColor: idx === selectedIndex ? "rgba(0, 170, 255, 0.2)" : "transparent",
                borderLeft: idx === selectedIndex ? "2px solid #00aaff" : "2px solid transparent",
                display: "flex",
                flexDirection: "column",
                gap: "6px",
              }}
              onMouseEnter={() => setSelectedIndex(idx)}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <span
                  style={{
                    color: "#00aaff",
                    fontFamily: "monospace",
                    fontSize: "9px",
                    fontWeight: "500",
                  }}
                >
                  {result.filename}
                </span>
                <span
                  style={{
                    color: "#666",
                    fontFamily: "monospace",
                    fontSize: "8px",
                  }}
                >
                  :{result.line}
                </span>
              </div>
              <div
                style={{
                  color: "#ccc",
                  fontFamily: "monospace",
                  fontSize: "8px",
                  whiteSpace: "pre",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {highlightMatch(result.content, result.matchStart, result.matchEnd)}
              </div>
              <div
                style={{
                  color: "#555",
                  fontFamily: "monospace",
                  fontSize: "7px",
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
          <span>{results.length} matches</span>
          <div style={{ display: "flex", gap: "16px" }}>
            <span>↑↓ Navigate</span>
            <span>Enter Select</span>
            <span>Esc Close</span>
          </div>
        </div>
      </div>
    </div>
  );
}
