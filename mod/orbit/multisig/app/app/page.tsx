'use client'

import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { CHAINS, type ChainId } from './config'
import type { MultisigWallet, Transaction, Chain } from './types'

// ============================================================
// Wallet Helpers
// ============================================================

async function connectMetaMask(): Promise<string> {
  const eth = (window as any).ethereum
  if (!eth) throw new Error('MetaMask not detected')
  const accounts = await eth.request({ method: 'eth_requestAccounts' })
  // Switch to Base
  try {
    await eth.request({
      method: 'wallet_switchEthereumChain',
      params: [{ chainId: '0x2105' }], // 8453
    })
  } catch (e: any) {
    if (e.code === 4902) {
      await eth.request({
        method: 'wallet_addEthereumChain',
        params: [{
          chainId: '0x2105',
          chainName: 'Base',
          rpcUrls: ['https://mainnet.base.org'],
          nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 },
          blockExplorerUrls: ['https://basescan.org'],
        }],
      })
    }
  }
  return accounts[0]
}

async function connectSubWallet(): Promise<string> {
  const { web3Enable, web3Accounts } = await import('@polkadot/extension-dapp')
  const extensions = await web3Enable('Multisig')
  if (extensions.length === 0) throw new Error('SubWallet/Polkadot.js extension not found')
  const accounts = await web3Accounts()
  if (accounts.length === 0) throw new Error('No accounts found in wallet')
  return accounts[0].address
}

async function connectPhantom(): Promise<string> {
  const phantom = (window as any).phantom?.solana || (window as any).solana
  if (!phantom?.isPhantom) throw new Error('Phantom not detected')
  const resp = await phantom.connect()
  return resp.publicKey.toString()
}

async function signEvm(message: string): Promise<string> {
  const eth = (window as any).ethereum
  const accounts = await eth.request({ method: 'eth_accounts' })
  const sig = await eth.request({
    method: 'personal_sign',
    params: [message, accounts[0]],
  })
  return sig
}

async function signSolana(message: string): Promise<string> {
  const phantom = (window as any).phantom?.solana || (window as any).solana
  const encoded = new TextEncoder().encode(message)
  const { signature } = await phantom.signMessage(encoded, 'utf8')
  return Buffer.from(signature).toString('hex')
}

async function sendEvmTx(to: string, data: string, value: string): Promise<string> {
  const eth = (window as any).ethereum
  const accounts = await eth.request({ method: 'eth_accounts' })
  const tx = await eth.request({
    method: 'eth_sendTransaction',
    params: [{ from: accounts[0], to, data, value: '0x' + BigInt(value || '0').toString(16) }],
  })
  return tx
}

// ============================================================
// Components
// ============================================================

function ChainTabs({ chain, setChain }: { chain: ChainId; setChain: (c: ChainId) => void }) {
  const chains: ChainId[] = ['base', 'tao', 'solana']
  return (
    <div className="flex gap-1 bg-gray-800 rounded-lg p-1">
      {chains.map((c) => (
        <button
          key={c}
          onClick={() => setChain(c)}
          className={`btn-chain px-4 py-1.5 rounded-md ${
            chain === c
              ? 'bg-white text-gray-900 font-semibold'
              : 'text-gray-400 hover:text-gray-200'
          }`}
        >
          {CHAINS[c].name}
        </button>
      ))}
    </div>
  )
}

function WalletButton({
  chain, wallet, onConnect,
}: {
  chain: ChainId; wallet: string | null; onConnect: () => void
}) {
  if (wallet) {
    return (
      <div className="flex items-center gap-2 bg-gray-800 rounded-lg px-3 py-1.5 text-sm">
        <span className="w-2 h-2 rounded-full bg-green-400" />
        <span className="font-mono text-xs">
          {wallet.slice(0, 6)}...{wallet.slice(-4)}
        </span>
      </div>
    )
  }
  return (
    <button onClick={onConnect} className="btn-primary text-sm">
      Connect {CHAINS[chain].wallet}
    </button>
  )
}

