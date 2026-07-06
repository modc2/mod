"use client";

import { useState, useEffect } from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";

interface CodeViewerProps {
  filePath: string | null;
  workDir: string;
  apiUrl?: string;
}

const LANGUAGE_MAP: Record<string, string> = {
  ".py": "python",
  ".js": "javascript",
  ".jsx": "jsx",
  ".ts": "typescript",
  ".tsx": "tsx",
  ".rs": "rust",
  ".go": "go",
  ".java": "java",
  ".cpp": "cpp",
  ".c": "c",
  ".sh": "bash",
  ".json": "json",
  ".md": "markdown",
  ".yaml": "yaml",
  ".yml": "yaml",
  ".toml": "toml",
  ".xml": "xml",
  ".html": "html",
  ".css": "css",
  ".sql": "sql",
  ".rb": "ruby",
  ".php": "php",
  ".swift": "swift",
  ".kt": "kotlin",
  ".r": "r",
  ".scala": "scala",
};

function getLanguageFromFilename(filename: string): string {
  const parts = filename.split(".");
  const ext = parts.length > 1 ? `.${parts[parts.length - 1]}` : "";
  return LANGUAGE_MAP[ext] || "text";
}

export default function CodeViewer({ filePath, workDir, apiUrl }: CodeViewerProps) {
  const [content, setContent] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lineCount, setLineCount] = useState(0);

  useEffect(() => {
    if (!filePath) {
      setContent("");
      return;
    }
    loadFileContent();
  }, [filePath, workDir]);

  const loadFileContent = async () => {
    if (!filePath) return;

    setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `${apiUrl || `/api${process.env.NEXT_PUBLIC_BASE_PATH || "/claude"}`}/files/content?path=${encodeURIComponent(filePath)}`
      );
      if (!res.ok) throw new Error("Failed to load file content");
      const data = await res.json();
      setContent(data.content || "");
      setLineCount(data.content ? data.content.split("\n").length : 0);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load file");
      setContent("");
    } finally {
      setLoading(false);
    }
  };

  if (!filePath) {
    return (
      <div
        style={{
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "#666",
          fontFamily: "monospace",
          fontSize: "13px",
        }}
      >
        Select a file to view
      </div>
    );
  }

  if (loading) {
    return (
      <div
        style={{
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "#00aaff",
          fontFamily: "monospace",
          fontSize: "13px",
        }}
      >
        Loading file...
      </div>
    );
  }

  if (error) {
    return (
      <div
        style={{
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "#ff3333",
          fontFamily: "monospace",
          fontSize: "13px",
        }}
      >
        Error: {error}
      </div>
    );
  }

  const language = getLanguageFromFilename(filePath);
  const filename = filePath.split("/").pop() || filePath;

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {/* Header */}
      <div
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid #333",
          backgroundColor: "#0f0f0f",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <span style={{ color: "#00aaff", fontFamily: "monospace", fontSize: "13px", fontWeight: "bold" }}>
            {filename}
          </span>
          <span style={{ color: "#666", fontFamily: "monospace", fontSize: "12px" }}>
            {language.toUpperCase()}
          </span>
        </div>
        <div style={{ color: "#666", fontFamily: "monospace", fontSize: "12px" }}>
          {lineCount} lines
        </div>
      </div>

      {/* Code Content */}
      <div style={{ flex: 1, overflow: "auto", backgroundColor: "#1e1e1e" }}>
        <SyntaxHighlighter
          language={language}
          style={vscDarkPlus}
          showLineNumbers
          lineNumberStyle={{
            minWidth: "3em",
            paddingRight: "1em",
            color: "#666",
            textAlign: "right",
            userSelect: "none",
          }}
          customStyle={{
            margin: 0,
            padding: "16px",
            fontSize: "13px",
            backgroundColor: "#1e1e1e",
            fontFamily: "'SF Mono', Monaco, 'Courier New', monospace",
          }}
          codeTagProps={{
            style: {
              fontFamily: "'SF Mono', Monaco, 'Courier New', monospace",
              lineHeight: "1.6",
            },
          }}
        >
          {content}
        </SyntaxHighlighter>
      </div>
    </div>
  );
}
