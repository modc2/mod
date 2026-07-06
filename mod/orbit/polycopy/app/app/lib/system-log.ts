"use client";

import { useState, useEffect, useCallback, useRef } from "react";

export interface LogEntry {
  timestamp: string;
  level: "INFO" | "TRADE" | "WARN" | "ERROR" | "SYS";
  message: string;
}

function ts(): string {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// Global log store so multiple consumers share state
let globalLogs: LogEntry[] = [
  { timestamp: ts(), level: "SYS", message: "POLYCOPY ENGINE INITIALIZED" },
  { timestamp: ts(), level: "INFO", message: "MULTI-CHAIN SCANNER READY" },
];
let listeners: Set<() => void> = new Set();

function notify() {
  listeners.forEach((fn) => fn());
}

export function addLog(level: LogEntry["level"], message: string) {
  globalLogs = [...globalLogs.slice(-99), { timestamp: ts(), level, message }];
  notify();
}

export function useSystemLog() {
  const [logs, setLogs] = useState<LogEntry[]>(globalLogs);
  const listenerRef = useRef<() => void>();

  useEffect(() => {
    const listener = () => setLogs([...globalLogs]);
    listenerRef.current = listener;
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);

  const log = useCallback((level: LogEntry["level"], message: string) => {
    addLog(level, message);
  }, []);

  return { logs, log };
}