function CreateForm({
  chain, wallet, onCreated,
}: {
  chain: ChainId; wallet: string; onCreated: () => void
}) {
  const [name, setName] = useState('')
  const [owners, setOwners] = useState('')
  const [threshold, setThreshold] = useState(2)
  const [address, setAddress] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    setLoading(true)
    setError('')
    const ownerList = owners
      .split('\n')
      .map((o) => o.trim())
      .filter(Boolean)

    if (ownerList.length < 2) {
      setError('Need at least 2 owners')
      setLoading(false)
      return
    }

    const data = {
      name: name || 'Multisig',
      owners: ownerList,
      threshold,
      address: address || undefined,
    }

    let res
    if (chain === 'base') res = await api.evmCreateMultisig(data)
    else if (chain === 'tao') res = await api.substrateCreateMultisig(data)
    else res = await api.solanaCreateMultisig(data)

    if (res.ok) {
      onCreated()
      setName('')
      setOwners('')
      setThreshold(2)
      setAddress('')
    } else {
      setError(res.error || 'Failed')
    }
    setLoading(false)
  }

  return (
    <div className="card space-y-4">
      <h3 className="text-lg font-semibold">Create / Import Multisig</h3>
      <div>
        <label className="label">Name</label>
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Team Wallet" />
      </div>
      <div>
        <label className="label">Owners (one per line)</label>
        <textarea
          className="input h-28 font-mono text-xs"
          value={owners}
          onChange={(e) => setOwners(e.target.value)}
          placeholder={chain === 'base' ? '0x1234...\n0x5678...' : chain === 'tao' ? '5GrwvaEF5zXb...\n5FHneW46xGXg...' : 'ABC123...\nDEF456...'}
        />
      </div>
      <div>
        <label className="label">Threshold</label>
        <input className="input w-20" type="number" min={1} value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} />
      </div>
      <div>
        <label className="label">
          {chain === 'base' ? 'Safe Address (leave empty to track off-chain)' : 'On-chain Address (optional)'}
        </label>
        <input className="input font-mono text-xs" value={address} onChange={(e) => setAddress(e.target.value)} placeholder="Existing multisig address..." />
      </div>
      {error && <p className="text-red-400 text-sm">{error}</p>}
      <button onClick={submit} disabled={loading} className="btn-primary w-full">
        {loading ? 'Creating...' : 'Create Multisig'}
      </button>
    </div>
  )
}

function MultisigCard({
  ms, onClick,
}: {
  ms: MultisigWallet; onClick: () => void
}) {
  const chainConf = CHAINS[ms.chain as ChainId]
  return (
    <button onClick={onClick} className="card w-full text-left hover:border-gray-600 transition-colors">
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-semibold">{ms.name}</h3>
        <span className="text-xs px-2 py-0.5 rounded-full" style={{ backgroundColor: chainConf.color + '33', color: chainConf.color }}>
          {chainConf.name}
        </span>
      </div>
      <div className="text-xs text-gray-400 space-y-1">
        <div>Threshold: {ms.threshold}/{ms.owners.length}</div>
        {ms.address && (
          <div className="font-mono truncate">{ms.address}</div>
        )}
      </div>
    </button>
  )
}

