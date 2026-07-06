"use client";

import type { SavedIndex, IndexTrader } from "./types";

const KEY = "copytensor:indexes:v1";
const ACTIVE_KEY = "copytensor:active_index:v1";

function read(): SavedIndex[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}

function write(list: SavedIndex[]) {
  if (typeof window === "undefined") return;
  localStorage.setItem(KEY, JSON.stringify(list));
  try {
    window.dispatchEvent(new CustomEvent("copytensor:indexes-changed"));
  } catch {}
}

export function loadIndexes(): SavedIndex[] {
  return read().sort((a, b) => b.updatedAt - a.updatedAt);
}

export function getIndex(id: string): SavedIndex | undefined {
  return read().find((x) => x.id === id);
}

export function saveIndex(idx: Omit<SavedIndex, "id" | "createdAt" | "updatedAt"> & { id?: string }): SavedIndex {
  const now = Date.now();
  const list = read();
  if (idx.id) {
    const i = list.findIndex((x) => x.id === idx.id);
    if (i >= 0) {
      const merged: SavedIndex = { ...list[i], ...idx, updatedAt: now } as SavedIndex;
      list[i] = merged;
      write(list);
      return merged;
    }
  }
  const created: SavedIndex = {
    ...idx,
    id: idx.id || `idx_${now.toString(36)}_${Math.random().toString(36).slice(2, 6)}`,
    createdAt: now,
    updatedAt: now,
  } as SavedIndex;
  list.push(created);
  write(list);
  return created;
}

export function updateIndex(id: string, patch: Partial<SavedIndex>): SavedIndex | undefined {
  const list = read();
  const i = list.findIndex((x) => x.id === id);
  if (i < 0) return undefined;
  list[i] = { ...list[i], ...patch, updatedAt: Date.now() };
  write(list);
  return list[i];
}

export function deleteIndex(id: string) {
  const list = read().filter((x) => x.id !== id);
  write(list);
  if (getActiveIndexId() === id) setActiveIndexId(null);
}

export function getActiveIndexId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACTIVE_KEY) || null;
}

export function setActiveIndexId(id: string | null) {
  if (typeof window === "undefined") return;
  if (id) localStorage.setItem(ACTIVE_KEY, id);
  else localStorage.removeItem(ACTIVE_KEY);
  try {
    window.dispatchEvent(new CustomEvent("copytensor:active-index-changed"));
  } catch {}
}

// Normalize weights so they sum to 1 across enabled traders.
export function normalizedWeights(traders: IndexTrader[]): Map<string, number> {
  const enabled = traders.filter((t) => t.enabled !== false && t.weight > 0);
  const total = enabled.reduce((s, t) => s + t.weight, 0);
  const out = new Map<string, number>();
  if (total <= 0) return out;
  for (const t of enabled) out.set(t.ss58, t.weight / total);
  return out;
}
