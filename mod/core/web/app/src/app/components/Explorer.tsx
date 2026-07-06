"use client";

import { useEffect, useMemo, useState } from "react";
import { api, type TreeNode, type FileContent } from "@/lib/api";

// A glyph per file kind — purely cosmetic, keeps the tree scannable.
function glyphFor(node: TreeNode): string {
  if (node.type === "dir") return "▸";
  const ext = node.name.split(".").pop()?.toLowerCase() ?? "";
  if (ext === "rs") return "🦀";
  if (ext === "py") return "🐍";
  if (ext === "ts" || ext === "tsx") return "◆";
  if (ext === "js" || ext === "jsx" || ext === "mjs") return "◇";
  if (ext === "json") return "{}";
  if (ext === "css") return "▤";
  if (ext === "md") return "¶";
  if (ext === "toml" || ext === "lock") return "⚙";
  if (ext === "sh") return "$";
  return "·";
}

function langFor(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  const map: Record<string, string> = {
    rs: "rust",
    py: "python",
    ts: "typescript",
    tsx: "tsx",
    js: "javascript",
    jsx: "jsx",
    mjs: "javascript",
    json: "json",
    css: "css",
    md: "markdown",
    toml: "toml",
    sh: "shell",
    yml: "yaml",
    yaml: "yaml",
    html: "html",
  };
  return map[ext] || ext || "text";
}

function Branch({
  nodes,
  depth,
  selected,
  onSelect,
}: {
  nodes: TreeNode[];
  depth: number;
  selected: string | null;
  onSelect: (n: TreeNode) => void;
}) {
  return (
    <ul className="tree-list">
      {nodes.map((n) => (
        <TreeRow
          key={n.path}
          node={n}
          depth={depth}
          selected={selected}
          onSelect={onSelect}
        />
      ))}
    </ul>
  );
}

function TreeRow({
  node,
  depth,
  selected,
  onSelect,
}: {
  node: TreeNode;
  depth: number;
  selected: string | null;
  onSelect: (n: TreeNode) => void;
}) {
  // Open the first two levels by default so the module's shape is visible.
  const [open, setOpen] = useState(depth < 1);
  const isDir = node.type === "dir";
  const active = selected === node.path;

  return (
    <li>
      <button
        className={`tree-row${active ? " active" : ""}`}
        style={{ paddingLeft: 10 + depth * 14 }}
        onClick={() => (isDir ? setOpen((o) => !o) : onSelect(node))}
      >
        <span className="tree-glyph" data-dir={isDir} data-open={open}>
          {isDir ? (open ? "▾" : "▸") : glyphFor(node)}
        </span>
        <span className="tree-name">{node.name}</span>
      </button>
      {isDir && open && node.children && node.children.length > 0 && (
        <Branch
          nodes={node.children}
          depth={depth + 1}
          selected={selected}
          onSelect={onSelect}
        />
      )}
    </li>
  );
}

// Pick a sensible file to show first: prefer a README, then a main source file.
function pickDefault(nodes: TreeNode[]): TreeNode | null {
  const flat: TreeNode[] = [];
  const walk = (ns: TreeNode[]) =>
    ns.forEach((n) => {
      if (n.type === "file") flat.push(n);
      if (n.children) walk(n.children);
    });
  walk(nodes);
  const byName = (re: RegExp) =>
    flat.find((f) => re.test(f.name.toLowerCase()));
  return (
    byName(/^readme\.md$/) ||
    byName(/^config\.json$/) ||
    flat.find((f) => /\.(rs|py|tsx?|js)$/.test(f.name)) ||
    flat[0] ||
    null
  );
}

export default function Explorer({ name }: { name: string }) {
  const [tree, setTree] = useState<TreeNode[] | null>(null);
  const [treeErr, setTreeErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [file, setFile] = useState<FileContent | null>(null);
  const [fileErr, setFileErr] = useState<string | null>(null);
  const [loadingFile, setLoadingFile] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .tree(name)
      .then((res) => {
        if (!alive) return;
        setTree(res.tree);
        const def = pickDefault(res.tree);
        if (def) setSelected(def.path);
      })
      .catch((e) => alive && setTreeErr(String(e)));
    return () => {
      alive = false;
    };
  }, [name]);

  useEffect(() => {
    if (!selected) return;
    let alive = true;
    setLoadingFile(true);
    setFileErr(null);
    api
      .file(name, selected)
      .then((f) => alive && setFile(f))
      .catch((e) => {
        if (!alive) return;
        setFile(null);
        setFileErr(String(e));
      })
      .finally(() => alive && setLoadingFile(false));
    return () => {
      alive = false;
    };
  }, [name, selected]);

  const lineNumbers = useMemo(() => {
    if (!file) return "";
    return Array.from({ length: file.lines || 1 }, (_, i) => i + 1).join("\n");
  }, [file]);

  return (
    <div className="explorer">
      <aside className="explorer-tree">
        {treeErr && <div className="explorer-msg">could not load tree</div>}
        {!tree && !treeErr && <div className="skel" style={{ height: 320 }} />}
        {tree && (
          <Branch
            nodes={tree}
            depth={0}
            selected={selected}
            onSelect={(n) => setSelected(n.path)}
          />
        )}
      </aside>

      <div className="explorer-view">
        <div className="explorer-bar">
          <span className="explorer-path">{selected || "select a file"}</span>
          {file && (
            <span className="explorer-meta">
              {langFor(file.path)} · {file.lines} lines ·{" "}
              {(file.bytes / 1024).toFixed(1)} KB
            </span>
          )}
        </div>
        <div className="explorer-code">
          {loadingFile && <div className="explorer-msg">loading…</div>}
          {fileErr && !loadingFile && (
            <div className="explorer-msg">{fileErr}</div>
          )}
          {file && !loadingFile && !fileErr && (
            <div className="code-scroll">
              <pre className="gutter" aria-hidden>
                {lineNumbers}
              </pre>
              <pre className="code-body">{file.content}</pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
