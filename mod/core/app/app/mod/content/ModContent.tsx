"use client";

import { useState, useEffect, useMemo, useRef } from 'react';
import { CopyButton } from '@/ui/CopyButton';
import {
  ChevronDownIcon,
  ChevronRightIcon,
  DocumentIcon,
  FolderIcon,
  FolderOpenIcon,
  MagnifyingGlassIcon,
  ClipboardDocumentIcon,
  CodeBracketIcon,
  DocumentChartBarIcon,
  DocumentTextIcon,
  PhotoIcon,
  FilmIcon,
  MusicalNoteIcon,
  ArchiveBoxIcon,
  HashtagIcon
} from '@heroicons/react/24/outline';
import { ModuleType } from '@/types';
import { userContext } from '@/context';
import { useTheme } from '@/context/ThemeContext';


export interface ModContentProps {
  mod: {
    content: Record<string, string> | undefined | string;
  };
}

export type FileNode = {
  name: string;
  path: string;
  type: 'file' | 'folder';
  children?: FileNode[];
  content?: string;
  language?: string;
  hash?: string;
  lineCount?: number;
  size?: string;
  cid?: string;
};

export const getFileIcon = (filename: string) => {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  const iconMap: Record<string, any> = {
    ts: CodeBracketIcon, tsx: CodeBracketIcon, js: CodeBracketIcon, jsx: CodeBracketIcon, py: CodeBracketIcon,
    json: DocumentChartBarIcon, css: DocumentTextIcon, html: DocumentTextIcon, md: DocumentTextIcon, txt: DocumentTextIcon,
    jpg: PhotoIcon, jpeg: PhotoIcon, png: PhotoIcon, gif: PhotoIcon, svg: PhotoIcon,
    mp4: FilmIcon, avi: FilmIcon, mov: FilmIcon, mp3: MusicalNoteIcon, wav: MusicalNoteIcon, zip: ArchiveBoxIcon, tar: ArchiveBoxIcon, gz: ArchiveBoxIcon,
  };
  return iconMap[ext] || DocumentIcon;
};

export const getLanguageFromPath = (path: string): string => {
  const ext = path.split('.').pop()?.toLowerCase() || '';
  const langMap: Record<string, string> = {
    ts: 'typescript', tsx: 'typescript', js: 'javascript', jsx: 'javascript',
    py: 'python', json: 'json', css: 'css', html: 'html', md: 'markdown',
  };
  return langMap[ext] || 'text';
};

