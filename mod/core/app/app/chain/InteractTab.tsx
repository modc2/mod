"use client"

// INTERACT — call any contract the console knows about: the deployed fleet,
// anything you built here, or a pasted address + ABI. Reads go through the
// network RPC; writes are signed by the wallet in the strip above.

import { useState, useEffect, useCallback, useMemo } from 'react'
import { ethers } from 'ethers'
import { toast } from 'react-toastify'
import {
  TERM_FONT, ACCENT, READ, WRITE, DANGER, chainApi, coerceArgs, jsonify,
  placeholderFor, readProvider, short, explorerUrl, txUrl, useIsMobile,
} from './shared'
import { Panel, Label, Btn, Input, Empty, panelStyle } from './ui'
import type { ChainWallet } from './WalletBar'

export interface InteractTarget {
  name: string
  address: string
  abi: any[]
  /** where the store mod holds this ABI, when it holds it */
  abiCid?: string
}

interface AbiFn {
  name: string
  inputs: { name: string; type: string }[]
  outputs: { name: string; type: string }[]
  stateMutability: string
}

const isRead = (f: AbiFn) => f.stateMutability === 'view' || f.stateMutability === 'pure'

export function InteractTab({
  wallet, network, target, setTarget,
}: {
  wallet: ChainWallet
  network: string
  target: InteractTarget | null
  setTarget: (t: InteractTarget | null) => void
}) {
  const [fleet, setFleet] = useState<InteractTarget[]>([])
  const [builds, setBuilds] = useState<InteractTarget[]>([])
  const [loading, setLoading] = useState(true)

  const [fn, setFn] = useState<string>('')
  const [args, setArgs] = useState<Record<string, string>>({})
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)

  const [manualAddr, setManualAddr] = useState('')
  const [manualAbi, setManualAbi] = useState('')
  const [manualCid, setManualCid] = useState('')
  const [fetchingCid, setFetchingCid] = useState(false)
  const [showManual, setShowManual] = useState(false)
  const mobile = useIsMobile()

  // ── load callable contracts ──
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    const q = wallet.address ? `?address=${wallet.address}&` : '?'
    Promise.all([
      chainApi(`/contracts/abis?network=${network}`).catch(() => ({ contracts: [] })),
      chainApi(`/build/deployments${q}network=${network}`).catch(() => ({ deployments: [] })),
    ]).then(([abis, mine]) => {
      if (cancelled) return
      setFleet((abis.contracts || []).map((c: any) => (
        { name: c.name, address: c.address, abi: c.abi, abiCid: c.abi_cid })))
      setBuilds((mine.deployments || [])
        .filter((d: any) => d.abi?.length)
        .map((d: any) => ({ name: d.name, address: d.address, abi: d.abi, abiCid: d.abi_cid })))
      setLoading(false)
    })
    return () => { cancelled = true }
  }, [network, wallet.address])

  const fns: AbiFn[] = useMemo(() => {
    if (!target?.abi) return []
    return target.abi
      .filter((f: any) => f.type === 'function')
      .map((f: any) => ({
        name: f.name, inputs: f.inputs || [], outputs: f.outputs || [],
        stateMutability: f.stateMutability || 'nonpayable',
      }))
      .sort((a: AbiFn, b: AbiFn) =>
        Number(isRead(b)) - Number(isRead(a)) || a.name.localeCompare(b.name))
  }, [target])

  const current = fns.find(f => f.name === fn) || null

  useEffect(() => { setFn(''); setArgs({}); setResult(null); setError(null); setValue('') }, [target?.address])
  useEffect(() => { setArgs({}); setResult(null); setError(null) }, [fn])

  const pick = (t: InteractTarget) => setTarget(t)

  const addManual = () => {
    try {
      if (!ethers.isAddress(manualAddr.trim())) throw new Error('invalid address')
      const abi = JSON.parse(manualAbi)
      if (!Array.isArray(abi)) throw new Error('ABI must be a JSON array')
      setTarget({ name: short(manualAddr.trim()), address: ethers.getAddress(manualAddr.trim()), abi })
      setShowManual(false)
    } catch (e: any) {
      toast.error(e?.message || 'invalid ABI')
    }
  }

  /**
   * Load an ABI the store mod is holding. Deploying from BUILD stores one for
   * every contract, so a CID is all it takes to drive a contract deployed from
   * another project, another wallet or another machine.
   */
  const loadCid = async () => {
    const cid = manualCid.trim()
    if (!cid) return
    if (!ethers.isAddress(manualAddr.trim())) { toast.error('enter the contract address too'); return }
    setFetchingCid(true)
    try {
      const d = await chainApi(`/build/abi/${encodeURIComponent(cid)}`)
      setTarget({
        name: short(manualAddr.trim()),
        address: ethers.getAddress(manualAddr.trim()),
        abi: d.abi,
        abiCid: cid,
      })
      setShowManual(false)
      toast.success(`ABI loaded — ${d.abi.length} entries`)
    } catch (e: any) {
      toast.error(e?.message || 'could not load that CID')
    } finally {
      setFetchingCid(false)
    }
  }

  const execute = useCallback(async () => {
    if (!target || !current) return
    setBusy(true); setResult(null); setError(null)
    try {
      const callArgs = coerceArgs(current.inputs, args)
      if (isRead(current)) {
        const contract = new ethers.Contract(target.address, target.abi, readProvider(network))
        const out = await contract[current.name](...callArgs)
        setResult(out)
      } else {
        if (!wallet.kind) throw new Error('Sign in with a wallet to send a transaction')
        const signer = await wallet.signer()
        const contract = new ethers.Contract(target.address, target.abi, signer)
        const overrides = current.stateMutability === 'payable' && value.trim()
          ? { value: ethers.parseEther(value.trim()) }
          : {}
        const tx = await contract[current.name](...callArgs, overrides)
        const receipt = await tx.wait()
        setResult({ tx_hash: tx.hash, status: receipt?.status === 1 ? 'success' : 'failed',
          block: receipt?.blockNumber, gas_used: receipt?.gasUsed })
        toast.success(`${current.name} confirmed`)
        wallet.refresh()
      }
    } catch (e: any) {
      setError(e?.shortMessage || e?.reason || e?.message || 'call failed')
    } finally {
      setBusy(false)
    }
  }, [target, current, args, value, network, wallet])

  const Group = ({ label, items, showAddress }: {
    label: string; items: InteractTarget[]; showAddress?: boolean
  }) => (
    items.length === 0 ? null : (
      <div style={{ marginBottom: '12px' }}>
        <Label>{label}</Label>
        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
          {items.map(c => (
            <Btn key={`${label}-${c.address}`} size="sm"
              active={target?.address?.toLowerCase() === c.address.toLowerCase()}
              title={c.address} onClick={() => pick(c)}>
              {/* builds repeat names — the address is what tells them apart */}
              {showAddress ? `${c.name} · ${short(c.address, 6, 4)}` : c.name}
            </Btn>
          ))}
        </div>
      </div>
    )
  )

  return (
    <div>
      {loading ? <Empty>Loading contracts…</Empty> : (
        <>
          <Group label={`MY BUILDS — ${network}`} items={builds} showAddress />
          <Group label={`FLEET CONTRACTS — ${network}`} items={fleet} />
        </>
      )}

      <div style={{ marginBottom: '16px' }}>
        <Btn size="sm" active={showManual} onClick={() => setShowManual(s => !s)}>
          + ANY CONTRACT
        </Btn>
        {showManual && (
          <Panel style={{ marginTop: '8px' }}>
            <Label>ADDRESS</Label>
            <Input value={manualAddr} onChange={setManualAddr} placeholder="0x…" />

            <Label style={{ marginTop: '14px', color: READ }} note="from the store">ABI CID</Label>
            <Input value={manualCid} onChange={setManualCid} placeholder="Qm…" onEnter={loadCid} />
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginTop: '8px', flexWrap: 'wrap' }}>
              <Btn size="sm" color={READ} onClick={loadCid} disabled={fetchingCid || !manualCid.trim()}>
                {fetchingCid ? 'LOADING…' : 'LOAD FROM CID'}
              </Btn>
              <span style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)', lineHeight: 1.5 }}>
                every deploy stores its ABI — the CID is on the build
              </span>
            </div>

            <Label style={{ marginTop: '14px' }} note="paste the JSON instead">OR ABI</Label>
            <textarea
              value={manualAbi}
              onChange={e => setManualAbi(e.target.value)}
              spellCheck={false}
              autoCapitalize="off"
              autoCorrect="off"
              placeholder='[{"type":"function","name":"…"}]'
              style={{
                width: '100%', height: '120px', fontFamily: TERM_FONT,
                fontSize: mobile ? '16px' : '14px',
                padding: '8px', border: '1px solid var(--border-color)', background: 'transparent',
                color: 'var(--text-primary)', outline: 'none', resize: 'vertical',
              }}
            />
            <div style={{ marginTop: '10px' }}>
              <Btn size="sm" onClick={addManual} disabled={!manualAbi.trim()}>LOAD PASTED ABI</Btn>
            </div>
          </Panel>
        )}
      </div>

      {!target ? (
        <Empty>Pick a contract to call.</Empty>
      ) : (
        <>
          <Panel style={{ marginBottom: '16px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontFamily: TERM_FONT, fontSize: '14px', color: 'var(--text-primary)' }}>{target.name}</div>
                <div style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)', wordBreak: 'break-all' }}>
                  {target.address}
                </div>
                {target.abiCid && (
                  <div style={{ fontFamily: TERM_FONT, fontSize: '13px', color: READ, wordBreak: 'break-all', marginTop: '3px' }}>
                    abi {target.abiCid}
                  </div>
                )}
              </div>
              <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                <Btn size="sm" active={false} onClick={() => {
                  navigator.clipboard.writeText(target.address); toast.success('Address copied')
                }}>COPY</Btn>
                {target.abiCid && (
                  <Btn size="sm" active={false} color={READ} onClick={() => {
                    navigator.clipboard.writeText(target.abiCid!); toast.success('ABI CID copied')
                  }}>ABI CID</Btn>
                )}
                {explorerUrl(network, target.address) && (
                  <a href={explorerUrl(network, target.address)} target="_blank" rel="noreferrer"
                    style={{
                      fontFamily: TERM_FONT, fontSize: '13px', padding: '4px 8px',
                      border: '1px solid var(--border-color)', color: 'var(--text-tertiary)', textDecoration: 'none',
                    }}>EXPLORER ↗</a>
                )}
              </div>
            </div>
          </Panel>

          <Label note={<><span style={{ color: READ }}>○ read</span> · <span style={{ color: WRITE }}>● write</span></>}>METHODS</Label>
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '16px' }}>
            {fns.map(f => (
              <Btn key={f.name} size="sm" color={isRead(f) ? READ : WRITE} active={fn === f.name}
                onClick={() => setFn(f.name)}>
                {isRead(f) ? '○' : '●'} {f.name}
              </Btn>
            ))}
            {fns.length === 0 && <Empty>This ABI has no callable functions.</Empty>}
          </div>

          {current && (
            <>
              {current.inputs.length > 0 && (
                <Panel style={{ marginBottom: '16px' }}>
                  <Label>PARAMETERS</Label>
                  {current.inputs.map((inp, i) => {
                    const key = inp.name || `arg${i}`
                    return (
                      <div key={key} style={{ marginBottom: '10px' }}>
                        <div style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)', marginBottom: '4px' }}>
                          {inp.name || `arg${i}`} <span style={{ opacity: 0.6 }}>({inp.type})</span>
                        </div>
                        <Input
                          value={args[key] || ''}
                          onChange={v => setArgs(prev => ({ ...prev, [key]: v }))}
                          placeholder={placeholderFor(inp.type)}
                        />
                      </div>
                    )
                  })}
                </Panel>
              )}

              <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap', marginBottom: '16px' }}>
                <Btn onClick={execute} disabled={busy || (!isRead(current) && !wallet.kind)}
                  full color={isRead(current) ? READ : WRITE}>
                  {busy ? '…' : isRead(current) ? 'CALL (read)' : `SEND (write)${wallet.kind ? ` [${wallet.kind.toUpperCase()}]` : ''}`}
                </Btn>
                {current.stateMutability === 'payable' && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <span style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)' }}>VALUE</span>
                    <div style={{ width: '120px' }}>
                      <Input value={value} onChange={setValue} placeholder="0.0 ETH" />
                    </div>
                  </div>
                )}
                {!isRead(current) && !wallet.kind && (
                  <span style={{ fontFamily: TERM_FONT, fontSize: '13px', color: WRITE }}>sign in above to send</span>
                )}
              </div>
            </>
          )}

          {error && (
            <div style={{
              fontFamily: TERM_FONT, fontSize: '14px', color: DANGER,
              border: `1px solid ${DANGER}`, padding: '12px', background: 'rgba(239,68,68,0.05)',
            }}>
              {error}
            </div>
          )}

          {result !== null && !error && (
            <div style={{ ...panelStyle, padding: '12px' }}>
              <Label style={{ color: ACCENT }}>RESULT</Label>
              <pre style={{
                fontFamily: TERM_FONT, fontSize: '14px', color: 'var(--text-primary)',
                whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0,
              }}>
                {typeof result === 'string' ? result : jsonify(result)}
              </pre>
              {result?.tx_hash && txUrl(network, result.tx_hash) && (
                <a href={txUrl(network, result.tx_hash)} target="_blank" rel="noreferrer"
                  style={{
                    display: 'inline-block', marginTop: '10px', fontFamily: TERM_FONT, fontSize: '13px',
                    padding: '4px 8px', border: '1px solid var(--border-color)',
                    color: 'var(--text-tertiary)', textDecoration: 'none',
                  }}>
                  TX ↗
                </a>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