function ProposeForm({
  multisig, wallet, chain, onProposed,
}: {
  multisig: MultisigWallet; wallet: string; chain: ChainId; onProposed: () => void
}) {
  const [to, setTo] = useState('')
  const [value, setValue] = useState('')
  const [data, setData] = useState('')
  const [desc, setDesc] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    setLoading(true)
    setError('')

    const payload = {
      multisig_id: multisig.id,
      to,
      value: value || '0',
      data: data || '0x',
      description: desc,
      proposer: wallet,
    }

    let res
    if (chain === 'base') res = await api.evmPropose(payload)
    else if (chain === 'tao') res = await api.substratePropose(payload)
    else res = await api.solanaPropose(payload)

    if (res.ok) {
      onProposed()
      setTo('')
      setValue('')
      setData('')
      setDesc('')
    } else {
      setError(res.error || 'Failed')
    }
    setLoading(false)
  }

  const chainConf = CHAINS[chain]

  return (
    <div className="card space-y-3">
      <h3 className="text-lg font-semibold">Propose Transaction</h3>
      <div>
        <label className="label">To</label>
        <input className="input font-mono text-xs" value={to} onChange={(e) => setTo(e.target.value)} placeholder="Recipient address" />
      </div>
      <div>
        <label className="label">Value ({chainConf.symbol} in smallest unit)</label>
        <input className="input" value={value} onChange={(e) => setValue(e.target.value)} placeholder="0" />
      </div>
      <div>
        <label className="label">Data (hex, optional)</label>
        <input className="input font-mono text-xs" value={data} onChange={(e) => setData(e.target.value)} placeholder="0x" />
      </div>
      <div>
        <label className="label">Description</label>
        <input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Send funds to..." />
      </div>
      {error && <p className="text-red-400 text-sm">{error}</p>}
      <button onClick={submit} disabled={loading} className="btn-primary w-full">
        {loading ? 'Proposing...' : 'Propose'}
      </button>
    </div>
  )
}

function TxCard({
  tx, multisig, wallet, chain, onUpdate,
}: {
  tx: Transaction; multisig: MultisigWallet; wallet: string; chain: ChainId; onUpdate: () => void
}) {
  const [loading, setLoading] = useState('')
  const [error, setError] = useState('')

  const isOwner = multisig.owners.some(
    (o) => o.toLowerCase() === wallet.toLowerCase()
  )
  const hasApproved = tx.approvals.some(
    (a) => a.owner.toLowerCase() === wallet.toLowerCase()
  )
  const canApprove = isOwner && !hasApproved && tx.status === 'pending'
  const canExecute = tx.approvals.length >= multisig.threshold || tx.status === 'approved'

  const approve = async () => {
    setLoading('approve')
    setError('')
    try {
      let signature: string
      const message = tx.call_hash || tx.id

      if (chain === 'base') {
        signature = await signEvm(message)
      } else if (chain === 'solana') {
        signature = await signSolana(message)
      } else {
        // For Substrate, we record the approval — the actual extrinsic signing
        // happens when the user submits via SubWallet
        signature = `approved-by-${wallet}-${Date.now()}`
      }

      let res
      if (chain === 'base') res = await api.evmApprove(tx.id, wallet, signature)
      else if (chain === 'tao') res = await api.substrateApprove(tx.id, wallet, signature)
      else res = await api.solanaApprove(tx.id, wallet, signature)

      if (res.ok) onUpdate()
      else setError(res.error || 'Approval failed')
    } catch (e: any) {
      setError(e.message)
    }
    setLoading('')
  }

  const execute = async () => {
    setLoading('execute')
    setError('')
    try {
      if (chain === 'base') {
        // Get the execTransaction calldata
        const execRes = await api.evmExecute(tx.id, wallet)
        if (execRes.ok && execRes.data) {
          const txHash = await sendEvmTx(execRes.data.to, execRes.data.data, '0')
          // Record the on-chain hash
          await api.evmExecute(tx.id, wallet, txHash)
          onUpdate()
        } else {
          setError(execRes.error || 'Failed to get execution data')
        }
      } else if (chain === 'tao') {
        const execRes = await api.substrateExecute(tx.id, wallet)
        if (execRes.ok) {
          alert(`Submit as_multi via SubWallet:\n\nCall data: ${execRes.data?.call_data}\nOther signatories: ${JSON.stringify(execRes.data?.other_signatories)}`)
          onUpdate()
        } else {
          setError(execRes.error || 'Failed')
        }
      } else {
        const execRes = await api.solanaExecute(tx.id, wallet)
        if (execRes.ok) {
          alert(`Execute via Phantom:\n\nTo: ${execRes.data?.to}\nLamports: ${execRes.data?.lamports}`)
          onUpdate()
        } else {
          setError(execRes.error || 'Failed')
        }
      }
    } catch (e: any) {
      setError(e.message)
    }
    setLoading('')
  }

  const statusColor = {
    pending: 'text-yellow-400',
    approved: 'text-blue-400',
    executed: 'text-green-400',
    failed: 'text-red-400',
    cancelled: 'text-gray-500',
  }[tx.status]

  return (
    <div className="card space-y-2">
      <div className="flex items-center justify-between">
        <span className="font-medium text-sm">{tx.description || `Tx #${tx.nonce}`}</span>
        <span className={`text-xs font-semibold uppercase ${statusColor}`}>{tx.status}</span>
      </div>
      <div className="text-xs text-gray-400 space-y-1">
        <div>To: <span className="font-mono">{tx.to.slice(0, 10)}...{tx.to.slice(-6)}</span></div>
        <div>Value: {tx.value} {CHAINS[chain].symbol}</div>
        <div>Approvals: {tx.approvals.length}/{multisig.threshold}</div>
        {tx.tx_hash && (
          <div>Hash: <span className="font-mono text-green-400">{tx.tx_hash.slice(0, 14)}...</span></div>
        )}
      </div>

      {/* Approval list */}
      {tx.approvals.length > 0 && (
        <div className="border-t border-gray-800 pt-2 mt-2">
          <div className="text-xs text-gray-500 mb-1">Signers:</div>
          {tx.approvals.map((a, i) => (
            <div key={i} className="text-xs font-mono text-gray-400">
              {a.owner.slice(0, 10)}...{a.owner.slice(-4)}
            </div>
          ))}
        </div>
      )}

      {error && <p className="text-red-400 text-xs">{error}</p>}

      <div className="flex gap-2 pt-1">
        {canApprove && (
          <button onClick={approve} disabled={!!loading} className="btn bg-blue-600 hover:bg-blue-500 text-white text-sm flex-1">
            {loading === 'approve' ? 'Signing...' : 'Approve'}
          </button>
        )}
        {canExecute && tx.status !== 'executed' && (
          <button onClick={execute} disabled={!!loading} className="btn bg-green-600 hover:bg-green-500 text-white text-sm flex-1">
            {loading === 'execute' ? 'Executing...' : 'Execute'}
          </button>
        )}
      </div>
    </div>
  )
}

