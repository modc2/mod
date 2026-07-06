"use client"

import { useState, useEffect } from 'react'
import { ethers } from 'ethers'
import { useMetaMask } from '@/wallet/MetaMaskProvider'
import { Game } from './types'
import { COPLAY_HUB_ABI } from './abi'

type PaymentStep = 'ready' | 'signing' | 'pending' | 'confirmed' | 'error'
type WalletMode = 'metamask' | 'local'

interface PaymentFlowProps {
  game: Game
  contractAddress: string | null
  onSuccess: () => void
  onCancel: () => void
}

export default function PaymentFlow({ game, contractAddress, onSuccess, onCancel }: PaymentFlowProps) {
  const { signer, provider: mmProvider, isConnected, connect } = useMetaMask()
  const [step, setStep] = useState<PaymentStep>('ready')
  const [txHash, setTxHash] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [walletMode, setWalletMode] = useState<WalletMode>('metamask')
  const [passphrase, setPassphrase] = useState('')

  const feeNum = parseFloat(game.entry_fee || '0')

  useEffect(() => {
    const saved = typeof window !== 'undefined' ? localStorage.getItem('wallet_mode') : null
    if (saved === 'local') setWalletMode('local')
    else setWalletMode('metamask')
  }, [])

  const payViaContract = async (txSigner: ethers.Signer) => {
    if (!contractAddress) throw new Error('Contract address not configured')
    if (game.chain_game_id === null || game.chain_game_id === undefined) {
      throw new Error('Game has no on-chain ID')
    }

    const contract = new ethers.Contract(contractAddress, COPLAY_HUB_ABI, txSigner)
    const entryFeeWei = ethers.parseEther(game.entry_fee || '0')
    const tx = await contract.joinGame(game.chain_game_id, { value: entryFeeWei })
    return tx
  }

  const payDirect = async (txSigner: ethers.Signer) => {
    const entryFeeWei = ethers.parseEther(game.entry_fee || '0')
    const tx = await txSigner.sendTransaction({
      to: game.organizer,
      value: entryFeeWei,
    })
    return tx
  }

  const handlePay = async () => {
    setError('')
    setStep('signing')

    try {
      let tx: ethers.TransactionResponse

      if (walletMode === 'metamask') {
        if (!signer) {
          await connect()
          setStep('ready')
          return
        }
        tx = contractAddress && game.chain_game_id !== null
          ? await payViaContract(signer)
          : await payDirect(signer)
      } else {
        // Local wallet
        if (!passphrase) {
          setError('Passphrase required for local wallet')
          setStep('ready')
          return
        }

        const { blake2AsHex } = await import('@polkadot/util-crypto')
        const seedHex = blake2AsHex(passphrase, 256)

        let provider: ethers.Provider
        if (mmProvider) {
          provider = mmProvider
        } else if (typeof window !== 'undefined' && window.ethereum) {
          provider = new ethers.BrowserProvider(window.ethereum)
        } else {
          provider = new ethers.JsonRpcProvider('https://sepolia.base.org')
        }

        const wallet = new ethers.Wallet(seedHex, provider)

        tx = contractAddress && game.chain_game_id !== null
          ? await payViaContract(wallet)
          : await payDirect(wallet)
      }

      setTxHash(tx.hash)
      setStep('pending')

      await tx.wait()
      setStep('confirmed')

      setTimeout(() => onSuccess(), 1500)
    } catch (err: any) {
      console.error('Payment error:', err)
      if (err.code === 4001 || err.code === 'ACTION_REJECTED') {
        setError('Transaction rejected')
      } else {
        setError(err.reason || err.message || 'Payment failed')
      }
      setStep('error')
    }
  }

  const stepColors: Record<PaymentStep, string> = {
    ready: 'rgb(245 158 11)',
    signing: 'rgb(99 102 241)',
    pending: 'rgb(99 102 241)',
    confirmed: 'rgb(16 185 129)',
    error: 'rgb(239 68 68)',
  }

  const color = stepColors[step]

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center font-mono" onClick={onCancel}>
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" />

      <div
        className="relative w-full max-w-md mx-4 border-4"
        style={{ backgroundColor: 'var(--bg-primary)', borderColor: `${color}60` }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-5 py-4 flex items-center gap-2" style={{ borderBottom: `1px solid ${color}30` }}>
          <span className="text-[14px] font-extrabold" style={{ color }}>&gt;_</span>
          <span className="text-[14px] font-extrabold uppercase tracking-[0.15em]" style={{ color: 'var(--text-primary)' }}>
            {step === 'confirmed' ? 'PAYMENT CONFIRMED' : step === 'error' ? 'PAYMENT FAILED' : 'JOIN GAME'}
          </span>
          <button
            onClick={onCancel}
            className="ml-auto text-[12px] font-extrabold hover:opacity-70 transition-opacity"
            style={{ color: 'var(--text-tertiary)' }}
          >
            [X]
          </button>
        </div>

        <div className="px-5 py-5 space-y-4">
          {/* Game info summary */}
          <div className="p-3 border-4" style={{ borderColor: 'var(--border-color)' }}>
            <div className="text-[13px] font-extrabold uppercase" style={{ color: 'var(--text-primary)' }}>
              {game.title}
            </div>
            <div className="text-[12px] font-bold mt-1" style={{ color: 'var(--text-tertiary)' }}>
              {game.location}{game.date ? ` — ${game.date}` : ''}
            </div>
          </div>

          {/* Amount */}
          <div className="text-center py-3">
            <div className="text-[10px] font-extrabold uppercase tracking-[0.3em] mb-1" style={{ color: 'var(--text-tertiary)' }}>
              ENTRY FEE
            </div>
            <div className="text-[32px] font-extrabold" style={{ color }}>
              {feeNum > 0 ? game.entry_fee : '0'} <span className="text-[16px]">ETH</span>
            </div>
          </div>

          {/* Wallet mode toggle */}
          {step === 'ready' && (
            <div>
              <div className="text-[10px] font-extrabold uppercase tracking-[0.3em] mb-2" style={{ color: 'var(--text-tertiary)' }}>
                PAY WITH
              </div>
              <div className="flex gap-2">
                {(['metamask', 'local'] as const).map(mode => (
                  <button
                    key={mode}
                    onClick={() => setWalletMode(mode)}
                    className="flex-1 py-2.5 text-[12px] font-extrabold uppercase tracking-wider transition-all border-4"
                    style={{
                      backgroundColor: walletMode === mode ? `${mode === 'metamask' ? 'rgb(246 133 27' : 'rgb(120 169 255'}/ 0.15)` : 'transparent',
                      color: walletMode === mode ? (mode === 'metamask' ? 'rgb(246 133 27)' : 'rgb(120 169 255)') : 'var(--text-secondary)',
                      borderColor: walletMode === mode ? `${mode === 'metamask' ? 'rgb(246 133 27' : 'rgb(120 169 255)'} / 0.4)` : 'var(--border-color)',
                    }}
                  >
                    {mode === 'metamask' ? 'METAMASK' : 'LOCAL WALLET'}
                  </button>
                ))}
              </div>

              {/* Local wallet passphrase */}
              {walletMode === 'local' && (
                <div className="mt-3">
                  <input
                    type="password"
                    value={passphrase}
                    onChange={e => setPassphrase(e.target.value)}
                    className="w-full px-4 py-3 text-[14px] font-mono font-bold focus:outline-none"
                    style={{
                      backgroundColor: 'var(--bg-input)',
                      border: '4px solid var(--border-color)',
                      color: 'var(--text-primary)',
                    }}
                    placeholder="ENTER PASSPHRASE"
                  />
                  <p className="text-[10px] mt-1 font-bold" style={{ color: 'var(--text-tertiary)' }}>
                    Your key is derived from this passphrase
                  </p>
                </div>
              )}

              {/* MetaMask connect prompt */}
              {walletMode === 'metamask' && !isConnected && (
                <div className="mt-3 p-3 border-4 flex items-center gap-3" style={{ borderColor: 'rgb(246 133 27 / 0.3)' }}>
                  <span className="text-[12px] font-bold" style={{ color: 'rgb(246 133 27)' }}>
                    MetaMask will prompt you to connect
                  </span>
                </div>
              )}
            </div>
          )}

          {/* Status display */}
          {step === 'signing' && (
            <div className="flex items-center justify-center gap-3 py-4">
              <div className="w-5 h-5 border-2 border-t-transparent animate-spin rounded-full" style={{ borderColor: `${color}40`, borderTopColor: color }} />
              <span className="text-[14px] font-extrabold uppercase" style={{ color }}>
                SIGN IN WALLET...
              </span>
            </div>
          )}

          {step === 'pending' && (
            <div className="space-y-3">
              <div className="flex items-center justify-center gap-3 py-2">
                <div className="w-5 h-5 border-2 border-t-transparent animate-spin rounded-full" style={{ borderColor: `${color}40`, borderTopColor: color }} />
                <span className="text-[14px] font-extrabold uppercase" style={{ color }}>
                  CONFIRMING...
                </span>
              </div>
              {txHash && (
                <div className="p-3 border-4" style={{ borderColor: 'var(--border-color)' }}>
                  <div className="text-[10px] font-extrabold uppercase tracking-[0.2em] mb-1" style={{ color: 'var(--text-tertiary)' }}>TX HASH</div>
                  <div className="text-[11px] font-mono font-bold break-all" style={{ color: 'var(--text-primary)' }}>
                    {txHash}
                  </div>
                </div>
              )}
            </div>
          )}

          {step === 'confirmed' && (
            <div className="flex items-center justify-center gap-3 py-4">
              <svg className="w-6 h-6" style={{ color }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
              </svg>
              <span className="text-[14px] font-extrabold uppercase" style={{ color }}>
                PAYMENT CONFIRMED
              </span>
            </div>
          )}

          {/* Error */}
          {error && (
            <div className="p-3 border-4" style={{ borderColor: 'rgb(239 68 68 / 0.3)', backgroundColor: 'rgb(239 68 68 / 0.08)' }}>
              <span className="text-[12px] font-extrabold" style={{ color: 'rgb(239 68 68)' }}>{error}</span>
            </div>
          )}

          {/* Action buttons */}
          {(step === 'ready' || step === 'error') && (
            <div className="flex gap-3 pt-2">
              <button
                onClick={handlePay}
                className="flex-1 py-3 text-[14px] font-extrabold uppercase tracking-wider transition-all hover:brightness-110"
                style={{ backgroundColor: 'rgb(16 185 129)', color: '#000' }}
              >
                {feeNum > 0 ? `PAY ${game.entry_fee} ETH` : 'JOIN (FREE)'}
              </button>
              <button
                onClick={onCancel}
                className="px-6 py-3 text-[14px] font-extrabold uppercase tracking-wider border-4 transition-all"
                style={{ color: 'var(--text-secondary)', borderColor: 'var(--border-color)' }}
              >
                CANCEL
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
