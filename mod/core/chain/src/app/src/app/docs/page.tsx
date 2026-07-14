"use client";

// ── Docs: tutorial (guided steps), guide, API reference, agent usage ─────────

import { useState, useEffect } from 'react'
import dynamic from 'next/dynamic'
import {
  AcademicCapIcon,
  BookOpenIcon,
  CommandLineIcon,
  CpuChipIcon,
} from '@heroicons/react/24/outline'
import { CheckCircleIcon as CheckSolid } from '@heroicons/react/24/solid'
import { Shell } from '../components/Shell'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8800'

// Shared modc2 localStorage origin — quota-safe access only.
const LS_TUTORIAL = 'chain.tutorial.done'
const safeGet = (k: string) => { try { return localStorage.getItem(k) } catch { return null } }
const safeSet = (k: string, v: string) => { try { localStorage.setItem(k, v) } catch {} }

// ── Content ──────────────────────────────────────────────────────────────────

const TUTORIAL_STEPS = [
  {
    title: 'Connect a wallet',
    body: <>Hit <b>Connect</b> in the top-right corner. Two options: <b>MetaMask</b> (your injected
      browser extension — the app switches it to the right chain automatically when you send) or a
      <b> browser wallet</b> — a local keypair created and stored in this browser, no extension needed.
      You can also import an existing private key.</>,
    links: [],
  },
  {
    title: 'Get gas on Base Sepolia',
    body: <>Writes cost gas. The default network is <b>Base Sepolia</b> (testnet), where ETH is free
      from a faucet — paste your connected address and claim. The browser wallet shows its address in
      the wallet modal.</>,
    links: [
      { label: 'Coinbase faucet', href: 'https://portal.cdp.coinbase.com/products/faucet' },
      { label: 'Alchemy faucet', href: 'https://www.alchemy.com/faucets/base-sepolia' },
    ],
  },
  {
    title: 'Pick a network',
    body: <>The network pill in the nav switches everything at once: <b>Base Sepolia</b> for testing,
      <b> Ganache</b> for a local chain on :8545, <b>Base Mainnet</b> for production. Each network has
      its own set of deployed contract addresses.</>,
    links: [],
  },
  {
    title: 'Explore the deployed fleet',
    body: <>The <b>Hub</b> shows every module — its live status, contracts and addresses. The
      <b> Contracts</b> page goes deeper: ABIs, verified source, and the IPFS CIDs each artifact is
      pinned under.</>,
    links: [{ label: 'Open Contracts', href: '/contracts' }],
  },
  {
    title: 'Interact with any contract',
    body: <>The <b>Interact</b> page lists every function of every deployed contract. Reads run
      instantly against the RPC; writes are signed by your connected wallet and sent on-chain, with a
      link to the transaction on the explorer.</>,
    links: [{ label: 'Open Interact', href: '/interact' }],
  },
  {
    title: 'Use the protocol',
    body: <>The <b>Protocol</b> page wires the modules into flows: mint the native token with
      USDC/USDT, register a mod in the on-chain Registry, stake into BlocTime for time-weighted
      ownership, and claim your share of the rewards pool.</>,
    links: [{ label: 'Open Protocol', href: '/protocol' }],
  },
  {
    title: 'Operate and administer',
    body: <><b>Control</b> verifies contracts and runs deploy scripts. <b>Owner</b> is the owner
      console — owner-only setters executed directly, or exported as a Safe multisig batch when
      ownership has been transferred to a Safe.</>,
    links: [{ label: 'Open Control', href: '/control' }, { label: 'Open Owner', href: '/admin' }],
  },
]

const MODULES: [string, string][] = [
  ['token', 'ERC-20s — USDC / USDT test stables + NativeToken'],
  ['oracle', 'Price feeds (manual, Chainlink, Pyth)'],
  ['registry', 'On-chain name → data registry for mods'],
  ['perms', 'Permission / role management'],
  ['tokengate', 'Whitelist of accepted payment tokens'],
  ['bloctime', 'Time-weighted staking — stake × lock = weight'],
  ['treasury', 'Protocol fee accrual and claims'],
  ['market', 'Mint / credit market for the native token'],
  ['debit', 'Signed debit pulls between client and provider'],
  ['safe', 'Gnosis-style multisig for ownership'],
  ['bridge', 'Cross-chain bridge (WIP)'],
]

