'use client'
import { useEffect, useState } from 'react'
import { Package, Upload, Database, Loader2, CheckCircle, AlertCircle } from 'lucide-react'
import {useUserContext} from '@/app/context'
import { web3Enable, web3FromAddress } from '@polkadot/extension-dapp'
import { stringToU8a, u8aToHex } from '@polkadot/util'
import ModCard from '@/app/mod/explore/ModCard'
import { ModuleType } from '@/app/types'
import { UrlTypeSelector, UrlType } from './UrlTypeSelector'

export const RegMod = ( ) => {
  const { client, localKey, network } = useUserContext()
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
  const [registrationType, setRegistrationType] = useState<'local' | 'onchain'>('local')
  const [localModules, setLocalModules] = useState<ModuleType[]>([])
  const [selectedLocalMod, setSelectedLocalMod] = useState<string>('')

  useEffect(() => {
    const walletMode = localStorage.getItem('wallet_mode')
    const address = localStorage.getItem('wallet_address')
    
    setIsSubwalletEnabled(walletMode === 'subwallet')
    setIsLocalWallet(walletMode === 'local')
    setWalletAddress(address || '')
  }, [client])

  useEffect(() => {
    const fetchLocalModules = async () => {
      if (!client) return
      try {
        const response = await client.call('mods', {})
        const localMods = response.filter((mod: ModuleType) => mod.net === 'local')
        setLocalModules(localMods)
        if (localMods.length > 0 && !selectedLocalMod) {
          setSelectedLocalMod(localMods[0].name)
        }
        console.log('Fetched local modules:', selectedLocalMod, localMods)
      } catch (err) {
        console.error('Failed to fetch local modules:', err)
      }
    }
    fetchLocalModules()
  }, [client, registrationType])

  const handleUrlChange = (value: string, inferredType: UrlType) => {
    setModUrl(value)
    setUrlType(inferredType)
    let name = value.split('/')[value.split('/').length - 1]
    name = name.endsWith('.git') ? name.slice(0, -4) : name
    name = name.toLowerCase()
    setModName(name)
  }

  const handleNameChange = (e) => {
    setModName(e.target.value || '')
  }

  const handleCreateModuleLocal = async () => {
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
        reg_payload = await client.call('reg_url', {'url': modUrl.trim(), 'key':walletAddress , 'collateral': collateral, 'payload': true})
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
        collateral: 0,
        net: 'local'
      }
      
      setCreatedMod(newMod)
      setSuccess(`Module created successfully! Response: ${JSON.stringify(response)}`)
      setModUrl('')
      setModName('')
      setCollateral(0.0)
      
      const updatedResponse = await client.call('mods', {})
      const updatedLocalMods = updatedResponse.filter((mod: ModuleType) => mod.net === 'local')
      setLocalModules(updatedLocalMods)
    } catch (err: any) {
      console.error('Module creation error:', err)
      setError(err.message || 'Failed to create module')
    } finally {
      setIsLoading(false)
    }
  }

  const handleCreateModuleOnchain = async () => {
    console.log('Starting onchain module registration with selectedLocalMod:', selectedLocalMod)
    if (!selectedLocalMod) {
      setError('Please select a local module to register onchain')
      return
    }

    setIsLoading(true)
    setError(null)
    setSuccess(null)
    setSignatureInfo(null)
    setCreatedMod(null)

    try {
      if (!network) {
        throw new Error('Network not initialized')
      }

      if (!isSubwalletEnabled || !walletAddress) {
        throw new Error('SubWallet connection required for onchain registration')
      }

      const extensions = await web3Enable('MOD')
      if (extensions.length === 0) {
        throw new Error('SubWallet not found. Please install it.')
      }
      
      const selectedMod = localModules.find(m => m.name === selectedLocalMod)
      if (!selectedMod) {
        throw new Error('Selected module not found')
      }

      const result = await network.register(
        walletAddress,
        selectedMod.name + '/' + walletAddress,
        selectedMod.cid || '',
        selectedMod.url || '0.0.0.0:8888',
      )

      const timestamp = Date.now()
      setSignatureInfo({
        signature: result.signature || 'onchain',
        timestamp: timestamp,
        address: walletAddress
      })

      const newMod: ModuleType = {
        name: selectedMod.name,
        key: walletAddress,
        desc: selectedMod.desc || '',
        cid: selectedMod.cid || '',
        created: timestamp,
        updated: timestamp,
        collateral: collateral,
        net: 'onchain'
      }

      setCreatedMod(newMod)
      setSuccess(`Module registered onchain successfully! TX: ${result.blockHash || 'pending'}`)
      setSelectedLocalMod('')
      setCollateral(0.0)
    } catch (err: any) {
      console.error('Onchain registration error:', err)
      setError(err.message || 'Failed to register module onchain')
    } finally {
      setIsLoading(false)
    }
  }

  const handleCreateModule = () => {
    if (registrationType === 'local') {
      handleCreateModuleLocal()
    } else {
      handleCreateModuleOnchain()
    }
  }

  return (
    <div className="space-y-6 animate-fadeIn">
      <div className="space-y-5 p-6 rounded-xl bg-gradient-to-br from-purple-500/10 via-pink-500/10 to-cyan-500/10 border-2 border-purple-500/30 shadow-2xl">
        <div className="flex items-center gap-3 pb-4 border-b-2 border-purple-500/30">
          <Upload size={24} className="text-purple-400" />
          <h3 className="text-2xl font-black text-purple-400 font-mono uppercase tracking-wide">Register Module</h3>
        </div>

        <div className="space-y-4">
          <div className="flex gap-3 mb-4">
            <button
              onClick={() => setRegistrationType('local')}
              className={`flex-1 px-6 py-3 rounded-xl font-bold text-sm uppercase tracking-wider transition-all border-2 ${
                registrationType === 'local'
                  ? 'bg-purple-500/30 text-purple-300 border-purple-500'
                  : 'bg-black/60 text-purple-500/60 border-purple-500/30 hover:bg-purple-500/10 hover:border-purple-500/50'
              }`}
            >
              LOCAL (API)
            </button>
            <button
              onClick={() => setRegistrationType('onchain')}
              className={`flex-1 px-6 py-3 rounded-xl font-bold text-sm uppercase tracking-wider transition-all border-2 ${
                registrationType === 'onchain'
                  ? 'bg-purple-500/30 text-purple-300 border-purple-500'
                  : 'bg-black/60 text-purple-500/60 border-purple-500/30 hover:bg-purple-500/10 hover:border-purple-500/50'
              }`}
            >
              ONCHAIN (NETWORK)
            </button>
          </div>

          {registrationType === 'local' ? (
            <>
              <div className="space-y-2">
                <label className="text-sm text-purple-400 font-mono uppercase font-bold tracking-wide">
                  Module Source
                </label>
                <UrlTypeSelector
                  value={modUrl}
                  onChange={handleUrlChange}
                  selectedType={urlType}
                  onTypeChange={setUrlType}
                />
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <label className="text-sm text-purple-400 font-mono uppercase font-bold tracking-wide">
                    Module Name
                  </label>
                  <div className="relative">
                    <input
                      type="text"
                      value={modName}
                      onChange={handleNameChange}
                      placeholder="my-awesome-module"
                      className="w-full bg-black/60 border-2 border-purple-500/40 rounded-lg px-4 py-3 pl-11 text-purple-300 font-mono text-base placeholder-purple-600/50 focus:outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500/30 transition-all"
                    />
                    <div className="absolute left-3 top-1/2 -translate-y-1/2 text-purple-500/70">
                      <Package size={20} />
                    </div>
                  </div>
                </div>

              </div>
            </>
          ) : (
            <>
              <div className="space-y-2">
                <label className="text-sm text-purple-400 font-mono uppercase font-bold tracking-wide">
                  Select Local Module
                </label>
                <select
                  value={selectedLocalMod}
                  onChange={(e) => setSelectedLocalMod(e.target.value)}
                  className="w-full bg-black/60 border-2 border-purple-500/40 rounded-lg px-4 py-3 text-purple-300 font-mono text-base focus:outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500/30 transition-all"
                >
                  <option value="">-- Select a local module --</option>
                  {localModules.map((mod) => (
                    <option key={mod.name} value={mod.name}>
                      {mod.name}
                    </option>
                  ))}
                </select>
                {localModules.length === 0 && (
                  <p className="text-purple-400/60 text-sm font-mono mt-2">
                    No local modules found. Please register a module locally first.
                  </p>
                )}
              </div>

            </>
          )}

          <button
            onClick={handleCreateModule}
            disabled={(!isSubwalletEnabled && !isLocalWallet) || (registrationType === 'onchain' && !selectedLocalMod) || (registrationType === 'local' && !modUrl.trim())}
            className="w-full py-4 border-2 border-purple-500/60 bg-gradient-to-r from-purple-500/20 to-pink-500/20 text-purple-400 hover:bg-purple-500/30 hover:border-purple-500 hover:scale-[1.02] transition-all duration-300 rounded-xl font-mono uppercase font-black text-lg disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100 flex items-center justify-center gap-3 shadow-lg"
          >
            {isLoading ? (
              <>
                <Loader2 size={20} className="animate-spin" />
                <span>DEPLOYING...</span>
              </>
            ) : (
              <>
                <Upload size={20} />
                <span>DEPLOY {registrationType === 'onchain' ? 'ONCHAIN' : 'LOCALLY'}</span>
              </>
            )}
          </button>
        </div>
      </div>

      {createdMod && (
        <div className="space-y-4">
          <div className="flex items-center justify-center gap-3 text-emerald-400 font-black text-xl uppercase tracking-wide">
            <CheckCircle size={24} />
            <span>MODULE DEPLOYED SUCCESSFULLY</span>
          </div>
          <ModCard mod={createdMod} card_enabled={true} />
        </div>
      )}

      {signatureInfo && (
        <div className="space-y-4 p-6 rounded-xl border-2 border-emerald-500/40 bg-gradient-to-br from-emerald-500/10 shadow-2xl">
          <div className="flex items-center gap-3 text-base font-mono uppercase font-bold text-emerald-400">
            <CheckCircle size={20} />
            <span>SIGNATURE VERIFICATION</span>
          </div>
          <div className="space-y-3">
            <div className="bg-black/60 p-4 rounded-lg border-2 border-emerald-500/30">
              <span className="text-emerald-500/70 block mb-2 font-mono uppercase text-xs font-bold">ADDRESS:</span>
              <p className="break-all text-emerald-300 font-mono text-sm">{signatureInfo.address}</p>
            </div>
            <div className="bg-black/60 p-4 rounded-lg border-2 border-emerald-500/30">
              <span className="text-emerald-500/70 block mb-2 font-mono uppercase text-xs font-bold">TIMESTAMP:</span>
              <p className="text-emerald-300 font-mono text-sm">{new Date(signatureInfo.timestamp).toISOString()}</p>
            </div>
            <div className="bg-black/60 p-4 rounded-lg border-2 border-emerald-500/30">
              <span className="text-emerald-500/70 block mb-2 font-mono uppercase text-xs font-bold">SIGNATURE:</span>
              <p className="break-all text-emerald-300 font-mono text-xs">{signatureInfo.signature}</p>
            </div>
          </div>
        </div>
      )}

      {error && (
        <div className="space-y-4 p-6 rounded-xl border-2 border-red-500/40 bg-gradient-to-br from-red-500/10 shadow-2xl">
          <div className="flex items-center gap-3 text-base font-mono uppercase font-bold text-red-400">
            <AlertCircle size={20} />
            <span>ERROR</span>
          </div>
          <div className="text-red-400 font-mono text-base bg-black/60 p-4 rounded-lg border-2 border-red-500/30 whitespace-pre-wrap font-bold">
            {error}
          </div>
        </div>
      )}

      {success && (
        <div className="space-y-4 p-6 rounded-xl border-2 border-emerald-500/40 bg-gradient-to-br from-emerald-500/10 shadow-2xl">
          <div className="flex items-center gap-3 text-base font-mono uppercase font-bold text-emerald-400">
            <CheckCircle size={20} />
            <span>SUCCESS</span>
          </div>
          <pre className="text-emerald-400 font-mono text-sm overflow-x-auto bg-black/60 p-4 rounded-lg border-2 border-emerald-500/30">
{success}
          </pre>
        </div>
      )}
    </div>
  )
}

export default RegMod