export const formatFileSize = (bytes: number): string =>
  bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1048576).toFixed(1)} MB`;

export const buildFileTree = (files: Record<string, string>): FileNode[] => {
  const root: FileNode = { name: '', path: '', type: 'folder', children: [] };

  Object.entries(files).forEach(([path, cid]) => {
    const parts = path.split('/').filter(Boolean);
    let current = root;
    parts.forEach((part, idx) => {
      const isFile = idx === parts.length - 1;
      const currentPath = parts.slice(0, idx + 1).join('/');
      let child = current.children!.find((c) => c.name === part);
      if (!child) {
        child = {
          name: part,
          path: currentPath,
          type: isFile ? 'file' : 'folder',
          children: isFile ? undefined : [],
          content: undefined,
          language: isFile ? getLanguageFromPath(part) : undefined,
          cid: isFile ? cid : undefined,
          lineCount: undefined,
          size: undefined,
        };
        current.children!.push(child);
      }
      if (!isFile) current = child;
    });
  });

  const sortNodes = (nodes?: FileNode[]) => {
    if (!nodes) return;
    nodes.sort((a, b) => (a.type === b.type ? a.name.localeCompare(b.name) : a.type === 'folder' ? -1 : 1));
    nodes.forEach((n) => sortNodes(n.children));
  };
  sortNodes(root.children);
  return root.children || [];
};

export const highlightSearchTerm = (text: string, term: string) => {
  if (!term) return text;
  const safe = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const parts = text.split(new RegExp(`(${safe})`, 'gi'));
  return (
    <>
      {parts.map((p, i) =>
        i % 2 === 1 ? (
          <span key={i} className="bg-yellow-400/30 text-yellow-300 font-bold">{p}</span>
        ) : (
          <span key={i}>{p}</span>
        )
      )}
    </>
  );
};


export function FileTreeItem({
  node, level, onSelect, expandedFolders, toggleFolder, selectedPath, onCopy, searchTerm, fileContents,
}: {
  node: FileNode; level: number; onSelect: (n: FileNode) => void;
  expandedFolders: Set<string>; toggleFolder: (p: string) => void; selectedPath?: string;
  onCopy: (n: FileNode) => void; searchTerm?: string;
  fileContents?: Record<string, string>;
}) {
  const isExpanded = expandedFolders.has(node.path);
  const isSelected = selectedPath === node.path;
  const FileIcon = node.type === 'file' ? getFileIcon(node.name) : (isExpanded ? FolderOpenIcon : FolderIcon);
  const [showCid, setShowCid] = useState(false);

  const matchesSearch = searchTerm
    ? node.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      node.path.toLowerCase().includes(searchTerm.toLowerCase())
    : true;

  const handleClick = () => (node.type === 'folder' ? toggleFolder(node.path) : onSelect(node));
  if (!matchesSearch && node.type === 'file') return null;

  return (
    <div>
      <div
        className="group flex cursor-pointer items-center px-2 py-2 text-[11px] transition-all duration-150 mx-0.5"
        style={{
          paddingLeft: `${level * 12 + 8}px`,
          backgroundColor: isSelected ? 'var(--bg-input)' : 'transparent',
          border: isSelected ? '1px solid var(--border-color)' : '1px solid transparent',
          color: isSelected ? 'var(--text-primary)' : 'var(--text-secondary)',
        }}
        onClick={handleClick}
        title={node.path}
      >
        {node.type === 'folder' ? (
          isExpanded
            ? <ChevronDownIcon className="mr-1.5 h-3 w-3" style={{ color: 'var(--text-tertiary)' }} />
            : <ChevronRightIcon className="mr-1.5 h-3 w-3" style={{ color: 'var(--text-tertiary)' }} />
        ) : null}
        <FileIcon className="mr-2 h-3.5 w-3.5 flex-shrink-0" style={{ color: 'var(--text-tertiary)' }} />
        <span className="flex-1 truncate font-mono font-medium">
          {searchTerm ? highlightSearchTerm(node.name, searchTerm) : node.name}
        </span>
        {node.type === 'file' ? (
          <>
            <span className="ml-2 text-[9px] font-mono" style={{ color: 'var(--text-tertiary)' }}>
              {fileContents?.[node.path] ? formatFileSize(fileContents[node.path].length) : (node.size || '...')}
            </span>
            <div
              className="relative ml-1.5"
              onMouseEnter={() => setShowCid(true)}
              onMouseLeave={() => setShowCid(false)}
            >
              <HashtagIcon className="h-3 w-3 transition-colors" style={{ color: 'var(--text-tertiary)' }} />
              {showCid && node.cid && (
                <div
                  className="absolute right-0 top-5 z-50 px-2.5 py-1.5 text-[9px] font-mono whitespace-nowrap"
                  style={{
                    border: '1px solid var(--border-color)',
                    backgroundColor: 'var(--bg-surface)',
                    color: 'var(--text-primary)',
                    boxShadow: 'var(--card-shadow)',
                  }}
                >
                  {node.cid.length > 46 ? `${node.cid.slice(0, 46)}...` : node.cid}
                </div>
              )}
            </div>
            <button
              onClick={(e) => { e.stopPropagation(); onCopy(node); }}
              className="ml-1.5 opacity-0 transition-all group-hover:opacity-100 p-1"
              title="Copy file content"
            >
              <ClipboardDocumentIcon className="h-3 w-3" style={{ color: 'var(--text-tertiary)' }} />
            </button>
          </>
        ) : (
          <button
            onClick={(e) => { e.stopPropagation(); onCopy(node); }}
            className="ml-1.5 opacity-0 transition-all group-hover:opacity-100 p-1"
            title="Copy folder contents"
          >
            <ClipboardDocumentIcon className="h-3 w-3" style={{ color: 'var(--text-tertiary)' }} />
          </button>
        )}
      </div>
      {node.type === 'folder' && isExpanded && node.children && (
        <div>
          {node.children.map((child) => (
            <FileTreeItem
              key={child.path}
              node={child}
              level={level + 1}
              onSelect={onSelect}
              expandedFolders={expandedFolders}
              toggleFolder={toggleFolder}
              selectedPath={selectedPath}
              onCopy={onCopy}
              searchTerm={searchTerm}
              fileContents={fileContents}
            />
          ))}
        </div>
      )}
    </div>
  );
}


export default function ModContent({ mod }: { mod: ModuleType }) {
  const files: Record<string, string> = typeof mod.content === 'object' && mod.content !== null ? mod.content as Record<string, string> : {};
  const { client } = userContext();
  const { effectiveTheme } = useTheme();
  const isLight = effectiveTheme === 'light';
  const [searchTerm, setSearchTerm] = useState('');
  const [collapsedFiles, setCollapsedFiles] = useState<Set<string>>(new Set());
  const [selectedVersion, setSelectedVersion] = useState<number>(0);
  const [selectedFile, setSelectedFile] = useState<string | null>(mod.content && typeof mod.content === 'object' ? Object.keys(mod.content)[0] : null);
  const [fileTree, setFileTree] = useState<FileNode[]>([]);
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set());
  const [searchResults, setSearchResults] = useState<{ path: string; lineNumbers: number[] }[]>([]);
  const codeRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const [fileContents, setFileContents] = useState<Record<string, string>>({});


  const handleFileSelect = (node: FileNode) => {
    if (node.type !== 'file') return;
    setSelectedFile(node.path);
    const el = codeRefs.current[node.path];
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  // Build file tree when files change
  useEffect(() => {
    const tree = buildFileTree(files);
    setFileTree(tree);
    setExpandedFolders(new Set());

    // Auto-select first file
    const findFirstFile = (nodes: FileNode[]): FileNode | null => {
      for (const n of nodes) {
        if (n.type === 'file') return n;
        if (n.children) {
          const found = findFirstFile(n.children);
          if (found) return found;
        }
      }
      return null;
    };

    const firstFile = findFirstFile(tree);
    if (firstFile) {
      setSelectedFile(firstFile.path);
    }
  }, [files]);

  // Fetch content for selected file when it changes
  useEffect(() => {
    if (!selectedFile || !client || fileContents[selectedFile]) return;
    const cid = files[selectedFile];
    if (!cid) return;
    let cancelled = false;
    client.call('get', { cid }).then((res: any) => {
      if (cancelled) return;
      const text = typeof res === 'string' ? res : JSON.stringify(res, null, 2);
      setFileContents(prev => ({ ...prev, [selectedFile]: text }));
    }).catch((err: any) => {
      if (cancelled) return;
      console.error('Failed to fetch content for', selectedFile, err);
      setFileContents(prev => ({ ...prev, [selectedFile]: `// Error loading file: ${err?.message || 'unknown'}` }));
    });
    return () => { cancelled = true; };
  }, [selectedFile, client, files]);

  useEffect(() => {
    if (!searchTerm) return;
    const folders = new Set<string>();
    const check = (n: FileNode, parent = '') => {
      const cp = parent ? `${parent}/${n.name}` : n.name;
      if (n.name.toLowerCase().includes(searchTerm.toLowerCase()) || n.path.toLowerCase().includes(searchTerm.toLowerCase())) {
        const parts = cp.split('/').filter(Boolean);
        for (let i = 0; i < parts.length - 1; i++) folders.add(parts.slice(0, i + 1).join('/'));
      }
      n.children?.forEach((c) => check(c, cp));
    };
    fileTree.forEach((n) => check(n));
    setExpandedFolders((prev) => { const newSet = new Set(prev); folders.forEach(folder => newSet.add(folder)); return newSet; });
  }, [searchTerm, fileTree]);

  useEffect(() => {
    if (!searchTerm) { setSearchResults([]); return; }
    const results: { path: string; lineNumbers: number[] }[] = [];
    Object.entries(fileContents).forEach(([path, content]) => {
      if (typeof content !== 'string') return;
      const lines = content.split('\n');
      const matchLines: number[] = [];
      const q = searchTerm.toLowerCase();
      lines.forEach((line, idx) => { if (line.toLowerCase().includes(q)) matchLines.push(idx + 1); });
      if (matchLines.length) results.push({ path, lineNumbers: matchLines });
    });
    setSearchResults(results);
    setCollapsedFiles(new Set());
  }, [searchTerm, fileContents]);

  const fileSections = useMemo(() =>
    Object.entries(files)
      .map(([path, cid]) => {
        if (typeof cid !== 'string') return null;
        const fetched = fileContents[path];
        return {
          path,
          name: path.split('/').pop() || path,
          content: fetched || '',
          language: getLanguageFromPath(path),
          cid,
          lineCount: fetched ? fetched.split('\n').length : 0,
          size: fetched ? formatFileSize(fetched.length) : '...',
          loaded: !!fetched,
        };
      })
      .filter((s): s is NonNullable<typeof s> => s !== null),
    [files, fileContents]
  );

  const filteredSections = useMemo(() => {
    if (!searchTerm) return fileSections;
    const q = searchTerm.toLowerCase();
    return fileSections.filter((s) => s && (s.path.toLowerCase().includes(q) || (fileContents[s.path] || '').toLowerCase().includes(q)));
  }, [fileSections, searchTerm, fileContents]);

  const filteredTree = useMemo(() => {
    if (!searchTerm) return fileTree;
    const matchesSearch = (n: FileNode): boolean => {
      if (n.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
          n.path.toLowerCase().includes(searchTerm.toLowerCase())) return true;
      if (n.type === 'folder' && n.children) {
        return n.children.some(matchesSearch);
      }
      return false;
    };
    return fileTree.filter(matchesSearch);
  }, [fileTree, searchTerm]);

  const stats = useMemo(() => {
    const totalLines = filteredSections.reduce((sum, s) => sum + s.lineCount, 0);
    const totalSize = filteredSections.reduce((sum, s) => sum + s.content.length, 0);
    return { fileCount: filteredSections.length, totalLines, totalSize: formatFileSize(totalSize) };
  }, [filteredSections]);

  const toggleFile = (path: string) => setCollapsedFiles((prev) => { const n = new Set(prev); n.has(path) ? n.delete(path) : n.add(path); return n; });
  const toggleFolder = (path: string) => setExpandedFolders((prev) => { const n = new Set(prev); n.has(path) ? n.delete(path) : n.add(path); return n; });

  const copyFileContent = (node: FileNode) => {
    if (node.type === 'file') {
      const content = fileContents[node.path];
      if (content) navigator.clipboard.writeText(content);
      return;
    }
    if (node.type === 'folder') {
      const buffer: string[] = [];
      const walk = (n: FileNode) => {
        if (n.type === 'file' && fileContents[n.path]) buffer.push(`// ${n.path}\n${fileContents[n.path]}`);
        n.children?.forEach(walk);
      };
      walk(node);
      navigator.clipboard.writeText(buffer.join('\n\n'));
    }
  };

  const renderLineNumbers = (content: string, startLine: number, path: string) => {
    const lines = content.split('\n');
    const matches = searchResults.find((r) => r.path === path)?.lineNumbers || [];
    return (
      <div className="select-none pr-3 font-mono text-[12px]" style={{ color: 'var(--text-tertiary)' }}>
        {lines.map((_, i) => {
          const ln = startLine + i;
          const isMatch = matches.includes(ln);
          return (
            <div key={i} className={`text-right ${isMatch ? 'bg-yellow-400/20 text-yellow-400' : ''}`}>
              {ln}
            </div>
          );
        })}
      </div>
    );
  };

  const renderCode = (content: string, language: string, path: string) => {
    const lines = content.split('\n');
    const matches = searchResults.find((r) => r.path === path)?.lineNumbers || [];
    return (
      <pre className="mod-scroll flex-1 overflow-x-auto">
        <code className="font-mono text-[12px] leading-relaxed" style={{ color: 'var(--text-primary)' }}>
          {lines.map((line, i) => {
            const ln = i + 1;
            const isMatch = matches.includes(ln);
            return (
              <div key={i} className={isMatch ? 'bg-yellow-400/20' : ''}>
                {searchTerm ? highlightSearchTerm(line, searchTerm) : line}
              </div>
            );
          })}
        </code>
      </pre>
    );
  };

  const versions = [
    { version: '1.0.0', timestamp: Date.now(), label: 'latest' },
    { version: '0.9.0', timestamp: Date.now() - 86400000, label: '' },
    { version: '0.8.0', timestamp: Date.now() - 172800000, label: '' },
  ];

  return (
    <div className="overflow-hidden font-mono" style={{ fontFamily: 'JetBrains Mono, monospace' }}>
      {/* Header bar */}
      <div className="px-4 py-2.5 flex items-center gap-4" style={{
        backgroundColor: 'var(--bg-surface)',
        border: '1px solid var(--border-color)',
      }}>
        {/* Search */}
        <div className="relative flex-1 max-w-sm">
          <MagnifyingGlassIcon className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2" style={{ color: 'var(--text-tertiary)' }} />
          <input
            type="text"
            placeholder="Search files..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full pl-9 pr-3 py-2 text-[11px] font-mono outline-none transition-all"
            style={{
              backgroundColor: 'var(--bg-input)',
              border: '1px solid var(--border-color)',
              color: 'var(--text-primary)',
            }}
          />
        </div>

        {/* Version selector */}
        <select
          value={selectedVersion}
          onChange={(e) => setSelectedVersion(Number(e.target.value))}
          className="px-2.5 py-2 text-[11px] font-mono outline-none transition-all flex-shrink-0"
          style={{
            backgroundColor: 'var(--bg-input)',
            border: '1px solid var(--border-color)',
            color: 'var(--text-primary)',
          }}
        >
          {versions.map((v, idx) => (
            <option key={idx} value={idx}>
              v{v.version} {v.label && `(${v.label})`}
            </option>
          ))}
        </select>

        {/* Stats */}
        <div className="flex items-center gap-2 text-[10px] font-mono flex-shrink-0" style={{ color: 'var(--text-tertiary)' }}>
          <span>{stats.fileCount} files</span>
          <span>·</span>
          <span>{stats.totalLines} lines</span>
          <span>·</span>
          <span>{stats.totalSize}</span>
          {searchTerm && searchResults.length > 0 && (
            <>
              <span>·</span>
              <span className="text-yellow-400">
                {searchResults.reduce((s, r) => s + r.lineNumbers.length, 0)} matches
              </span>
            </>
          )}
        </div>
      </div>

      <div className="flex gap-0 mt-0">
        {/* File tree sidebar */}
        <div className="mod-scroll-y w-64 max-h-[600px] overflow-y-auto py-3" style={{
          backgroundColor: 'var(--bg-surface)',
          borderRight: '1px solid var(--border-color)',
          borderBottom: '1px solid var(--border-color)',
          borderLeft: '1px solid var(--border-color)',
        }}>
          <div className="mb-2 px-3">
            <div className="flex items-center justify-between">
              <h3 className="text-[10px] font-bold tracking-wider uppercase font-mono" style={{ color: 'var(--text-tertiary)' }}>
                Files
              </h3>
              <div className="flex gap-1">
                <button
                  onClick={() => {
                    const all = new Set<string>();
                    const collect = (nodes: FileNode[]) => nodes.forEach((n) => { if (n.type === 'folder') { all.add(n.path); n.children && collect(n.children); }});
                    collect(fileTree);
                    setExpandedFolders(all);
                  }}
                  className="p-1 transition-all"
                  title="Expand all"
                >
                  <ChevronDownIcon className="h-3 w-3" style={{ color: 'var(--text-tertiary)' }} />
                </button>
                <button
                  onClick={() => setExpandedFolders(new Set())}
                  className="p-1 transition-all"
                  title="Collapse all"
                >
                  <ChevronRightIcon className="h-3 w-3" style={{ color: 'var(--text-tertiary)' }} />
                </button>
              </div>
            </div>
          </div>
          <div className="space-y-0">
            {filteredTree.map((node) => (
              <FileTreeItem
                key={node.path}
                node={node}
                level={0}
                onSelect={handleFileSelect}
                expandedFolders={expandedFolders}
                toggleFolder={toggleFolder}
                selectedPath={selectedFile || undefined}
                onCopy={copyFileContent}
                searchTerm={searchTerm}
                fileContents={fileContents}
              />
            ))}
          </div>
        </div>

        {/* Code panel */}
        <div className="mod-scroll-y max-h-[600px] flex-1 overflow-y-auto" style={{
          border: '1px solid var(--border-color)',
          borderLeft: 'none',
        }}>
          {filteredSections.map((section) => {
            const isCollapsed = collapsedFiles.has(section.path);
            const isSelected = selectedFile === section.path;
            const FileIcon = getFileIcon(section.name);
            const matches = searchResults.find((r) => r.path === section.path)?.lineNumbers.length || 0;
            if (selectedFile && !isSelected) return null;
            if (!client) return null;
            const content = fileContents[section.path] || '';
            const isLoading = !fileContents[section.path];

            return (
              <div
                key={section.path}
                ref={(el) => { codeRefs.current[section.path] = el; }}
                className="overflow-hidden transition-all duration-150"
                style={{
                  borderBottom: '1px solid var(--border-color)',
                }}
              >
                <div
                  className="flex cursor-pointer items-center justify-between px-4 py-2.5 transition-all duration-150"
                  style={{ backgroundColor: 'var(--bg-surface)' }}
                  onClick={() => toggleFile(section.path)}
                >
                  <div className="flex items-center gap-2.5 flex-1 min-w-0 overflow-hidden">
                    {isCollapsed
                      ? <ChevronRightIcon className="h-3.5 w-3.5 flex-shrink-0" style={{ color: 'var(--text-tertiary)' }} />
                      : <ChevronDownIcon className="h-3.5 w-3.5 flex-shrink-0" style={{ color: 'var(--text-tertiary)' }} />
                    }
                    <FileIcon className="h-4 w-4 flex-shrink-0" style={{ color: 'var(--text-tertiary)' }} />
                    <span className="font-mono text-[12px] font-medium truncate flex-shrink" style={{ color: 'var(--text-primary)' }}>
                      {searchTerm && section.path.toLowerCase().includes(searchTerm.toLowerCase())
                        ? highlightSearchTerm(section.path, searchTerm)
                        : section.path}
                    </span>
                    <div className="flex items-center gap-2 flex-shrink-0 whitespace-nowrap">
                      <span className="text-[9px] font-mono px-1.5 py-0.5" style={{
                        color: 'var(--text-tertiary)',
                        backgroundColor: 'var(--bg-input)',
                        border: '1px solid var(--border-color)',
                      }}>
                        {section.language}
                      </span>
                      <span className="text-[9px] font-mono select-all px-1.5 py-0.5"
                        style={{
                          color: 'var(--text-tertiary)',
                          backgroundColor: 'var(--bg-input)',
                          border: '1px solid var(--border-color)',
                        }}
                        onClick={(e) => e.stopPropagation()}
                        title={section.cid}>
                        {section.cid.length > 20 ? `${section.cid.slice(0, 10)}...${section.cid.slice(-8)}` : section.cid}
                      </span>
                      {!!matches && <span className="text-[9px] text-yellow-400 font-mono px-1.5 py-0.5 bg-yellow-400/10">{matches} matches</span>}
                    </div>
                  </div>
                  <div className="flex items-center gap-3 text-[10px] font-mono flex-shrink-0 ml-4 whitespace-nowrap" style={{ color: 'var(--text-tertiary)' }}>
                    <span>{section.size}</span>
                    <span>{section.lineCount} ln</span>
                    <CopyButton content={content} />
                  </div>
                </div>

                {!isCollapsed && (
                  <div className="flex overflow-hidden" style={{ backgroundColor: 'var(--bg-primary)' }}>
                    {isLoading ? (
                      <div className="flex-1 flex items-center justify-center py-12 text-[11px] font-mono" style={{ color: 'var(--text-tertiary)' }}>
                        Loading...
                      </div>
                    ) : (
                      <>
                        {renderLineNumbers(content, 1, section.path)}
                        <div className="mod-scroll flex-1 overflow-x-auto p-4">
                          {renderCode(content, section.language, section.path)}
                        </div>
                      </>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between px-4 py-2.5" style={{
        backgroundColor: 'var(--bg-surface)',
        border: '1px solid var(--border-color)',
        borderTop: 'none',
      }}>
        <div className="text-[10px] font-mono" style={{ color: 'var(--text-tertiary)' }}>
          {searchTerm && searchResults.length > 0 && (
            <span>Found "{searchTerm}" in {searchResults.length} files</span>
          )}
        </div>
        <button
          onClick={() => {
            const all = filteredSections.filter(s => fileContents[s.path]).map((s) => `// ${s.path}\n${fileContents[s.path]}`).join('\n\n');
            navigator.clipboard.writeText(all);
          }}
          className="flex items-center gap-2 text-[10px] font-mono transition-all px-3 py-1.5"
          style={{
            color: 'var(--text-secondary)',
            border: '1px solid var(--border-color)',
          }}
        >
          <DocumentIcon className="h-3.5 w-3.5" />
          <span className="uppercase tracking-wider">Copy All</span>
        </button>
      </div>

      <style jsx>{`
        .mod-scroll {
          scrollbar-width: thin;
          scrollbar-color: transparent transparent;
        }
        .mod-scroll:hover {
          scrollbar-color: var(--scrollbar-thumb) transparent;
        }
        .mod-scroll::-webkit-scrollbar {
          height: 6px;
          background: transparent;
        }
        .mod-scroll::-webkit-scrollbar-thumb {
          background: transparent;
          border-radius: 3px;
        }
        .mod-scroll:hover::-webkit-scrollbar-thumb {
          background: var(--scrollbar-thumb);
        }
        .mod-scroll::-webkit-scrollbar-track {
          background: transparent;
        }
        .mod-scroll-y {
          scrollbar-width: thin;
          scrollbar-color: var(--scrollbar-thumb) transparent;
        }
        .mod-scroll-y::-webkit-scrollbar {
          width: 6px;
          background: transparent;
        }
        .mod-scroll-y::-webkit-scrollbar-thumb {
          background: var(--scrollbar-thumb);
          border-radius: 3px;
        }
        .mod-scroll-y::-webkit-scrollbar-track {
          background: transparent;
        }
      `}</style>
    </div>
  );
}