const API_GROUPS: { name: string; rows: [string, string, string][] }[] = [
  {
    name: 'Status & deploy',
    rows: [
      ['GET', '/health', 'Liveness'],
      ['GET', '/info', 'Module names, ports, endpoint list'],
      ['GET', '/mods', 'Per-module API/app ports + alive status'],
      ['GET', '/status', 'Deployments per network with contract addresses'],
      ['POST', '/deploy', '{network, mods?} — deploy all or selected modules'],
      ['GET', '/block · /timestamp', 'Current block / chain time'],
    ],
  },
  {
    name: 'Contracts',
    rows: [
      ['POST', '/contracts', '{network} — addresses for a network'],
      ['GET', '/contracts/mods', 'Module → contract mapping'],
      ['GET', '/contracts/source', 'Verified source for a contract'],
      ['GET', '/contracts/abis', 'ABIs for all deployed contracts'],
      ['GET', '/cid/{cid}', 'Fetch a pinned IPFS artifact'],
      ['POST', '/call', 'Read any contract function'],
    ],
  },
  {
    name: 'Protocol',
    rows: [
      ['GET', '/wallet · /balances · /tokens', 'Server wallet, balances, token list'],
      ['POST', '/mint · /credit · /transfer', 'Market mint / credit / ERC-20 transfer'],
      ['POST', '/stake · /unstake', 'BlocTime staking'],
      ['GET', '/stakes · /bloctime/owner', 'Stake positions, holder check'],
      ['POST', '/register · /registry/register', 'Register a mod on-chain'],
      ['GET', '/registry/mods · /registry/all', 'Read the registry'],
      ['GET/POST', '/pool/*', 'Rewards pool: claimable, claim, epochs, snapshot'],
      ['GET/POST', '/yield/*', 'Yield vault strategies: deposit, withdraw, harvest'],
    ],
  },
  {
    name: 'Admin & control',
    rows: [
      ['GET', '/admin/owners', 'Current owner of each contract'],
      ['POST', '/admin/encode · /admin/send', 'Encode / execute owner-only calls'],
      ['POST', '/admin/transfer-all', 'Transfer all ownership (e.g. to a Safe)'],
      ['GET', '/control/status', 'Compile / deploy toolchain status'],
      ['POST', '/control/verify · /control/deploy-script', 'Verify on explorer, run deploy scripts'],
    ],
  },
]

const PY_EXAMPLE = `import mod as m
chain = m.mod('chain')            # orchestrator, defaults to testnet

chain.deploy(network='testnet')   # deploy all groups (parallel within, sequential across)
chain.deploy_mod('market')        # deploy one module (deps auto-resolved)

chain.balance(token='usdc')       # read balances
chain.stake(10**18, 1000)         # stake into BlocTime for 1000 blocks
chain.register('my-mod', 'Qm...') # register name → data on-chain
chain.pool_claimable()            # rewards pool share`

const CLI_EXAMPLE = `m chain/deploy network=testnet
m chain/balances address=0x...
m chain/stake amount=1000000000000000000 lock_blocks=1000
m chain/register name=my-mod data=Qm...`

const CURL_EXAMPLE = `curl ${API_URL}/status
curl ${API_URL}/mods
curl -X POST ${API_URL}/deploy -H 'Content-Type: application/json' \\
  -d '{"network": "testnet", "mods": ["market"]}'`

// ── Small building blocks ────────────────────────────────────────────────────

function SectionCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="glass overflow-hidden">
      <div className="px-5 py-3 border-b hairline">
        <p className="text-[13px] font-semibold text-white/85 tracking-tight">{title}</p>
      </div>
      <div className="p-5">{children}</div>
    </div>
  )
}

function Code({ children }: { children: string }) {
  return (
    <pre className="rounded-xl bg-black/40 border hairline p-4 text-[11.5px] leading-relaxed text-cyan-100/80 font-mono overflow-x-auto whitespace-pre">
      {children}
    </pre>
  )
}

// ── Tabs ─────────────────────────────────────────────────────────────────────

const TABS = [
  { key: 'tutorial', label: 'Tutorial', icon: AcademicCapIcon },
  { key: 'guide', label: 'Guide', icon: BookOpenIcon },
  { key: 'api', label: 'API', icon: CommandLineIcon },
  { key: 'agents', label: 'Agents', icon: CpuChipIcon },
] as const

type TabKey = typeof TABS[number]['key']

