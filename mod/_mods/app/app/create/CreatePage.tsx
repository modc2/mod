'use client'
import { useEffect, useState } from 'react'
import { Package, Upload, Database, Send, Loader2, CheckCircle, AlertCircle } from 'lucide-react'
import {useUserContext} from '@/app/block/context/UserContext'
import { web3Enable, web3FromAddress } from '@polkadot/extension-dapp'
import { stringToU8a, u8aToHex } from '@polkadot/util'
import ModCard from '@/app/mod/explore/ModCard'
import { ModuleType } from '@/app/types'
import { UrlTypeSelector, UrlType } from './UrlTypeSelector'

export const CreateMod = ( ) => {
  const { client, localKey } = useUserContext()
  const [isSubwalletEnabled, setIsSubwalletEnabled] = useState(false)
  const [modUrl, setModUrl] = useState('')
  const [urlType, setUrlType] = useState<UrlType>('git')
  const [modName, setModName] = useState('')
  const [collateral, setCollateral] = useState(0.0)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [signatureInfo, setSignatureInfo] = useState<{signature: string, timestamp: number, address: string} | null>(null)
  const [isLocalWallet, setIsLocalWallet] = useState(false)
  const [walletAddress, setWalletAddress] = useState('')
  const [createdMod, setCreatedMod] = useState<ModuleType | null>(null)

  useEffect(() => {
    const walletMode = localStorage.getItem('wallet_mode')
    const address = localStorage.getItem('wallet_address')
    
    setIsSubwalletEnabled(walletMode === 'subwallet')
    setIsLocalWallet(walletMode === 'local')
    setWalletAddress(address || '')
  }, [client])

  const handleUrlChange = (value: string, inferredType: UrlType) => {
    setModUrl(value)
    setUrlType(inferredType)
    let name = value.split('/')[value.split('/').length - 1]
    // remove .git suffix if present
    name = name.endsWith('.git') ? name.slice(0, -4) : name
    name = name.toLowerCase()
    setModName(name)
  }

  const handleNameChange = (e) => {
    setModName(e.target.value || '')
  }

  const handleCreateModule = async () => {
    if (!modUrl.trim()) {
      setError('Please enter a valid URL or hash')
      return
    }

    setIsLoading(true)
    setError(null)
    setSuccess(null)
    setSignatureInfo(null)
    setCreatedMod(null)

    try {
      if (!client) {
        throw new Error('Client not initialized')
      }
      let signature: string
      let signerAddress: string
      let reg_payload: any
      if (isSubwalletEnabled && walletAddress) {
        reg_payload = await client.call('reg_payload', {'url': modUrl.trim(), 'key':walletAddress , 'collateral': collateral})
        let messageToSign = JSON.stringify(reg_payload)

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
      } else if (isLocalWallet) {
        if (!localKey) {
          throw new Error('Local key not found. Please sign in with Local Key.')
        }
        reg_payload = await client.call('reg_payload', {'url': modUrl.trim(), 'key':localKey.address , 'collateral': collateral})
        let messageToSign = JSON.stringify(reg_payload)
        signature = localKey.sign(messageToSign)
        signerAddress = localKey.address
      } else {
        throw new Error('No signing method available. Please connect SubWallet or sign in with Local Key first.')
      }
      const previewData = {
        ...reg_payload,
        signature: signature,
      }

      const response = await client.call('reg', {mod: previewData} )
      
      const timestamp = Date.now()
      setSignatureInfo({
        signature: signature,
        timestamp: timestamp,
        address: signerAddress
      })
      
      const newMod: ModuleType = {
        name: modName.trim() || response?.name || 'New Module',
        key: signerAddress,
        desc: response?.desc || '',
        cid: response?.cid || '',
        created: timestamp,
        updated: timestamp,
        collateral: 0
      }
      
      setCreatedMod(newMod)
      setSuccess(`Module created successfully! Response: ${JSON.stringify(response)}`)
      setModUrl('')
      setModName('')
      setCollateral(0.0)
    } catch (err: any) {
      console.error('Module creation error:', err)
      setError(err.message || 'Failed to create module')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-black via-purple-950/20 to-black">
      <div className="max-w-4xl mx-auto px-6 py-12 space-y-8">
        
        <div className="text-center space-y-3 mb-10">
          <p className="text-white text-3xl font-bold font-mono drop-shadow-[0_0_20px_rgba(255,255,255,0.3)]">
            (+) mod (+)
          </p>
        </div>

        <div className="rounded-xl bg-gradient-to-br from-purple-900/30 via-black to-cyan-900/30 border-2 border-white/60 p-8 shadow-[0_0_60px_rgba(255,255,255,0.3)]">
          <div className="space-y-6">
            <div className="space-y-3">
              <label className="flex items-center gap-2 text-white font-black text-2xl uppercase tracking-widest drop-shadow-[0_0_10px_rgba(255,255,255,0.5)]">
                <Database size={28} />
                <span>Repository URL or Storage Hash</span>
              </label>
              <UrlTypeSelector
                value={modUrl}
                onChange={handleUrlChange}
                selectedType={urlType}
                onTypeChange={setUrlType}
              />
            </div>

            <div className="space-y-3">
              <label className="flex items-center gap-2 text-white font-black text-2xl uppercase tracking-widest drop-shadow-[0_0_10px_rgba(255,255,255,0.5)]">
                <Package size={28} />
                <span>Module Name</span>
              </label>
              <div className="relative">
                <input
                  type="text"
                  value={modName}
                  onChange={handleNameChange}
                  placeholder="my-awesome-module"
                  className="w-full bg-black/90 border-2 border-white/60 rounded-lg px-4 py-5 pl-12 text-white font-mono text-xl placeholder-white/40 focus:outline-none focus:border-white focus:shadow-[0_0_30px_rgba(255,255,255,0.5)] transition-all"
                />
                <div className="absolute left-4 top-1/2 -translate-y-1/2 text-white/70">
                  <Package size={26} />
                </div>
              </div>
            </div>

            <div className="space-y-3">
              <label className="flex items-center gap-2 text-white font-black text-2xl uppercase tracking-widest drop-shadow-[0_0_10px_rgba(255,255,255,0.5)]">
                <Database size={28} />
                <span>Collateral Amount</span>
              </label>
              <div className="relative">
                <input
                  type="number"
                  step="0.01"
                  value={collateral}
                  onChange={(e) => setCollateral(parseFloat(e.target.value) || 0.0)}
                  placeholder="0.00"
                  className="w-full bg-black/90 border-2 border-white/60 rounded-lg px-4 py-5 pl-12 text-white font-mono text-xl placeholder-white/40 focus:outline-none focus:border-white focus:shadow-[0_0_30px_rgba(255,255,255,0.5)] transition-all"
                />
                <div className="absolute left-4 top-1/2 -translate-y-1/2 text-white/70">
                  <Database size={26} />
                </div>
              </div>
            </div>

            <button
              onClick={handleCreateModule}
              disabled={!modUrl.trim() || isLoading || (!isSubwalletEnabled && !isLocalWallet)}
              className="w-full py-6 bg-gradient-to-r from-purple-600 via-pink-600 to-purple-600 hover:from-purple-500 hover:via-pink-500 hover:to-purple-500 border-2 border-white/60 text-white font-black rounded-lg uppercase text-2xl disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-3 transition-all shadow-[0_0_50px_rgba(255,255,255,0.5)] hover:shadow-[0_0_60px_rgba(255,255,255,0.7)]"
            >
              {isLoading ? (
                <>
                  <Loader2 size={30} className="animate-spin" />
                  <span>DEPLOYING MODULE...</span>
                </>
              ) : (
                <>
                  <Upload size={30} />
                  <span>DEPLOY MODULE</span>
                </>
              )}
            </button>
          </div>
        </div>

        {createdMod && (
          <div className="space-y-6">
            <div className="flex items-center justify-center gap-3 text-white font-black text-3xl uppercase tracking-wide drop-shadow-[0_0_20px_rgba(255,255,255,0.6)]">
              <CheckCircle size={32} />
              <span>MODULE SUCCESSFULLY DEPLOYED</span>
            </div>
            <ModCard mod={createdMod} card_enabled={true} />
          </div>
        )}

        {signatureInfo && (
          <div className="rounded-lg bg-black/80 border-2 border-white/60 p-6 shadow-[0_0_40px_rgba(255,255,255,0.2)]">
            <div className="flex items-center gap-2 text-white font-black text-2xl uppercase mb-5 tracking-widest drop-shadow-[0_0_10px_rgba(255,255,255,0.5)]">
              <CheckCircle size={24} />
              <span>SIGNATURE VERIFICATION</span>
            </div>
            <div className="space-y-4 text-white font-mono text-lg">
              <div className="bg-black/90 p-4 rounded-lg border-2 border-white/40">
                <span className="text-white/80 block mb-2 font-black uppercase text-base">ADDRESS:</span>
                <p className="break-all text-white">{signatureInfo.address}</p>
              </div>
              <div className="bg-black/90 p-4 rounded-lg border-2 border-white/40">
                <span className="text-white/80 block mb-2 font-black uppercase text-base">TIMESTAMP:</span>
                <p className="text-white">{new Date(signatureInfo.timestamp).toISOString()}</p>
              </div>
              <div className="bg-black/90 p-4 rounded-lg border-2 border-white/40">
                <span className="text-white/80 block mb-2 font-black uppercase text-base">SIGNATURE:</span>
                <p className="break-all text-white text-base">{signatureInfo.signature}</p>
              </div>
            </div>
          </div>
        )}

        {error && (
          <div className="rounded-lg bg-black/80 border-2 border-red-500/60 p-6 shadow-[0_0_40px_rgba(239,68,68,0.3)]">
            <div className="flex items-center gap-2 text-white font-black text-2xl uppercase mb-3 tracking-widest drop-shadow-[0_0_10px_rgba(255,255,255,0.5)]">
              <AlertCircle size={24} />
              <span>ERROR</span>
            </div>
            <p className="text-white font-mono text-lg bg-black/90 p-4 rounded-lg border-2 border-red-500/40">{error}</p>
          </div>
        )}

        {success && (
          <div className="rounded-lg bg-black/80 border-2 border-green-500/60 p-6 shadow-[0_0_40px_rgba(34,197,94,0.3)]">
            <div className="flex items-center gap-2 text-white font-black text-2xl uppercase mb-3 tracking-widest drop-shadow-[0_0_10px_rgba(255,255,255,0.5)]">
              <CheckCircle size={24} />
              <span>SUCCESS</span>
            </div>
            <p className="text-white font-mono text-lg bg-black/90 p-4 rounded-lg border-2 border-green-500/40">{success}</p>
          </div>
        )}
      </div>
    </div>
  )
}

export default CreateMod