// ============================================================
// Main Page
// ============================================================

export default function Home() {
  const [chain, setChain] = useState<ChainId>('base')
  const [wallet, setWallet] = useState<string | null>(null)
  const [multisigs, setMultisigs] = useState<MultisigWallet[]>([])
  const [selected, setSelected] = useState<MultisigWallet | null>(null)
  const [transactions, setTransactions] = useState<Transaction[]>([])
  const [view, setView] = useState<'list' | 'create' | 'detail'>('list')
  const [error, setError] = useState('')

  const fetchMultisigs = useCallback(async () => {
    const res = await api.listMultisigs(chain as Chain)
    if (res.ok && res.data) setMultisigs(res.data)
  }, [chain])

  const fetchTransactions = useCallback(async () => {
    if (!selected) return
    const res = await api.listTxs(selected.id)
    if (res.ok && res.data) setTransactions(res.data)
  }, [selected])

  useEffect(() => {
    fetchMultisigs()
    setWallet(null)
    setSelected(null)
    setView('list')
  }, [chain, fetchMultisigs])

  useEffect(() => {
    if (selected) fetchTransactions()
  }, [selected, fetchTransactions])

  const connect = async () => {
    setError('')
    try {
      let addr: string
      if (chain === 'base') addr = await connectMetaMask()
      else if (chain === 'tao') addr = await connectSubWallet()
      else addr = await connectPhantom()
      setWallet(addr)
    } catch (e: any) {
      setError(e.message)
    }
  }

  const selectMultisig = (ms: MultisigWallet) => {
    setSelected(ms)
    setView('detail')
  }

  const deleteMultisig = async (id: string) => {
    if (!confirm('Delete this multisig?')) return
    await api.deleteMultisig(id)
    setSelected(null)
    setView('list')
    fetchMultisigs()
  }

  return (
    <main className="max-w-2xl mx-auto px-4 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Multisig</h1>
          <p className="text-sm text-gray-500 mt-1">Multi-chain multisig wallet</p>
        </div>
        <div className="flex items-center gap-3">
          <ChainTabs chain={chain} setChain={setChain} />
          <WalletButton chain={chain} wallet={wallet} onConnect={connect} />
        </div>
      </div>

      {error && (
        <div className="bg-red-900/30 border border-red-800 rounded-lg px-4 py-2 mb-4 text-sm text-red-300">
          {error}
        </div>
      )}

      {/* Navigation */}
      {view !== 'list' && (
        <button
          onClick={() => { setView('list'); setSelected(null) }}
          className="text-sm text-gray-400 hover:text-white mb-4 flex items-center gap-1"
        >
          &larr; Back
        </button>
      )}

      {/* List View */}
      {view === 'list' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold">{CHAINS[chain].name} Multisigs</h2>
            <button
              onClick={() => setView('create')}
              disabled={!wallet}
              className="btn-primary text-sm"
            >
              + Create
            </button>
          </div>

          {!wallet && (
            <div className="card text-center py-10 text-gray-500">
              Connect {CHAINS[chain].wallet} to get started
            </div>
          )}

          {wallet && multisigs.length === 0 && (
            <div className="card text-center py-10 text-gray-500">
              No multisigs yet. Create one to get started.
            </div>
          )}

          <div className="space-y-3">
            {multisigs.map((ms) => (
              <MultisigCard key={ms.id} ms={ms} onClick={() => selectMultisig(ms)} />
            ))}
          </div>
        </div>
      )}

      {/* Create View */}
      {view === 'create' && wallet && (
        <CreateForm
          chain={chain}
          wallet={wallet}
          onCreated={() => { setView('list'); fetchMultisigs() }}
        />
      )}

      {/* Detail View */}
      {view === 'detail' && selected && (
        <div className="space-y-4">
          <div className="card">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-lg font-semibold">{selected.name}</h2>
              <button
                onClick={() => deleteMultisig(selected.id)}
                className="text-xs text-red-400 hover:text-red-300"
              >
                Delete
              </button>
            </div>
            <div className="text-sm text-gray-400 space-y-1">
              <div>Chain: <span className="text-white">{CHAINS[selected.chain as ChainId].name}</span></div>
              <div>Threshold: <span className="text-white">{selected.threshold}/{selected.owners.length}</span></div>
              {selected.address && (
                <div>Address: <span className="font-mono text-xs text-white">{selected.address}</span></div>
              )}
              <div className="pt-2">
                <div className="text-xs text-gray-500 mb-1">Owners:</div>
                {selected.owners.map((o, i) => (
                  <div key={i} className="font-mono text-xs text-gray-300 truncate">{o}</div>
                ))}
              </div>
            </div>
          </div>

          {/* Propose */}
          {wallet && (
            <ProposeForm
              multisig={selected}
              wallet={wallet}
              chain={chain}
              onProposed={fetchTransactions}
            />
          )}

          {/* Transactions */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold">Transactions</h3>
              <button onClick={fetchTransactions} className="text-xs text-gray-400 hover:text-white">
                Refresh
              </button>
            </div>
            {transactions.length === 0 ? (
              <div className="card text-center py-6 text-gray-500 text-sm">No transactions yet</div>
            ) : (
              <div className="space-y-3">
                {transactions.map((tx) => (
                  <TxCard
                    key={tx.id}
                    tx={tx}
                    multisig={selected}
                    wallet={wallet || ''}
                    chain={chain}
                    onUpdate={fetchTransactions}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </main>
  )
}