function DocsInner() {
  const [tab, setTab] = useState<TabKey>('tutorial')
  const [done, setDone] = useState<Set<number>>(new Set())

  useEffect(() => {
    const saved = safeGet(LS_TUTORIAL)
    if (saved) {
      try { setDone(new Set(JSON.parse(saved))) } catch {}
    }
  }, [])

  const toggleDone = (i: number) => {
    setDone(prev => {
      const next = new Set(prev)
      next.has(i) ? next.delete(i) : next.add(i)
      safeSet(LS_TUTORIAL, JSON.stringify([...next]))
      return next
    })
  }

  return (
    <Shell active="docs" footer="chain — docs">
      {/* Header + tab bar */}
      <div className="fade-up pt-1 flex flex-col sm:flex-row sm:items-end justify-between gap-3" style={{ '--i': 0 } as any}>
        <div>
          <h1 className="text-[24px] md:text-[30px] font-semibold tracking-[-0.03em] leading-tight text-white">
            Docs &{' '}
            <span className="bg-gradient-to-r from-cyan-300 via-sky-300 to-violet-300 bg-clip-text text-transparent">
              tutorial
            </span>
          </h1>
          <p className="mt-1 text-[13px] text-white/40">
            Everything you need to go from zero to on-chain — for humans and agents.
          </p>
        </div>
        <div className="flex gap-1 p-1 rounded-full bg-white/[0.03] border border-white/[0.07] self-start sm:self-auto">
          {TABS.map(t => (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={`navlink flex items-center gap-1.5 ${tab === t.key ? 'navlink-active' : ''}`}>
              <t.icon className="w-3.5 h-3.5" /> {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* ═══ Tutorial ═══ */}
      {tab === 'tutorial' && (
        <div className="space-y-3">
          <div className="flex items-center justify-between fade-up" style={{ '--i': 1 } as any}>
            <p className="text-[12px] text-white/35">
              Click a step's circle to mark it done — progress is saved in this browser.
            </p>
            <span className="chip chip-live tabular-nums">{done.size}/{TUTORIAL_STEPS.length} done</span>
          </div>
          {TUTORIAL_STEPS.map((s, i) => {
            const isDone = done.has(i)
            return (
              <div key={s.title} className={`glass glass-hover p-4 flex gap-4 fade-up ${isDone ? 'opacity-60' : ''}`}
                style={{ '--i': i + 2 } as any}>
                <button onClick={() => toggleDone(i)} className="shrink-0 mt-0.5" title={isDone ? 'Mark not done' : 'Mark done'}>
                  {isDone
                    ? <CheckSolid className="w-6 h-6 text-emerald-400" />
                    : <span className="w-6 h-6 rounded-full border border-white/20 flex items-center justify-center text-[11px] font-semibold text-white/40 hover:border-cyan-400/50 hover:text-cyan-300 transition-colors">{i + 1}</span>
                  }
                </button>
                <div className="min-w-0">
                  <p className={`text-[14px] font-semibold tracking-tight ${isDone ? 'text-white/50 line-through' : 'text-white/90'}`}>
                    {s.title}
                  </p>
                  <p className="mt-1 text-[12.5px] leading-relaxed text-white/45 [&_b]:text-white/75 [&_b]:font-semibold">
                    {s.body}
                  </p>
                  {s.links.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {s.links.map(l => (
                        <a key={l.href} href={l.href}
                          target={l.href.startsWith('http') ? '_blank' : undefined}
                          rel="noopener noreferrer"
                          className="btn btn-ghost !py-1 !px-3 !text-[11px]">
                          {l.label} {l.href.startsWith('http') ? '↗' : '→'}
                        </a>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* ═══ Guide ═══ */}
      {tab === 'guide' && (
        <div className="space-y-4 fade-up" style={{ '--i': 1 } as any}>
          <SectionCard title="What is chain?">
            <p className="text-[12.5px] leading-relaxed text-white/50">
              <b className="text-white/80">chain</b> is the hub for a fleet of modular smart contracts on Base.
              Each module owns one concern — tokens, oracles, staking, treasury, market — and they compose
              into one protocol. This console deploys the fleet, inspects it, and drives every function
              from the browser, signed by your own wallet.
            </p>
          </SectionCard>

          <SectionCard title="The modules">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8">
              {MODULES.map(([name, desc]) => (
                <div key={name} className="row flex items-baseline gap-3 py-1.5 px-1">
                  <span className="font-mono text-[12px] text-cyan-300/80 w-20 shrink-0">{name}</span>
                  <span className="text-[12px] text-white/45">{desc}</span>
                </div>
              ))}
            </div>
          </SectionCard>

          <SectionCard title="Deploy order">
            <p className="text-[12px] text-white/45 mb-3">
              Modules deploy as dependency groups — parallel within a group, sequential across groups:
            </p>
            <div className="flex flex-wrap items-center gap-2 text-[11.5px] font-mono">
              {[['token', 'oracle', 'registry', 'perms'], ['tokengate', 'bloctime'], ['treasury'], ['market'], ['debit']].map((group, gi) => (
                <div key={gi} className="flex items-center gap-2">
                  {gi > 0 && <span className="text-white/25">→</span>}
                  <div className="flex gap-1.5 rounded-xl border hairline bg-white/[0.02] px-2.5 py-1.5">
                    {group.map(g => <span key={g} className="text-white/60">{g}</span>)}
                  </div>
                </div>
              ))}
            </div>
          </SectionCard>

          <SectionCard title="Networks">
            <div className="space-y-1">
              {[
                ['Base Sepolia', '84532', 'https://sepolia.base.org', 'default — free faucet ETH'],
                ['Base Mainnet', '8453', 'https://mainnet.base.org', 'production'],
                ['Ganache', '1337', 'http://localhost:8545', 'local dev chain'],
              ].map(([name, id, rpc, note]) => (
                <div key={id} className="row flex flex-wrap items-baseline gap-x-4 gap-y-0.5 py-1.5 px-1 text-[12px]">
                  <span className="text-white/75 font-medium w-28">{name}</span>
                  <span className="font-mono text-white/35 tabular-nums">chain {id}</span>
                  <span className="font-mono text-white/30">{rpc}</span>
                  <span className="text-white/40">{note}</span>
                </div>
              ))}
            </div>
          </SectionCard>

          <SectionCard title="Wallets & signing">
            <ul className="space-y-2 text-[12.5px] text-white/50 leading-relaxed list-disc pl-4 [&_b]:text-white/75">
              <li><b>MetaMask</b> — connect once; the app asks it to switch (or add) the right chain
                whenever you send a transaction, so you never sign on the wrong network.</li>
              <li><b>Browser wallet</b> — an ethers keypair generated locally and kept in this browser's
                storage. Export the private key to back it up; fund the address with gas before writing.</li>
              <li>Reads never need a wallet — they go straight to the network RPC.</li>
            </ul>
          </SectionCard>
        </div>
      )}

      {/* ═══ API ═══ */}
      {tab === 'api' && (
        <div className="space-y-4 fade-up" style={{ '--i': 1 } as any}>
          <SectionCard title="Base URL">
            <Code>{`${API_URL}          # FastAPI — interactive docs at ${API_URL}/docs`}</Code>
          </SectionCard>
          {API_GROUPS.map(g => (
            <SectionCard key={g.name} title={g.name}>
              <div className="space-y-0.5">
                {g.rows.map(([method, path, desc]) => (
                  <div key={path} className="row flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-1.5 px-1">
                    <span className={`font-mono text-[10.5px] font-semibold w-16 shrink-0 ${method.includes('POST') ? 'text-amber-300/80' : 'text-emerald-300/80'}`}>{method}</span>
                    <span className="font-mono text-[12px] text-white/70">{path}</span>
                    <span className="text-[11.5px] text-white/35">{desc}</span>
                  </div>
                ))}
              </div>
            </SectionCard>
          ))}
          <SectionCard title="Examples">
            <Code>{CURL_EXAMPLE}</Code>
          </SectionCard>
        </div>
      )}

      {/* ═══ Agents ═══ */}
      {tab === 'agents' && (
        <div className="space-y-4 fade-up" style={{ '--i': 1 } as any}>
          <SectionCard title="For coding agents">
            <p className="text-[12.5px] leading-relaxed text-white/50">
              The module ships a <span className="font-mono text-cyan-300/80">skill.md</span> at the repo
              root (<span className="font-mono text-white/60">core/chain/skill.md</span>) describing every
              capability, function and endpoint — point your agent at it. The Python orchestrator in{' '}
              <span className="font-mono text-white/60">src/mod.py</span> is the full-power surface; the
              HTTP API mirrors the common operations.
            </p>
          </SectionCard>
          <SectionCard title="Python (mod protocol)">
            <Code>{PY_EXAMPLE}</Code>
          </SectionCard>
          <SectionCard title="CLI">
            <Code>{CLI_EXAMPLE}</Code>
          </SectionCard>
          <SectionCard title="Key paths">
            <div className="space-y-0.5">
              {[
                ['core/chain/skill.md', 'agent-facing capability sheet'],
                ['core/chain/config.json', 'per-network deployments — addresses + pinned ABI/src CIDs'],
                ['core/chain/src/mod.py', 'Python orchestrator (deploy, stake, register, pool, yield…)'],
                ['core/chain/src/api/api.py', 'FastAPI server (port 8800)'],
                ['core/chain/src/contracts/', 'Solidity sources, one directory per module, each with a README'],
              ].map(([path, desc]) => (
                <div key={path} className="row flex flex-wrap items-baseline gap-x-3 py-1.5 px-1">
                  <span className="font-mono text-[11.5px] text-white/70">{path}</span>
                  <span className="text-[11.5px] text-white/35">{desc}</span>
                </div>
              ))}
            </div>
          </SectionCard>
        </div>
      )}
    </Shell>
  )
}

export default dynamic(() => Promise.resolve(DocsInner), { ssr: false })
