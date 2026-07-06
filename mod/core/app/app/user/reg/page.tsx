"use client";
import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { userContext } from '@/context/UserContext'
// dynamic import: @polkadot/extension-dapp accesses window at load time
import { stringToU8a, u8aToHex } from '@polkadot/util'
import { Loader2, Upload } from 'lucide-react'
import { Key } from '@/key'
import { cryptoWaitReady } from '@polkadot/util-crypto'

export default function CreateModPage() {
  const router = useRouter()
  const { client } = userContext()
  const [modName, setModName] = useState('')
  const [modUrl, setModUrl] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [isSubwalletEnabled, setIsSubwalletEnabled] = useState(false)
  const [walletAddress, setWalletAddress] = useState('')
  const [localKey, setLocalKey] = useState<Key | null>(null)

  useEffect(() => {
    const init = async () => {
      await cryptoWaitReady()
      const walletMode = localStorage.getItem('wallet_mode')
      const address = localStorage.getItem('wallet_address')
      setIsSubwalletEnabled(walletMode === 'subwallet')
      setWalletAddress(address || '')

      if (walletMode === 'local') {
        const password = localStorage.getItem('wallet_password')
        if (password) {
          const key = new Key(password)
          setLocalKey(key)
        }
      }
    }
    init()
  }, [])

  const handleCreate = async () => {
    if (!modName.trim() || !modUrl.trim()) {
      setError('Please enter both name and URL')
      return
    }

    setIsLoading(true)
    setError(null)

    try {
      if (!client) {
        throw new Error('Client not initialized')
      }

      let signature: string
      let signerAddress: string
      let reg_payload: any

      if (isSubwalletEnabled && walletAddress) {
        reg_payload = await client.call('reg_url', {'url': modUrl.trim(), 'key': walletAddress, 'payload': true})
        let messageToSign = JSON.stringify(reg_payload)

        const { web3Enable, web3FromAddress } = await import('@polkadot/extension-dapp')
        const extensions = await web3Enable('MOD')
        if (extensions.length === 0) {
          throw new Error('SubWallet not found. Please install it.')
        }

        const injector = await web3FromAddress(walletAddress)
        const signRaw = injector?.signer?.signRaw
        if (signRaw) {
          const { signature: sig } = await signRaw({
            address: walletAddress,
            data: u8aToHex(stringToU8a(messageToSign)),
            type: 'bytes'
          })
          signature = sig
          signerAddress = walletAddress
        } else {
          throw new Error('SubWallet signing not available')
        }
      } else if (localKey) {
        reg_payload = await client.call('reg_payload', {'url': modUrl.trim(), 'key': localKey.address})
        let messageToSign = JSON.stringify(reg_payload)
        signature = localKey.sign(messageToSign)
        signerAddress = localKey.address
      } else {
        throw new Error('No signing method available')
      }

      const previewData = {
        ...reg_payload,
        name: modName.trim(),
        signature: signature,
      }

      const response = await client.call('reg', {mod: previewData}, true, {}, 120000)
      
      router.push(`/mod/${response.name}`)
    } catch (err: any) {
      console.error('Module creation error:', err)
      setError(err.message || 'Failed to create module')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="min-h-screen p-8" style={{ backgroundColor: 'var(--bg-primary)' }}>
      <div className="max-w-2xl mx-auto space-y-6">
        <h1 className="text-4xl font-black text-cyan-400 font-mono uppercase tracking-wide">Create New Module</h1>
        
        <div className="space-y-4 p-6 rounded-xl bg-gradient-to-br from-cyan-500/10 via-blue-500/10 to-purple-500/10 border-2 border-cyan-500/30">
          <div className="space-y-2">
            <label className="text-sm text-cyan-400 font-mono uppercase font-bold">Module Name</label>
            <input
              type="text"
              value={modName}
              onChange={(e) => setModName(e.target.value)}
              placeholder="my-awesome-module"
              className="w-full bg-black/60 border-2 border-cyan-500/40 rounded-lg px-4 py-3 text-cyan-300 font-mono placeholder-cyan-600/50 focus:outline-none focus:border-cyan-500"
            />
          </div>

          <div className="space-y-2">
            <label className="text-sm text-cyan-400 font-mono uppercase font-bold">Module URL or IPFS Hash</label>
            <input
              type="text"
              value={modUrl}
              onChange={(e) => setModUrl(e.target.value)}
              placeholder="https://github.com/user/repo or ipfs://Qm..."
              className="w-full bg-black/60 border-2 border-cyan-500/40 rounded-lg px-4 py-3 text-cyan-300 font-mono placeholder-cyan-600/50 focus:outline-none focus:border-cyan-500"
            />
          </div>

          {error && (
            <div className="p-4 bg-red-500/20 border border-red-500/40 rounded-lg text-red-400 font-mono text-sm">
              {error}
            </div>
          )}

          <button
            onClick={handleCreate}
            disabled={isLoading || !modName.trim() || !modUrl.trim()}
            className="w-full py-4 border-2 border-cyan-500/60 bg-gradient-to-r from-cyan-500/20 to-blue-500/20 text-cyan-400 hover:bg-cyan-500/30 hover:border-cyan-500 transition-all rounded-xl font-mono uppercase font-black text-lg disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-3"
          >
            {isLoading ? (
              <>
                <Loader2 size={20} className="animate-spin" />
                <span>CREATING...</span>
              </>
            ) : (
              <>
                <Upload size={20} />
                <span>CREATE MODULE</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
