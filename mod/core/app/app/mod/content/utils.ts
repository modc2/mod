'use client';

import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { CopyButton } from '@/app/block/ui/CopyButton';
import {
  ChevronDownIcon, ChevronRightIcon, DocumentIcon, FolderIcon, FolderOpenIcon,
  MagnifyingGlassIcon, CodeBracketIcon, DocumentTextIcon, PhotoIcon, FilmIcon,
  MusicalNoteIcon, ArchiveBoxIcon, DocumentChartBarIcon, ClipboardDocumentIcon
} from '@heroicons/react/24/outline';
import { ModuleType } from '@/app/types';
import client from '@/app/block/client';
import { useUserContext } from '@/app/context';



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

export const ui = {
  panel: '#0b0b0b',
  panelAlt: '#121212',
  panelAlt2: '#151515',
  border: '#2a2a2a',
  text: '#e7e7e7',
  textDim: '#a8a8a8',
  green: '#22c55e',
  yellow: '#facc15',
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

export const languageColors: Record<string, string> = {
  typescript: 'text-blue-400', javascript: 'text-yellow-400', python: 'text-green-400',
  json: 'text-orange-400', css: 'text-pink-400', html: 'text-red-400',
  markdown: 'text-gray-400', text: 'text-gray-300',
};

export const formatFileSize = (bytes: number): string =>
  bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1048576).toFixed(1)} MB`;

export const buildFileTree = (files: Record<string, string>): FileNode[] => {
  const root: FileNode = { name: '', path: '', type: 'folder', children: [] };

  Object.entries(files).forEach(([path, content]) => {
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
          content: isFile ? content : undefined,
          language: isFile ? getLanguageFromPath(part) : undefined,
          cid: isFile ? content : undefined,
          lineCount: isFile ? content.split('\n').length : undefined,
          size: isFile ? formatFileSize(content.length) : undefined,
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
  node, level, onSelect, expandedFolders, toggleFolder, selectedPath, onCopy, searchTerm,
}: {
  node: FileNode; level: number; onSelect: (n: FileNode) => void;
  expandedFolders: Set<string>; toggleFolder: (p: string) => void; selectedPath?: string;
  onCopy: (n: FileNode) => void; searchTerm?: string;
}) {
  const isExpanded = expandedFolders.has(node.path);
  const isSelected = selectedPath === node.path;
  const FileIcon = node.type === 'file' ? getFileIcon(node.name) : (isExpanded ? FolderOpenIcon : FolderIcon);

  const matchesSearch = searchTerm
    ? node.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      node.path.toLowerCase().includes(searchTerm.toLowerCase())
    : true;

  const handleClick = () => (node.type === 'folder' ? toggleFolder(node.path) : onSelect(node));
  if (!matchesSearch && node.type === 'file') return null;

  return (
    <div>
      <div
        className={`group micro-row flex cursor-pointer items-center rounded-md px-2 py-1.5 text-xs transition-all duration-150
        ${isSelected ? 'bg-emerald-900/25 text-emerald-300' : 'text-gray-400'}
        ${matchesSearch && searchTerm ? 'ring-1 ring-yellow-400/30' : ''}`}
        style={{ paddingLeft: `${level * 12 + 8}px` }}
        onClick={handleClick}
        title={node.path}
      >
        {node.type === 'folder' ? (
          isExpanded ? <ChevronDownIcon className="mr-1 h-3 w-3" /> : <ChevronRightIcon className="mr-1 h-3 w-3" />
        ) : null}
        <FileIcon className={`mr-2 h-4 w-4 flex-shrink-0 ${node.type === 'folder' ? 'text-yellow-500' : 'text-gray-400'}`} />
        <span className="flex-1 truncate font-mono">
          {searchTerm ? highlightSearchTerm(node.name, searchTerm) : node.name}
        </span>
        {node.type === 'file' ? (
          <>
            <span className="ml-2 text-xs opacity-60">{node.size}</span>
            <button
              onClick={(e) => { e.stopPropagation(); onCopy(node); }}
              className="ml-2 opacity-0 transition-opacity group-hover:opacity-100"
              title="Copy file content"
            >
              <ClipboardDocumentIcon className="h-3 w-3 text-emerald-400 hover:text-emerald-300" />
            </button>
          </>
        ) : (
          <button
            onClick={(e) => { e.stopPropagation(); onCopy(node); }}
            className="ml-2 opacity-0 transition-opacity group-hover:opacity-100"
            title="Copy folder contents"
          >
            <ClipboardDocumentIcon className="h-3 w-3 text-emerald-400 hover:text-emerald-300" />
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
            />
          ))}
        </div>
      )}
    </div>
  );
}

