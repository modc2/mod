"use client";

import { useState, useEffect } from "react";

interface FileNode {
  name: string;
  path: string;
  type: "file" | "directory";
  children?: FileNode[];
}

interface FileTreeProps {
  workDir: string;
  onFileSelect: (path: string) => void;
  selectedFile: string | null;
  apiUrl?: string;
}

const FILE_COLORS: Record<string, string> = {
  // Code files
  ".py": "#3572A5",
  ".js": "#f1e05a",
  ".ts": "#2b7489",
  ".tsx": "#2b7489",
  ".jsx": "#f1e05a",
  ".rs": "#dea584",
  ".go": "#00ADD8",
  ".java": "#b07219",
  ".cpp": "#f34b7d",
  ".c": "#555555",
  ".sh": "#89e051",

  // Markup/Config
  ".json": "#ffb000",
  ".md": "#083fa1",
  ".yaml": "#cb171e",
  ".yml": "#cb171e",
  ".toml": "#9c4221",
  ".xml": "#0060ac",
  ".html": "#e34c26",
  ".css": "#563d7c",

  // Other
  ".txt": "#cccccc",
  ".log": "#888888",
  ".ini": "#d1dbe0",
};

const FILE_ICONS: Record<string, string> = {
  ".py": "🐍",
  ".js": "📜",
  ".ts": "📘",
  ".tsx": "📘",
  ".jsx": "📜",
  ".rs": "🦀",
  ".go": "🔷",
  ".java": "☕",
  ".sh": "🔧",
  ".json": "📋",
  ".md": "📝",
  ".yaml": "⚙️",
  ".yml": "⚙️",
  ".toml": "⚙️",
  ".txt": "📄",
  ".log": "📊",
  "directory": "📁",
  "default": "📄",
};

function getFileExtension(filename: string): string {
  const parts = filename.split(".");
  return parts.length > 1 ? `.${parts[parts.length - 1]}` : "";
}

function getFileColor(filename: string): string {
  const ext = getFileExtension(filename);
  return FILE_COLORS[ext] || "#cccccc";
}

function getFileIcon(node: FileNode): string {
  if (node.type === "directory") return FILE_ICONS.directory;
  const ext = getFileExtension(node.name);
  return FILE_ICONS[ext] || FILE_ICONS.default;
}

function FileTreeNode({
  node,
  depth,
  onFileSelect,
  selectedFile
}: {
  node: FileNode;
  depth: number;
  onFileSelect: (path: string) => void;
  selectedFile: string | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const isSelected = selectedFile === node.path;

  const handleClick = () => {
    if (node.type === "directory") {
      setExpanded(!expanded);
    } else {
      onFileSelect(node.path);
    }
  };

  const color = node.type === "file" ? getFileColor(node.name) : "#33ff33";
  const icon = getFileIcon(node);

  return (
    <div>
      <div
        onClick={handleClick}
        style={{
          paddingLeft: `${depth * 16}px`,
          cursor: "pointer",
          color: color,
          backgroundColor: isSelected ? "rgba(0, 170, 255, 0.2)" : "transparent",
          padding: "4px 8px",
          display: "flex",
          alignItems: "center",
          gap: "6px",
          fontFamily: "monospace",
          fontSize: "9px",
          borderLeft: isSelected ? "2px solid #00aaff" : "none",
        }}
      >
        {node.type === "directory" && (
          <span style={{ color: "#888", fontSize: "7px" }}>
            {expanded ? "▼" : "▶"}
          </span>
        )}
        <span>{icon}</span>
        <span>{node.name}</span>
      </div>
      {node.type === "directory" && expanded && node.children && (
        <div>
          {node.children.map((child, idx) => (
            <FileTreeNode
              key={idx}
              node={child}
              depth={depth + 1}
              onFileSelect={onFileSelect}
              selectedFile={selectedFile}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function FileTree({ workDir, onFileSelect, selectedFile, apiUrl }: FileTreeProps) {
  const [tree, setTree] = useState<FileNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadFileTree();
  }, [workDir]);

  const loadFileTree = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${apiUrl || `/api${process.env.NEXT_PUBLIC_BASE_PATH || "/claude"}`}/files/tree?path=${encodeURIComponent(workDir)}`);
      if (!res.ok) throw new Error("Failed to load file tree");
      const data = await res.json();
      setTree(data.tree || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load files");
    } finally {
      setLoading(false);
    }
  };

  // Auto-select config.json on initial load
  useEffect(() => {
    if (tree.length > 0 && !selectedFile) {
      const configNode = tree.find(n => n.type === "file" && n.name === "config.json");
      if (configNode) {
        onFileSelect(configNode.path);
      }
    }
  }, [tree]);

  if (loading) {
    return (
      <div style={{ padding: "16px", color: "#00aaff", fontFamily: "monospace" }}>
        Loading file tree...
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: "16px", color: "#ff3333", fontFamily: "monospace" }}>
        Error: {error}
      </div>
    );
  }

  return (
    <div style={{
      height: "100%",
      overflowY: "auto",
      backgroundColor: "#0a0a0a",
      borderRight: "1px solid #333",
    }}>
      <div style={{
        padding: "12px",
        borderBottom: "1px solid #333",
        color: "#00aaff",
        fontFamily: "monospace",
        fontSize: "8px",
        fontWeight: "bold",
      }}>
        FILES
      </div>
      {tree.map((node, idx) => (
        <FileTreeNode
          key={idx}
          node={node}
          depth={0}
          onFileSelect={onFileSelect}
          selectedFile={selectedFile}
        />
      ))}
    </div>
  );
}
