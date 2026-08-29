/* /code — read the contract.

   Four source files, tens of thousands of characters. On the old page they
   were fetched on every visit whether or not anyone scrolled that far;
   here they load for the people who came to verify. */

"use client";

import dynamic from 'next/dynamic'
import { useState } from 'react'
import { toast } from 'react-toastify'
import { NextUp, PageHead, Shell } from '../../components/chrome'
import { Reveal } from '../../components/motion'
import { SourceFile, useResource } from '../../lib/api'
import { LAUNCH } from '../../lib/whitepaper'

const LANG_LABEL: Record<string, string> = { solidity: 'Solidity', python: 'Python', typescript: 'TypeScript' }

function CodeViewer({ files }: { files: SourceFile[] }) {
  const [active, setActive] = useState(0)
  if (!files.length) {
    return <div className="glass rounded-3xl py-20 text-center text-white/55 text-sm uppercase tracking-widest">Loading source…</div>
  }
  const f = files[Math.min(active, files.length - 1)]
  const lines = f.content.replace(/\n$/, '').split('\n')
  return (
    <div className="rounded-2xl overflow-hidden border border-white/10 bg-paper shadow-2xl shadow-black/50">
      {/* tab strip */}
      <div className="flex items-stretch overflow-x-auto border-b border-white/[0.07] bg-white/[0.02]">
        {files.map((file, i) => (
          <button key={file.name} onClick={() => setActive(i)}
            className={`px-4 py-3 text-xs font-mono whitespace-nowrap border-r border-white/[0.05] transition-colors ${i === active ? 'bg-white/[0.06] text-coral' : 'text-white/60 hover:text-white/80'}`}>
            {file.name.split('/').pop()}
          </button>
        ))}
      </div>
      {/* chrome bar */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-white/[0.05] bg-white/[0.015]">
        <div className="flex items-center gap-2">
          <span className="w-2.5 h-2.5 rounded-full bg-peach" /><span className="w-2.5 h-2.5 rounded-full bg-sun" /><span className="w-2.5 h-2.5 rounded-full bg-emerald-300" />
          <span className="ml-3 text-[11px] font-mono text-white/65">{f.name}</span>
          <span className="text-[10px] uppercase tracking-widest text-coral border border-coral/25 rounded px-1.5 py-0.5">{LANG_LABEL[f.language] || f.language}</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-[10px] text-white/55 hidden sm:inline">{f.lines} lines · {(f.bytes / 1024).toFixed(1)} KB</span>
          <button onClick={() => { navigator.clipboard.writeText(f.content); toast.success(`${f.name} copied`) }}
            className="text-[10px] font-bold uppercase tracking-widest text-white/60 hover:text-coral transition-colors">Copy</button>
        </div>
      </div>
      <p className="px-4 py-2.5 text-xs text-white/60 border-b border-white/[0.05] bg-white/[0.01]">{f.description}</p>
      {/* code body */}
      <div className="overflow-auto max-h-[42rem] text-[12.5px] leading-[1.55] font-mono">
        <table className="w-full border-collapse">
          <tbody>
            {lines.map((ln, i) => (
              <tr key={i} className="hover:bg-white/[0.025]">
                <td className="select-none text-right pr-4 pl-4 text-white/45 w-12 align-top tabular-nums sticky left-0 bg-paper">{i + 1}</td>
                <td className="pr-6 text-white/85 whitespace-pre align-top">{ln || ' '}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function CodeInner() {
  const { data: sources } = useResource<SourceFile[]>('source', [])

  return (
    <Shell>
      <PageHead kicker="Open Source · No Black Box" title="Read the contract.">
        The whole thing is right here — the Solidity that holds your shares, the logic that splits
        the rent, the API that serves it, and the MCP server that lets an agent drive it.
        No trust required. Verify.
      </PageHead>

      <div className="max-w-6xl mx-auto px-5 md:px-8">
        <Reveal>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-8">
            {[
              { k: 'Contract', v: 'OpenHouse.sol' },
              { k: 'Network', v: `${LAUNCH.chain} · ${LAUNCH.chainId}` },
              { k: 'Stage', v: `${LAUNCH.stage} · launch TBA` },
              { k: 'License', v: 'MIT' },
            ].map((s, i) => (
              <div key={i} className="glass rounded-xl p-4">
                <div className="text-[10px] uppercase tracking-widest text-white/58 font-bold mb-1">{s.k}</div>
                <div className="font-mono text-sm text-coral truncate">{s.v}</div>
              </div>
            ))}
          </div>
        </Reveal>
        <Reveal delay={60}>
          <CodeViewer files={Array.isArray(sources) ? sources : []} />
        </Reveal>

        <Reveal delay={100}>
          <div className="glass rounded-3xl p-7 md:p-8 mt-8">
            <div className="text-coral text-[11px] font-bold uppercase tracking-[0.25em] mb-3">Drive it yourself</div>
            <p className="text-white/72 text-sm leading-relaxed max-w-2xl">
              Everything on this site is a REST call, and every REST call is also an MCP tool.
              Point an agent at <code className="text-coral/90 bg-coral/5 border border-coral/15 rounded px-1.5 py-0.5 text-[12px] font-mono">POST /openhouse/api/mcp</code> and
              it can read the live deal, quote a payment split, check a renter's equity or pull
              the cap table — the same numbers this page shows, no auth needed.
            </p>
            <div className="grid sm:grid-cols-2 gap-3 mt-5">
              {[
                { k: 'Transport', v: 'JSON-RPC 2.0 · Streamable HTTP' },
                { k: 'Tools', v: '17 — 13 read, 4 write' },
              ].map(s => (
                <div key={s.k} className="rounded-xl bg-white/[0.03] border border-white/[0.06] p-4">
                  <div className="text-[10px] uppercase tracking-widest text-white/58 font-bold mb-1">{s.k}</div>
                  <div className="font-mono text-[13px] text-white/80">{s.v}</div>
                </div>
              ))}
            </div>
          </div>
        </Reveal>
      </div>

      <NextUp here="/code" />
    </Shell>
  )
}

export default dynamic(() => Promise.resolve(CodeInner), { ssr: false })
