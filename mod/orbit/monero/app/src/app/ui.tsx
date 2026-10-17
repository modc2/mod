'use client'

import { ReactNode, useState } from 'react'

export const C = {
  bg: '#0e0d10',
  panel: '#181519',
  panel2: '#201c21',
  line: '#332c34',
  text: '#ece8ee',
  dim: '#968d99',
  orange: '#ff6600',      // Monero brand
  green: '#4ade80',
  red: '#f87171',
  blue: '#60a5fa',
}

export function Panel({ title, right, children, style }: {
  title?: string, right?: ReactNode, children: ReactNode, style?: any
}) {
  return (
    <div style={{
      background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10,
      padding: 18, marginBottom: 16, ...style,
    }}>
      {title && (
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          marginBottom: 14, gap: 10,
        }}>
          <h2 style={{
            margin: 0, fontSize: 12, letterSpacing: 1.4, textTransform: 'uppercase',
            color: C.dim, fontWeight: 600,
          }}>{title}</h2>
          {right}
        </div>
      )}
      {children}
    </div>
  )
}

export function Field({ label, value, mono, color }: {
  label: string, value: ReactNode, mono?: boolean, color?: string
}) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 11, color: C.dim, marginBottom: 3 }}>{label}</div>
      <div style={{
        fontSize: 13, color: color || C.text, wordBreak: 'break-all',
        fontFamily: mono ? 'ui-monospace, SFMono-Regular, Menlo, monospace' : undefined,
      }}>{value}</div>
    </div>
  )
}

export function Stat({ label, value, sub }: { label: string, value: ReactNode, sub?: ReactNode }) {
  return (
    <div style={{
      background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 8,
      padding: '12px 14px', minWidth: 0,
    }}>
      <div style={{ fontSize: 10, color: C.dim, textTransform: 'uppercase', letterSpacing: 1 }}>{label}</div>
      <div style={{ fontSize: 19, fontWeight: 600, marginTop: 4, wordBreak: 'break-all' }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: C.dim, marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

export function Input({ label, hint, ...props }: any) {
  return (
    <label style={{ display: 'block', marginBottom: 12 }}>
      <div style={{ fontSize: 11, color: C.dim, marginBottom: 4 }}>{label}</div>
      <input {...props} style={{
        width: '100%', boxSizing: 'border-box', padding: '9px 11px',
        background: C.bg, border: `1px solid ${C.line}`, borderRadius: 6,
        color: C.text, fontSize: 13, outline: 'none',
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        ...(props.style || {}),
      }} />
      {hint && <div style={{ fontSize: 10, color: C.dim, marginTop: 3 }}>{hint}</div>}
    </label>
  )
}

export function Select({ label, children, ...props }: any) {
  return (
    <label style={{ display: 'block', marginBottom: 12 }}>
      <div style={{ fontSize: 11, color: C.dim, marginBottom: 4 }}>{label}</div>
      <select {...props} style={{
        width: '100%', padding: '9px 11px', background: C.bg, color: C.text,
        border: `1px solid ${C.line}`, borderRadius: 6, fontSize: 13,
        ...(props.style || {}),
      }}>{children}</select>
    </label>
  )
}

export function Button({ children, variant, ...props }: any) {
  const danger = variant === 'danger'
  const ghost = variant === 'ghost'
  return (
    <button {...props} style={{
      padding: '9px 16px', borderRadius: 6, fontSize: 13, fontWeight: 600,
      cursor: props.disabled ? 'not-allowed' : 'pointer',
      opacity: props.disabled ? 0.5 : 1,
      background: ghost ? 'transparent' : danger ? C.red : C.orange,
      color: ghost ? C.dim : '#150d08',
      border: ghost ? `1px solid ${C.line}` : 'none',
      ...(props.style || {}),
    }}>{children}</button>
  )
}

export function Note({ kind = 'info', children }: {
  kind?: 'info' | 'warn' | 'error' | 'ok', children: ReactNode
}) {
  const colors = { info: C.blue, warn: C.orange, error: C.red, ok: C.green }
  const c = colors[kind]
  return (
    <div style={{
      background: `${c}14`, border: `1px solid ${c}55`, color: c,
      borderRadius: 6, padding: '10px 12px', fontSize: 12.5, marginBottom: 12,
      lineHeight: 1.5, wordBreak: 'break-word',
    }}>{children}</div>
  )
}

export function Code({ children }: { children: ReactNode }) {
  return (
    <pre style={{
      background: C.bg, border: `1px solid ${C.line}`, borderRadius: 6,
      padding: 12, fontSize: 11, color: C.dim, overflowX: 'auto',
      whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0, maxHeight: 260,
    }}>{children}</pre>
  )
}

export function Copy({ text }: { text: string }) {
  const [done, setDone] = useState(false)
  return (
    <button
      onClick={() => {
        navigator.clipboard?.writeText(text)
        setDone(true); setTimeout(() => setDone(false), 1200)
      }}
      style={{
        background: 'transparent', border: `1px solid ${C.line}`, color: done ? C.green : C.dim,
        borderRadius: 4, fontSize: 10, padding: '2px 7px', cursor: 'pointer', marginLeft: 8,
      }}>{done ? 'copied' : 'copy'}</button>
  )
}

export function Spinner({ label }: { label?: string }) {
  return <span style={{ color: C.dim, fontSize: 12 }}>{label || 'loading'}…</span>
}

export function Bar({ value, max }: { value: number, max: number }) {
  const pct = max ? Math.min(100, Math.round((value / max) * 100)) : 0
  return (
    <div style={{ background: C.bg, borderRadius: 4, height: 6, overflow: 'hidden' }}>
      <div style={{ width: `${pct}%`, height: '100%', background: C.orange, transition: 'width .3s' }} />
    </div>
  )
}
