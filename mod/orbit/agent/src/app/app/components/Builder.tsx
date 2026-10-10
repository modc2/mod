'use client'

// Builder — the HUB's agents shelf. Three modes, one tab, and the line
// between them is what each one is for:
//
//   BROWSE — AgentsPanel.tsx: the registry. An agent is MADE here: its
//            prompt, its model, its toolbox, its memory module, its mod.py.
//   FLOW   — Flow.tsx: the canvas. Agents are CONNECTED here — whole agents,
//            picked off the registry, wired to each other through gates,
//            routers, joins and loops. It never makes one.
//   TASK   — TaskBuilder.tsx: what agents are scored on, drafted for you if
//            you like, and filed straight into the arena pool.
//
// The canvas used to be the third way to build one agent: an AGENT node with
// a Prompt, a Model, a Toolbox and a Memory wired into its ports. That was a
// form drawn as a graph — four fields, four wires, one row of boxes that
// could only ever be wired one way — and it competed with the two real forms
// next to it (the rail's editor and BROWSE) for the same job. A graph earns
// its shape when the thing being drawn is genuinely a graph, which the
// *relationships between* agents are and the inside of one is not. So the
// canvas kept the drag, the ports and the wires, and changed what a node is:
// one node is now one agent.

import { useState } from 'react'
import TaskBuilder from './TaskBuilder'
import AgentsPanel from './AgentsPanel'
import Flow from './Flow'

type Mode = 'browse' | 'flow' | 'task'

type Props = {
  onUseAgent: (name: string, memoryIds: string[]) => void
  onAgentsChanged: () => void
  /** an agent to start a new flow with, when FLOW was opened from one */
  initialAgent?: string | null
  /** a saved flow to open on the canvas */
  initialGraph?: string | null
  // which face of the shelf is up. Absent, it lands on BROWSE — the agents
  // themselves — because that is what "agents" means to someone arriving.
  initialMode?: Mode
  /** land with BROWSE's new-agent form already open */
  initialCreate?: boolean
  // the caller's signed token — the server records it as the owner of
  // whatever is saved, and checks it before an edit or a delete
  token?: string | null
  // the host owns everything nobody else does
  isHost?: boolean
  onSignIn?: () => void
  address?: string | null
  // TASK saves into the arena pool, so it offers a way over to the board
  onOpenArena?: () => void
  // BROWSE hands a prompt straight to an agent: select it in the console and
  // put the text in the box. Falls back to onUseAgent when absent.
  onRunAgent?: (name: string, prompt: string, memoryIds: string[]) => void
  // opening the rail's agent editor from the canvas — "this flow needs an
  // agent that doesn't exist yet" has one answer, and it isn't on the canvas
  onEditAgent?: (name?: string | null) => void
}

const MODES: { key: Mode; icon: string; label: string; hint: string }[] = [
  { key: 'browse', icon: '✦', label: 'Browse',
    hint: 'every agent in the registry — each one a folder of code under src/agents/. Agents are MADE here' },
  { key: 'flow', icon: '⋔', label: 'Flow',
    hint: 'connect agents: gates, routers, joins and loops between whole agents — a graph the server can run' },
  { key: 'task', icon: '◎', label: 'Task',
    hint: 'write what the agents are scored on — drafted for you, if you like' },
]

export default function Builder({
  onUseAgent, onAgentsChanged, initialAgent, initialGraph, initialMode, initialCreate,
  token, isHost, onSignIn, address, onOpenArena, onRunAgent, onEditAgent,
}: Props) {
  const [mode, setMode] = useState<Mode>(
    initialMode || (initialGraph || initialAgent ? 'flow' : 'browse'))
  // an agent carried over from BROWSE: "wire this one into something" starts
  // a flow with it already on the canvas
  const [seedAgent, setSeedAgent] = useState<string | null>(initialAgent || null)

  const modeBar = (
    <div className="shrink-0 border-b border-white/[0.06] bg-surface-1 px-3 py-2 flex items-center gap-3">
      <div className="flex items-center gap-1 p-0.5 rounded-lg border border-white/[0.07] bg-white/[0.02]">
        {MODES.map(md => (
          <button key={md.key} onClick={() => setMode(md.key)} title={md.hint}
            className={`flex items-center gap-1.5 px-3 py-1 rounded-md text-[11px] transition ${
              mode === md.key
                ? 'bg-emerald-500/15 text-emerald-200 border border-emerald-500/25'
                : 'border border-transparent text-gray-500 hover:text-gray-300 hover:bg-white/[0.04]'
            }`}>
            <span className="text-xs">{md.icon}</span>
            <span className="uppercase tracking-wider font-medium">{md.label}</span>
          </button>
        ))}
      </div>
      <span className="text-[10px] text-gray-600 truncate min-w-0">
        {MODES.find(md => md.key === mode)!.hint}
      </span>
    </div>
  )

  return (
    <div className="h-full flex flex-col min-h-0">
      {modeBar}
      <div className="flex-1 min-h-0">
        {mode === 'browse' && (
          <AgentsPanel
            token={token} address={address} isHost={isHost} onSignIn={onSignIn}
            initialCreate={initialCreate}
            onUseInFlow={name => { setSeedAgent(name); setMode('flow') }}
            onRun={(name, prompt, memoryIds) => onRunAgent
              ? onRunAgent(name, prompt, memoryIds)
              : onUseAgent(name, memoryIds)}
            onChanged={onAgentsChanged}
          />
        )}
        {mode === 'flow' && (
          <Flow
            key={`${seedAgent || 'new'}·${initialGraph || ''}`}
            token={token} isHost={isHost} address={address} onSignIn={onSignIn}
            initialAgent={seedAgent} initialGraph={initialGraph}
            onOpenAgents={n => (onEditAgent ? onEditAgent(n) : setMode('browse'))}
          />
        )}
        {mode === 'task' && (
          <TaskBuilder token={token} address={address} isHost={isHost}
            onSignIn={onSignIn} onOpenArena={onOpenArena} />
        )}
      </div>
    </div>
  )
}
